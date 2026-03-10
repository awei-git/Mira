import SwiftUI

@main
struct MiraApp: App {
    @State private var bridge = BridgeService()

    var body: some Scene {
        WindowGroup {
            if bridge.isSetup {
                MainTabView(bridge: bridge)
            } else {
                SetupView(bridge: bridge)
            }
        }
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
