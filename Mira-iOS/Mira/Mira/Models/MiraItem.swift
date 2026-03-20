import Foundation

// MARK: - Core Model

/// Unified content type for all Mira bridge communication.
/// Replaces TBMessage, TBThread, and MiraTask.
struct MiraItem: Codable, Identifiable, Equatable {
    let id: String
    var type: ItemType
    var title: String
    var status: ItemStatus
    var tags: [String]
    var origin: ItemOrigin
    var pinned: Bool
    var quick: Bool
    var parentId: String?
    var createdAt: String
    var updatedAt: String
    var messages: [ItemMessage]
    var error: ItemError?
    var resultPath: String?

    enum CodingKeys: String, CodingKey {
        case id, type, title, status, tags, origin, pinned, quick
        case parentId = "parent_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case messages, error
        case resultPath = "result_path"
    }
}

enum ItemType: String, Codable {
    case request, discussion, feed
}

enum ItemStatus: String, Codable {
    case queued, working
    case needsInput = "needs-input"
    case done, failed, archived
}

enum ItemOrigin: String, Codable {
    case user, agent
    // Decode unknown origins as agent
    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = ItemOrigin(rawValue: value) ?? .agent
    }
}

// MARK: - Messages

struct ItemMessage: Codable, Identifiable, Equatable {
    let id: String
    let sender: String
    let content: String
    let timestamp: String
    var kind: MessageKind

    var date: Date {
        ISO8601DateFormatter.shared.date(from: timestamp) ?? .distantPast
    }

    var isAgent: Bool { sender == "agent" }
    var isUser: Bool { !isAgent }

    /// Parse status card content if kind is statusCard
    var statusCard: StatusCard? {
        guard kind == .statusCard else { return nil }
        guard let data = content.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = dict["text"] as? String else { return nil }
        return StatusCard(text: text, icon: dict["icon"] as? String ?? "gear")
    }
}

enum MessageKind: String, Codable {
    case text
    case statusCard = "status_card"
    case error
    case recall

    // Decode unknown kinds as text
    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = MessageKind(rawValue: value) ?? .text
    }
}

struct StatusCard {
    let text: String
    let icon: String
}

// MARK: - Error

struct ItemError: Codable, Equatable {
    let code: String
    let message: String
    let retryable: Bool
    let timestamp: String
}

// MARK: - Heartbeat

struct MiraHeartbeat: Codable {
    let timestamp: String
    let status: String
    var busy: Bool?
    var activeCount: Int?

    enum CodingKeys: String, CodingKey {
        case timestamp, status, busy
        case activeCount = "active_count"
    }

    var date: Date {
        ISO8601DateFormatter.shared.date(from: timestamp) ?? .distantPast
    }

    var isRecent: Bool {
        Date().timeIntervalSince(date) < 180
    }

    var isBusy: Bool { busy ?? false }
}

// MARK: - Manifest

struct MiraManifest: Codable {
    let updatedAt: String
    let items: [ManifestEntry]

    enum CodingKeys: String, CodingKey {
        case updatedAt = "updated_at"
        case items
    }
}

struct ManifestEntry: Codable {
    let id: String
    let type: String
    let status: String
    let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id, type, status
        case updatedAt = "updated_at"
    }
}

// MARK: - Commands (iOS → Agent)

struct MiraCommand: Codable {
    let id: String
    let type: String
    let timestamp: String
    var sender: String
    var title: String?
    var content: String?
    var itemId: String?
    var parentId: String?
    var tags: [String]?
    var quick: Bool?
    var pinned: Bool?
    var query: String?

    enum CodingKeys: String, CodingKey {
        case id, type, timestamp, sender, title, content
        case itemId = "item_id"
        case parentId = "parent_id"
        case tags, quick, pinned, query
    }
}

// MARK: - Computed Properties

extension MiraItem {
    var date: Date {
        ISO8601DateFormatter.shared.date(from: updatedAt) ?? .distantPast
    }

    var createdDate: Date {
        ISO8601DateFormatter.shared.date(from: createdAt) ?? .distantPast
    }

    var isActive: Bool {
        [.queued, .working, .needsInput].contains(status)
    }

    var needsAttention: Bool {
        status == .needsInput
    }

    var lastMessage: ItemMessage? {
        messages.last
    }

    var lastMessagePreview: String {
        guard let msg = lastMessage else { return "" }
        if msg.kind == .statusCard, let card = msg.statusCard {
            return card.text
        }
        return String(msg.content.prefix(100))
    }

    var statusIcon: String {
        switch status {
        case .queued: return "clock"
        case .working: return "circle.dotted.circle"
        case .needsInput: return "exclamationmark.bubble"
        case .done: return "checkmark.circle"
        case .failed: return "xmark.circle"
        case .archived: return "archivebox"
        }
    }

    var statusColor: String {
        switch status {
        case .queued: return "secondary"
        case .working: return "blue"
        case .needsInput: return "orange"
        case .done: return "green"
        case .failed: return "red"
        case .archived: return "secondary"
        }
    }

    var typeIcon: String {
        switch type {
        case .request: return "arrow.up.circle"
        case .discussion: return "bubble.left.and.bubble.right"
        case .feed: return "doc.text"
        }
    }
}

// MARK: - Todo

struct MiraTodo: Codable, Identifiable, Equatable {
    let id: String
    var title: String
    var priority: TodoPriority
    var status: TodoStatus
    var createdAt: String
    var updatedAt: String
    var response: String?       // agent's response when done

    enum CodingKeys: String, CodingKey {
        case id, title, priority, status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case response
    }

    var date: Date {
        ISO8601DateFormatter.shared.date(from: updatedAt) ?? .distantPast
    }
}

enum TodoPriority: String, Codable, CaseIterable {
    case high, medium, low
}

enum TodoStatus: String, Codable {
    case pending, working, done
}

// MARK: - Profile

struct MiraProfile: Codable, Identifiable, Hashable {
    let id: String
    let displayName: String
    let agentName: String
    var avatar: String

    enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case agentName = "agent_name"
        case avatar
    }
}

struct MiraProfiles: Codable {
    let profiles: [MiraProfile]
}

// MARK: - ISO8601 Shared Formatter

extension ISO8601DateFormatter {
    static let shared: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}
