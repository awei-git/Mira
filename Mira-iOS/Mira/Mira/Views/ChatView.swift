import SwiftUI
import UniformTypeIdentifiers
import QuickLook

struct ChatView: View {
    @Bindable var bridge: BridgeService
    @State private var draft = ""
    @State private var showSettings = false
    @State private var showThreadList = false
    @State private var showArtifacts = false
    @State private var showFilePicker = false
    @State private var attachedFiles: [AttachedFile] = []
    @State private var previewURL: URL?
    @State private var showSearch = false
    @State private var searchText = ""
    @State private var activeSearch = ""
    @State private var scrollProxy: ScrollViewProxy?
    @FocusState private var inputFocused: Bool
    @FocusState private var searchFocused: Bool
    @State private var showOlderMessages = false

    /// Messages filtered by current thread
    private var filteredMessages: [TBMessage] {
        var msgs = bridge.messages
        if !bridge.currentThreadId.isEmpty {
            msgs = msgs.filter { $0.threadId == bridge.currentThreadId }
        }
        if !activeSearch.isEmpty {
            let query = activeSearch.lowercased()
            msgs = msgs.filter { $0.content.lowercased().contains(query) }
        }
        return msgs
    }

    /// Recent messages (today + yesterday); older ones hidden behind "show more"
    private var recentMessages: [TBMessage] {
        if showOlderMessages || !bridge.currentThreadId.isEmpty || !activeSearch.isEmpty {
            return filteredMessages
        }
        let cal = Calendar.current
        return filteredMessages.filter { msg in
            cal.isDateInToday(msg.date) || cal.isDateInYesterday(msg.date)
        }
    }

    private var olderMessageCount: Int {
        if showOlderMessages || !bridge.currentThreadId.isEmpty || !activeSearch.isEmpty {
            return 0
        }
        return filteredMessages.count - recentMessages.count
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Search bar
                if showSearch {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                        TextField("搜索消息...", text: $searchText)
                            .font(.system(size: 13))
                            .textFieldStyle(.plain)
                            .focused($searchFocused)
                            .onSubmit { activeSearch = searchText }
                        if !searchText.isEmpty {
                            Button {
                                searchText = ""
                                activeSearch = ""
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Button {
                            showSearch = false
                            searchText = ""
                            activeSearch = ""
                        } label: {
                            Text("取消").font(.system(size: 13))
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color(.systemGray6))
                }

                // Thread indicator
                if !bridge.currentThreadId.isEmpty,
                   let thread = bridge.threads.first(where: { $0.id == bridge.currentThreadId }) {
                    HStack {
                        Image(systemName: "bubble.left.and.text.bubble.right")
                            .font(.system(size: 10))
                        Text(thread.title)
                            .font(.system(size: 11, weight: .medium))
                        Spacer()
                        Button {
                            bridge.currentThreadId = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(Color(.systemGray6))
                }

                // Messages
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 6) {
                            // "Show older" button
                            if olderMessageCount > 0 {
                                Button {
                                    withAnimation { showOlderMessages = true }
                                } label: {
                                    Text("显示更早的 \(olderMessageCount) 条消息")
                                        .font(.system(size: 12))
                                        .foregroundStyle(.blue)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 8)
                                }
                            }

                            ForEach(recentMessages) { msg in
                                ChatBubble(
                                    message: msg,
                                    ackStatus: bridge.ackStatus(for: msg.id),
                                    onFileTap: { path in
                                        openFile(path)
                                    }
                                )
                                .id(msg.id)
                            }
                        }
                        .padding(.horizontal, 10)
                        .padding(.top, 6)
                    }
                    .onAppear {
                        scrollProxy = proxy
                        scrollToBottom(proxy)
                    }
                    .onChange(of: recentMessages.count) {
                        scrollToBottom(proxy)
                    }
                }

                Divider()

                // Attached files chips
                if !attachedFiles.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(attachedFiles) { file in
                                HStack(spacing: 4) {
                                    Image(systemName: "doc.text")
                                        .font(.system(size: 10))
                                    Text(file.displayName)
                                        .font(.system(size: 11))
                                        .lineLimit(1)
                                    Button {
                                        attachedFiles.removeAll { $0.id == file.id }
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .font(.system(size: 10))
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color(.systemGray5))
                                .clipShape(Capsule())
                            }
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, 4)
                    }
                }

                // Input bar
                HStack(alignment: .bottom, spacing: 6) {
                    ZStack(alignment: .topLeading) {
                        if draft.isEmpty {
                            Text("消息... (@@附件)")
                                .font(.body)
                                .foregroundStyle(.tertiary)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 10)
                        }
                        TextEditor(text: $draft)
                            .font(.body)
                            .frame(minHeight: 36, maxHeight: 160)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .scrollContentBackground(.hidden)
                            .focused($inputFocused)
                            .onChange(of: draft) { _, newValue in
                                if let range = newValue.range(of: "@@") {
                                    draft = newValue.replacingCharacters(in: range, with: "")
                                    showFilePicker = true
                                }
                            }
                    }
                    .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 14))

                    Button {
                        sendMessage()
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 28))
                    }
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              && attachedFiles.isEmpty)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showSearch.toggle()
                        if showSearch {
                            searchFocused = true
                        }
                    } label: {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 14))
                    }
                }
                ToolbarItem(placement: .principal) {
                    HStack(spacing: 5) {
                        Text("Mira").font(.headline)
                        agentStatusIcon
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        Button {
                            showArtifacts = true
                        } label: {
                            Image(systemName: "tray.full")
                                .font(.system(size: 14))
                        }
                        Button {
                            showThreadList = true
                        } label: {
                            Image(systemName: "list.bullet")
                                .font(.system(size: 14))
                        }
                        Button {
                            showSettings = true
                        } label: {
                            Image(systemName: "gearshape")
                                .font(.system(size: 14))
                        }
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsSheet(bridge: bridge)
            }
            .sheet(isPresented: $showThreadList) {
                ThreadListView(bridge: bridge)
            }
            .sheet(isPresented: $showArtifacts) {
                ArtifactsView(bridge: bridge)
            }
            .quickLookPreview($previewURL)
            .fileImporter(
                isPresented: $showFilePicker,
                allowedContentTypes: [.item],
                allowsMultipleSelection: true
            ) { result in
                if case .success(let urls) = result {
                    for url in urls {
                        let macPath = iCloudMacPath(for: url)
                        let name = url.lastPathComponent
                        attachedFiles.append(AttachedFile(
                            displayName: name,
                            macPath: macPath
                        ))
                    }
                }
                // Re-focus input after picker dismisses
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    inputFocused = true
                }
            }
            .refreshable {
                bridge.refresh()
            }
        }
    }

    @ViewBuilder
    private var agentStatusIcon: some View {
        if !bridge.agentOnline {
            Image(systemName: "xmark.circle.fill")
                .font(.system(size: 10))
                .foregroundStyle(.red)
        } else if let hb = bridge.heartbeat, hb.isBusy {
            ProgressView()
                .controlSize(.mini)
        } else {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 10))
                .foregroundStyle(.green)
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        if let last = filteredMessages.last {
            withAnimation(.easeOut(duration: 0.15)) {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }

    private func sendMessage() {
        var text = draft.trimmingCharacters(in: .whitespacesAndNewlines)

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
        bridge.send(text, threadId: bridge.currentThreadId)
        inputFocused = false
        draft = ""
        attachedFiles = []
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            inputFocused = true
        }
        // Scroll to the sent message
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            if let proxy = scrollProxy {
                scrollToBottom(proxy)
            }
        }
    }

    /// Convert iOS iCloud URL to the equivalent Mac absolute path.
    private func iCloudMacPath(for url: URL) -> String {
        let path = url.path
        // iOS iCloud path: /private/var/mobile/Library/Mobile Documents/com~apple~CloudDocs/...
        // Mac iCloud path: ~/Library/Mobile Documents/com~apple~CloudDocs/...
        if let range = path.range(of: "com~apple~CloudDocs/") {
            let relative = String(path[range.upperBound...])
            return "~/Library/Mobile Documents/com~apple~CloudDocs/" + relative
        }
        // Fallback: return the filename
        return url.lastPathComponent
    }

    private func openFile(_ path: String) {
        let fm = FileManager.default

        // Resolve relative to bridge folder, or use absolute
        if let base = bridge.bridgeBaseURL {
            let fileURL = base.appendingPathComponent(path)
            if fm.isReadableFile(atPath: fileURL.path) {
                previewURL = fileURL
                return
            }
            // File might need iCloud download first
            if fm.fileExists(atPath: fileURL.path) || !path.hasPrefix("/") {
                try? fm.startDownloadingUbiquitousItem(at: fileURL)
                // Retry after a short delay
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    if fm.isReadableFile(atPath: fileURL.path) {
                        previewURL = fileURL
                    }
                }
                return
            }
        }
        // Try as absolute path
        let url = URL(fileURLWithPath: path)
        if fm.isReadableFile(atPath: url.path) {
            previewURL = url
        }
    }
}

// MARK: - Settings sheet

struct SettingsSheet: View {
    @Bindable var bridge: BridgeService
    @Environment(\.dismiss) private var dismiss
    @State private var editingSenderID: String = ""
    @State private var showFolderPicker = false

    var body: some View {
        NavigationStack {
            Form {
                Section("身份") {
                    TextField("Sender ID", text: $editingSenderID)
                        .font(.system(size: 13))
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                }

                Section("连接") {
                    HStack {
                        Text("Agent").font(.system(size: 13))
                        Spacer()
                        if bridge.agentOnline {
                            Label("在线", systemImage: "checkmark.circle.fill")
                                .font(.system(size: 12))
                                .foregroundStyle(.green)
                        } else {
                            Label("离线", systemImage: "xmark.circle.fill")
                                .font(.system(size: 12))
                                .foregroundStyle(.red)
                        }
                    }

                    if let hb = bridge.heartbeat {
                        HStack {
                            Text("最后心跳").font(.system(size: 13))
                            Spacer()
                            Text(hb.date, style: .relative)
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                    }

                    Button("更换 Mira 文件夹") {
                        showFolderPicker = true
                    }
                    .font(.system(size: 13))
                }

                Section("信息") {
                    HStack {
                        Text("消息数").font(.system(size: 13))
                        Spacer()
                        Text("\(bridge.messages.count)")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Sender ID").font(.system(size: 13))
                        Spacer()
                        Text(bridge.senderID)
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Threads").font(.system(size: 13))
                        Spacer()
                        Text("\(bridge.threads.count)")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Debug Log") {
                    Text(bridge.debugLog.isEmpty ? "无日志" : bridge.debugLog)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        bridge.senderID = editingSenderID
                        dismiss()
                    }
                }
            }
            .onAppear {
                editingSenderID = bridge.senderID
            }
            .fileImporter(
                isPresented: $showFolderPicker,
                allowedContentTypes: [UTType.folder],
                allowsMultipleSelection: false
            ) { result in
                if case .success(let urls) = result, let url = urls.first {
                    bridge.setFolder(url)
                }
            }
        }
    }
}

// MARK: - Attached file model

struct AttachedFile: Identifiable {
    let id = UUID()
    let displayName: String
    let macPath: String
}
