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

    var body: some View {
        NavigationStack {
            ZStack(alignment: .bottomTrailing) {
                warmBg.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 0) {
                        // Search / Recall bar
                        Button { showRecall = true } label: {
                            HStack {
                                Image(systemName: "magnifyingglass")
                                    .foregroundStyle(.secondary)
                                Text("Search conversations...")
                                    .foregroundStyle(.secondary)
                                Spacer()
                            }
                            .font(.subheadline)
                            .padding(10)
                            .background(cardBg)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
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

                        // Main list
                        VStack(spacing: 0) {
                            // Active items first
                            ForEach(store.activeRequests) { item in
                                NavigationLink(value: item.id) {
                                    ChatListRow(item: item)
                                }
                                .buttonStyle(.plain)
                                divider
                            }

                            // Discussions
                            ForEach(store.discussions) { item in
                                NavigationLink(value: item.id) {
                                    ChatListRow(item: item)
                                }
                                .buttonStyle(.plain)
                                divider
                            }

                            // Feeds
                            ForEach(store.todayFeeds) { item in
                                NavigationLink(value: item.id) {
                                    ChatListRow(item: item)
                                }
                                .buttonStyle(.plain)
                                divider
                            }

                            // Done
                            ForEach(store.doneItems.prefix(10)) { item in
                                NavigationLink(value: item.id) {
                                    ChatListRow(item: item)
                                }
                                .buttonStyle(.plain)
                                divider
                            }
                        }
                        .background(cardBg)

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
            .alert("Recall", isPresented: $showRecall) {
                TextField("What do you want to recall?", text: $recallQuery)
                Button("Search") {
                    if !recallQuery.isEmpty {
                        commands.recall(query: recallQuery)
                        recallQuery = ""
                    }
                }
                Button("Cancel", role: .cancel) { }
            }
            .refreshable { sync.refresh() }
        }
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

    private var divider: some View {
        Divider().padding(.leading, 76)
    }
}

// MARK: - Attention Banner (like WeChat pinned/unread highlight)

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

// MARK: - Chat List Row (WeChat style)

struct ChatListRow: View {
    let item: MiraItem

    var body: some View {
        HStack(spacing: 12) {
            // Avatar
            ZStack {
                RoundedRectangle(cornerRadius: 10)
                    .fill(avatarColor.opacity(0.15))
                    .frame(width: 48, height: 48)
                Image(systemName: avatarIcon)
                    .font(.system(size: 20))
                    .foregroundStyle(avatarColor)
            }

            // Content
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.title)
                        .font(.system(size: 16, weight: .regular))
                        .lineLimit(1)
                    Spacer()
                    Text(timeString)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                HStack {
                    // Status indicator for active items
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
        .padding(.vertical, 10)
    }

    private var previewText: some View {
        Group {
            if let last = item.messages.last {
                if last.isAgent {
                    Text("Mira: \(cleanPreview(last))")
                } else {
                    Text(cleanPreview(last))
                }
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
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
        .font(.caption)
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
        return String(msg.content.prefix(80))
    }

    private var avatarIcon: String {
        switch item.type {
        case .request:
            if item.status == .done { return "checkmark" }
            if item.status == .failed { return "xmark" }
            return "arrow.up.circle"
        case .discussion: return "bubble.left.and.bubble.right"
        case .feed:
            if item.tags.contains("briefing") { return "newspaper" }
            if item.tags.contains("reflection") || item.tags.contains("philosophy") { return "sparkles" }
            if item.tags.contains("journal") { return "book" }
            if item.tags.contains("market") { return "chart.line.uptrend.xyaxis" }
            return "doc.text"
        }
    }

    private var avatarColor: Color {
        switch item.type {
        case .request:
            if item.status == .done { return accentGreen }
            if item.status == .failed { return .red }
            return .blue
        case .discussion: return .purple
        case .feed:
            if item.tags.contains("briefing") { return .blue }
            if item.tags.contains("reflection") || item.tags.contains("philosophy") { return .orange }
            return .secondary
        }
    }

    private var timeString: String {
        let s = Date().timeIntervalSince(item.date)
        if s < 60 { return "now" }
        if s < 3600 { return "\(Int(s / 60))m ago" }
        if s < 86400 {
            let f = DateFormatter()
            f.dateFormat = "h:mm a"
            return f.string(from: item.date)
        }
        if s < 172800 { return "Yesterday" }
        let f = DateFormatter()
        f.dateFormat = "MMM d"
        return f.string(from: item.date)
    }
}

// MARK: - Shared Item Row (used in ThreadsView)

struct ItemRow: View {
    let item: MiraItem
    var body: some View {
        ChatListRow(item: item)
    }
}
