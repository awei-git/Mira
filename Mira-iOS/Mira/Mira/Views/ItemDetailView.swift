import SwiftUI

struct ItemDetailView: View {
    let itemId: String
    @Environment(ItemStore.self) private var store
    @Environment(CommandWriter.self) private var commands
    @Environment(NotificationManager.self) private var notifications
    @State private var replyText = ""
    @State private var showTagEditor = false
    @FocusState private var inputFocused: Bool

    private var item: MiraItem? { store.item(for: itemId) }

    var body: some View {
        if let item = item {
            VStack(spacing: 0) {
                // Messages
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(item.messages) { msg in
                                MessageBubble(message: msg)
                                    .id(msg.id)
                            }
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                    }
                    .background(Color(hex: 0x0B141A)) // WhatsApp dark chat bg
                    .onChange(of: item.messages.count) { _, _ in
                        if let last = item.messages.last {
                            withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }

                // Error banner
                if let error = item.error {
                    ErrorBanner(error: error) {
                        commands.reply(to: itemId, content: "/retry")
                    }
                }

                // Reply input
                if item.status != .archived {
                    replyBar(item: item)
                }
            }
            .navigationTitle(item.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    Menu {
                        Button { commands.pin(itemId: itemId, pinned: !item.pinned) } label: {
                            Label(item.pinned ? "Unpin" : "Pin",
                                  systemImage: item.pinned ? "pin.slash" : "pin")
                        }
                        Button { showTagEditor = true } label: {
                            Label("Tags", systemImage: "tag")
                        }
                        Divider()
                        Button(role: .destructive) {
                            commands.archive(itemId: itemId)
                        } label: {
                            Label("Archive", systemImage: "archivebox")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .onAppear { notifications.markAsRead(itemId) }
        } else {
            ContentUnavailableView("Item not found",
                                   systemImage: "questionmark.circle",
                                   description: Text("This item may have been archived."))
        }
    }

    @ViewBuilder
    private func replyBar(item: MiraItem) -> some View {
        HStack(spacing: 8) {
            TextField("Reply...", text: $replyText, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .focused($inputFocused)
                .padding(10)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 18))

            Button {
                let text = replyText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return }
                commands.reply(to: itemId, content: text)
                replyText = ""
                inputFocused = false
            } label: {
                Image(systemName: replyText.isEmpty ? "mic.fill" : "arrow.up.circle.fill")
                    .font(.title2)
                    .foregroundStyle(replyText.isEmpty ? Color(hex: 0x8696A0) : Color(hex: 0x00A884))
            }
            .disabled(replyText.isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

// MARK: - Message Bubble

struct MessageBubble: View {
    let message: ItemMessage

    var body: some View {
        switch message.kind {
        case .statusCard:
            statusCardView
        case .error:
            errorView
        case .text, .recall:
            textBubble
        }
    }

    // WhatsApp dark mode bubble colors
    private static let userBubbleColor = Color(hex: 0x005C4B)   // outgoing dark teal
    private static let agentBubbleColor = Color(hex: 0x202C33)  // incoming dark gray

    private var textBubble: some View {
        HStack(alignment: .bottom) {
            if message.isUser { Spacer(minLength: 50) }
            VStack(alignment: message.isUser ? .trailing : .leading, spacing: 2) {
                Text(markdownContent)
                    .font(.body)
                    .foregroundStyle(Color(hex: 0xE9EDEF))
                    .tint(Color(hex: 0x53BDEB))
                    .padding(.horizontal, 12)
                    .padding(.top, 8)
                    .padding(.bottom, 4)
                HStack(spacing: 4) {
                    Text(timeString)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    if message.isUser {
                        Image(systemName: "checkmark")
                            .font(.system(size: 9))
                            .foregroundStyle(Color(hex: 0x53BDEB)) // blue tick
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 6)
            }
            .background(message.isUser ? Self.userBubbleColor : Self.agentBubbleColor)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.04), radius: 1, y: 1)
            if message.isAgent { Spacer(minLength: 50) }
        }
    }

    private var markdownContent: AttributedString {
        (try? AttributedString(markdown: message.content,
                               options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(message.content)
    }

    private var statusCardView: some View {
        HStack(spacing: 6) {
            if let card = message.statusCard {
                Image(systemName: card.icon)
                    .font(.caption)
                    .foregroundStyle(.blue)
                Text(card.text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private var errorView: some View {
        HStack {
            Spacer(minLength: 40)
            VStack(alignment: .trailing, spacing: 4) {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                    Text(message.content)
                        .font(.body)
                }
                .padding(12)
                .background(.red.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 16))
                Text(timeString)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            Spacer(minLength: 40)
        }
    }

    private var timeString: String {
        let f = DateFormatter()
        f.dateFormat = "h:mm a"
        return f.string(from: message.date)
    }
}

// MARK: - Error Banner

struct ErrorBanner: View {
    let error: ItemError
    let onRetry: () -> Void

    var body: some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            VStack(alignment: .leading) {
                Text(error.message)
                    .font(.caption)
                Text(error.code)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if error.retryable {
                Button("Retry", action: onRetry)
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .controlSize(.small)
            }
        }
        .padding(10)
        .background(.red.opacity(0.08))
    }
}
