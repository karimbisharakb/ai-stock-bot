import Foundation

struct PaycheckDeployment: Codable {
    let configured: Bool
    let message: String?
    let paycheckAmount: Double?
    let paycheckDay: Int?
    let allocationPercent: Double?
    let nextPaycheckDate: String?
    let daysUntilPaycheck: Int?
    let deployableAmount: Double?
    let existingTickers: [String]?
    let recommendations: [PaycheckRecommendation]?
    // Also returned by /setup
    let success: Bool?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case configured
        case message
        case paycheckAmount = "paycheck_amount"
        case paycheckDay = "paycheck_day"
        case allocationPercent = "allocation_percent"
        case nextPaycheckDate = "next_paycheck_date"
        case daysUntilPaycheck = "days_until_paycheck"
        case deployableAmount = "deployable_amount"
        case existingTickers = "existing_tickers"
        case recommendations
        case success
        case error
    }
}

struct PaycheckRecommendation: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let amount: Double
    let rationale: String
}
