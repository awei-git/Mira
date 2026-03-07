import SwiftUI

struct TaskDetailView: View {
    var bridge: BridgeService
    let taskId: String
    @State private var replyText = ""

    private var task: MiraTask? {
        bridge.tasks.first { $0.id == taskId }
    }

    var body: some View {
        if let task = task {
            VStack(spacing: 0) {
                // Messages
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            // Task header
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Image(systemName: task.statusIcon)
                                        .foregroundStyle(colorForStatus(task.statusColor))
                                    Text(task.status)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    Text(task.createdDate, style: .date)
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                                if !task.tags.isEmpty {
                                    HStack(spacing: 6) {
                                        ForEach(task.tags, id: \.self) { tag in
                                            Text(tag)
                                                .font(.caption2)
                                                .padding(.horizontal, 6)
                                                .padding(.vertical, 2)
                                                .background(.quaternary, in: Capsule())
                                        }
                                    }
                                }
                            }
                            .padding()

                            // Messages
                            ForEach(Array(task.messages.enumerated()), id: \.offset) { idx, msg in
                                TaskMessageBubble(message: msg)
                                    .id(idx)
                            }
                        }
                        .padding(.bottom, 8)
                    }
                    .onChange(of: task.messages.count) {
                        withAnimation {
                            proxy.scrollTo(task.messages.count - 1, anchor: .bottom)
                        }
                    }
                }

                Divider()

                // Retry button for failed tasks
                if task.status == "failed" {
                    Button {
                        let original = task.messages.first?.content ?? task.title
                        bridge.sendTaskMessage(taskId, content: original)
                    } label: {
                        Label("重试", systemImage: "arrow.counterclockwise")
                            .font(.subheadline.bold())
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.blue)
                    .padding(.horizontal, 12)
                    .padding(.top, 8)
                }

                // Reply input
                HStack(spacing: 8) {
                    TextField("回复...", text: $replyText, axis: .vertical)
                        .textFieldStyle(.plain)
                        .lineLimit(1...5)
                        .padding(10)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 20))

                    Button {
                        guard !replyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                        bridge.sendTaskMessage(taskId, content: replyText)
                        replyText = ""
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .disabled(replyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
            }
            .navigationTitle(task.title)
            .navigationBarTitleDisplayMode(.inline)
        } else {
            ContentUnavailableView("任务未找到", systemImage: "questionmark.circle")
        }
    }

    private func colorForStatus(_ name: String) -> Color {
        switch name {
        case "blue": return .blue
        case "orange": return .orange
        case "green": return .green
        case "red": return .red
        default: return .gray
        }
    }
}

struct TaskMessageBubble: View {
    let message: TaskMessage
    @State private var expanded = false

    private var isAgent: Bool { message.isFromAgent }
    private var isLong: Bool { message.content.count > 300 }

    var body: some View {
        HStack {
            if !isAgent { Spacer(minLength: 40) }

            VStack(alignment: isAgent ? .leading : .trailing, spacing: 4) {
                if isAgent {
                    Text("Mira")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                let displayText = isLong && !expanded
                    ? String(message.content.prefix(300)) + "..."
                    : message.content

                Group {
                    if let rich = try? AttributedString(markdown: displayText, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                        Text(rich)
                    } else {
                        Text(displayText)
                    }
                }
                    .font(.body)
                    .tint(.blue)
                    .textSelection(.enabled)
                    .padding(12)
                    .background(
                        isAgent ? Color(.systemGray6) : Color.blue.opacity(0.15),
                        in: RoundedRectangle(cornerRadius: 16)
                    )

                HStack {
                    if isLong {
                        Button(expanded ? "收起" : "展开") {
                            expanded.toggle()
                        }
                        .font(.caption2)
                    }
                    Text(formatTime(message.date))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }

            if isAgent { Spacer(minLength: 40) }
        }
        .padding(.horizontal)
    }

    private func formatTime(_ date: Date) -> String {
        let f = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            f.dateFormat = "HH:mm"
        } else {
            f.dateFormat = "MM/dd HH:mm"
        }
        return f.string(from: date)
    }
}
