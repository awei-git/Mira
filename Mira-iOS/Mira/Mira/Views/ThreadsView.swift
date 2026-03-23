import SwiftUI

struct ThreadsView: View {
    @Environment(ItemStore.self) private var store
    @Environment(SyncEngine.self) private var sync
    @State private var filter: ThreadFilter = .all
    @State private var searchText = ""
    @State private var showNewItem = false
    @State private var expandedSections: Set<String> = ["Today", "Yesterday"]

    enum ThreadFilter: String, CaseIterable {
        case all = "All"
        case timeline = "Timeline"
        case requests = "Requests"
        case pinned = "Pinned"
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Filter chips
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(ThreadFilter.allCases, id: \.self) { f in
                            FilterChip(label: f.rawValue, selected: filter == f) {
                                filter = f
                            }
                        }
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                }

                // Item list
                List {
                    ForEach(groupedItems, id: \.key) { group in
                        let isRecent = group.key == "Today" || group.key == "Yesterday"

                        if isRecent {
                            Section(group.key) {
                                threadItems(group.items)
                            }
                        } else {
                            // Older sections: collapsed by default
                            Section {
                                if expandedSections.contains(group.key) {
                                    threadItems(group.items)
                                }
                            } header: {
                                Button {
                                    withAnimation {
                                        if expandedSections.contains(group.key) {
                                            expandedSections.remove(group.key)
                                        } else {
                                            expandedSections.insert(group.key)
                                        }
                                    }
                                } label: {
                                    HStack {
                                        Text(group.key)
                                        Text("(\(group.items.count))")
                                            .foregroundStyle(.secondary)
                                        Spacer()
                                        Image(systemName: expandedSections.contains(group.key)
                                              ? "chevron.down" : "chevron.right")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }
                .listStyle(.plain)
            }
            .navigationTitle("Threads")
            .navigationDestination(for: String.self) { id in
                ItemDetailView(itemId: id)
            }
            .searchable(text: $searchText, prompt: "Search threads...")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showNewItem = true } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showNewItem) {
                NewItemSheet()
            }
            .refreshable { sync.refresh() }
        }
    }

    private var isSearching: Bool { !searchText.isEmpty }

    private var filteredItems: [MiraItem] {
        var result: [MiraItem]

        if isSearching {
            // Search has no time limit — can find anything
            result = store.search(searchText)
        } else {
            // Normal browsing: hide items older than 10 days
            let tenDaysAgo = Calendar.current.date(byAdding: .day, value: -10,
                                                    to: Calendar.current.startOfDay(for: Date()))!
            let base: [MiraItem]
            switch filter {
            case .all:
                base = store.allVisible
            case .timeline:
                base = store.allVisible.filter { $0.type == .feed || $0.type == .discussion }
            case .requests:
                base = store.filtered(type: .request)
            case .pinned:
                base = store.pinnedItems
            }
            result = base.filter { $0.createdDate >= tenDaysAgo }
        }

        return result
    }

    @ViewBuilder
    private func threadItems(_ items: [MiraItem]) -> some View {
        ForEach(items) { item in
            NavigationLink(value: item.id) {
                ThreadRow(item: item)
            }
            .swipeActions(edge: .trailing) {
                Button(role: .destructive) {
                } label: {
                    Label("Archive", systemImage: "archivebox")
                }
            }
            .swipeActions(edge: .leading) {
                Button {
                } label: {
                    Label(item.pinned ? "Unpin" : "Pin",
                          systemImage: item.pinned ? "pin.slash" : "pin")
                }
                .tint(.yellow)
            }
        }
    }

    /// Map of feed ID → created date, for binding discussions to their source feed's day
    private var feedDateMap: [String: Date] {
        var map: [String: Date] = [:]
        for item in store.items where item.type == .feed {
            map[item.id] = item.createdDate
        }
        return map
    }

    private var groupedItems: [(key: String, items: [MiraItem])] {
        let cal = Calendar.current
        var groups: [String: [MiraItem]] = [:]
        let feedDates = feedDateMap

        for item in filteredItems {
            // Discussions with parentId use the parent feed's creation date
            let groupDate: Date
            if item.type == .discussion, let pid = item.parentId, let fd = feedDates[pid] {
                groupDate = fd
            } else {
                groupDate = item.createdDate
            }

            let key: String
            if cal.isDateInToday(groupDate) {
                key = "Today"
            } else if cal.isDateInYesterday(groupDate) {
                key = "Yesterday"
            } else {
                let df = DateFormatter()
                df.dateFormat = "MMM d"
                key = df.string(from: groupDate)
            }
            groups[key, default: []].append(item)
        }

        let order = ["Today", "Yesterday"]
        let sorted = groups.sorted { (a: (key: String, value: [MiraItem]), b: (key: String, value: [MiraItem])) in
            let ai = order.firstIndex(of: a.key) ?? 99
            let bi = order.firstIndex(of: b.key) ?? 99
            if ai != bi { return ai < bi }
            return (a.value.first?.date ?? .distantPast) > (b.value.first?.date ?? .distantPast)
        }
        return sorted.map { (key: $0.key, items: $0.value) }
    }
}

// MARK: - Components

struct FilterChip: View {
    let label: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.caption.weight(selected ? .semibold : .regular))
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(selected ? Color.primary.opacity(0.1) : .clear)
                .clipShape(Capsule())
                .overlay(Capsule().stroke(Color.primary.opacity(0.2), lineWidth: 1))
        }
        .buttonStyle(.plain)
    }
}

struct ThreadRow: View {
    let item: MiraItem

    var body: some View {
        HStack(spacing: 10) {
            // Type + status icon
            ZStack {
                Circle()
                    .fill(statusColor.opacity(0.15))
                    .frame(width: 36, height: 36)
                Image(systemName: item.typeIcon)
                    .font(.system(size: 14))
                    .foregroundStyle(statusColor)
            }

            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    if item.pinned {
                        Image(systemName: "pin.fill")
                            .font(.caption2)
                            .foregroundStyle(.yellow)
                    }
                    Text(item.title)
                        .font(.subheadline.weight(.medium))
                        .lineLimit(1)
                }
                HStack(spacing: 4) {
                    if let statusText = statusLabel {
                        Text(statusText)
                            .font(.caption2)
                            .foregroundStyle(statusColor)
                    }
                    Text(item.lastMessagePreview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if !item.tags.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(item.tags.prefix(3), id: \.self) { tag in
                            Text("#\(tag)")
                                .font(.caption2)
                                .foregroundStyle(.blue)
                        }
                    }
                }
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(relativeTime)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                Text("\(item.messages.count)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 4)
    }

    private var statusColor: Color {
        switch item.status {
        case .queued: return .secondary
        case .working: return .blue
        case .needsInput: return .orange
        case .done: return .green
        case .failed: return .red
        case .archived: return .secondary
        }
    }

    private var statusLabel: String? {
        switch item.status {
        case .working: return "Working"
        case .needsInput: return "Needs input"
        case .failed: return "Failed"
        default: return nil
        }
    }

    private var relativeTime: String {
        let seconds = Date().timeIntervalSince(item.date)
        if seconds < 60 { return "now" }
        if seconds < 3600 { return "\(Int(seconds / 60))m" }
        if seconds < 86400 { return "\(Int(seconds / 3600))h" }
        return "\(Int(seconds / 86400))d"
    }
}
