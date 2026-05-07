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
        // Poll on every app foreground — catches signals while app was suspended
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { await self?.pollSignals() }
        }
        // Also poll on a 15-minute timer while in foreground
        Timer.scheduledTimer(withTimeInterval: 900, repeats: true) { [weak self] _ in
            Task { await self?.pollSignals() }
        }
    }

    func pollSignals() async {
        guard let signals = try? await NetworkManager.shared.fetchSignals() else { return }
        // Dedup: track which signal IDs we've already notified about
        let seenKey = "seen_signal_ids"
        let seenArray = UserDefaults.standard.stringArray(forKey: seenKey) ?? []
        var seenSet = Set(seenArray)

        let newSignals = signals.filter { $0.confidence >= 80 && !seenSet.contains($0.id) }
        for signal in newSignals {
            sendLocalNotification(ticker: signal.ticker, confidence: signal.confidence, direction: signal.direction)
            seenSet.insert(signal.id)
        }

        if !newSignals.isEmpty {
            // Keep only the most recent 500 IDs to prevent unbounded growth
            let trimmed = Array(seenSet.suffix(500))
            UserDefaults.standard.set(trimmed, forKey: seenKey)
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
    static let analyzeTickerRequested = Notification.Name("analyzeTickerRequested")
}
