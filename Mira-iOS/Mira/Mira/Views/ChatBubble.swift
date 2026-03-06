import SwiftUI

struct ChatBubble: View {
    let message: TBMessage
    let ackStatus: String?
    var onFileTap: ((String) -> Void)? = nil

    @State private var isExpanded = false

    private var isAgent: Bool { message.isFromAgent }
    private let foldThreshold = 300

    var body: some View {
        HStack {
            if !isAgent { Spacer(minLength: 40) }

            VStack(alignment: isAgent ? .leading : .trailing, spacing: 2) {
                // Sender label (for agent messages)
                if isAgent {
                    Text("Mira")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }

                // Bubble
                VStack(alignment: .leading, spacing: 4) {
                    contentView
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(isAgent ? Color(.systemGray5) : Color.blue)
                .foregroundColor(isAgent ? .primary : .white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .contextMenu {
                    Button {
                        UIPasteboard.general.string = message.content
                    } label: {
                        Label("拷贝", systemImage: "doc.on.doc")
                    }
                }

                // Status + time
                HStack(spacing: 3) {
                    Text(timeString)
                        .font(.system(size: 9))
                        .foregroundStyle(.tertiary)

                    if !isAgent {
                        statusLabel(ackStatus)
                    }
                }
            }

            if isAgent { Spacer(minLength: 40) }
        }
    }

    // MARK: - Content with folding + file links

    @ViewBuilder
    private var contentView: some View {
        let content = message.content
        let shouldFold = content.count > foldThreshold && !isExpanded

        if shouldFold {
            // Collapsed: show preview + expand button
            parseContent(String(content.prefix(foldThreshold)) + "...")
                .font(.body)

            Button {
                withAnimation(.easeInOut(duration: 0.2)) { isExpanded = true }
            } label: {
                Text("展开")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(isAgent ? .blue : .white.opacity(0.8))
            }
        } else {
            parseContent(content)
                .font(.body)

            if content.count > foldThreshold {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { isExpanded = false }
                } label: {
                    Text("收起")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(isAgent ? .blue : .white.opacity(0.8))
                }
            }
        }
    }

    // MARK: - Parse content for file links

    @ViewBuilder
    private func parseContent(_ text: String) -> some View {
        let parts = parseFileLinks(text)
        if parts.count == 1, case .text(let t) = parts[0] {
            // Simple text, no links
            Text(t)
                .textSelection(.enabled)
        } else {
            // Has file links — build inline
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                    switch part {
                    case .text(let t):
                        Text(t)
                            .textSelection(.enabled)
                    case .fileLink(let label, let path):
                        Button {
                            onFileTap?(path)
                        } label: {
                            HStack(spacing: 3) {
                                Image(systemName: "doc.text")
                                    .font(.system(size: 11))
                                Text(label)
                                    .font(.system(size: 12, weight: .medium))
                                    .underline()
                            }
                            .foregroundColor(isAgent ? .blue : .white)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Status label

    @ViewBuilder
    private func statusLabel(_ status: String?) -> some View {
        switch status {
        case "received":
            Image(systemName: "checkmark")
                .font(.system(size: 8))
                .foregroundStyle(.secondary)
        case "processing":
            ProgressView()
                .controlSize(.mini)
        case "done":
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 8))
                .foregroundStyle(.green)
        case "error":
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 8))
                .foregroundStyle(.red)
        default:
            Image(systemName: "arrow.up.circle")
                .font(.system(size: 8))
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Time formatting

    private var timeString: String {
        let date = message.date
        let f = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            f.dateFormat = "HH:mm"
        } else {
            f.dateFormat = "MM/dd HH:mm"
        }
        return f.string(from: date)
    }
}

// MARK: - File link parsing

private enum ContentPart {
    case text(String)
    case fileLink(label: String, path: String)
}

private func parseFileLinks(_ text: String) -> [ContentPart] {
    // Match [label](file://path) patterns
    let pattern = #"\[([^\]]+)\]\(file://([^)]+)\)"#
    guard let regex = try? NSRegularExpression(pattern: pattern) else {
        return [.text(text)]
    }

    var parts: [ContentPart] = []
    var lastEnd = text.startIndex

    let nsText = text as NSString
    let matches = regex.matches(in: text, range: NSRange(location: 0, length: nsText.length))

    for match in matches {
        let matchRange = Range(match.range, in: text)!
        let labelRange = Range(match.range(at: 1), in: text)!
        let pathRange = Range(match.range(at: 2), in: text)!

        // Text before this match
        if lastEnd < matchRange.lowerBound {
            let before = String(text[lastEnd..<matchRange.lowerBound])
            if !before.isEmpty { parts.append(.text(before)) }
        }

        let label = String(text[labelRange])
        let path = String(text[pathRange])
        parts.append(.fileLink(label: label, path: path))
        lastEnd = matchRange.upperBound
    }

    // Remaining text
    if lastEnd < text.endIndex {
        let remaining = String(text[lastEnd...])
        if !remaining.isEmpty { parts.append(.text(remaining)) }
    }

    return parts.isEmpty ? [.text(text)] : parts
}
