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

            Text("选择 iCloud Drive 中的 MtJoy 文件夹来开始。")
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

            Text("选择 MtJoy 根文件夹（包含 Mira/、Apps/ 等）\n也兼容直接选择 Mira-bridge 文件夹")
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
