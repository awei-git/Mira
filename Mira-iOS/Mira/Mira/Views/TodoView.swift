import SwiftUI

struct TodoView: View {
    @Environment(TodoStore.self) private var store
    @State private var newText = ""
    @State private var selectedId: String?
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            ZStack(alignment: .bottomTrailing) {
                Color(hex: 0x111B21).ignoresSafeArea()
                    .onTapGesture { inputFocused = false }

                VStack(spacing: 0) {
                    // Input bar
                    HStack(spacing: 8) {
                        TextField("Add idea... (high: for urgent)", text: $newText)
                            .textFieldStyle(.plain)
                            .focused($inputFocused)
                            .padding(10)
                            .background(Color(hex: 0x1F2C34))
                            .clipShape(RoundedRectangle(cornerRadius: 18))
                            .onSubmit { addTodo() }
                        Button { addTodo() } label: {
                            Image(systemName: "plus.circle.fill")
                                .font(.title2)
                                .foregroundStyle(Color(hex: 0x00A884))
                        }
                        .disabled(newText.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)

                    // List
                    List {
                        if !store.pending.isEmpty {
                            Section("Pending (\(store.pending.count))") {
                                ForEach(store.pending) { todo in
                                    TodoRow(todo: todo, selectedId: $selectedId)
                                }
                            }
                        }
                        if !store.working.isEmpty {
                            Section("Working (\(store.working.count))") {
                                ForEach(store.working) { todo in
                                    TodoRow(todo: todo, selectedId: $selectedId)
                                }
                            }
                        }
                        if !store.done.isEmpty {
                            Section("Done") {
                                ForEach(store.done.prefix(10)) { todo in
                                    TodoRow(todo: todo, selectedId: $selectedId)
                                }
                            }
                        }
                    }
                    .listStyle(.plain)
                    .scrollDismissesKeyboard(.interactively)
                }
            }
            .navigationTitle("Todo")
            .sheet(isPresented: Binding(
                get: { selectedId != nil },
                set: { if !$0 { selectedId = nil } }
            )) {
                if let id = selectedId {
                    TodoDetailSheet(todoId: id)
                }
            }
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { inputFocused = false }
                }
            }
            .refreshable { store.refresh() }
            .onAppear { store.refresh() }
        }
    }

    private func addTodo() {
        let t = newText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        newText = ""
        inputFocused = false
        // Parse priority: "high: do something"
        let prefixes = ["high", "medium", "low"]
        var priority = "medium"
        var title = t
        for p in prefixes {
            if t.lowercased().hasPrefix(p + ":") || t.lowercased().hasPrefix(p + " ") {
                priority = p
                title = String(t.dropFirst(p.count + 1)).trimmingCharacters(in: .whitespaces)
                break
            }
        }
        store.add(title: title, priority: priority)
    }
}

// MARK: - Todo Row

struct TodoRow: View {
    let todo: MiraTodo
    @Binding var selectedId: String?
    @Environment(TodoStore.self) private var store

    var body: some View {
        Button { selectedId = todo.id } label: {
            HStack(spacing: 10) {
                Text(priorityIcon)
                    .font(.system(size: 16))

                VStack(alignment: .leading, spacing: 3) {
                    Text(todo.title)
                        .font(.subheadline.weight(.medium))
                        .lineLimit(2)
                        .strikethrough(todo.status == "done")
                        .foregroundStyle(todo.status == "done" ? .secondary : .primary)

                    HStack(spacing: 6) {
                        Text(todo.priority)
                            .font(.caption2)
                            .foregroundStyle(priorityColor)
                        if !todo.followups.isEmpty {
                            Text("\(todo.followups.count) 💬")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Text(relativeTime)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }

                Spacer()

                if todo.status != "done" {
                    Button {
                        store.complete(todo.id)
                    } label: {
                        Image(systemName: "checkmark.circle")
                            .foregroundStyle(Color(hex: 0x00A884))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                store.remove(todo.id)
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
        .swipeActions(edge: .leading) {
            Button {
                let next = todo.priority == "high" ? "medium" : todo.priority == "medium" ? "low" : "high"
                store.updatePriority(todo.id, to: next)
            } label: {
                Label("Priority", systemImage: "arrow.up.arrow.down")
            }
            .tint(.orange)
        }
    }

    private var priorityIcon: String {
        switch todo.priority {
        case "high": return "🔴"
        case "low": return "🟢"
        default: return "🟡"
        }
    }

    private var priorityColor: Color {
        switch todo.priority {
        case "high": return .red
        case "low": return .green
        default: return .yellow
        }
    }

    private var relativeTime: String {
        let s = Date().timeIntervalSince(todo.date)
        if s < 60 { return "now" }
        if s < 3600 { return "\(Int(s / 60))m" }
        if s < 86400 { return "\(Int(s / 3600))h" }
        return "\(Int(s / 86400))d"
    }
}

// MARK: - Todo Detail Sheet

struct TodoDetailSheet: View {
    let todoId: String
    @Environment(TodoStore.self) private var store
    @Environment(CommandWriter.self) private var commands
    @Environment(\.dismiss) private var dismiss
    @State private var replyText = ""

    private var todo: MiraTodo? { store.todos.first { $0.id == todoId } }

    var body: some View {
        NavigationStack {
            if let todo {
                VStack(spacing: 0) {
                    // Info header
                    HStack {
                        Text(todo.priority.uppercased())
                            .font(.caption.weight(.bold))
                            .foregroundStyle(priorityColor(todo.priority))
                        Text("·")
                            .foregroundStyle(.secondary)
                        Text(todo.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(todo.date, style: .relative)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)

                    // Chat-style followups
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            // Original todo as first message
                            HStack {
                                Spacer(minLength: 50)
                                VStack(alignment: .trailing, spacing: 2) {
                                    Text(todo.title)
                                        .font(.body)
                                        .foregroundStyle(Color(hex: 0xE9EDEF))
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 8)
                                }
                                .background(Color(hex: 0x005C4B))
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                            }

                            // Followups
                            ForEach(Array(todo.followups.enumerated()), id: \.offset) { _, fu in
                                HStack {
                                    if fu.source != "agent" { Spacer(minLength: 50) }
                                    VStack(alignment: fu.source == "agent" ? .leading : .trailing, spacing: 2) {
                                        Text(mdAttributed(fu.content))
                                            .font(.body)
                                            .foregroundStyle(Color(hex: 0xE9EDEF))
                                            .tint(Color(hex: 0x53BDEB))
                                            .padding(.horizontal, 12)
                                            .padding(.vertical, 8)
                                        HStack(spacing: 4) {
                                            if fu.source == "agent" {
                                                Text("Mira")
                                                    .font(.system(size: 11))
                                                    .foregroundStyle(Color(hex: 0x00A884))
                                            }
                                            Text(fu.date, style: .time)
                                                .font(.system(size: 11))
                                                .foregroundStyle(.secondary)
                                        }
                                        .padding(.horizontal, 12)
                                        .padding(.bottom, 4)
                                    }
                                    .background(fu.source == "agent" ? Color(hex: 0x202C33) : Color(hex: 0x005C4B))
                                    .clipShape(RoundedRectangle(cornerRadius: 12))
                                    if fu.source == "agent" { Spacer(minLength: 50) }
                                }
                            }
                        }
                        .padding()
                    }

                    // Reply bar
                    if todo.status != "done" {
                        HStack(spacing: 8) {
                            TextField("Add followup...", text: $replyText, axis: .vertical)
                                .textFieldStyle(.plain)
                                .lineLimit(1...5)
                                .padding(10)
                                .background(.ultraThinMaterial)
                                .clipShape(RoundedRectangle(cornerRadius: 18))

                            Button {
                                let text = replyText.trimmingCharacters(in: .whitespacesAndNewlines)
                                guard !text.isEmpty else { return }
                                store.addFollowup(todoId, content: text)
                                commands.todoFollowup(todoId: todoId, content: text)
                                replyText = ""
                            } label: {
                                Image(systemName: "arrow.up.circle.fill")
                                    .font(.title2)
                                    .foregroundStyle(replyText.isEmpty ? Color(hex: 0x8696A0) : Color(hex: 0x00A884))
                            }
                            .disabled(replyText.isEmpty)
                        }
                        .padding(.horizontal)
                        .padding(.vertical, 8)
                    }
                }
                .navigationTitle(todo.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Close") { dismiss() }
                    }
                    if todo.status != "done" {
                        ToolbarItem(placement: .primaryAction) {
                            Button { store.complete(todoId); dismiss() } label: {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(Color(hex: 0x00A884))
                            }
                        }
                    }
                }
            }
        }
    }

    private func priorityColor(_ p: String) -> Color {
        switch p {
        case "high": return .red
        case "low": return .green
        default: return .yellow
        }
    }

    private func mdAttributed(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s,
                               options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(s)
    }
}
