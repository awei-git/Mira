import SwiftUI

struct NewItemSheet: View {
    @Environment(CommandWriter.self) private var commands
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var content = ""
    @State private var itemType: NewItemType = .request
    @State private var isQuick = false

    enum NewItemType: String, CaseIterable {
        case request = "Request"
        case discussion = "Discussion"
    }

    var body: some View {
        NavigationStack {
            Form {
                Picker("Type", selection: $itemType) {
                    ForEach(NewItemType.allCases, id: \.self) { type in
                        Text(type.rawValue).tag(type)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)

                Section {
                    TextField("Title", text: $title)
                    TextField("What do you need?", text: $content, axis: .vertical)
                        .lineLimit(3...10)
                }

                if itemType == .request {
                    Toggle("Quick (auto-archive when done)", isOn: $isQuick)
                }
            }
            .navigationTitle("New \(itemType.rawValue)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send") {
                        send()
                        dismiss()
                    }
                    .disabled(content.isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func send() {
        let finalTitle = title.isEmpty ? String(content.prefix(50)) : title
        switch itemType {
        case .request:
            commands.createRequest(title: finalTitle, content: content, quick: isQuick)
        case .discussion:
            commands.createDiscussion(title: finalTitle, content: content)
        }
    }
}
