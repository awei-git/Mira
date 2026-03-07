import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    var bridge: BridgeService
    @State private var showFolderPicker = false
    @State private var showDebug = false

    var body: some View {
        NavigationStack {
            List {
                // Agent status
                Section("Agent") {
                    HStack {
                        Text("状态")
                        Spacer()
                        Circle()
                            .fill(bridge.agentOnline ? .green : .red)
                            .frame(width: 10, height: 10)
                        Text(bridge.agentOnline ? "在线" : "离线")
                            .foregroundStyle(.secondary)
                    }
                    if let hb = bridge.heartbeat, hb.isBusy {
                        HStack {
                            Text("任务")
                            Spacer()
                            Text("\(hb.activeCount ?? 0) 个运行中")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                // Sender ID
                Section("身份") {
                    HStack {
                        Text("Sender ID")
                        Spacer()
                        Text(bridge.senderID)
                            .foregroundStyle(.secondary)
                    }
                }

                // Folder
                Section("文件夹") {
                    if let url = bridge.bridgeBaseURL {
                        Text(url.lastPathComponent)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Button("重新选择文件夹") {
                        showFolderPicker = true
                    }
                }

                // Debug
                Section("调试") {
                    Button("查看日志") {
                        showDebug = true
                    }
                }
            }
            .navigationTitle("Settings")
            .fileImporter(
                isPresented: $showFolderPicker,
                allowedContentTypes: [.folder],
                allowsMultipleSelection: false
            ) { result in
                if case .success(let urls) = result, let url = urls.first {
                    bridge.setFolder(url)
                }
            }
            .sheet(isPresented: $showDebug) {
                NavigationStack {
                    ScrollView {
                        Text(bridge.debugLog)
                            .font(.system(.caption, design: .monospaced))
                            .padding()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .navigationTitle("Debug Log")
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showDebug = false }
                        }
                    }
                }
            }
        }
    }
}
