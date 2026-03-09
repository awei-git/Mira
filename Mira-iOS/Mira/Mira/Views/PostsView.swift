import SwiftUI

struct PostsView: View {
    var bridge: BridgeService
    @State private var posts: [SubstackPost] = []

    var body: some View {
        NavigationStack {
            Group {
                if posts.isEmpty {
                    ContentUnavailableView(
                        "暂无文章",
                        systemImage: "doc.text",
                        description: Text("Mira 的 Substack 文章会显示在这里")
                    )
                } else {
                    List(posts) { post in
                        PostRow(post: post)
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Mira's Posts")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { loadPosts() }
            .onAppear { loadPosts() }
        }
    }

    private func loadPosts() {
        guard let tasksDir = bridge.tasksURL else { return }
        let postsFile = tasksDir.appendingPathComponent("substack_posts.json")
        let fm = FileManager.default

        if !fm.isReadableFile(atPath: postsFile.path) {
            try? fm.startDownloadingUbiquitousItem(at: postsFile)
            return
        }

        guard let data = try? Data(contentsOf: postsFile),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return }

        posts = arr.compactMap { dict in
            guard let id = dict["id"] as? Int,
                  let title = dict["title"] as? String,
                  let url = dict["url"] as? String
            else { return nil }
            return SubstackPost(
                id: id,
                title: title,
                url: url,
                commentCount: dict["comment_count"] as? Int ?? 0,
                postDate: dict["post_date"] as? String ?? ""
            )
        }
    }
}

struct SubstackPost: Identifiable {
    let id: Int
    let title: String
    let url: String
    let commentCount: Int
    let postDate: String

    var displayDate: String {
        // Extract date part from ISO string
        String(postDate.prefix(10))
    }
}

struct PostRow: View {
    let post: SubstackPost

    var body: some View {
        Button {
            if let url = URL(string: post.url) {
                UIApplication.shared.open(url)
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(post.title)
                        .font(.body)
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                    HStack(spacing: 12) {
                        Text(post.displayDate)
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                        if post.commentCount > 0 {
                            Label("\(post.commentCount)", systemImage: "bubble.right")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer()
                Image(systemName: "safari")
                    .font(.body)
                    .foregroundStyle(.blue)
            }
            .padding(.vertical, 4)
        }
    }
}
