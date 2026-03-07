import Foundation
import SwiftUI

/// Handles reading/writing JSON files to the Mira iCloud Drive folder.
@MainActor
@Observable
final class BridgeService {

    // MARK: - State

    var messages: [TBMessage] = []
    var acks: [String: TBAck] = [:]       // messageId → ack
    var heartbeat: TBHeartbeat?
    var threads: [TBThread] = []
    var currentThreadId: String = ""
    var agentOnline: Bool { heartbeat?.isRecent ?? false }
    var isSetup: Bool { bridgeURL != nil }
    var error: String?
    var debugLog: String = ""

    // Task-based state (new dashboard UI)
    var tasks: [MiraTask] = []
    var needsInputCount: Int { tasks.filter(\.needsInput).count }
    var activeTasks: [MiraTask] { tasks.filter(\.isActive).sorted { $0.updatedDate > $1.updatedDate } }
    var doneTasks: [MiraTask] { tasks.filter { $0.status == "done" }.sorted { $0.updatedDate > $1.updatedDate } }
    var todayBriefings: [MiraTask] {
        let cal = Calendar.current
        return tasks.filter { $0.isBriefing && cal.isDateInToday($0.createdDate) }
    }
    var todayJournals: [MiraTask] {
        let cal = Calendar.current
        return tasks.filter { $0.isJournal && cal.isDateInToday($0.createdDate) }
    }

    /// Expose the base URL for file link resolution
    var bridgeBaseURL: URL? { bridgeURL }

    /// Artifacts directory (inside mira/ — agent syncs copies here)
    var artifactsURL: URL? {
        bridgeURL?.appendingPathComponent("artifacts")
    }

    // MARK: - Private

    private var bridgeURL: URL?
    private var timer: Timer?
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    private var inboxURL: URL? { bridgeURL?.appendingPathComponent("inbox") }
    private var outboxURL: URL? { bridgeURL?.appendingPathComponent("outbox") }
    private var ackURL: URL? { bridgeURL?.appendingPathComponent("ack") }
    private var heartbeatURL: URL? { bridgeURL?.appendingPathComponent("heartbeat.json") }
    private var threadsURL: URL? { bridgeURL?.appendingPathComponent("threads") }
    private var threadsIndexURL: URL? { threadsURL?.appendingPathComponent("index.json") }
    private var tasksURL: URL? { bridgeURL?.appendingPathComponent("tasks") }

    // MARK: - Settings

    var senderID: String {
        get { UserDefaults.standard.string(forKey: "sender_id") ?? defaultSenderID() }
        set { UserDefaults.standard.set(newValue, forKey: "sender_id") }
    }

    init() {
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        migrateSenderID()
        restoreBookmark()
    }

    /// One-time migration: replace stale sender_id with proper default.
    private func migrateSenderID() {
        guard !UserDefaults.standard.bool(forKey: "sender_id_v2") else { return }
        UserDefaults.standard.set(defaultSenderID(), forKey: "sender_id")
        UserDefaults.standard.set(true, forKey: "sender_id_v2")
    }

    private func log(_ msg: String) {
        let ts = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        debugLog += "[\(ts)] \(msg)\n"
        // Keep last 50 lines
        let lines = debugLog.split(separator: "\n", omittingEmptySubsequences: false)
        if lines.count > 50 {
            debugLog = lines.suffix(50).joined(separator: "\n")
        }
    }

    // MARK: - Folder setup (document picker)

    func setFolder(_ url: URL) {
        guard url.startAccessingSecurityScopedResource() else {
            error = "无法访问所选文件夹"
            log("setFolder: startAccessingSecurityScopedResource failed")
            return
        }
        do {
            let bookmark = try url.bookmarkData(
                options: .minimalBookmark,
                includingResourceValuesForKeys: nil,
                relativeTo: nil
            )
            UserDefaults.standard.set(bookmark, forKey: "bridge_bookmark")
            bridgeURL = url
            error = nil
            log("setFolder: OK → \(url.path)")
            ensureDirectories()
            startPolling()
        } catch {
            self.error = "无法保存文件夹书签: \(error.localizedDescription)"
            log("setFolder error: \(error)")
        }
    }

    private func restoreBookmark() {
        guard let data = UserDefaults.standard.data(forKey: "bridge_bookmark") else {
            log("restoreBookmark: no saved bookmark")
            return
        }
        do {
            var isStale = false
            let url = try URL(resolvingBookmarkData: data, bookmarkDataIsStale: &isStale)
            guard url.startAccessingSecurityScopedResource() else {
                log("restoreBookmark: startAccessing failed for \(url.path)")
                return
            }
            if isStale {
                let newData = try url.bookmarkData(
                    options: .minimalBookmark,
                    includingResourceValuesForKeys: nil,
                    relativeTo: nil
                )
                UserDefaults.standard.set(newData, forKey: "bridge_bookmark")
                log("restoreBookmark: refreshed stale bookmark")
            }
            bridgeURL = url
            log("restoreBookmark: OK → \(url.path)")
            ensureDirectories()
            startPolling()
        } catch {
            self.error = "书签恢复失败: \(error.localizedDescription)"
            log("restoreBookmark error: \(error)")
        }
    }

    private func ensureDirectories() {
        guard let base = bridgeURL else { return }
        let fm = FileManager.default
        for sub in ["inbox", "outbox", "ack", "threads", "tasks"] {
            let dir = base.appendingPathComponent(sub)
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }

    // MARK: - Send message

    func send(_ text: String, threadId: String = "") {
        guard let inbox = inboxURL else {
            log("send: inboxURL is nil")
            return
        }

        // Use current thread if none specified
        let effectiveThreadId = threadId.isEmpty ? currentThreadId : threadId

        let msg = TBMessage.new(content: text, sender: senderID, threadId: effectiveThreadId)
        let ts = dateStamp()
        let filename = "\(senderID)_\(ts)_\(msg.id).json"
        let fileURL = inbox.appendingPathComponent(filename)

        do {
            let data = try encoder.encode(msg)
            try data.write(to: fileURL, options: .atomic)
            messages.append(msg)
            log("send: OK → \(filename)")
        } catch {
            self.error = "发送失败: \(error.localizedDescription)"
            log("send error: \(error)")
        }
    }

    // MARK: - Task management

    /// Create a new task and write to both inbox (for Mac) and tasks/ (for display)
    func createTask(title: String, content: String) {
        guard let inbox = inboxURL, let tasksDir = tasksURL else {
            log("createTask: URLs nil")
            return
        }

        let task = MiraTask.new(title: title, content: content, sender: senderID)
        tasks.append(task)

        // Write task JSON to tasks/ for display
        writeTaskFile(task)

        // Also write to inbox so Mac picks it up (backward compat with existing dispatch)
        let msg = TBMessage.new(content: content, sender: senderID, threadId: task.id)
        let ts = dateStamp()
        let filename = "\(senderID)_\(ts)_\(msg.id).json"
        let fileURL = inbox.appendingPathComponent(filename)
        do {
            let data = try encoder.encode(msg)
            try data.write(to: fileURL, options: .atomic)
            log("createTask: \(task.id) '\(title)'")
        } catch {
            self.error = "发送失败: \(error.localizedDescription)"
            log("createTask error: \(error)")
        }
    }

    /// Send a follow-up message to an existing task
    func sendTaskMessage(_ taskId: String, content: String) {
        guard let inbox = inboxURL else { return }
        guard let idx = tasks.firstIndex(where: { $0.id == taskId }) else { return }

        let now = ISO8601DateFormatter().string(from: Date())
        let newMsg = TaskMessage(sender: senderID, content: content, timestamp: now)
        tasks[idx].messages.append(newMsg)
        tasks[idx].updatedAt = now
        if tasks[idx].status == "done" || tasks[idx].status == "failed" {
            tasks[idx].status = "queued"
        }
        writeTaskFile(tasks[idx])

        // Also send via inbox
        let msg = TBMessage.new(content: content, sender: senderID, threadId: taskId)
        let ts = dateStamp()
        let filename = "\(senderID)_\(ts)_\(msg.id).json"
        let fileURL = inbox.appendingPathComponent(filename)
        do {
            let data = try encoder.encode(msg)
            try data.write(to: fileURL, options: .atomic)
            log("sendTaskMessage: \(taskId)")
        } catch {
            log("sendTaskMessage error: \(error)")
        }
    }

    private func writeTaskFile(_ task: MiraTask) {
        guard let tasksDir = tasksURL else { return }
        let fileURL = tasksDir.appendingPathComponent("\(task.id).json")
        do {
            let data = try encoder.encode(task)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            log("writeTaskFile error: \(error)")
        }
    }

    // MARK: - Thread management

    func createThread(title: String) {
        let thread = TBThread.new(title: title)
        threads.append(thread)
        saveThreadIndex()
        currentThreadId = thread.id
        log("createThread: \(thread.id) '\(title)'")
    }

    func archiveThread(_ threadId: String) {
        guard let idx = threads.firstIndex(where: { $0.id == threadId }) else { return }
        threads[idx].archived = true
        saveThreadIndex()

        // Also send an archive command to Mac agent
        send("/archive \(threadId)", threadId: threadId)
        log("archiveThread: \(threadId)")
    }

    private func saveThreadIndex() {
        guard let indexURL = threadsIndexURL else { return }
        do {
            let data = try encoder.encode(threads)
            try data.write(to: indexURL, options: .atomic)
        } catch {
            log("saveThreadIndex error: \(error)")
        }
    }

    // MARK: - Polling

    func startPolling() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refresh()
            }
        }
        refresh()
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() {
        loadSentMessages()
        loadReplies()
        loadAcks()
        loadHeartbeat()
        loadThreads()
        loadTasks()
    }

    // MARK: - Load sent messages from inbox (persist across restarts)

    private func loadSentMessages() {
        guard let inbox = inboxURL else { return }
        let fm = FileManager.default

        do {
            let files = try fm.contentsOfDirectory(
                at: inbox, includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )
            for fileURL in files where fileURL.pathExtension == "json" {
                let name = fileURL.lastPathComponent
                guard name.hasPrefix(senderID) else { continue }

                let data = try Data(contentsOf: fileURL)
                let msg = try decoder.decode(TBMessage.self, from: data)
                if !messages.contains(where: { $0.id == msg.id }) {
                    messages.append(msg)
                }
            }
        } catch {
            log("loadSentMessages error: \(error)")
        }
    }

    // MARK: - Read replies from outbox

    private func loadReplies() {
        guard let outbox = outboxURL else {
            log("loadReplies: outboxURL is nil")
            return
        }
        let fm = FileManager.default

        do {
            let files = try fm.contentsOfDirectory(
                at: outbox, includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )

            for fileURL in files where fileURL.pathExtension == "json" {
                let name = fileURL.lastPathComponent

                // Trigger iCloud download if file is evicted (cloud-only)
                if !fm.isReadableFile(atPath: fileURL.path) {
                    try? fm.startDownloadingUbiquitousItem(at: fileURL)
                    log("loadReplies: triggered download for \(name)")
                    continue
                }

                do {
                    let data = try Data(contentsOf: fileURL)
                    let msg = try decoder.decode(TBMessage.self, from: data)
                    if !messages.contains(where: { $0.id == msg.id }) {
                        messages.append(msg)
                        log("loadReplies: loaded \(msg.id) from \(name)")
                    }
                } catch {
                    log("loadReplies: decode failed for \(name): \(error)")
                }
            }
        } catch {
            log("loadReplies: contentsOfDirectory error: \(error)")
        }

        messages.sort { $0.date < $1.date }
    }

    // MARK: - Read acks

    private func loadAcks() {
        guard let ackDir = ackURL else { return }
        let fm = FileManager.default

        do {
            let files = try fm.contentsOfDirectory(
                at: ackDir, includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )
            for fileURL in files where fileURL.pathExtension == "json" {
                let data = try Data(contentsOf: fileURL)
                let ack = try decoder.decode(TBAck.self, from: data)
                acks[ack.messageId] = ack
            }
        } catch {
            log("loadAcks error: \(error)")
        }
    }

    // MARK: - Heartbeat

    private func loadHeartbeat() {
        guard let url = heartbeatURL else {
            log("loadHeartbeat: URL is nil")
            return
        }
        let fm = FileManager.default
        if !fm.isReadableFile(atPath: url.path) {
            try? fm.startDownloadingUbiquitousItem(at: url)
            log("loadHeartbeat: triggered download")
            return
        }
        do {
            let data = try Data(contentsOf: url)
            heartbeat = try decoder.decode(TBHeartbeat.self, from: data)
        } catch {
            log("loadHeartbeat error: \(error)")
        }
    }

    // MARK: - Threads

    private func loadThreads() {
        guard let indexURL = threadsIndexURL else { return }
        do {
            let data = try Data(contentsOf: indexURL)
            let loaded = try decoder.decode([TBThread].self, from: data)
            // Merge: keep local changes, add new remote threads
            for remote in loaded {
                if let idx = threads.firstIndex(where: { $0.id == remote.id }) {
                    // Update existing
                    threads[idx].lastActive = remote.lastActive
                    threads[idx].archived = remote.archived
                    if threads[idx].title != remote.title {
                        threads[idx].title = remote.title
                    }
                } else {
                    threads.append(remote)
                }
            }
        } catch {
            // No threads file yet — that's fine
        }
    }

    // MARK: - Load tasks

    private func loadTasks() {
        guard let tasksDir = tasksURL else { return }
        let fm = FileManager.default

        // Load status.json as source of truth for task status
        // Maps both task_id and thread_id to the real status
        var statusMap: [String: String] = [:]
        let statusFile = tasksDir.appendingPathComponent("status.json")
        if fm.isReadableFile(atPath: statusFile.path),
           let statusData = try? Data(contentsOf: statusFile),
           let records = try? JSONSerialization.jsonObject(with: statusData) as? [[String: Any]] {
            for rec in records {
                guard let st = rec["status"] as? String else { continue }
                let mapped: String
                switch st {
                case "error", "timeout": mapped = "failed"
                default: mapped = st
                }
                if let tid = rec["task_id"] as? String { statusMap[tid] = mapped }
                // Also map by thread_id (iOS task_id may differ from Python task_id)
                if let threadId = rec["thread_id"] as? String, !threadId.isEmpty {
                    statusMap[threadId] = mapped
                }
            }
        }

        var loaded: [MiraTask] = []
        do {
            let files = try fm.contentsOfDirectory(
                at: tasksDir, includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )
            for fileURL in files where fileURL.pathExtension == "json" {
                // Skip non-task files and auto-generated content (shown as cards, not tasks)
                let name = fileURL.deletingPathExtension().lastPathComponent
                if name == "status" || name == "history" { continue }
                if name.hasPrefix("briefing_") || name.hasPrefix("journal_") { continue }

                // Trigger iCloud download if needed
                if !fm.isReadableFile(atPath: fileURL.path) {
                    try? fm.startDownloadingUbiquitousItem(at: fileURL)
                    continue
                }
                do {
                    let data = try Data(contentsOf: fileURL)
                    var task = try decoder.decode(MiraTask.self, from: data)
                    // Fix status from status.json if task file is stale
                    if let trueStatus = statusMap[task.id], trueStatus != task.status {
                        task.status = trueStatus
                    }
                    loaded.append(task)
                } catch {
                    log("loadTasks: decode failed for \(fileURL.lastPathComponent): \(error)")
                }
            }
        } catch {
            log("loadTasks error: \(error)")
        }

        tasks = loaded.sorted { $0.updatedDate > $1.updatedDate }
    }

    // MARK: - Helpers

    func ackStatus(for messageId: String) -> String? {
        acks[messageId]?.status
    }

    private func dateStamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd_HHmmss"
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: Date())
    }

    private func defaultSenderID() -> String {
        let name = UIDevice.current.name
        log("defaultSenderID: UIDevice.current.name = '\(name)'")

        // Try to extract person name from "XXX's iPhone" or "XXX的iPhone"
        if let range = name.range(of: "'s", options: .caseInsensitive) ?? name.range(of: "的", options: .caseInsensitive) {
            let person = name[name.startIndex..<range.lowerBound]
                .trimmingCharacters(in: .whitespaces)
                .lowercased()
                .replacingOccurrences(of: " ", with: "-")
            if !person.isEmpty { return person }
        }

        // iOS 16+ returns generic names — just default to "ang"
        let lower = name.lowercased()
        if lower.contains("iphone") || lower.contains("ipad") || lower.contains("ipod") {
            return "ang"
        }

        return lower
            .replacingOccurrences(of: " ", with: "-")
            .replacingOccurrences(of: "'", with: "")
    }
}
