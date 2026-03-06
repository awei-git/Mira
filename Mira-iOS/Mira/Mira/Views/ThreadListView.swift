import SwiftUI

struct ThreadListView: View {
    @Bindable var bridge: BridgeService
    @Environment(\.dismiss) private var dismiss
    @State private var showNewThread = false
    @State private var newThreadTitle = ""

    var body: some View {
        NavigationStack {
            List {
                // "All messages" option
                Button {
                    bridge.currentThreadId = ""
                    dismiss()
                } label: {
                    HStack {
                        Image(systemName: "bubble.left.and.bubble.right")
                            .font(.system(size: 14))
                            .foregroundStyle(.blue)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("全部消息")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(.primary)
                            Text("\(bridge.messages.count) 条消息")
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if bridge.currentThreadId.isEmpty {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12))
                                .foregroundStyle(.blue)
                        }
                    }
                }

                // Active threads
                Section("对话") {
                    ForEach(activeThreads) { thread in
                        Button {
                            bridge.currentThreadId = thread.id
                            dismiss()
                        } label: {
                            threadRow(thread)
                        }
                        .swipeActions(edge: .trailing) {
                            Button("归档") {
                                bridge.archiveThread(thread.id)
                            }
                            .tint(.orange)
                        }
                    }
                }

                // Archived threads
                if !archivedThreads.isEmpty {
                    Section("已归档") {
                        ForEach(archivedThreads) { thread in
                            threadRow(thread)
                                .opacity(0.6)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("对话列表")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("完成") { dismiss() }
                        .font(.system(size: 14))
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showNewThread = true
                    } label: {
                        Image(systemName: "plus.circle")
                            .font(.system(size: 16))
                    }
                }
            }
            .alert("新对话", isPresented: $showNewThread) {
                TextField("对话标题", text: $newThreadTitle)
                Button("创建") {
                    createThread()
                }
                Button("取消", role: .cancel) {
                    newThreadTitle = ""
                }
            } message: {
                Text("输入新对话的标题")
            }
        }
    }

    private var activeThreads: [TBThread] {
        bridge.threads
            .filter { !$0.archived }
            .sorted { $0.lastActiveDate > $1.lastActiveDate }
    }

    private var archivedThreads: [TBThread] {
        bridge.threads.filter { $0.archived }
    }

    @ViewBuilder
    private func threadRow(_ thread: TBThread) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(thread.title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                // Last message preview
                if let lastMsg = bridge.messages.last(where: { $0.threadId == thread.id }) {
                    Text(lastMsg.content)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(relativeTime(thread.lastActiveDate))
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)

                // Message count badge
                let count = bridge.messages.filter { $0.threadId == thread.id }.count
                if count > 0 {
                    Text("\(count)")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Color(.systemGray5))
                        .clipShape(Capsule())
                }
            }
            if bridge.currentThreadId == thread.id {
                Image(systemName: "checkmark")
                    .font(.system(size: 12))
                    .foregroundStyle(.blue)
            }
        }
    }

    private func createThread() {
        let title = newThreadTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        bridge.createThread(title: title)
        newThreadTitle = ""
    }

    private func relativeTime(_ date: Date) -> String {
        let interval = Date().timeIntervalSince(date)
        if interval < 60 { return "刚刚" }
        if interval < 3600 { return "\(Int(interval / 60))分钟前" }
        if interval < 86400 { return "\(Int(interval / 3600))小时前" }
        return "\(Int(interval / 86400))天前"
    }
}
