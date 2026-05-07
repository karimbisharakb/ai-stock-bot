import Foundation

struct PortfolioHealth: Codable {
    let overallScore: Int
    let grade: String
    let summary: String
    let holdingScores: [HoldingHealthScore]

    enum CodingKeys: String, CodingKey {
        case overallScore = "overall_score"
        case grade
        case summary
        case holdingScores = "holding_scores"
    }
}

struct HoldingHealthScore: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let score: Int
    let detail: String

    var scoreColor: String {
        switch score {
        case 8...10: return "positive"
        case 6...7:  return "accent"
        case 4...5:  return "warning"
        default:     return "negative"
        }
    }
}
