import Foundation

struct MarketData: Codable {
    let sp500Change: Double
    let sp500Price: Double
    let tsxChange: Double
    let tsxPrice: Double
    let nasdaqChange: Double
    let nasdaqPrice: Double
    let vix: Double
    let usdCadRate: Double
    let marketStatus: String

    enum CodingKeys: String, CodingKey {
        case sp500Change = "sp500_change"
        case sp500Price = "sp500_price"
        case tsxChange = "tsx_change"
        case tsxPrice = "tsx_price"
        case nasdaqChange = "nasdaq_change"
        case nasdaqPrice = "nasdaq_price"
        case vix
        case usdCadRate = "usd_cad_rate"
        case marketStatus = "market_status"
    }
}

struct AnalysisResult: Codable {
    let ticker: String
    let overallScore: Int
    let riskScore: Int
    let growthScore: Int
    let revenue: String
    let revenueGrowth: String
    let eps: String
    let peRatio: String
    let businessModel: String
    let moat: String
    let catalysts: [String]
    let bullCase: String
    let bearCase: String
    let verdict: String
    let metrics: [AnalysisMetric]
    let claudeReasoning: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case overallScore = "overall_score"
        case riskScore = "risk_score"
        case growthScore = "growth_score"
        case revenue
        case revenueGrowth = "revenue_growth"
        case eps
        case peRatio = "pe_ratio"
        case businessModel = "business_model"
        case moat
        case catalysts
        case bullCase = "bull_case"
        case bearCase = "bear_case"
        case verdict
        case metrics
        case claudeReasoning = "claude_reasoning"
    }
}

struct AnalysisMetric: Codable, Identifiable {
    let id = UUID()
    let label: String
    let value: String
    let rating: MetricRating

    enum CodingKeys: String, CodingKey {
        case label
        case value
        case rating
    }
}

enum MetricRating: String, Codable {
    case good = "good"
    case neutral = "neutral"
    case poor = "poor"
}
