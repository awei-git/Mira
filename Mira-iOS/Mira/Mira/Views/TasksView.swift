import SwiftUI

struct TasksView: View {
    var bridge: BridgeService
    @State private var showNewTask = false
    @State private var newTaskTitle = ""
    @State private var newTaskContent = ""
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
                        "没有任务",
                        systemImage: "tray",
                        description: Text("点 + 创建新任务")
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
            .navigationTitle("Tasks")
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
            .alert("新任务", isPresented: $showNewTask) {
                TextField("标题", text: $newTaskTitle)
                TextField("描述", text: $newTaskContent)
                Button("发送") {
                    let title = newTaskTitle.isEmpty ? newTaskContent.prefix(30).description : newTaskTitle
                    let content = newTaskContent.isEmpty ? newTaskTitle : newTaskContent
                    guard !content.isEmpty else { return }
                    bridge.createTask(title: title, content: content)
                    newTaskTitle = ""
                    newTaskContent = ""
                }
                Button("取消", role: .cancel) {
                    newTaskTitle = ""
                    newTaskContent = ""
                }
            }
        }
    }
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
