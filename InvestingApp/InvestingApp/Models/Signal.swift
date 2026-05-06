import Foundation

struct Signal: Codable, Identifiable {
    let id: String
    let ticker: String
    let direction: String
    let confidence: Int
    let verdict: String
    let timestamp: String
    let indicators: [Indicator]
    let reasoning: String
    let priceAtSignal: Double?
    let priceAfter3d: Double?
    let priceAfter7d: Double?
    var notified: Bool = false

    enum CodingKeys: String, CodingKey {
        case id
        case ticker
        case direction
        case confidence
        case verdict
        case timestamp
        case indicators
        case reasoning
        case priceAtSignal = "price_at_signal"
        case priceAfter3d = "price_after_3d"
        case priceAfter7d = "price_after_7d"
    }

    var verdictColor: String {
        switch verdict.uppercased() {
        case "CONFIRMED": return "green"
        case "REJECTED": return "red"
        default: return "amber"
        }
    }

    var outcomePercent3d: Double? {
        guard let at = priceAtSignal, let after = priceAfter3d, at > 0 else { return nil }
        return ((after - at) / at) * 100
    }

    var outcomePercent7d: Double? {
        guard let at = priceAtSignal, let after = priceAfter7d, at > 0 else { return nil }
        return ((after - at) / at) * 100
    }
}

struct Indicator: Codable, Identifiable {
    let id = UUID()
    let name: String
    let value: String
    let passed: Bool
    let detail: String?

    enum CodingKeys: String, CodingKey {
        case name
        case value
        case passed
        case detail
    }
}
