import SwiftUI
import QuickLook

// MARK: - App Registry

struct RegisteredApp: Identifiable {
    let id: String          // e.g. "mira", "masterminds", "tetra"
    let name: String
    let icon: String
    let color: Color
    let rootPath: String    // relative to MtJoy/
    let statusPath: String  // relative to app root, where status.json lives
}

private let appRegistry: [RegisteredApp] = [
    RegisteredApp(id: "mira", name: "Mira", icon: "brain.head.profile", color: .purple,
                  rootPath: "Mira", statusPath: ""),
    RegisteredApp(id: "masterminds", name: "神仙会", icon: "person.3", color: .orange,
                  rootPath: "MasterMinds", statusPath: "data/status.json"),
    RegisteredApp(id: "tetra", name: "Tetra", icon: "chart.bar.xaxis", color: .blue,
                  rootPath: "Tetra", statusPath: "output/status.json"),
]

// MARK: - Status Protocol v2

struct AppStatus: Codable {
    let app: String
    let version: Int
    let updatedAt: String
    let outputs: [AppOutput]
}

struct AppOutput: Codable, Identifiable {
    let type: String        // progress, report, deep_dive, alert
    let id: String
    let title: String
    let updatedAt: String
    var status: String?
    var stage: AppStage?
    var highlights: [String]?
    var path: String?
    var content: String?
    var severity: String?
    var message: String?
    var topic: String?
    var period: String?
    var parent: String?
}

struct AppStage: Codable {
    let current: Int
    let total: Int
    let label: String
}

// MARK: - Library View

struct LibraryView: View {
    var bridge: BridgeService

    var body: some View {
        NavigationStack {
            List(appRegistry) { app in
                NavigationLink {
                    if app.id == "mira" {
                        MiraLibraryView(bridge: bridge)
                            .navigationTitle(app.name)
                    } else {
                        AppOutputView(app: app, mtjoyURL: bridge.mtjoyURL)
                            .navigationTitle(app.name)
                    }
                } label: {
                    AppRow(app: app, mtjoyURL: bridge.mtjoyURL)
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Library")
        }
    }
}

// MARK: - App Row

struct AppRow: View {
    let app: RegisteredApp
    let mtjoyURL: URL?

    private var subtitle: String? {
        guard let mtjoy = mtjoyURL else { return nil }
        let statusFile = mtjoy
            .appendingPathComponent(app.rootPath)
            .appendingPathComponent(app.statusPath)
        guard let data = try? Data(contentsOf: statusFile),
              let status = try? JSONDecoder().decode(AppStatus.self, from: data)
        else { return nil }
        // Show first progress output's stage label
        if let progress = status.outputs.first(where: { $0.type == "progress" }),
           let stage = progress.stage {
            return "\(stage.label) (\(stage.current)/\(stage.total))"
        }
        return nil
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: app.icon)
                .font(.title2)
                .foregroundStyle(app.color)
                .frame(width: 36, height: 36)
                .background(app.color.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(app.name)
                    .font(.body.weight(.medium))
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - App Output View (reads status.json)

struct AppOutputView: View {
    let app: RegisteredApp
    let mtjoyURL: URL?
    @State private var status: AppStatus?
    @State private var previewURL: URL?

    private var appRootURL: URL? {
        mtjoyURL?.appendingPathComponent(app.rootPath)
    }

    var body: some View {
        Group {
            if let status, !status.outputs.isEmpty {
                List {
                    // Progress items
                    let progresses = status.outputs.filter { $0.type == "progress" }
                    if !progresses.isEmpty {
                        Section("状态") {
                            ForEach(progresses) { output in
                                ProgressRow(output: output)
                            }
                        }
                    }

                    // Reports
                    let reports = status.outputs.filter { $0.type == "report" }
                    if !reports.isEmpty {
                        Section("报告") {
                            ForEach(reports) { output in
                                Button {
                                    openReport(output)
                                } label: {
                                    OutputRow(output: output, icon: "doc.text")
                                }
                            }
                        }
                    }

                    // Deep dives
                    let dives = status.outputs.filter { $0.type == "deep_dive" }
                    if !dives.isEmpty {
                        Section("深度分析") {
                            ForEach(dives) { output in
                                NavigationLink {
                                    DeepDiveView(output: output)
                                } label: {
                                    OutputRow(output: output, icon: "magnifyingglass")
                                }
                            }
                        }
                    }

                    // Alerts
                    let alerts = status.outputs.filter { $0.type == "alert" }
                    if !alerts.isEmpty {
                        Section("提醒") {
                            ForEach(alerts) { output in
                                AlertRow(output: output)
                            }
                        }
                    }
                }
                .listStyle(.insetGrouped)
            } else {
                ContentUnavailableView(
                    "暂无输出",
                    systemImage: "tray",
                    description: Text("\(app.name) 还没有生成输出")
                )
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { loadStatus() }
        .refreshable { loadStatus() }
        .quickLookPreview($previewURL)
    }

    private func loadStatus() {
        guard let mtjoy = mtjoyURL, !app.statusPath.isEmpty else { return }
        let statusFile = mtjoy
            .appendingPathComponent(app.rootPath)
            .appendingPathComponent(app.statusPath)
        let fm = FileManager.default
        if !fm.isReadableFile(atPath: statusFile.path) {
            try? fm.startDownloadingUbiquitousItem(at: statusFile)
            return
        }
        guard let data = try? Data(contentsOf: statusFile) else { return }
        status = try? JSONDecoder().decode(AppStatus.self, from: data)
    }

    private func openReport(_ output: AppOutput) {
        guard let path = output.path else { return }
        let fm = FileManager.default

        // Handle absolute paths (e.g. PDF reports with full path)
        let fileURL: URL
        if path.hasPrefix("/") {
            fileURL = URL(fileURLWithPath: path)
        } else if let root = appRootURL {
            fileURL = root.appendingPathComponent(path)
        } else {
            return
        }

        if fm.isReadableFile(atPath: fileURL.path) {
            previewURL = fileURL
        } else {
            try? fm.startDownloadingUbiquitousItem(at: fileURL)
            // Retry after a short delay to allow download to start
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                if fm.isReadableFile(atPath: fileURL.path) {
                    previewURL = fileURL
                }
            }
        }
    }
}

// MARK: - Output Row Views

struct ProgressRow: View {
    let output: AppOutput

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(output.title)
                    .font(.subheadline.weight(.medium))
                Spacer()
                if let stage = output.stage {
                    Text("\(stage.label)")
                        .font(.caption)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(.blue.opacity(0.1), in: Capsule())
                }
            }
            if let stage = output.stage {
                ProgressView(value: Double(stage.current), total: Double(stage.total))
                    .tint(.blue)
            }
            if let highlights = output.highlights, !highlights.isEmpty {
                ForEach(highlights, id: \.self) { h in
                    Text("· \(h)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

struct OutputRow: View {
    let output: AppOutput
    let icon: String

    var body: some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(.secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(output.title)
                    .font(.subheadline)
                    .lineLimit(2)
                Text(formatDate(output.updatedAt))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private func formatDate(_ iso: String) -> String {
        String(iso.prefix(10))
    }
}

struct AlertRow: View {
    let output: AppOutput

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: output.severity == "critical" ? "exclamationmark.triangle.fill" : "exclamationmark.triangle")
                .foregroundStyle(output.severity == "critical" ? .red : .orange)
            Text(output.message ?? output.title)
                .font(.subheadline)
        }
    }
}

struct DeepDiveView: View {
    let output: AppOutput
    @State private var rendered: AttributedString?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(output.title)
                    .font(.title2.bold())
                if let topic = output.topic {
                    Text(topic)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Divider()
                if let content = output.content {
                    if let rendered {
                        Text(rendered)
                            .font(.body)
                            .lineSpacing(6)
                            .textSelection(.enabled)
                    } else {
                        Text(content)
                            .font(.body)
                            .lineSpacing(6)
                            .textSelection(.enabled)
                    }
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if rendered == nil, let content = output.content {
                rendered = try? AttributedString(
                    markdown: content,
                    options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                )
            }
        }
    }
}

// MARK: - Mira sub-library (artifacts browser)

struct MiraLibraryView: View {
    var bridge: BridgeService

    private let sections: [(title: String, folder: String, icon: String)] = [
        ("Briefings", "briefings", "newspaper"),
        ("Writings", "writings", "doc.richtext"),
        ("Research", "research", "magnifyingglass.circle"),
    ]

    var body: some View {
        if let artifactsURL = bridge.artifactsURL {
            List(sections, id: \.folder) { section in
                let dir = artifactsURL.appendingPathComponent(section.folder)
                NavigationLink {
                    LibraryFolderView(folderURL: dir)
                        .navigationTitle(section.title)
                } label: {
                    Label(section.title, systemImage: section.icon)
                }
            }
            .listStyle(.insetGrouped)
            .navigationBarTitleDisplayMode(.inline)
        } else {
            ContentUnavailableView("未设置文件夹", systemImage: "folder.badge.questionmark")
        }
    }
}

// MARK: - Generic folder browser

struct LibraryFolderView: View {
    let folderURL: URL
    @State private var items: [LibraryItem] = []
    @State private var previewURL: URL?

    var body: some View {
        Group {
            if items.isEmpty {
                ContentUnavailableView("暂无内容", systemImage: "doc.text")
            } else {
                List(items) { item in
                    if item.isDirectory {
                        NavigationLink {
                            LibraryFolderView(folderURL: item.url)
                                .navigationTitle(item.name)
                        } label: {
                            LibraryItemRow(item: item)
                        }
                    } else {
                        Button {
                            triggerDownload(item.url)
                            previewURL = item.url
                        } label: {
                            LibraryItemRow(item: item)
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .onAppear { loadItems() }
        .refreshable { loadItems() }
        .quickLookPreview($previewURL)
    }

    private func loadItems() {
        let fm = FileManager.default
        guard fm.fileExists(atPath: folderURL.path) else {
            items = []
            return
        }
        do {
            let contents = try fm.contentsOfDirectory(
                at: folderURL,
                includingPropertiesForKeys: [.isDirectoryKey, .contentModificationDateKey],
                options: [.skipsHiddenFiles]
            )
            items = contents.compactMap { url in
                let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .contentModificationDateKey])
                let isDir = values?.isDirectory ?? false
                return LibraryItem(
                    name: url.lastPathComponent,
                    url: url,
                    date: values?.contentModificationDate ?? .distantPast,
                    isDirectory: isDir,
                    childCount: isDir ? (try? fm.contentsOfDirectory(atPath: url.path).filter { !$0.hasPrefix(".") }.count) ?? 0 : 0
                )
            }
            .sorted { lhs, rhs in
                if lhs.isDirectory != rhs.isDirectory { return lhs.isDirectory }
                return lhs.date > rhs.date
            }
        } catch {
            items = []
        }
    }

    private func triggerDownload(_ url: URL) {
        let fm = FileManager.default
        if !fm.isReadableFile(atPath: url.path) {
            try? fm.startDownloadingUbiquitousItem(at: url)
        }
    }
}

struct LibraryItem: Identifiable {
    let name: String
    let url: URL
    let date: Date
    let isDirectory: Bool
    let childCount: Int

    var id: String { url.path }
}

struct LibraryItemRow: View {
    let item: LibraryItem

    var body: some View {
        HStack {
            Image(systemName: item.isDirectory ? "folder" : fileIcon(item.name))
                .foregroundStyle(item.isDirectory ? .blue : .secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.body)
                    .lineLimit(1)
                Text(formatDate(item.date))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            if item.isDirectory && item.childCount > 0 {
                Text("\(item.childCount)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
            }
        }
    }

    private func fileIcon(_ name: String) -> String {
        let ext = (name as NSString).pathExtension.lowercased()
        switch ext {
        case "md", "txt": return "doc.text"
        case "json": return "curlybraces"
        case "pdf": return "doc.richtext"
        case "jpg", "jpeg", "png", "heic", "gif", "webp": return "photo"
        case "mp4", "mov", "m4v", "avi": return "film"
        case "mp3", "m4a", "wav", "aac": return "waveform"
        case "swift", "py", "js", "ts": return "chevron.left.forwardslash.chevron.right"
        default: return "doc"
        }
    }

    private func formatDate(_ date: Date) -> String {
        let f = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            f.dateFormat = "'Today' HH:mm"
        } else {
            f.dateFormat = "MM/dd HH:mm"
        }
        return f.string(from: date)
    }
}
