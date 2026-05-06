import SwiftUI
import UserNotifications

@main
struct InvestingAppApp: App {
    @StateObject private var notificationManager = NotificationManager.shared

    init() {
        UITabBar.appearance().backgroundColor = UIColor(red: 0.05, green: 0.08, blue: 0.12, alpha: 1.0)
        UITabBar.appearance().unselectedItemTintColor = UIColor(white: 0.4, alpha: 1)
        UITabBar.appearance().tintColor = UIColor(red: 0, green: 0.831, blue: 1.0, alpha: 1)
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.dark)
                .onAppear {
                    notificationManager.requestAuthorization()
                    notificationManager.scheduleBackgroundPolling()
                }
        }
    }
}

final class NotificationManager: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()

    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }

    func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
    }

    func scheduleBackgroundPolling() {
        Timer.scheduledTimer(withTimeInterval: 900, repeats: true) { _ in
            Task { await self.pollSignals() }
        }
    }

    func pollSignals() async {
        guard let signals = try? await NetworkManager.shared.fetchSignals() else { return }
        let highConviction = signals.filter { $0.confidence >= 80 && !$0.notified }
        for signal in highConviction {
            sendLocalNotification(ticker: signal.ticker, confidence: signal.confidence, direction: signal.direction)
        }
    }

    func sendLocalNotification(ticker: String, confidence: Int, direction: String) {
        let content = UNMutableNotificationContent()
        let emoji = direction.lowercased().contains("buy") ? "📈" : "📉"
        content.title = "\(emoji) \(ticker) — Strong \(direction.capitalized) signal"
        content.body = "\(confidence)% confidence — tap to view"
        content.sound = .default
        content.userInfo = ["tab": "opportunities"]

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 0.1, repeats: false)
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: trigger)
        UNUserNotificationCenter.current().add(request)
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                 didReceive response: UNNotificationResponse) async {
        NotificationCenter.default.post(name: .openOpportunitiesTab, object: nil)
    }
}

extension Notification.Name {
    static let openOpportunitiesTab = Notification.Name("openOpportunitiesTab")
    static let tradeConfirmed = Notification.Name("tradeConfirmed")
}
