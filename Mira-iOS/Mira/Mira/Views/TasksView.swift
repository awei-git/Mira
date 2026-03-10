import SwiftUI
import UniformTypeIdentifiers

struct TasksView: View {
    var bridge: BridgeService
    @State private var showNewTask = false
    @State private var filter: TaskFilter = .all

    enum TaskFilter: String, CaseIterable {
        case all = "全部"
        case active = "进行中"
        case done = "已完成"
        case auto = "自动"
    }

    var filteredTasks: [MiraTask] {
        switch filter {
        case .all: return bridge.tasks.filter { !$0.isAuto }
        case .active: return bridge.activeTasks.filter { !$0.isAuto }
        case .done: return bridge.doneTasks.filter { !$0.isAuto }
        case .auto: return bridge.tasks.filter(\.isAuto)
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Filter bar
                Picker("Filter", selection: $filter) {
                    ForEach(TaskFilter.allCases, id: \.self) { f in
                        Text(f.rawValue).tag(f)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.vertical, 8)

                if filteredTasks.isEmpty {
                    ContentUnavailableView(
                        "没有对话",
                        systemImage: "bubble.left.and.bubble.right",
                        description: Text("点 + 开始新对话")
                    )
                } else {
                    List {
                        ForEach(filteredTasks) { task in
                            NavigationLink(value: task.id) {
                                TaskRow(task: task)
                            }
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Threads")
            .navigationDestination(for: String.self) { taskId in
                TaskDetailView(bridge: bridge, taskId: taskId)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNewTask = true } label: {
                        Image(systemName: "plus.circle.fill")
                    }
                }
            }
            .refreshable { bridge.refresh() }
            .sheet(isPresented: $showNewTask) {
                NewTaskSheet(bridge: bridge, isPresented: $showNewTask)
            }
        }
    }
}

// MARK: - New Task Sheet (with @@ file picker)

struct NewTaskSheet: View {
    var bridge: BridgeService
    @Binding var isPresented: Bool
    @State private var title = ""
    @State private var content = ""
    @State private var attachedFiles: [TaskAttachedFile] = []
    @State private var showFilePicker = false
    @FocusState private var contentFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                TextField("标题 (可选)", text: $title)
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal)

                // Attached files
                if !attachedFiles.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(attachedFiles) { file in
                                HStack(spacing: 4) {
                                    Image(systemName: "doc")
                                        .font(.caption2)
                                    Text(file.displayName)
                                        .font(.caption)
                                        .lineLimit(1)
                                    Button {
                                        attachedFiles.removeAll { $0.id == file.id }
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(.quaternary, in: Capsule())
                            }
                        }
                        .padding(.horizontal)
                    }
                }

                ZStack(alignment: .topLeading) {
                    if content.isEmpty {
                        Text("描述... (@@附件)")
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 10)
                    }
                    TextEditor(text: $content)
                        .focused($contentFocused)
                        .frame(minHeight: 120)
                        .scrollContentBackground(.hidden)
                        .onChange(of: content) { _, newValue in
                            if let range = newValue.range(of: "@@") {
                                content = newValue.replacingCharacters(in: range, with: "")
                                showFilePicker = true
                            }
                        }
                }
                .padding(.horizontal)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(.quaternary)
                        .padding(.horizontal)
                )

                // Attach button
                HStack {
                    Button {
                        showFilePicker = true
                    } label: {
                        Label("附件", systemImage: "paperclip")
                            .font(.callout)
                    }
                    Spacer()
                }
                .padding(.horizontal)

                Spacer()
            }
            .padding(.top)
            .navigationTitle("新对话")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("取消") {
                        isPresented = false
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("发送") {
                        sendTask()
                    }
                    .bold()
                    .disabled(content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              && attachedFiles.isEmpty)
                }
            }
            .fileImporter(
                isPresented: $showFilePicker,
                allowedContentTypes: [.item, .folder],
                allowsMultipleSelection: true
            ) { result in
                if case .success(let urls) = result {
                    for url in urls {
                        let macPath = iCloudMacPath(for: url)
                        let name = url.lastPathComponent
                        attachedFiles.append(TaskAttachedFile(
                            displayName: name,
                            macPath: macPath
                        ))
                    }
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    contentFocused = true
                }
            }
            .onAppear {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    contentFocused = true
                }
            }
        }
    }

    private func sendTask() {
        var text = content.trimmingCharacters(in: .whitespacesAndNewlines)

        // Append file paths
        if !attachedFiles.isEmpty {
            let filePaths = attachedFiles.map { "@file:\($0.macPath)" }.joined(separator: "\n")
            if text.isEmpty {
                text = filePaths
            } else {
                text += "\n\n" + filePaths
            }
        }

        guard !text.isEmpty else { return }

        let taskTitle = title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? String(text.prefix(30))
            : title.trimmingCharacters(in: .whitespacesAndNewlines)

        bridge.createTask(title: taskTitle, content: text)
        isPresented = false
    }

    private func iCloudMacPath(for url: URL) -> String {
        let path = url.path
        if let range = path.range(of: "com~apple~CloudDocs/") {
            let relative = String(path[range.upperBound...])
            return "~/Library/Mobile Documents/com~apple~CloudDocs/" + relative
        }
        return url.lastPathComponent
    }
}

struct TaskAttachedFile: Identifiable {
    let id = UUID()
    let displayName: String
    let macPath: String
}

struct TaskRow: View {
    let task: MiraTask

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: task.statusIcon)
                .foregroundStyle(colorForStatus(task.statusColor))
                .font(.title3)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(task.title)
                    .font(.body)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    ForEach(task.tags.prefix(3), id: \.self) { tag in
                        Text(tag)
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(.quaternary, in: Capsule())
                    }
                    Spacer()
                    Text(relativeTime(task.updatedDate))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Text(task.lastMessage.prefix(80))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
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
