import SwiftUI
import QuickLook

// MARK: - Artifact Categories

private struct ArtifactCategory: Identifiable {
    let id: String
    let name: String
    let icon: String
    let color: Color
}

private let knownCategories: [ArtifactCategory] = [
    ArtifactCategory(id: "writings", name: "Writings", icon: "doc.richtext", color: .purple),
    ArtifactCategory(id: "briefings", name: "Briefings", icon: "newspaper", color: .blue),
    ArtifactCategory(id: "audio", name: "Audio", icon: "waveform", color: .orange),
    ArtifactCategory(id: "video", name: "Video", icon: "film", color: .red),
    ArtifactCategory(id: "photos", name: "Photos", icon: "photo.on.rectangle", color: .green),
    ArtifactCategory(id: "research", name: "Research", icon: "magnifyingglass.circle", color: .cyan),
]

// MARK: - Artifacts View (tab root)

struct ArtifactsView: View {
    @Environment(BridgeConfig.self) private var config

    var body: some View {
        NavigationStack {
            if let artifactsURL = config.artifactsURL {
                ArtifactsRootView(artifactsURL: artifactsURL)
                    .navigationTitle("Artifacts")
            } else {
                ContentUnavailableView(
                    "No folder set",
                    systemImage: "folder.badge.questionmark",
                    description: Text("Select Mira-Bridge folder in Settings")
                )
                .navigationTitle("Artifacts")
            }
        }
    }
}

struct ArtifactsRootView: View {
    let artifactsURL: URL
    @State private var folders: [ArtifactFolder] = []

    var body: some View {
        List(folders) { folder in
            NavigationLink {
                ArtifactFolderBrowser(folderURL: folder.url)
                    .navigationTitle(folder.category?.name ?? folder.name)
            } label: {
                HStack(spacing: 12) {
                    if let cat = folder.category {
                        Image(systemName: cat.icon)
                            .font(.title3)
                            .foregroundStyle(cat.color)
                            .frame(width: 32, height: 32)
                            .background(cat.color.opacity(0.12), in: RoundedRectangle(cornerRadius: 7))
                    } else {
                        Image(systemName: "folder")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                            .frame(width: 32, height: 32)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(folder.category?.name ?? folder.name)
                            .font(.body.weight(.medium))
                        if folder.itemCount > 0 {
                            Text("\(folder.itemCount) items")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .listStyle(.insetGrouped)
        .onAppear { loadFolders() }
        .refreshable { loadFolders() }
    }

    private func loadFolders() {
        let fm = FileManager.default
        // Trigger iCloud download for the artifacts root and known subdirs
        try? fm.startDownloadingUbiquitousItem(at: artifactsURL)
        for cat in knownCategories {
            try? fm.startDownloadingUbiquitousItem(at: artifactsURL.appendingPathComponent(cat.id))
        }

        guard fm.fileExists(atPath: artifactsURL.path) else {
            folders = []
            return
        }
        do {
            let contents = try fm.contentsOfDirectory(
                at: artifactsURL,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
            let dirs = contents.filter { url in
                (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
            }

            // Known categories first (in defined order), then unknown folders
            let knownIds = Set(knownCategories.map(\.id))
            var result: [ArtifactFolder] = []

            for cat in knownCategories {
                if let url = dirs.first(where: { $0.lastPathComponent == cat.id }) {
                    let count = (try? fm.contentsOfDirectory(atPath: url.path).filter { !$0.hasPrefix(".") && !$0.hasPrefix("_") }.count) ?? 0
                    result.append(ArtifactFolder(name: cat.id, url: url, category: cat, itemCount: count))
                }
            }

            for url in dirs where !knownIds.contains(url.lastPathComponent) && !url.lastPathComponent.hasPrefix("_") {
                let name = url.lastPathComponent
                let count = (try? fm.contentsOfDirectory(atPath: url.path).filter { !$0.hasPrefix(".") }.count) ?? 0
                result.append(ArtifactFolder(name: name, url: url, category: nil, itemCount: count))
            }

            folders = result
        } catch {
            folders = []
        }
    }
}

private struct ArtifactFolder: Identifiable {
    let name: String
    let url: URL
    let category: ArtifactCategory?
    let itemCount: Int
    var id: String { url.path }
}

// MARK: - Folder Browser (Files-like)

struct ArtifactFolderBrowser: View {
    let folderURL: URL
    @State private var items: [ArtifactItem] = []
    @State private var previewURL: URL?

    var body: some View {
        Group {
            if items.isEmpty {
                ContentUnavailableView("Empty", systemImage: "tray")
            } else {
                List(items) { item in
                    if item.isDirectory {
                        NavigationLink {
                            ArtifactFolderBrowser(folderURL: item.url)
                                .navigationTitle(item.name)
                        } label: {
                            ArtifactItemRow(item: item)
                        }
                    } else {
                        Button {
                            triggerDownload(item.url)
                            previewURL = item.url
                        } label: {
                            ArtifactItemRow(item: item)
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { loadItems() }
        .refreshable { loadItems() }
        .quickLookPreview($previewURL)
    }

    private func loadItems() {
        let fm = FileManager.default
        try? fm.startDownloadingUbiquitousItem(at: folderURL)
        guard fm.fileExists(atPath: folderURL.path) else {
            items = []
            return
        }
        do {
            let contents = try fm.contentsOfDirectory(
                at: folderURL,
                includingPropertiesForKeys: [.isDirectoryKey, .contentModificationDateKey, .fileSizeKey],
                options: [.skipsHiddenFiles]
            )
            items = contents.compactMap { url in
                let name = url.lastPathComponent
                if name.hasPrefix("_") || name.hasPrefix(".") { return nil }
                let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .contentModificationDateKey, .fileSizeKey])
                let isDir = values?.isDirectory ?? false
                return ArtifactItem(
                    name: name,
                    url: url,
                    date: values?.contentModificationDate ?? .distantPast,
                    size: Int64(values?.fileSize ?? 0),
                    isDirectory: isDir,
                    childCount: isDir ? (try? fm.contentsOfDirectory(atPath: url.path).filter { !$0.hasPrefix(".") && !$0.hasPrefix("_") }.count) ?? 0 : 0
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

// MARK: - Item Model

struct ArtifactItem: Identifiable {
    let name: String
    let url: URL
    let date: Date
    let size: Int64
    let isDirectory: Bool
    let childCount: Int

    var id: String { url.path }
}

// MARK: - Item Row

struct ArtifactItemRow: View {
    let item: ArtifactItem

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: item.isDirectory ? "folder.fill" : fileIcon(item.name))
                .foregroundStyle(item.isDirectory ? .blue : iconColor(item.name))
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(displayName(item.name))
                    .font(.body)
                    .lineLimit(2)
                HStack(spacing: 8) {
                    Text(formatDate(item.date))
                    if !item.isDirectory && item.size > 0 {
                        Text(formatSize(item.size))
                    }
                    if item.isDirectory && item.childCount > 0 {
                        Text("\(item.childCount) items")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
    }

    private func displayName(_ name: String) -> String {
        // Strip extension for common document types, keep for media
        let ext = (name as NSString).pathExtension.lowercased()
        switch ext {
        case "md", "txt", "json":
            return (name as NSString).deletingPathExtension
        default:
            return name
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
        case "mp3", "m4a", "wav", "aac", "flac": return "waveform"
        case "swift", "py", "js", "ts": return "chevron.left.forwardslash.chevron.right"
        case "epub": return "book"
        default: return "doc"
        }
    }

    private func iconColor(_ name: String) -> Color {
        let ext = (name as NSString).pathExtension.lowercased()
        switch ext {
        case "md", "txt": return .secondary
        case "pdf": return .red
        case "jpg", "jpeg", "png", "heic", "gif", "webp": return .green
        case "mp4", "mov", "m4v", "avi": return .red
        case "mp3", "m4a", "wav", "aac", "flac": return .orange
        case "swift", "py", "js", "ts": return .blue
        default: return .secondary
        }
    }

    private func formatDate(_ date: Date) -> String {
        let f = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            f.dateFormat = "HH:mm"
        } else if Calendar.current.isDate(date, equalTo: Date(), toGranularity: .year) {
            f.dateFormat = "M/d"
        } else {
            f.dateFormat = "yyyy/M/d"
        }
        return f.string(from: date)
    }

    private func formatSize(_ bytes: Int64) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        if bytes < 1024 * 1024 { return "\(bytes / 1024) KB" }
        return String(format: "%.1f MB", Double(bytes) / 1_048_576)
    }
}
