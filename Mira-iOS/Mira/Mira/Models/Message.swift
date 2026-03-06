import Foundation

/// A message in the Mira protocol (matches Mac-side JSON format).
struct TBMessage: Identifiable, Codable, Equatable {
    let id: String
    let sender: String
    let timestamp: String
    var content: String
    var type: String = "text"
    var threadId: String = ""
    var priority: String = "normal"

    /// For outbox messages (replies from agent)
    var inReplyTo: String?
    var recipient: String?

    enum CodingKeys: String, CodingKey {
        case id, sender, timestamp, content, type, priority
        case threadId = "thread_id"
        case inReplyTo = "in_reply_to"
        case recipient
    }

    init(id: String, sender: String, timestamp: String, content: String,
         type: String = "text", threadId: String = "", priority: String = "normal",
         inReplyTo: String? = nil, recipient: String? = nil) {
        self.id = id; self.sender = sender; self.timestamp = timestamp
        self.content = content; self.type = type; self.threadId = threadId
        self.priority = priority; self.inReplyTo = inReplyTo; self.recipient = recipient
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        sender = try c.decode(String.self, forKey: .sender)
        timestamp = try c.decode(String.self, forKey: .timestamp)
        content = try c.decode(String.self, forKey: .content)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? "text"
        threadId = try c.decodeIfPresent(String.self, forKey: .threadId) ?? ""
        priority = try c.decodeIfPresent(String.self, forKey: .priority) ?? "normal"
        inReplyTo = try c.decodeIfPresent(String.self, forKey: .inReplyTo)
        recipient = try c.decodeIfPresent(String.self, forKey: .recipient)
    }

    var date: Date {
        ISO8601DateFormatter().date(from: timestamp) ?? .distantPast
    }

    var isFromAgent: Bool {
        sender == "agent"
    }

    static func new(content: String, sender: String, threadId: String = "") -> TBMessage {
        TBMessage(
            id: UUID().uuidString.prefix(8).lowercased().description,
            sender: sender,
            timestamp: ISO8601DateFormatter().string(from: Date()),
            content: content,
            threadId: threadId
        )
    }
}

/// Ack status from the Mac agent.
struct TBAck: Codable {
    let messageId: String
    let status: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case messageId = "message_id"
        case status, timestamp
    }
}

/// Agent heartbeat.
struct TBHeartbeat: Codable {
    let timestamp: String
    let status: String
    var busy: Bool?
    var activeCount: Int?
    var activeTasks: [ActiveTask]?
    var lastCompleted: String?

    enum CodingKeys: String, CodingKey {
        case timestamp, status, busy
        case activeCount = "active_count"
        case activeTasks = "active_tasks"
        case lastCompleted = "last_completed"
    }

    struct ActiveTask: Codable {
        let taskId: String
        let preview: String
        let startedAt: String
        var tags: [String]?

        enum CodingKeys: String, CodingKey {
            case taskId = "task_id"
            case preview
            case startedAt = "started_at"
            case tags
        }
    }

    var date: Date {
        ISO8601DateFormatter().date(from: timestamp) ?? .distantPast
    }

    var isRecent: Bool {
        Date().timeIntervalSince(date) < 180  // 3 minutes
    }

    var isBusy: Bool {
        busy ?? false
    }
}
