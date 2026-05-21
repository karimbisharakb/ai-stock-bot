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
    static let alphaValidationSummary = "\(base)/api/v1/alpha/validation/summary"
    static let alphaValidation = "\(base)/api/v1/alpha/validation"
    static let alphaAlertCandidates = "\(base)/api/v1/alpha/alert-candidates"
    static let alphaAlertGateSummary = "\(base)/api/v1/alpha/alert-gate/summary"
    static let alphaNotificationDryRun = "\(base)/api/v1/alpha/notifications/dry-run"
    static let alphaNotificationDryRunGenerate = "\(base)/api/v1/alpha/notifications/dry-run/generate"
    static func alphaNotificationDryRunReview(_ id: String) -> String { "\(base)/api/v1/alpha/notifications/dry-run/\(id)/review" }
    static func alphaNotificationDryRunDismiss(_ id: String) -> String { "\(base)/api/v1/alpha/notifications/dry-run/\(id)/dismiss" }
    static let alphaNotificationQC = "\(base)/api/v1/alpha/notifications/qc"
    static let alphaNotificationQCSummary = "\(base)/api/v1/alpha/notifications/qc/summary"
    static let alphaNotificationDeliveryLog = "\(base)/api/v1/alpha/notifications/delivery-log"
    static func alphaNotificationSend(_ id: String) -> String { "\(base)/api/v1/alpha/notifications/\(id)/send" }
    static let canonicalPortfolio = "\(base)/api/v1/portfolio"
    static let portfolioReconciliation = "\(base)/api/v1/portfolio/reconciliation"
    static let portfolioSnapshots = "\(base)/api/v1/portfolio/snapshots"
    static let portfolioReconcile = "\(base)/api/v1/portfolio/reconcile"
    static let manualPortfolio = "\(base)/api/v1/portfolio/manual"
    static let manualPortfolioUpsert = "\(base)/api/v1/portfolio/manual/positions/upsert"
    static func manualPortfolioDeactivate(_ ticker: String) -> String { "\(base)/api/v1/portfolio/manual/positions/\(ticker)/deactivate" }
    static let manualPortfolioAccount = "\(base)/api/v1/portfolio/manual/account"
    static let manualPortfolioReconcile = "\(base)/api/v1/portfolio/reconcile/manual"
    static let portfolioThesis = "\(base)/api/v1/portfolio/thesis"
    static func portfolioThesisDetail(_ ticker: String) -> String { "\(base)/api/v1/portfolio/thesis/\(ticker)" }
    static func portfolioThesisUpsert(_ ticker: String) -> String { "\(base)/api/v1/portfolio/thesis/\(ticker)/upsert" }
    static func portfolioThesisJournal(_ ticker: String) -> String { "\(base)/api/v1/portfolio/thesis/\(ticker)/journal" }
    static let portfolioReviews = "\(base)/api/v1/portfolio/reviews"
    static let decisionChecklists = "\(base)/api/v1/decisions/checklists"
    static func decisionChecklistDetail(_ id: String) -> String { "\(base)/api/v1/decisions/checklists/\(id)" }
    static let decisionChecklistCreate = "\(base)/api/v1/decisions/checklists/create"
    static func decisionChecklistItem(_ id: String) -> String { "\(base)/api/v1/decisions/checklists/\(id)/item" }
    static func decisionChecklistApprove(_ id: String) -> String { "\(base)/api/v1/decisions/checklists/\(id)/approve" }
    static func decisionChecklistReject(_ id: String) -> String { "\(base)/api/v1/decisions/checklists/\(id)/reject" }
    static let decisionSummary = "\(base)/api/v1/decisions/summary"
    static let portfolioRisk = "\(base)/api/v1/portfolio/risk"
    static func decisionSizeCheck(ticker: String, decisionType: String) -> String {
        let encodedTicker = ticker.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ticker
        let encodedType = decisionType.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? decisionType
        return "\(base)/api/v1/decisions/size-check?ticker=\(encodedTicker)&decision_type=\(encodedType)"
    }
    static let marketRegime = "\(base)/api/v1/market/regime"
    static let marketRegimeHistory = "\(base)/api/v1/market/regime/history"
    static let marketRegimeRefresh = "\(base)/api/v1/market/regime/refresh"
    static let replayRuns = "\(base)/api/v1/replay/runs"
    static let replayRunCreate = "\(base)/api/v1/replay/run"
    static func replayRunDetail(_ id: String) -> String { "\(base)/api/v1/replay/runs/\(id)" }
    static func replayRunEvents(_ id: String, limit: Int = 200) -> String {
        "\(base)/api/v1/replay/runs/\(id)/events?limit=\(limit)"
    }
    static let portfolioStress = "\(base)/api/v1/portfolio/stress"
    static let portfolioStressRun = "\(base)/api/v1/portfolio/stress/run"
    static let portfolioStressHistory = "\(base)/api/v1/portfolio/stress/history"
    static let strategyScorecards = "\(base)/api/v1/strategies/scorecards"
    static let strategySummary = "\(base)/api/v1/strategies/summary"
    static func strategyDetail(_ strategy: String) -> String { "\(base)/api/v1/strategies/\(strategy)" }
    static let plannerSummary = "\(base)/api/v1/planner/summary"
    static let plannerProjections = "\(base)/api/v1/planner/projections"
    static let plannerRefresh = "\(base)/api/v1/planner/refresh"
    static func dailyBrief(mode: String) -> String { "\(base)/api/v1/brief/daily?mode=\(mode)" }
    static func eodBrief(mode: String) -> String { "\(base)/api/v1/brief/eod?mode=\(mode)" }
    static func marketPulse(period: String) -> String {
        "\(base)/api/v1/market/pulse?period=\(encodedQuery(period))"
    }
    static func sectorPerformance(period: String) -> String {
        "\(base)/api/v1/research/sector?period=\(encodedQuery(period))"
    }
    static func stockResearch(_ ticker: String, period: String) -> String {
        "\(base)/api/v1/research/stock/\(encodedPath(ticker))?period=\(encodedQuery(period))"
    }
    static func etfResearch(_ ticker: String, period: String) -> String {
        "\(base)/api/v1/research/etf/\(encodedPath(ticker))?period=\(encodedQuery(period))"
    }
    static let macroResearch = "\(base)/api/v1/research/macro"
    static let marketNews = "\(base)/api/v1/research/news"
    static func tickerNews(_ ticker: String) -> String { "\(base)/api/v1/research/news/\(encodedPath(ticker))" }
    static func aiStockResearch(_ ticker: String) -> String { "\(base)/api/v1/research/ai/stock/\(encodedPath(ticker))" }
    static func aiETFResearch(_ ticker: String) -> String { "\(base)/api/v1/research/ai/etf/\(encodedPath(ticker))" }
    static let aiMacroResearch = "\(base)/api/v1/research/ai/macro"
    static let researchWatchlist = "\(base)/api/v1/research/watchlist"
    static let researchWatchlistSuggestions = "\(base)/api/v1/research/watchlist/suggestions"
    static func researchWatchlistDetail(_ ticker: String) -> String { "\(base)/api/v1/research/watchlist/\(encodedPath(ticker))" }
    static let researchWatchlistUpsert = "\(base)/api/v1/research/watchlist/upsert"
    static func researchWatchlistNote(_ ticker: String) -> String { "\(base)/api/v1/research/watchlist/\(encodedPath(ticker))/note" }
    static func researchWatchlistArchive(_ ticker: String) -> String { "\(base)/api/v1/research/watchlist/\(encodedPath(ticker))/archive" }
    static let researchWorkflowQueue = "\(base)/api/v1/research/workflow/queue"
    static let researchWorkflowSummary = "\(base)/api/v1/research/workflow/summary"
    static func researchWorkflowAction(_ itemId: String, action: String) -> String {
        "\(base)/api/v1/research/workflow/\(encodedPath(itemId))/\(action)"
    }
    static func weeklyReview(mode: String? = nil) -> String {
        if let mode { return "\(base)/api/v1/review/weekly?mode=\(encodedQuery(mode))" }
        return "\(base)/api/v1/review/weekly"
    }
    static let weeklyReviewHistory = "\(base)/api/v1/review/weekly/history"
    static func catalysts(ticker: String? = nil) -> String {
        guard let ticker, !ticker.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return "\(base)/api/v1/catalysts"
        }
        return "\(base)/api/v1/catalysts?ticker=\(encodedQuery(ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()))"
    }
    static let catalystsSummary = "\(base)/api/v1/catalysts/summary"
    static func catalystDetail(_ id: String) -> String { "\(base)/api/v1/catalysts/\(encodedPath(id))" }
    static let catalystUpsert = "\(base)/api/v1/catalysts/upsert"
    static func catalystComplete(_ id: String) -> String { "\(base)/api/v1/catalysts/\(encodedPath(id))/complete" }
    static func catalystArchive(_ id: String) -> String { "\(base)/api/v1/catalysts/\(encodedPath(id))/archive" }
    static let notifications = "\(base)/api/v1/notifications"
    static let notificationsSummary = "\(base)/api/v1/notifications/summary"
    static func notificationDetail(_ id: String) -> String { "\(base)/api/v1/notifications/\(encodedPath(id))" }
    static let notificationsGenerate = "\(base)/api/v1/notifications/generate"
    static func notificationAction(_ id: String, action: String) -> String {
        "\(base)/api/v1/notifications/\(encodedPath(id))/\(action)"
    }
    static let notificationsMarkAllRead = "\(base)/api/v1/notifications/mark-all-read"
    static let notificationsArchiveRead = "\(base)/api/v1/notifications/archive-read"
    static let notificationPreferences = "\(base)/api/v1/notifications/preferences"
    static let notificationPreferenceCategories = "\(base)/api/v1/notifications/preferences/categories"
    static func notificationPreferenceCategory(_ category: String) -> String {
        "\(base)/api/v1/notifications/preferences/categories/\(encodedPath(category))"
    }
    static func notificationDigest(mode: String) -> String {
        "\(base)/api/v1/notifications/digest?mode=\(encodedQuery(mode))"
    }
    static func systemReleaseCheck(mode: String) -> String {
        "\(base)/api/v1/system/release-check?mode=\(encodedQuery(mode))"
    }
    static let systemRoutes = "\(base)/api/v1/system/routes"
    static let systemFlags = "\(base)/api/v1/system/flags"

    // L3 proposal workflow endpoints
    static let alphaProposals = "\(base)/api/v1/alpha/learning/proposals"
    static let alphaProposalsGenerate = "\(base)/api/v1/alpha/learning/proposals/generate"
    static func alphaProposalsApproveShadow(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/approve-shadow" }
    static func alphaProposalsReject(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/reject" }
    static func alphaProposalShadowResults(_ id: String) -> String { "\(base)/api/v1/alpha/learning/proposals/\(id)/shadow-results" }

    // Backups (Phase I30 / A29)
    static let backupList = "\(base)/api/v1/backups"
    static func backupDetail(_ id: String) -> String { "\(base)/api/v1/backups/\(encodedPath(id))" }
    static let backupCreate = "\(base)/api/v1/backups/create"
    static func backupVerify(_ id: String) -> String { "\(base)/api/v1/backups/\(encodedPath(id))/verify" }
    static func backupDownloadInfo(_ id: String) -> String { "\(base)/api/v1/backups/\(encodedPath(id))/download-info" }
    static func backupRestorePreview(_ id: String) -> String { "\(base)/api/v1/backups/\(encodedPath(id))/restore-preview" }

    private static func encodedPath(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
            .addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? value
    }

    private static func encodedQuery(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? value
    }
}
