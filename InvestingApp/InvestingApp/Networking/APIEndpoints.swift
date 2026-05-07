import Foundation

enum APIEndpoints {
    static let base = "https://ai-stock-bot-production.up.railway.app"

    static let portfolio = "\(base)/api/portfolio"
    static let portfolioHealth = "\(base)/api/portfolio/health"
    static let opportunities = "\(base)/api/opportunities"
    static let signals = "\(base)/api/signals"
    static let analyze = "\(base)/api/analyze"
    static let parseScreenshot = "\(base)/api/parse-screenshot"
    static let confirmTrade = "\(base)/api/confirm-trade"
    static let market = "\(base)/api/market"
    static let cash = "\(base)/api/cash"
    static let testCash = "\(base)/api/test-cash"
    static let settings = "\(base)/api/settings"
    static let predatorAlerts = "\(base)/api/predator/alerts"
    static let predatorWatchlist = "\(base)/api/predator/watchlist"
    static let predatorHistory = "\(base)/api/predator/history"

    // Watchlist
    static let watchlist = "\(base)/api/watchlist"
    static let watchlistAdd = "\(base)/api/watchlist/add"
    static func watchlistDelete(_ id: Int) -> String { "\(base)/api/watchlist/\(id)" }

    // Planner
    static let plannerSetup = "\(base)/api/planner/setup"
    static let plannerNextDeployment = "\(base)/api/planner/next-deployment"
}
