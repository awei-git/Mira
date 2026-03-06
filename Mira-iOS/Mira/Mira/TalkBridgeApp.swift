import SwiftUI

@main
struct MiraApp: App {
    @State private var bridge = BridgeService()

    var body: some Scene {
        WindowGroup {
            if bridge.isSetup {
                ChatView(bridge: bridge)
            } else {
                SetupView(bridge: bridge)
            }
        }
    }
}
