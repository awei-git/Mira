import SwiftUI

struct ThreadListView: View {
    @Bindable var bridge: BridgeService
    @Environment(\.dismiss) private var dismiss
    @State private var showOlder = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    // "All messages" option
                    Button {
                        bridge.currentThreadId = ""
                        dismiss()
                    } label: {
                        HStack {
                            Image(systemName: "bubble.left.and.bubble.right")
                                .font(.system(size: 14))
                                .foregroundStyle(.blue)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("全部消息")
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundStyle(.primary)
                                Text("\(bridge.messages.count) 条消息")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if bridge.currentThreadId.isEmpty {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 12))
                                    .foregroundStyle(.blue)
                            }
                        }
                        .padding(.horizontal)
                        .padding(.vertical, 8)
                    }

                    Divider().padding(.horizontal)

                    // Today
                    if !todayThreads.isEmpty {
                        sectionHeader("今天")
                        threadList(todayThreads)
                    }

                    // Yesterday
                    if !yesterdayThreads.isEmpty {
                        sectionHeader("昨天")
                        threadList(yesterdayThreads)
                    }

                    // Older — collapsed by default
                    if !olderThreads.isEmpty {
                        Button {
                            withAnimation { showOlder.toggle() }
                        } label: {
                            HStack {
                                Image(systemName: showOlder ? "chevron.down" : "chevron.right")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(.secondary)
                                    .frame(width: 14)
                                Text("更早")
                                    .font(.subheadline.bold())
                                    .foregroundStyle(.primary)
                                Text("(\(olderThreads.count))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Spacer()
                            }
                            .padding(.horizontal)
                        }
                        if showOlder {
                            threadList(olderThreads)
                        }
                    }
                }
                .padding(.vertical)
            }
            .navigationTitle("对话列表")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("完成") { dismiss() }
                        .font(.system(size: 14))
                }
            }
        }
    }

    // MARK: - Section header

    @ViewBuilder
    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.subheadline.bold())
            .foregroundStyle(.secondary)
            .padding(.horizontal)
            .padding(.bottom, -4)
    }

    // MARK: - Thread list

    @ViewBuilder
    private func threadList(_ threads: [DiscoveredThread]) -> some View {
        VStack(spacing: 0) {
            ForEach(threads, id: \.threadId) { thread in
                Button {
                    bridge.currentThreadId = thread.threadId
                    dismiss()
                } label: {
                    threadRow(thread)
                }
                if thread.threadId != threads.last?.threadId {
                    Divider().padding(.leading, 16)
                }
            }
        }
    }

    // MARK: - Data model

    struct DiscoveredThread {
        let threadId: String
        let title: String
        let messageCount: Int
        let lastMessage: String
        let lastDate: Date
        let dateLabel: String
        let isActive: Bool
    }

    // MARK: - Thread discovery

    private var allThreads: [DiscoveredThread] {
        let cal = Calendar.current
        let now = Date()
        let df = DateFormatter()
        df.locale = Locale(identifier: "zh_CN")
        df.dateFormat = "M月d日"

        var threadMap: [String: [TBMessage]] = [:]
        for msg in bridge.messages {
            let tid = msg.threadId.isEmpty ? "__no_thread__" : msg.threadId
            threadMap[tid, default: []].append(msg)
        }

        var discovered: [DiscoveredThread] = []

        for (tid, msgs) in threadMap where tid != "__no_thread__" {
            let sorted = msgs.sorted { $0.date < $1.date }
            guard let last = sorted.last else { continue }

            let daysDiff = cal.dateComponents([.day], from: last.date, to: now).day ?? 0
            if daysDiff > 10 { continue }

            let task = bridge.tasks.first { $0.id == tid }
            let title = threadTitle(threadId: tid, task: task, messages: sorted)

            if let task, task.status == "done", msgs.count <= 2 {
                continue
            }

            let dateLabel: String
            if cal.isDateInToday(last.date) {
                dateLabel = "今天"
            } else if cal.isDateInYesterday(last.date) {
                dateLabel = "昨天"
            } else {
                dateLabel = df.string(from: last.date)
            }

            discovered.append(DiscoveredThread(
                threadId: tid,
                title: title,
                messageCount: msgs.count,
                lastMessage: last.content,
                lastDate: last.date,
                dateLabel: dateLabel,
                isActive: task?.isActive ?? false
            ))
        }

        discovered.sort { $0.lastDate > $1.lastDate }
        return discovered
    }

    private var todayThreads: [DiscoveredThread] {
        allThreads.filter { $0.dateLabel == "今天" }
    }

    private var yesterdayThreads: [DiscoveredThread] {
        allThreads.filter { $0.dateLabel == "昨天" }
    }

    private var olderThreads: [DiscoveredThread] {
        allThreads.filter { $0.dateLabel != "今天" && $0.dateLabel != "昨天" }
    }

    private func threadTitle(threadId: String, task: MiraTask?, messages: [TBMessage]) -> String {
        if let task {
            let t = task.title
            if t.hasPrefix("评论: ") {
                return String(t.dropFirst(4))
            }
            if t.hasPrefix("Mira writes:") {
                return String(t.dropFirst(13)).trimmingCharacters(in: .whitespaces)
            }
            if t.count <= 30 { return t }
            return String(t.prefix(28)) + "…"
        }

        if let first = messages.first(where: { !$0.isFromAgent }) {
            let text = first.content.prefix(28)
            return text.count < first.content.count ? text + "…" : String(text)
        }

        return threadId
    }

    // MARK: - Row view

    @ViewBuilder
    private func threadRow(_ thread: DiscoveredThread) -> some View {
        HStack {
            if thread.isActive {
                Circle()
                    .fill(.blue)
                    .frame(width: 6, height: 6)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(thread.title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                HStack(spacing: 6) {
                    if thread.dateLabel != "今天" {
                        Text(thread.dateLabel)
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    }
                    Text(thread.lastMessage)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(timeLabel(thread.lastDate))
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)

                if thread.messageCount > 2 {
                    Text("\(thread.messageCount)")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Color(.systemGray5))
                        .clipShape(Capsule())
                }
            }
            if bridge.currentThreadId == thread.threadId {
                Image(systemName: "checkmark")
                    .font(.system(size: 12))
                    .foregroundStyle(.blue)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
    }

    private func timeLabel(_ date: Date) -> String {
        let cal = Calendar.current
        if cal.isDateInToday(date) {
            let df = DateFormatter()
            df.dateFormat = "HH:mm"
            return df.string(from: date)
        }
        return ""
    }
}
