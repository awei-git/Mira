import Foundation
import UserNotifications
import Observation

/// Manages local notifications and badge count for item updates.
@Observable
final class NotificationManager {
    var unreadCount: Int = 0
    private var notifiedIds: Set<String> = []
    private var readIds: Set<String> = []

    init() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
    }

    func processChanges(items: [MiraItem]) {
        var newUnread = 0
        for item in items {
            if readIds.contains(item.id) { continue }
            // Count needs-input and new agent messages
            if item.needsAttention { newUnread += 1 }

            // Send notification for new needs-input items
            if item.needsAttention && !notifiedIds.contains(item.id) {
                notifiedIds.insert(item.id)
                sendNotification(item)
            }

            // Notify on done (for requests the user is waiting on)
            if item.status == .done && item.origin == .user && !notifiedIds.contains("\(item.id)_done") {
                notifiedIds.insert("\(item.id)_done")
                sendDoneNotification(item)
            }
        }
        unreadCount = newUnread
        updateBadge()
    }

    func markAsRead(_ itemId: String) {
        readIds.insert(itemId)
        notifiedIds.insert(itemId)
    }

    private func updateBadge() {
        UNUserNotificationCenter.current().setBadgeCount(unreadCount)
    }

    private func sendNotification(_ item: MiraItem) {
        let content = UNMutableNotificationContent()
        switch item.type {
        case .discussion:
            content.title = "Mira 想和你聊聊"
            content.body = item.title
        case .request:
            content.title = "需要你的回复"
            content.body = item.title
        case .feed:
            content.title = "Mira"
            content.body = item.title
        }
        content.sound = .default
        content.interruptionLevel = item.needsAttention ? .timeSensitive : .active
        let request = UNNotificationRequest(identifier: item.id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    private func sendDoneNotification(_ item: MiraItem) {
        let content = UNMutableNotificationContent()
        content.title = "✓ \(item.title)"
        content.body = item.lastMessagePreview
        content.sound = .default
        let request = UNNotificationRequest(identifier: "\(item.id)_done", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}
