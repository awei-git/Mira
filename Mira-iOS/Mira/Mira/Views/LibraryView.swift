import SwiftUI
import QuickLook

struct LibraryView: View {
    var bridge: BridgeService

    var body: some View {
        NavigationStack {
            if let artifactsURL = bridge.artifactsURL {
                LibraryFolderView(folderURL: artifactsURL)
                    .navigationTitle("Library")
            } else {
                ContentUnavailableView(
                    "未设置文件夹",
                    systemImage: "folder.badge.questionmark"
                )
                .navigationTitle("Library")
            }
        }
    }
}

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
                // Folders first, then by date
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
