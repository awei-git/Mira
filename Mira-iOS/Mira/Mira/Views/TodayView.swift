import SwiftUI

struct TodayView: View {
    var bridge: BridgeService
    @State private var todayCards: [BriefingFileCard] = []
    @State private var previousCards: [BriefingFileCard] = []
    @State private var substackPosts: [SubstackPost] = []
    @State private var showActiveTasks = false
    @State private var showCompletedTasks = false
    @State private var showPosts = false
    @State private var showPreviousDays = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {

                    // Needs-input alerts
                    let needsInput = bridge.tasks.filter(\.needsInput)
                    if !needsInput.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("\(needsInput.count) 个任务等你回复", systemImage: "exclamationmark.bubble")
                                .font(.subheadline.bold())
                                .foregroundStyle(.orange)
                            ForEach(needsInput) { task in
                                NavigationLink(value: task.id) {
                                    TaskRowCompact(task: task)
                                }
                            }
                        }
                        .padding()
                        .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                        .padding(.horizontal)
                    }

                    // Today's briefings / reports
                    if !todayCards.isEmpty {
                        ForEach(todayCards) { card in
                            NavigationLink {
                                ReportDetailView(card: card, bridge: bridge)
                            } label: {
                                ReportCardView(card: card)
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal)
                        }
                    }

                    // Active tasks (collapsible)
                    let active = bridge.activeTasks.filter { !$0.needsInput }
                    if !active.isEmpty {
                        DisclosureGroup(isExpanded: $showActiveTasks) {
                            ForEach(active) { task in
                                NavigationLink(value: task.id) {
                                    TaskRowCompact(task: task)
                                }
                            }
                        } label: {
                            Label("进行中 (\(active.count))", systemImage: "arrow.triangle.2.circlepath")
                                .font(.headline)
                        }
                        .padding(.horizontal)
                    }

                    // Recently completed tasks (today only, collapsible)
                    let cal = Calendar.current
                    let recentDone = bridge.doneTasks.filter { cal.isDateInToday($0.updatedDate) }
                    if !recentDone.isEmpty {
                        DisclosureGroup(isExpanded: $showCompletedTasks) {
                            ForEach(recentDone) { task in
                                NavigationLink(value: task.id) {
                                    TaskRowCompact(task: task)
                                }
                            }
                        } label: {
                            Label("已完成 (\(recentDone.count))", systemImage: "checkmark.circle")
                                .font(.headline)
                        }
                        .padding(.horizontal)
                    }

                    // Substack posts (collapsible, show only recent 5)
                    if !substackPosts.isEmpty {
                        let recentPosts = Array(substackPosts.prefix(5))
                        DisclosureGroup(isExpanded: $showPosts) {
                            VStack(spacing: 0) {
                                ForEach(recentPosts) { post in
                                    PostRow(post: post)
                                        .padding(.horizontal)
                                    if post.id != recentPosts.last?.id {
                                        Divider().padding(.leading)
                                    }
                                }
                            }
                            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                        } label: {
                            Label("Mira's Posts (\(recentPosts.count))", systemImage: "doc.richtext")
                                .font(.headline)
                        }
                        .padding(.horizontal)
                    }

                    // Empty state
                    if todayCards.isEmpty && bridge.activeTasks.isEmpty && needsInput.isEmpty && substackPosts.isEmpty {
                        ContentUnavailableView(
                            "今天还没有报告",
                            systemImage: "moon.zzz",
                            description: Text("Mira 会在这里推送 briefing 和任务进展")
                        )
                        .padding(.top, 40)
                    }

                    // Previous days (collapsible, limit to last 7)
                    if !previousCards.isEmpty {
                        let recentPrevious = Array(previousCards.prefix(7))
                        DisclosureGroup(isExpanded: $showPreviousDays) {
                            ForEach(recentPrevious) { card in
                                NavigationLink {
                                    ReportDetailView(card: card, bridge: bridge)
                                } label: {
                                    ReportCardView(card: card)
                                }
                                .buttonStyle(.plain)
                            }
                        } label: {
                            Label("往期 (\(recentPrevious.count))", systemImage: "clock.arrow.circlepath")
                                .font(.headline)
                        }
                        .padding(.horizontal)
                    }
                }
                .padding(.vertical)
            }
            .navigationTitle("Today")
            .navigationDestination(for: String.self) { taskId in
                if let task = bridge.tasks.first(where: { $0.id == taskId }) {
                    TaskDetailView(bridge: bridge, taskId: task.id)
                }
            }
            .refreshable { loadContent(); bridge.refresh() }
            .onAppear { loadContent() }
        }
    }

    private func loadContent() {
        guard let artifacts = bridge.artifactsURL else {
            bridge.debugLog += "[Today] artifactsURL is nil\n"
            return
        }
        let fm = FileManager.default

        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        let todayStr = df.string(from: Date())

        // Load briefings + journals
        let briefingsDir = artifacts.appendingPathComponent("briefings")
        let allFiles = loadMarkdownFiles(in: briefingsDir, fm: fm)

        // Load writings (each subfolder is a project, find latest draft)
        let writingsDir = artifacts.appendingPathComponent("writings")
        let writingFiles = loadWritingProjects(in: writingsDir, fm: fm)

        // Split into today vs previous
        var today: [BriefingFileCard] = []
        var previous: [BriefingFileCard] = []

        for file in allFiles {
            let name = file.deletingPathExtension().lastPathComponent
            let content = (try? String(contentsOf: file, encoding: .utf8)) ?? ""

            let isJournal = name.contains("journal") || name.contains("zhesi")
            let modDate = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? Date()
            let icon = iconForFile(name)
            let title = titleForFile(name, todayStr: todayStr, isJournal: isJournal)
            let preview = extractPreview(from: content, maxLength: 120)

            let card = BriefingFileCard(
                id: file.path,
                title: title,
                preview: preview,
                content: content,
                icon: icon,
                date: modDate
            )

            if name.hasPrefix(todayStr) {
                today.append(card)
            } else {
                previous.append(card)
            }
        }

        // Add writing projects
        for (file, projectName) in writingFiles {
            let content = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
            if content.isEmpty { continue }
            let modDate = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? Date()
            let preview = extractPreview(from: content, maxLength: 120)
            let card = BriefingFileCard(
                id: file.path,
                title: projectName,
                preview: preview,
                content: content,
                icon: "pencil.line",
                date: modDate
            )
            if Calendar.current.isDateInToday(modDate) {
                today.append(card)
            } else {
                previous.append(card)
            }
        }

        todayCards = today.sorted { $0.date > $1.date }
        // Show last 30 previous entries, newest first
        previousCards = previous.sorted { $0.date > $1.date }.prefix(30).map { $0 }

        // Load Substack posts
        loadSubstackPosts()
    }

    private func loadSubstackPosts() {
        guard let tasksDir = bridge.tasksURL else { return }
        let postsFile = tasksDir.appendingPathComponent("substack_posts.json")
        let fm = FileManager.default

        if !fm.isReadableFile(atPath: postsFile.path) {
            try? fm.startDownloadingUbiquitousItem(at: postsFile)
            return
        }

        guard let data = try? Data(contentsOf: postsFile),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return }

        substackPosts = arr.compactMap { dict in
            guard let id = dict["id"] as? Int,
                  let title = dict["title"] as? String,
                  let url = dict["url"] as? String
            else { return nil }
            return SubstackPost(
                id: id,
                title: title,
                url: url,
                commentCount: dict["comment_count"] as? Int ?? 0,
                postDate: dict["post_date"] as? String ?? ""
            )
        }
    }

    /// Load writing projects — returns (latest draft file, project display name) pairs
    private func loadWritingProjects(in dir: URL, fm: FileManager) -> [(URL, String)] {
        guard fm.fileExists(atPath: dir.path) else { return [] }
        if !fm.isReadableFile(atPath: dir.path) {
            try? fm.startDownloadingUbiquitousItem(at: dir)
        }
        guard let folders = try? fm.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var results: [(URL, String)] = []
        for folder in folders {
            let vals = try? folder.resourceValues(forKeys: [.isDirectoryKey])
            guard vals?.isDirectory == true else { continue }

            let projectName = folder.lastPathComponent
                .replacingOccurrences(of: "-", with: " ")
                .replacingOccurrences(of: "_", with: " ")
                .capitalized

            // Find latest draft: check drafts/ subfolder then root
            let draftsDir = folder.appendingPathComponent("drafts")
            var candidates = loadMarkdownFiles(in: draftsDir, fm: fm)
            candidates += loadMarkdownFiles(in: folder, fm: fm)

            // Pick the most recently modified md file that has real content (>500 bytes)
            let best = candidates
                .compactMap { url -> (URL, Date)? in
                    let vals = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
                    let size = vals?.fileSize ?? 0
                    guard size > 500 else { return nil }  // skip status notes / metadata
                    let d = vals?.contentModificationDate ?? .distantPast
                    return (url, d)
                }
                .max(by: { $0.1 < $1.1 })

            if let (file, _) = best {
                results.append((file, projectName))
            }
        }
        return results
    }

    private func loadMarkdownFiles(in dir: URL, fm: FileManager) -> [URL] {
        // Try to list directory contents — works even for cloud-only dirs on iOS
        guard let files = try? fm.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: [.contentModificationDateKey, .ubiquitousItemDownloadingStatusKey],
            options: [.skipsHiddenFiles]
        ) else {
            // Directory might not be downloaded yet
            try? fm.startDownloadingUbiquitousItem(at: dir)
            return []
        }

        return files.filter { $0.pathExtension == "md" }.compactMap { url in
            // Check iCloud download status
            let values = try? url.resourceValues(forKeys: [.ubiquitousItemDownloadingStatusKey])
            let status = values?.ubiquitousItemDownloadingStatus
            if status == .notDownloaded {
                try? fm.startDownloadingUbiquitousItem(at: url)
                // Still return the URL so the card shows (content will be empty/loading)
                return url
            }
            // Try reading — if it fails, trigger download
            if (try? Data(contentsOf: url, options: .mappedIfSafe)) != nil {
                return url
            }
            try? fm.startDownloadingUbiquitousItem(at: url)
            return url  // return anyway so card is visible
        }
    }

    private func iconForFile(_ name: String) -> String {
        if name.contains("deep_dive") { return "magnifyingglass" }
        if name.contains("journal") || name.contains("zhesi") || name.count == 10 { return "book" }
        if name.contains("analyst") || name.contains("market") { return "chart.line.uptrend.xyaxis" }
        if name.contains("skill") { return "lightbulb" }
        return "newspaper"
    }

    /// Map internal file suffixes to display names
    private static let suffixDisplayNames: [String: String] = [
        "zhesi": "Reflection",
        "deep_dive": "Deep Dive",
        "market": "Market",
        "analyst_pre_market": "Pre-Market Analysis",
        "analyst_post_market": "Post-Market Analysis",
        "analyst_morning": "Morning Analysis",
        "analyst_afternoon": "Afternoon Analysis",
        "arxiv_huggingface": "Arxiv & HuggingFace",
        "reddit_hacker_news": "Reddit & Hacker News",
        "literaryhub_brain_pickings": "Literary Hub & Brain Pickings",
        "quanta_magazine_aeon_essays": "Quanta & Aeon",
        "noah_smith_stratechery": "Econ & Strategy",
        "reddit_hacker_news_ai_news": "Reddit & HN",
    ]

    private func titleForFile(_ name: String, todayStr: String, isJournal: Bool) -> String {
        let datePrefix = String(name.prefix(10))
        let datePart = datePrefix == todayStr ? "Today" : datePrefix

        // Check suffix display map first (zhesi → Reflection, etc.)
        if name != datePrefix {
            let suffix = name.replacingOccurrences(of: datePrefix + "_", with: "")
            if let display = Self.suffixDisplayNames[suffix] {
                return "\(display) \(datePart)"
            }
        }
        if isJournal {
            return "Journal \(datePart)"
        }
        if name == datePrefix {
            return "Briefing \(datePart)"
        }
        let suffix = name.replacingOccurrences(of: datePrefix + "_", with: "")
        return "\(suffix.replacingOccurrences(of: "_", with: " ").capitalized) \(datePart)"
    }

    private func extractPreview(from content: String, maxLength: Int) -> String {
        let lines = content.components(separatedBy: .newlines)
        var preview = ""
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") || trimmed.hasPrefix("---") { continue }
            if !preview.isEmpty { preview += " " }
            preview += trimmed
            if preview.count >= maxLength { break }
        }
        return String(preview.prefix(maxLength))
    }
}

// MARK: - Models

struct BriefingFileCard: Identifiable {
    let id: String
    let title: String
    let preview: String
    let content: String
    let icon: String
    let date: Date
}

// MARK: - Card View (collapsed)

struct ReportCardView: View {
    let card: BriefingFileCard

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: card.icon)
                    .foregroundStyle(.blue)
                    .font(.subheadline)
                Text(card.title)
                    .font(.subheadline.bold())
                Spacer()
                Text(card.date, style: .time)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text(card.preview)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Detail View (full content)

struct ReportDetailView: View {
    let card: BriefingFileCard
    var bridge: BridgeService?
    @State private var rendered: AttributedString?
    @State private var commentText = ""
    @FocusState private var inputFocused: Bool

    /// Thread ID for this card's comments (based on filename)
    private var commentThreadId: String {
        let filename = (card.id as NSString).lastPathComponent
        return "comment_\(filename.replacingOccurrences(of: ".md", with: ""))"
    }

    /// Find the matching task for this card's comment thread
    private var matchingTask: MiraTask? {
        guard let bridge else { return nil }
        // First try: match by our comment thread ID
        if let task = bridge.tasks.first(where: { $0.id == commentThreadId }) {
            return task
        }
        // Fallback: match by card title in task title (handles legacy tasks)
        let cardTitle = card.title
        let filename = (card.id as NSString).lastPathComponent
        let datePart = String(filename.prefix(10))  // YYYY-MM-DD
        // Extract keywords from card title for fuzzy matching
        // e.g., "Reflection Today" → check "Reflection", "Journal", "Briefing", date
        let titleWords = Set(cardTitle.split(separator: " ").map { $0.lowercased() })
        // Map between display names that refer to the same content
        let synonyms: Set<String> = ["reflection", "journal", "zhesi", "briefing"]
        let cardSynonyms = titleWords.intersection(synonyms)

        return bridge.tasks.first { task in
            guard task.title.contains("评论") else { return false }
            // Direct title match
            if task.title.contains(cardTitle) { return true }
            // Date match (check both YYYY-MM-DD and "Today")
            if task.title.contains(datePart) { return true }
            // "Today" in task title + card is from today
            if task.title.contains("Today") && cardTitle.contains("Today") {
                // Check if they refer to related content (journal/reflection/zhesi)
                let taskLower = task.title.lowercased()
                if !cardSynonyms.isEmpty && cardSynonyms.contains(where: { taskLower.contains($0) }) {
                    return true
                }
                // Also match if task mentions any synonym for this card type
                if synonyms.contains(where: { taskLower.contains($0) }) &&
                   !cardSynonyms.isEmpty {
                    return true
                }
            }
            return false
        }
    }

    /// All messages in the comment thread
    private var comments: [TaskMessage] {
        guard let task = matchingTask else { return [] }
        return task.messages
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        // Header
                        HStack {
                            Image(systemName: card.icon)
                                .font(.title2)
                                .foregroundStyle(.tint)
                            Text(card.title)
                                .font(.title2.bold())
                            Spacer()
                        }
                        .padding(.bottom, 4)

                        Text(card.date, style: .date)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.bottom, 16)

                        Divider()
                            .padding(.bottom, 16)

                        if card.content.isEmpty {
                            VStack(spacing: 12) {
                                ProgressView()
                                Text("正在从 iCloud 下载...")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 40)
                        } else if let rendered {
                            Text(rendered)
                                .font(.body)
                                .lineSpacing(6)
                                .tint(.blue)
                                .textSelection(.enabled)
                        } else {
                            Text(card.content)
                                .font(.body)
                                .lineSpacing(6)
                                .textSelection(.enabled)
                        }

                        // Comments section
                        if bridge != nil {
                            Divider()
                                .padding(.vertical, 16)

                            if comments.isEmpty {
                                Text("写点评论，Mira 会回复你")
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                                    .padding(.bottom, 8)
                            } else {
                                Text("评论")
                                    .font(.headline)
                                    .padding(.bottom, 8)

                                ForEach(Array(comments.enumerated()), id: \.offset) { idx, msg in
                                    TaskMessageBubble(message: msg)
                                        .id("comment_\(idx)")
                                }
                            }
                        }
                    }
                    .padding()
                }
                .onChange(of: comments.count) {
                    if let last = comments.indices.last {
                        withAnimation {
                            proxy.scrollTo("comment_\(last)", anchor: .bottom)
                        }
                    }
                }
            }

            // Comment input
            if bridge != nil {
                Divider()
                HStack(spacing: 8) {
                    TextField("写评论...", text: $commentText, axis: .vertical)
                        .focused($inputFocused)
                        .textFieldStyle(.plain)
                        .lineLimit(1...5)
                        .padding(10)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 20))

                    Button {
                        sendComment()
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .disabled(commentText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if rendered == nil {
                rendered = (try? AttributedString(
                    markdown: card.content,
                    options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                ))
            }
        }
    }

    private func sendComment() {
        guard let bridge else { return }
        let text = commentText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        commentText = ""
        inputFocused = false

        if let task = matchingTask {
            // Reply to existing thread
            bridge.sendTaskMessage(task.id, content: text)
        } else {
            // Create new comment thread with stable ID
            bridge.createTaskWithId(
                id: commentThreadId,
                title: "评论: \(card.title)",
                content: text
            )
        }
    }
}

// MARK: - Shared Subviews

struct SectionHeader: View {
    let title: String
    let icon: String

    var body: some View {
        Label(title, systemImage: icon)
            .font(.headline)
            .padding(.horizontal)
            .padding(.top, 4)
    }
}

struct TaskRowCompact: View {
    let task: MiraTask

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: task.statusIcon)
                .foregroundStyle(colorForStatus(task.statusColor))
                .font(.body)
            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .font(.subheadline)
                    .lineLimit(1)
                Text(task.lastMessage.prefix(60))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Text(relativeTime(task.updatedDate))
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
    }

    private func colorForStatus(_ name: String) -> Color {
        switch name {
        case "blue": return .blue
        case "orange": return .orange
        case "green": return .green
        case "red": return .red
        default: return .gray
        }
    }
}

func relativeTime(_ date: Date) -> String {
    let seconds = -date.timeIntervalSinceNow
    if seconds < 60 { return "刚刚" }
    if seconds < 3600 { return "\(Int(seconds / 60))分钟前" }
    if seconds < 86400 { return "\(Int(seconds / 3600))小时前" }
    return "\(Int(seconds / 86400))天前"
}
