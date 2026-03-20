import SwiftUI
import LocalAuthentication
import UniformTypeIdentifiers

struct ProfilePickerView: View {
    @Environment(BridgeConfig.self) private var config
    @State private var authError: String?
    @State private var showFolderPicker = false

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            Image(systemName: "bubble.left.and.bubble.right.fill")
                .font(.system(size: 56))
                .foregroundStyle(Color(hex: 0x00A884))

            Text("Mira")
                .font(.largeTitle.weight(.bold))
                .foregroundStyle(Color(hex: 0xE9EDEF))

            if config.isSetup {
                // Folder connected — show profiles
                VStack(spacing: 12) {
                    ForEach(config.profiles) { profile in
                        Button { authenticate(profile: profile) } label: {
                            ProfileRow(profile: profile)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 24)
            } else {
                // Need folder access first
                VStack(spacing: 12) {
                    Text("Connect to iCloud Drive")
                        .font(.subheadline)
                        .foregroundStyle(Color(hex: 0x8696A0))

                    Button { showFolderPicker = true } label: {
                        HStack {
                            Image(systemName: "folder.badge.plus")
                            Text("Select MtJoy Folder")
                        }
                        .font(.headline)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 12)
                        .background(Color(hex: 0x00A884))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
            }

            if let err = authError {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }

            if let err = config.error {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }

            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: 0x111B21).ignoresSafeArea())
        .fileImporter(
            isPresented: $showFolderPicker,
            allowedContentTypes: [.folder],
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                config.setFolder(url)
            }
        }
        .onAppear {
            // Auto-authenticate if saved profile + folder both exist
            if config.isSetup,
               let savedId = UserDefaults.standard.string(forKey: "selected_profile"),
               let profile = config.profiles.first(where: { $0.id == savedId }) {
                authenticate(profile: profile)
            }
        }
    }

    private func authenticate(profile: MiraProfile) {
        let context = LAContext()
        var error: NSError?

        if context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) {
            context.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: "Unlock \(profile.displayName)'s profile"
            ) { success, err in
                DispatchQueue.main.async {
                    if success { config.selectProfile(profile) }
                    else { authError = err?.localizedDescription }
                }
            }
        } else if context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) {
            context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason: "Unlock \(profile.displayName)'s profile"
            ) { success, err in
                DispatchQueue.main.async {
                    if success { config.selectProfile(profile) }
                    else { authError = err?.localizedDescription }
                }
            }
        } else {
            config.selectProfile(profile)
        }
    }
}

// MARK: - Profile Row

struct ProfileRow: View {
    let profile: MiraProfile

    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: profile.avatar)
                .font(.system(size: 28))
                .foregroundStyle(Color(hex: 0x00A884))
                .frame(width: 52, height: 52)
                .background(Color(hex: 0x1F2C34))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 2) {
                Text(profile.displayName)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color(hex: 0xE9EDEF))
                Text("Agent: \(profile.agentName)")
                    .font(.caption)
                    .foregroundStyle(Color(hex: 0x8696A0))
            }

            Spacer()

            Image(systemName: "faceid")
                .font(.title3)
                .foregroundStyle(Color(hex: 0x8696A0))
        }
        .padding(16)
        .background(Color(hex: 0x1F2C34))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
