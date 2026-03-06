import SwiftUI
import UniformTypeIdentifiers

/// First-run: user picks the shared Mira folder in iCloud Drive.
struct SetupView: View {
    @Bindable var bridge: BridgeService
    @State private var showPicker = false

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 64))
                .foregroundStyle(.blue)

            Text("Mira")
                .font(.largeTitle.bold())

            Text("选择 iCloud Drive 中的 Mira 文件夹来开始。")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)

            Button {
                showPicker = true
            } label: {
                Label("选择文件夹", systemImage: "folder.badge.plus")
                    .font(.headline)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)

            if let error = bridge.error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }

            Spacer()

            Text("在 Mac 上先创建 ~/iCloud Drive/Mira 文件夹\n如果是家人的手机，让 Mac 用户共享此文件夹给你")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding()
        }
        .fileImporter(
            isPresented: $showPicker,
            allowedContentTypes: [.folder],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first {
                    bridge.setFolder(url)
                }
            case .failure(let error):
                bridge.error = error.localizedDescription
            }
        }
    }
}
