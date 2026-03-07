import SwiftUI

struct TodayView: View {
    var bridge: BridgeService
    @State private var todayCards: [BriefingFileCard] = []
    @State private var previousCards: [BriefingFileCard] = []

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
                                ReportDetailView(card: card)
                            } label: {
                                ReportCardView(card: card)
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal)
                        }
                    }

                    // Active tasks
                    let active = bridge.activeTasks.filter { !$0.needsInput }
                    if !active.isEmpty {
                        SectionHeader(title: "进行中", icon: "arrow.triangle.2.circlepath")
                        ForEach(active) { task in
                            NavigationLink(value: task.id) {
                                TaskRowCompact(task: task)
                            }
                        }
                        .padding(.horizontal)
                    }

                    // Empty state
                    if todayCards.isEmpty && bridge.activeTasks.isEmpty && needsInput.isEmpty {
                        ContentUnavailableView(
                            "今天还没有报告",
                            systemImage: "moon.zzz",
                            description: Text("Mira 会在这里推送 briefing 和任务进展")
                        )
                        .padding(.top, 40)
                    }

                    // Previous days
                    if !previousCards.isEmpty {
                        SectionHeader(title: "往期", icon: "clock.arrow.circlepath")
                        ForEach(previousCards) { card in
                            NavigationLink {
                                ReportDetailView(card: card)
                            } label: {
                                ReportCardView(card: card)
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal)
                        }
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
        guard let artifacts = bridge.artifactsURL else { return }
        let fm = FileManager.default

        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        let todayStr = df.string(from: Date())

        // Load all content from briefings dir (briefings + journals live here)
        let briefingsDir = artifacts.appendingPathComponent("briefings")
        let allFiles = loadMarkdownFiles(in: briefingsDir, fm: fm)

        // Split into today vs previous
        var today: [BriefingFileCard] = []
        var previous: [BriefingFileCard] = []

        for file in allFiles {
            let name = file.deletingPathExtension().lastPathComponent
            let content = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
            if content.isEmpty { continue }

            let isJournal = name.contains("journal")
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

        todayCards = today.sorted { $0.date > $1.date }
        // Show last 14 previous entries, newest first
        previousCards = previous.sorted { $0.date > $1.date }.prefix(14).map { $0 }
    }

    private func loadMarkdownFiles(in dir: URL, fm: FileManager) -> [URL] {
        guard fm.fileExists(atPath: dir.path) else { return [] }
        if !fm.isReadableFile(atPath: dir.path) {
            try? fm.startDownloadingUbiquitousItem(at: dir)
        }
        guard let files = try? fm.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        return files.filter { $0.pathExtension == "md" }.compactMap { url in
            if !fm.isReadableFile(atPath: url.path) {
                try? fm.startDownloadingUbiquitousItem(at: url)
                return nil
            }
            return url
        }
    }

    private func iconForFile(_ name: String) -> String {
        if name.contains("deep_dive") { return "magnifyingglass" }
        if name.contains("journal") || name.count == 10 { return "book" }  // yyyy-MM-dd in journal
        return "newspaper"
    }

    private func titleForFile(_ name: String, todayStr: String, isJournal: Bool) -> String {
        // Extract date prefix (yyyy-MM-dd)
        let datePrefix = String(name.prefix(10))
        let datePart = datePrefix == todayStr ? "Today" : datePrefix

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
    @State private var rendered: AttributedString?

    var body: some View {
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

                if let rendered {
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
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            // Parse markdown once, off the view builder
            if rendered == nil {
                rendered = (try? AttributedString(
                    markdown: card.content,
                    options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                ))
            }
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
