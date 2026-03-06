import Foundation

/// A conversation thread in Mira.
struct TBThread: Identifiable, Codable, Equatable {
    let id: String
    var title: String
    let createdAt: String
    var lastActive: String
    var archived: Bool

    enum CodingKeys: String, CodingKey {
        case id, title, archived
        case createdAt = "created_at"
        case lastActive = "last_active"
    }

    init(id: String, title: String, createdAt: String, lastActive: String, archived: Bool = false) {
        self.id = id
        self.title = title
        self.createdAt = createdAt
        self.lastActive = lastActive
        self.archived = archived
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        lastActive = try c.decodeIfPresent(String.self, forKey: .lastActive) ?? ""
        archived = try c.decodeIfPresent(Bool.self, forKey: .archived) ?? false
    }

    var lastActiveDate: Date {
        ISO8601DateFormatter().date(from: lastActive) ?? .distantPast
    }

    /// Create a new thread locally
    static func new(title: String) -> TBThread {
        TBThread(
            id: UUID().uuidString.prefix(8).lowercased().description,
            title: title,
            createdAt: ISO8601DateFormatter().string(from: Date()),
            lastActive: ISO8601DateFormatter().string(from: Date())
        )
    }
}
