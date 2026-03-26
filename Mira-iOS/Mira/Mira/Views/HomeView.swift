import SwiftUI

// WhatsApp iOS dark mode — exact palette
let waAccent    = Color(hex: 0x00A884) // teal green (brighter for dark)
let waBadge     = Color(hex: 0x25D366) // whatsapp green — badges, online dot
let waListBg    = Color(hex: 0x111B21) // dark charcoal bg
let waCardBg    = Color(hex: 0x1F2C34) // card/row bg (slightly lighter)
let waChatBg    = Color(hex: 0x0B141A) // chat wallpaper (darkest)
let waOutBubble = Color(hex: 0x005C4B) // outgoing bubble (dark teal)
let waInBubble  = Color(hex: 0x202C33) // incoming bubble (dark gray)
let waTextPri   = Color(hex: 0xE9EDEF) // primary text (light)
let waTextSec   = Color(hex: 0x8696A0) // secondary text (gray)
let waLink      = Color(hex: 0x53BDEB) // in-chat links (brighter blue)

// Feed type colors
let colorExplore   = Color(hex: 0x4A9EFF) // blue
let colorMira      = Color(hex: 0xE8A838) // warm gold
let colorZhesi     = Color(hex: 0xD97706) // amber
let colorTetra     = Color(hex: 0x22C55E) // green
let colorRequest   = Color(hex: 0x818CF8) // indigo
let colorDiscuss   = Color(hex: 0xA78BFA) // purple

// Convenience
private let accentGreen = waAccent
private let warmBg = waListBg
private let cardBg = waCardBg

extension Color {
    init(hex: UInt, alpha: Double = 1.0) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: alpha
        )
    }
}

struct HomeView: View {
    @Environment(BridgeConfig.self) private var config
    @Environment(SyncEngine.self) private var sync
    @Environment(ItemStore.self) private var store
    @Environment(CommandWriter.self) private var commands
    @State private var showNewItem = false
    @State private var showRecall = false
    @State private var recallQuery = ""
    @State private var searchText = ""
    @State private var expandedSections: Set<String> = ["Today", "Yesterday"]

    var body: some View {
        NavigationStack {
            ZStack(alignment: .bottomTrailing) {
                warmBg.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 0) {
                        // Search bar
                        HStack {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(.secondary)
                            TextField("Search conversations...", text: $searchText)
                                .font(.body)
                                .foregroundStyle(waTextPri)
                        }
                        .padding(12)
                        .background(cardBg)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(warmBg)

                        // Needs attention banner
                        if !store.needsAttention.isEmpty {
                            VStack(spacing: 0) {
                                ForEach(store.needsAttention) { item in
                                    NavigationLink(value: item.id) {
                                        AttentionBanner(item: item)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            .padding(.bottom, 8)
                        }

                        // Date-grouped items
                        ForEach(groupedItems, id: \.key) { group in
                            let isRecent = group.key == "Today" || group.key == "Yesterday"

                            // Date separator
                            dateSeparator(group.key, count: group.items.count, expanded: isRecent || expandedSections.contains(group.key)) {
                                if !isRecent {
                                    withAnimation {
                                        if expandedSections.contains(group.key) {
                                            expandedSections.remove(group.key)
                                        } else {
                                            expandedSections.insert(group.key)
                                        }
                                    }
                                }
                            }

                            if isRecent || expandedSections.contains(group.key) {
                                VStack(spacing: 1) {
                                    ForEach(group.items) { item in
                                        NavigationLink(value: item.id) {
                                            ChatListRow(item: item)
                                        }
                                        .buttonStyle(.plain)
                                    }
                                }
                                .background(cardBg)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .padding(.horizontal, 12)
                                .padding(.bottom, 8)
                            }
                        }

                        Spacer(minLength: 80)
                    }
                }

                // FAB
                Button { showNewItem = true } label: {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(.white)
                        .frame(width: 54, height: 54)
                        .background(accentGreen)
                        .clipShape(Circle())
                        .shadow(color: .black.opacity(0.15), radius: 6, y: 3)
                }
                .padding(.trailing, 16)
                .padding(.bottom, 12)
            }
            .navigationTitle(config.profile?.displayName ?? "Mira")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    statusPill
                }
            }
            .navigationDestination(for: String.self) { id in
                ItemDetailView(itemId: id)
            }
            .sheet(isPresented: $showNewItem) {
                NewItemSheet()
            }
            .refreshable { sync.refresh() }
        }
    }

    // MARK: - Date Separator

    private func dateSeparator(_ label: String, count: Int, expanded: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(label)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(waTextPri)
                Text("\(count)")
                    .font(.caption)
                    .foregroundStyle(waTextSec)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(waCardBg)
                    .clipShape(Capsule())
                Spacer()
                if label != "Today" && label != "Yesterday" {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(waTextSec)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Grouping

    private var filteredItems: [MiraItem] {
        let base: [MiraItem]
        if searchText.isEmpty {
            let tenDaysAgo = Calendar.current.date(byAdding: .day, value: -10,
                                                    to: Calendar.current.startOfDay(for: Date()))!
            base = store.allVisible.filter { $0.createdDate >= tenDaysAgo }
        } else {
            base = store.search(searchText)
        }
        // Exclude request (todo/task) items — they're shown in the todo card
        return base.filter { $0.type != .request }
    }

    private var groupedItems: [(key: String, items: [MiraItem])] {
        let cal = Calendar.current
        var groups: [String: [MiraItem]] = [:]

        for item in filteredItems {
            let key: String
            if cal.isDateInToday(item.createdDate) {
                key = "Today"
            } else if cal.isDateInYesterday(item.createdDate) {
                key = "Yesterday"
            } else {
                let df = DateFormatter()
                df.dateFormat = "MMM d"
                key = df.string(from: item.createdDate)
            }
            groups[key, default: []].append(item)
        }

        // Sort: today first, then yesterday, then by date descending
        let order = ["Today", "Yesterday"]
        return groups
            .sorted { a, b in
                let ai = order.firstIndex(of: a.key) ?? 99
                let bi = order.firstIndex(of: b.key) ?? 99
                if ai != bi { return ai < bi }
                let ad = a.value.first?.date ?? .distantPast
                let bd = b.value.first?.date ?? .distantPast
                return ad > bd
            }
            .map { (key: $0.key, items: $0.value.sorted { $0.date > $1.date }) }
    }

    private var statusPill: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(sync.agentOnline ? accentGreen : .red)
                .frame(width: 8, height: 8)
            if let hb = sync.heartbeat, hb.isBusy {
                Text("\(hb.activeCount ?? 0)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(accentGreen)
            }
        }
    }
}

// MARK: - Attention Banner

struct AttentionBanner: View {
    let item: MiraItem

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.bubble.fill")
                .font(.system(size: 16))
                .foregroundStyle(.white)
                .frame(width: 36, height: 36)
                .background(.orange)
                .clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(1)
                Text("Waiting for your reply")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(cardBg)
    }
}

// MARK: - Chat List Row

struct ChatListRow: View {
    let item: MiraItem

    var body: some View {
        HStack(spacing: 12) {
            // Avatar with type-specific color
            ZStack {
                RoundedRectangle(cornerRadius: 10)
                    .fill(itemColor.opacity(0.15))
                    .frame(width: 48, height: 48)
                Image(systemName: avatarIcon)
                    .font(.system(size: 20))
                    .foregroundStyle(itemColor)
            }

            // Content
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.title)
                        .font(.system(size: 17, weight: .medium))
                        .foregroundStyle(waTextPri)
                        .lineLimit(1)
                    Spacer()
                    Text(timeString)
                        .font(.caption)
                        .foregroundStyle(waTextSec)
                }
                HStack {
                    if item.status == .working {
                        HStack(spacing: 3) {
                            ProgressView()
                                .scaleEffect(0.5)
                                .frame(width: 12, height: 12)
                            statusText
                        }
                    } else {
                        previewText
                    }
                    Spacer()
                    badges
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(itemColor.opacity(0.03))
    }

    private var previewText: some View {
        Group {
            if let last = item.messages.last(where: { $0.kind != .statusCard }) {
                if last.isAgent {
                    Text("Mira: \(cleanPreview(last))")
                } else {
                    Text(cleanPreview(last))
                }
            }
        }
        .font(.subheadline)
        .foregroundStyle(waTextSec)
        .lineLimit(1)
    }

    private var statusText: some View {
        Group {
            if let last = item.messages.last, last.kind == .statusCard,
               let card = last.statusCard {
                Text(card.text)
            } else {
                Text("Working...")
            }
        }
        .font(.subheadline)
        .foregroundStyle(.blue)
        .lineLimit(1)
    }

    @ViewBuilder
    private var badges: some View {
        HStack(spacing: 4) {
            if item.pinned {
                Image(systemName: "pin.fill")
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
            }
            if item.status == .failed {
                Image(systemName: "exclamationmark.circle.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
            }
            if item.status == .needsInput {
                Text("!")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 18, height: 18)
                    .background(.orange)
                    .clipShape(Circle())
            }
        }
    }

    private func cleanPreview(_ msg: ItemMessage) -> String {
        if msg.kind == .statusCard { return "" }
        return String(msg.content.prefix(100))
    }

    // Type-specific color for the left accent
    private var itemColor: Color {
        if item.id.contains("explore") { return colorExplore }
        if item.id.contains("mira") { return colorMira }
        if item.id.contains("zhesi") { return colorZhesi }
        if item.id.contains("tetra") { return colorTetra }
        if item.type == .request { return colorRequest }
        if item.type == .discussion { return colorDiscuss }
        return waTextSec
    }

    private var avatarIcon: String {
        switch item.type {
        case .request:
            if item.status == .done { return "checkmark" }
            if item.status == .failed { return "xmark" }
            return "arrow.up.circle"
        case .discussion: return "bubble.left.and.bubble.right"
        case .feed:
            if item.id.contains("explore") { return "globe" }
            if item.id.contains("zhesi") { return "sparkles" }
            if item.id.contains("tetra") { return "chart.line.uptrend.xyaxis" }
            if item.id.contains("mira") { return "brain.head.profile" }
            if item.id.contains("reflect") || item.id.contains("eval") { return "gauge.with.dots.needle.bottom.50percent" }
            return "doc.text"
        }
    }

    private var timeString: String {
        let s = Date().timeIntervalSince(item.date)
        if s < 60 { return "now" }
        if s < 3600 { return "\(Int(s / 60))m ago" }
        if s < 86400 {
            let f = DateFormatter()
            f.dateFormat = "HH:mm"
            return f.string(from: item.date)
        }
        if s < 172800 { return "Yesterday" }
        let f = DateFormatter()
        f.dateFormat = "MMM d"
        return f.string(from: item.date)
    }
}

// MARK: - Shared Item Row (used elsewhere)

struct ItemRow: View {
    let item: MiraItem
    var body: some View {
        ChatListRow(item: item)
    }
}
