import Foundation
import Observation

/// Reads/writes todos.json directly via iCloud file I/O.
@Observable
final class TodoStore {
    let config: BridgeConfig
    private(set) var todos: [MiraTodo] = []

    init(config: BridgeConfig) {
        self.config = config
    }

    // MARK: - Read

    func refresh() {
        guard let url = config.todosURL else { return }
        try? FileManager.default.startDownloadingUbiquitousItem(at: url)
        guard let data = try? Data(contentsOf: url) else { return }
        let decoded = (try? JSONDecoder().decode([MiraTodo].self, from: data)) ?? []
        todos = decoded
    }

    // MARK: - Write

    func add(title: String, priority: String = "medium") {
        var t = todos
        let now = Self.now()
        let new = MiraTodo(id: "todo_\(UUID().uuidString.prefix(8).lowercased())",
                           title: title, priority: priority, status: "pending",
                           tags: [], createdAt: now, updatedAt: now, followups: [])
        t.append(new)
        save(t)
    }

    func updatePriority(_ id: String, to priority: String) {
        var t = todos
        guard let idx = t.firstIndex(where: { $0.id == id }) else { return }
        t[idx].priority = priority
        t[idx].updatedAt = Self.now()
        save(t)
    }

    func complete(_ id: String) {
        var t = todos
        guard let idx = t.firstIndex(where: { $0.id == id }) else { return }
        t[idx].status = "done"
        t[idx].updatedAt = Self.now()
        save(t)
    }

    func addFollowup(_ id: String, content: String, source: String = "user") {
        var t = todos
        guard let idx = t.firstIndex(where: { $0.id == id }) else { return }
        t[idx].followups.append(TodoFollowup(content: content, source: source, timestamp: Self.now()))
        t[idx].updatedAt = Self.now()
        save(t)
    }

    func remove(_ id: String) {
        save(todos.filter { $0.id != id })
    }

    // MARK: - Computed

    var pending: [MiraTodo] {
        todos.filter { $0.status == "pending" }.sorted { $0.priorityOrder < $1.priorityOrder }
    }

    var working: [MiraTodo] {
        todos.filter { $0.status == "working" }.sorted { $0.priorityOrder < $1.priorityOrder }
    }

    var done: [MiraTodo] {
        todos.filter { $0.status == "done" }.sorted { $0.date > $1.date }
    }

    // MARK: - Internal

    private func save(_ todos: [MiraTodo]) {
        self.todos = todos
        guard let url = config.todosURL else { return }
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(todos)
            try data.write(to: url, options: .atomic)
        } catch { }
    }

    private static func now() -> String {
        ISO8601DateFormatter.shared.string(from: Date())
    }
}

