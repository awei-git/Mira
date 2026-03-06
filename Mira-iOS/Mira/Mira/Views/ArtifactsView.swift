import SwiftUI
import QuickLook

struct ArtifactsView: View {
    @Bindable var bridge: BridgeService
    @Environment(\.dismiss) private var dismiss
    @State private var previewURL: URL?
    @State private var expandedSection: String? = nil

    private let sections: [(title: String, folder: String, icon: String)] = [
        ("Briefings", "briefings", "newspaper"),
        ("Writings", "writings", "doc.richtext"),
        ("Research", "research", "magnifyingglass.circle"),
    ]

    var body: some View {
        NavigationStack {
            List {
                ForEach(sections, id: \.folder) { section in
                    let items = listTopLevel(in: section.folder)
                    if !items.isEmpty {
                        Section {
                            DisclosureGroup(
                                isExpanded: Binding(
                                    get: { expandedSection == section.folder },
                                    set: { expandedSection = $0 ? section.folder : nil }
                                )
                            ) {
                                ForEach(items) { item in
                                    if item.isDirectory {
                                        // Drill-down for project folders
                                        NavigationLink {
                                            ProjectDetailView(
                                                projectName: item.name,
                                                projectURL: item.url,
                                                previewURL: $previewURL
                                            )
                                        } label: {
                                            itemRow(item, icon: "folder")
                                        }
                                    } else {
                                        Button { openFile(item.url) } label: {
                                            itemRow(item, icon: section.icon)
                                        }
                                    }
                                }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: section.icon)
                                        .font(.system(size: 14))
                                        .foregroundStyle(.blue)
                                        .frame(width: 22)
                                    Text(section.title)
                                        .font(.system(size: 14, weight: .medium))
                                    Spacer()
                                    Text("\(items.count)")
                                        .font(.system(size: 11))
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                }

                if allEmpty {
                    ContentUnavailableView(
                        "No Artifacts",
                        systemImage: "tray",
                        description: Text("Briefings, writings, and research will appear here.")
                    )
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Artifacts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("完成") { dismiss() }
                        .font(.system(size: 14))
                }
            }
            .quickLookPreview($previewURL)
        }
    }

    @ViewBuilder
    private func itemRow(_ item: ArtifactItem, icon: String) -> some View {
        HStack {
            Image(systemName: icon)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(item.name)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(item.dateLabel)
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            if item.isDirectory {
                Text("\(item.childCount)")
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Color(.systemGray5))
                    .clipShape(Capsule())
            }
        }
    }

    private var allEmpty: Bool {
        sections.allSatisfy { listTopLevel(in: $0.folder).isEmpty }
    }

    private func listTopLevel(in subfolder: String) -> [ArtifactItem] {
        guard let base = bridge.artifactsURL else { return [] }
        let dir = base.appendingPathComponent(subfolder)
        let fm = FileManager.default

        try? fm.startDownloadingUbiquitousItem(at: dir)

        guard let contents = try? fm.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var items: [ArtifactItem] = []
        for url in contents {
            let vals = try? url.resourceValues(forKeys: [.isDirectoryKey, .contentModificationDateKey])
            let isDir = vals?.isDirectory ?? false
            let date = vals?.contentModificationDate ?? Date.distantPast

            if isDir {
                let children = (try? fm.contentsOfDirectory(
                    at: url, includingPropertiesForKeys: nil,
                    options: .skipsHiddenFiles
                ))?.count ?? 0
                if children > 0 {
                    items.append(ArtifactItem(
                        name: url.lastPathComponent,
                        url: url,
                        date: date,
                        isDirectory: true,
                        childCount: children
                    ))
                }
            } else if ["md", "txt", "docx"].contains(url.pathExtension) {
                items.append(ArtifactItem(
                    name: url.deletingPathExtension().lastPathComponent,
                    url: url,
                    date: date,
                    isDirectory: false,
                    childCount: 0
                ))
            }
        }

        items.sort { $0.date > $1.date }
        return items
    }

    private func openFile(_ url: URL) {
        let fm = FileManager.default
        if fm.isReadableFile(atPath: url.path) {
            previewURL = url
        } else {
            try? fm.startDownloadingUbiquitousItem(at: url)
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                if fm.isReadableFile(atPath: url.path) {
                    previewURL = url
                }
            }
        }
    }
}

// MARK: - Project detail (drill-down into a folder)

struct ProjectDetailView: View {
    let projectName: String
    let projectURL: URL
    @Binding var previewURL: URL?

    var body: some View {
        List {
            ForEach(listProjectFiles(), id: \.url) { file in
                if file.isDirectory {
                    NavigationLink {
                        ProjectDetailView(
                            projectName: file.name,
                            projectURL: file.url,
                            previewURL: $previewURL
                        )
                    } label: {
                        fileRow(file, icon: "folder")
                    }
                } else {
                    Button { openFile(file.url) } label: {
                        fileRow(file, icon: iconFor(file.url.pathExtension))
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(projectName)
        .navigationBarTitleDisplayMode(.inline)
        .quickLookPreview($previewURL)
    }

    @ViewBuilder
    private func fileRow(_ file: ArtifactItem, icon: String) -> some View {
        HStack {
            Image(systemName: icon)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(file.name)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                Text(file.dateLabel)
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            if file.isDirectory {
                Image(systemName: "chevron.right")
                    .font(.system(size: 10))
                    .foregroundStyle(.quaternary)
            }
        }
    }

    private func iconFor(_ ext: String) -> String {
        switch ext {
        case "md": return "doc.text"
        case "txt": return "doc.plaintext"
        case "docx": return "doc.fill"
        default: return "doc"
        }
    }

    private func listProjectFiles() -> [ArtifactItem] {
        let fm = FileManager.default
        try? fm.startDownloadingUbiquitousItem(at: projectURL)

        guard let contents = try? fm.contentsOfDirectory(
            at: projectURL,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var items: [ArtifactItem] = []
        for url in contents {
            let vals = try? url.resourceValues(forKeys: [.isDirectoryKey, .contentModificationDateKey])
            let isDir = vals?.isDirectory ?? false
            let date = vals?.contentModificationDate ?? Date.distantPast

            items.append(ArtifactItem(
                name: isDir ? url.lastPathComponent : url.deletingPathExtension().lastPathComponent,
                url: url,
                date: date,
                isDirectory: isDir,
                childCount: isDir ? ((try? fm.contentsOfDirectory(at: url, includingPropertiesForKeys: nil, options: .skipsHiddenFiles))?.count ?? 0) : 0
            ))
        }

        // Sort: directories first, then by date
        items.sort {
            if $0.isDirectory != $1.isDirectory { return $0.isDirectory }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
        return items
    }

    private func openFile(_ url: URL) {
        let fm = FileManager.default
        if fm.isReadableFile(atPath: url.path) {
            previewURL = url
        } else {
            try? fm.startDownloadingUbiquitousItem(at: url)
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                if fm.isReadableFile(atPath: url.path) {
                    previewURL = url
                }
            }
        }
    }
}

// MARK: - Model

struct ArtifactItem: Identifiable {
    let name: String
    let url: URL
    let date: Date
    let isDirectory: Bool
    let childCount: Int

    var id: URL { url }

    var dateLabel: String {
        let f = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            f.dateFormat = "HH:mm"
            return "Today \(f.string(from: date))"
        } else {
            f.dateFormat = "MM/dd HH:mm"
            return f.string(from: date)
        }
    }
}
