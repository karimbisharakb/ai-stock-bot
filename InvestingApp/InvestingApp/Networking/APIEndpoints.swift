import Foundation

enum APIEndpoints {
    static let base = "https://ai-stock-bot-production.up.railway.app"

    static let portfolio = "\(base)/api/portfolio"
    static let opportunities = "\(base)/api/opportunities"
    static let signals = "\(base)/api/signals"
    static let analyze = "\(base)/api/analyze"
    static let parseScreenshot = "\(base)/api/parse-screenshot"
    static let confirmTrade = "\(base)/api/confirm-trade"
    static let market = "\(base)/api/market"
    static let cash = "\(base)/api/cash"
    static let testCash = "\(base)/api/test-cash"
    static let settings = "\(base)/api/settings"
}
