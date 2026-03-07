import Foundation

/// A task in the Mira system — the core unit of interaction.
/// Each task has a status, messages (conversation), and optional tags.
struct MiraTask: Codable, Identifiable {
    let id: String
    var title: String
    var status: String          // queued, working, needs-input, done, failed
    var tags: [String]
    var origin: String          // "user" or "auto"
    var createdAt: String
    var updatedAt: String
    var messages: [TaskMessage]
    var resultPath: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status, tags, origin, messages
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case resultPath = "result_path"
    }

    var createdDate: Date {
        ISO8601DateFormatter().date(from: createdAt) ?? .distantPast
    }

    var updatedDate: Date {
        ISO8601DateFormatter().date(from: updatedAt) ?? .distantPast
    }

    var isActive: Bool {
        ["queued", "working", "needs-input"].contains(status)
    }

    var needsInput: Bool {
        status == "needs-input"
    }

    var isAuto: Bool {
        origin == "auto"
    }

    var isBriefing: Bool {
        tags.contains("briefing")
    }

    var isJournal: Bool {
        tags.contains("journal")
    }

    var statusIcon: String {
        switch status {
        case "queued": return "clock"
        case "working": return "arrow.triangle.2.circlepath"
        case "needs-input": return "exclamationmark.bubble"
        case "done": return "checkmark.circle"
        case "failed": return "xmark.circle"
        default: return "questionmark.circle"
        }
    }

    var statusColor: String {
        switch status {
        case "queued": return "gray"
        case "working": return "blue"
        case "needs-input": return "orange"
        case "done": return "green"
        case "failed": return "red"
        default: return "gray"
        }
    }

    /// Last message content (for preview)
    var lastMessage: String {
        messages.last?.content ?? ""
    }

    /// Create a new user task locally
    static func new(title: String, content: String, sender: String) -> MiraTask {
        let id = UUID().uuidString.prefix(8).lowercased()
        let now = ISO8601DateFormatter().string(from: Date())
        return MiraTask(
            id: "task_\(id)",
            title: title,
            status: "queued",
            tags: [],
            origin: "user",
            createdAt: now,
            updatedAt: now,
            messages: [
                TaskMessage(sender: sender, content: content, timestamp: now)
            ],
            resultPath: nil
        )
    }
}

struct TaskMessage: Codable, Identifiable {
    let sender: String
    let content: String
    let timestamp: String

    var id: String { "\(sender)_\(timestamp)" }

    var isFromAgent: Bool { sender == "agent" }

    var date: Date {
        ISO8601DateFormatter().date(from: timestamp) ?? .distantPast
    }
}
