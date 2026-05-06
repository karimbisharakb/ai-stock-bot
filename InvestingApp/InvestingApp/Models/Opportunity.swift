import Foundation

struct Opportunity: Codable, Identifiable {
    let id: String
    let ticker: String
    let catalyst: String
    let confidence: Int
    let entryPrice: Double
    let currency: String
    let suggestedPositionCAD: Double
    let catalystDetail: String
    let riskFactors: [String]
    let claudeReasoning: String
    let indicators: [Indicator]
    let outcome3d: Double?
    let outcome7d: Double?
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case id
        case ticker
        case catalyst
        case confidence
        case entryPrice = "entry_price"
        case currency
        case suggestedPositionCAD = "suggested_position_cad"
        case catalystDetail = "catalyst_detail"
        case riskFactors = "risk_factors"
        case claudeReasoning = "claude_reasoning"
        case indicators
        case outcome3d = "outcome_3d"
        case outcome7d = "outcome_7d"
        case timestamp
    }

    var confidenceLevel: ConfidenceLevel {
        switch confidence {
        case 80...: return .high
        case 60..<80: return .medium
        default: return .low
        }
    }
}

enum ConfidenceLevel {
    case high, medium, low

    var color: String {
        switch self {
        case .high: return "green"
        case .medium: return "amber"
        case .low: return "red"
        }
    }
}
