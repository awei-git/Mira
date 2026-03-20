import SwiftUI
import UserNotifications

@main
struct MiraApp: App {
    @State private var bridge = BridgeService()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            Group {
                if bridge.isSetup {
                    MainTabView(bridge: bridge)
                } else {
                    SetupView(bridge: bridge)
                }
            }
            .task {
                await requestNotificationPermission()
            }
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active {
                bridge.refresh()
                updateBadge()
            }
        }
    }

    private func requestNotificationPermission() async {
        let center = UNUserNotificationCenter.current()
        try? await center.requestAuthorization(options: [.alert, .sound, .badge])
    }

    private func updateBadge() {
        UNUserNotificationCenter.current().setBadgeCount(bridge.unreadCount)
    }
}

struct MainTabView: View {
    var bridge: BridgeService

    var body: some View {
        VStack(spacing: 0) {
            // Persistent status bar — visible on all tabs
            HStack(spacing: 6) {
                Circle()
                    .fill(bridge.agentOnline ? .green : .red)
                    .frame(width: 8, height: 8)
                Text("Mira")
                    .font(.subheadline.weight(.medium))
                if let hb = bridge.heartbeat, hb.isBusy {
                    Text("·")
                        .foregroundStyle(.secondary)
                    Text("\(hb.activeCount ?? 0) 个任务运行中")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.bar)

            if !bridge.iCloudAvailable {
                HStack(spacing: 6) {
                    Image(systemName: "icloud.slash")
                        .font(.caption)
                    Text("iCloud unavailable — showing cached data")
                        .font(.caption)
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 4)
                .frame(maxWidth: .infinity)
                .background(Color.orange)
            }

            if bridge.isInitialLoading {
                HStack(spacing: 6) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Syncing with iCloud…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
                .frame(maxWidth: .infinity)
                .background(.bar)
            }

            TabView {
                TodayView(bridge: bridge)
                    .tabItem {
                        Label("Today", systemImage: "sun.max")
                    }

                TasksView(bridge: bridge)
                    .tabItem {
                        Label("Threads", systemImage: "bubble.left.and.text.bubble.right")
                    }
                    .badge(bridge.needsInputCount)

                LibraryView(bridge: bridge)
                    .tabItem {
                        Label("Library", systemImage: "folder")
                    }

                SettingsView(bridge: bridge)
                    .tabItem {
                        Label("Settings", systemImage: "gearshape")
                    }
            }
        }
    }
}
