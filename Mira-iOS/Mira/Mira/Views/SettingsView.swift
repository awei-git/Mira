import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @Environment(BridgeConfig.self) private var config
    @Environment(SyncEngine.self) private var sync
    @Environment(ItemStore.self) private var store
    @State private var showFolderPicker = false

    var body: some View {
        NavigationStack {
            List {
                Section("Agent") {
                    HStack {
                        Circle()
                            .fill(sync.agentOnline ? .green : .red)
                            .frame(width: 10, height: 10)
                        Text(sync.agentOnline ? "Online" : "Offline")
                        Spacer()
                        if let hb = sync.heartbeat {
                            Text(hb.timestamp.prefix(19).replacingOccurrences(of: "T", with: " "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let hb = sync.heartbeat, hb.isBusy {
                        HStack {
                            Text("Active tasks")
                            Spacer()
                            Text("\(hb.activeCount ?? 0)")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Data") {
                    LabeledContent("Items", value: "\(store.items.count)")
                    LabeledContent("Active", value: "\(store.activeRequests.count)")
                    LabeledContent("Discussions", value: "\(store.discussions.count)")
                    LabeledContent("Feeds", value: "\(store.feeds.count)")
                }

                Section("Profile") {
                    if let p = config.profile {
                        LabeledContent("User", value: p.displayName)
                        LabeledContent("Agent", value: p.agentName)
                    }
                    Button("Switch Profile") {
                        config.profile = nil
                        UserDefaults.standard.removeObject(forKey: "selected_profile")
                    }
                }

                Section("Workspace") {
                    if let url = config.bridgeURL {
                        Text(url.path())
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Button("Change Workspace") {
                        showFolderPicker = true
                    }
                }

                if let error = config.error {
                    Section("Error") {
                        Text(error)
                            .foregroundStyle(.red)
                            .font(.caption)
                    }
                }

                Section("Debug Log") {
                    Text(sync.debugLog.isEmpty ? "No log yet" : sync.debugLog)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .fileImporter(
                isPresented: $showFolderPicker,
                allowedContentTypes: [.folder],
                allowsMultipleSelection: false
            ) { result in
                if case .success(let urls) = result, let url = urls.first {
                    config.setFolder(url)
                }
            }
        }
    }
}
