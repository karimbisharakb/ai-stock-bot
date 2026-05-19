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

    // App-facing v1 read endpoints
    static let v1Health = "\(base)/api/v1/health"
    static let alphaTop = "\(base)/api/v1/alpha/top"
    static let alphaReport = "\(base)/api/v1/alpha/report"
    static let alphaOutcomes = "\(base)/api/v1/alpha/outcomes"
    static let alphaLearning = "\(base)/api/v1/alpha/learning"
    static let alphaLearningRecommendations = "\(base)/api/v1/alpha/learning/recommendations"
    static let alphaShadowPolicy = "\(base)/api/v1/alpha/learning/shadow-policy"

    // L3 proposal workflow endpoints
    static let alphaProposals = "\(base)/api/v1/alpha/learning/proposals"
    static let alphaProposalsGenerate = "\(base)/api/v1/alpha/learning/proposals/generate"
    static func alphaProposalsApproveShadow(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/approve-shadow" }
    static func alphaProposalsReject(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/reject" }
    static func alphaProposalShadowResults(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/shadow-results" }
}
