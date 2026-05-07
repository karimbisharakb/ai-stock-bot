import Foundation

struct WatchlistAlert: Codable, Identifiable {
    let id: Int
    let ticker: String
    let alertPrice: Double
    let direction: String  // "above" or "below"
    let note: String?
    let createdAt: String
    let triggered: Int  // 0 or 1
    let triggeredAt: String?

    var isTriggered: Bool { triggered == 1 }

    var directionLabel: String {
        direction == "above" ? "≥" : "≤"
    }

    enum CodingKeys: String, CodingKey {
        case id
        case ticker
        case alertPrice = "alert_price"
        case direction
        case note
        case createdAt = "created_at"
        case triggered
        case triggeredAt = "triggered_at"
    }
}
