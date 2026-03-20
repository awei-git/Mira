import SwiftUI

@main
struct MiraApp: App {
    @State private var config = BridgeConfig()
    @State private var store = ItemStore()
    @State private var syncEngine: SyncEngine?
    @State private var commands: CommandWriter?
    @State private var notifications = NotificationManager()
    @State private var showSplash = true

    var body: some Scene {
        WindowGroup {
            ZStack {
                // Teal background visible immediately (no black flash)
                Color(hex: 0x008069).ignoresSafeArea()

                if showSplash {
                    SplashView()
                        .transition(.opacity)
                } else if !config.isProfileSelected {
                    ProfilePickerView()
                        .environment(config)
                        .onChange(of: config.isProfileSelected) { _, selected in
                            if selected && config.isSetup { startServices() }
                        }
                } else if let engine = syncEngine, let cmds = commands {
                    MainTabView()
                        .environment(config)
                        .environment(store)
                        .environment(notifications)
                        .environment(engine)
                        .environment(cmds)
                        .onAppear { engine.startPolling() }
                        .transition(.opacity)
                } else if config.isProfileSelected {
                    // Profile selected but services not yet started
                    ProgressView("Loading...")
                        .foregroundStyle(.white)
                        .onAppear { startServices() }
                }
            }
            .preferredColorScheme(.dark)
            .onAppear {
                if config.isSetup && config.isProfileSelected {
                    startServices()
                }
                withAnimation(.easeOut(duration: 0.2)) { showSplash = false }
            }
        }
    }

    private func startServices() {
        guard syncEngine == nil else { return }
        store.loadFromCache()
        let cmd = CommandWriter(config: config, store: store)
        let engine = SyncEngine(config: config, store: store)
        commands = cmd
        syncEngine = engine
        engine.startPolling()
    }
}

// MARK: - Main Tab View

struct MainTabView: View {
    @Environment(SyncEngine.self) private var sync
    @Environment(ItemStore.self) private var store

    var body: some View {
        TabView {
            HomeView()
                .tabItem {
                    Label("Home", systemImage: "house")
                }
                .badge(store.needsAttention.count)

            ThreadsView()
                .tabItem {
                    Label("Threads", systemImage: "bubble.left.and.text.bubble.right")
                }

            LibraryView()
                .tabItem {
                    Label("Library", systemImage: "books.vertical")
                }

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gearshape")
                }
        }
        .tint(Color(hex: 0x00A884)) // WhatsApp dark teal
    }
}

// MARK: - Splash Screen

struct SplashView: View {
    @State private var pulse = false

    var body: some View {
        ZStack {
            Color(hex: 0x008069).ignoresSafeArea()

            VStack(spacing: 20) {
                Image(systemName: "bubble.left.and.bubble.right.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(.white)
                    .scaleEffect(pulse ? 1.08 : 1.0)
                    .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true),
                               value: pulse)

                Text("Mira")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(.white)

                ProgressView()
                    .tint(.white)
                    .padding(.top, 8)
            }
        }
        .onAppear { pulse = true }
    }
}
