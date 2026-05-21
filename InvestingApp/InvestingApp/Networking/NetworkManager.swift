import Foundation
import UIKit

enum NetworkError: LocalizedError {
    case invalidURL
    case noData
    case decodingError(Error)
    case serverError(Int, String)
    case networkError(Error)
    case offline
    case timeout

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .noData: return "No data received"
        case .decodingError(let e): return "Decode error: \(e.localizedDescription)"
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let e): return e.localizedDescription
        case .offline: return "Offline. Showing cached data when available."
        case .timeout: return "Request timed out. Try again in a moment."
        }
    }
}

final class NetworkManager {
    static let shared = NetworkManager()

    private let session: URLSession
    private let decoder = JSONDecoder()

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 18
        config.timeoutIntervalForResource = 30
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }

    // MARK: - Portfolio

    func fetchPortfolio() async throws -> Portfolio {
        return try await get(url: APIEndpoints.portfolio)
    }

    // MARK: - Opportunities

    func fetchOpportunities() async throws -> [Opportunity] {
        struct Wrapper: Decodable { let opportunities: [Opportunity] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.opportunities)
        return wrapper.opportunities
    }

    // MARK: - Signals

    func fetchSignals() async throws -> [Signal] {
        struct Wrapper: Decodable { let signals: [Signal] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.signals)
        return wrapper.signals
    }

    // MARK: - Analyze

    func analyzeStock(ticker: String) async throws -> AnalysisResult {
        let body = ["ticker": ticker]
        return try await post(url: APIEndpoints.analyze, body: body)
    }

    // MARK: - Screenshot

    func parseScreenshot(image: UIImage) async throws -> ParsedTrade {
        guard let imageData = image.jpegData(compressionQuality: 0.85) else {
            throw NetworkError.noData
        }
        let base64 = imageData.base64EncodedString()
        let body = ["image": base64]
        return try await post(url: APIEndpoints.parseScreenshot, body: body)
    }

    // MARK: - Confirm Trade

    func confirmTrade(trade: ParsedTrade, ticker: String, shares: Double, priceCAD: Double, totalCAD: Double, type: String) async throws {
        var body: [String: Any] = [
            "ticker": ticker,
            "shares": shares,
            "price_cad": priceCAD,
            "total_cad": totalCAD,
            "type": type,
            "currency": trade.currency
        ]
        if let value = trade.pricePerShareUSD { body["price_per_share_usd"] = value }
        if let value = trade.pricePerShareCAD { body["price_per_share_cad"] = value }
        if let value = trade.exchangeRate { body["exchange_rate"] = value }
        if let value = trade.notes { body["notes"] = value }
        struct ConfirmResponse: Decodable { let success: Bool; let error: String? }
        let response: ConfirmResponse = try await postAny(url: APIEndpoints.confirmTrade, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Trade failed on server")
        }
    }

    // MARK: - Portfolio Health

    func fetchPortfolioHealth() async throws -> PortfolioHealth {
        return try await get(url: APIEndpoints.portfolioHealth)
    }

    // MARK: - Watchlist

    func fetchWatchlist() async throws -> [WatchlistAlert] {
        struct Wrapper: Decodable { let watchlist: [WatchlistAlert] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.watchlist)
        return wrapper.watchlist
    }

    func addWatchlistAlert(ticker: String, alertPrice: Double, direction: String, note: String) async throws -> Int {
        let body: [String: Any] = [
            "ticker": ticker,
            "alert_price": alertPrice,
            "direction": direction,
            "note": note
        ]
        struct AddResponse: Decodable { let success: Bool; let id: Int?; let error: String? }
        let response: AddResponse = try await postAny(url: APIEndpoints.watchlistAdd, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to add alert")
        }
        return response.id ?? 0
    }

    func deleteWatchlistAlert(id: Int) async throws {
        guard let reqURL = URL(string: APIEndpoints.watchlistDelete(id)) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "DELETE"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        struct DeleteResponse: Decodable { let success: Bool; let error: String? }
        let response: DeleteResponse = try await perform(request: request)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to delete alert")
        }
    }

    // MARK: - Planner

    func fetchPlannerNextDeployment() async throws -> PaycheckDeployment {
        return try await get(url: APIEndpoints.plannerNextDeployment)
    }

    func savePlannerSetup(paycheckAmount: Double, paycheckDay: Int, allocationPercent: Double) async throws -> PaycheckDeployment {
        let body: [String: Any] = [
            "paycheck_amount": paycheckAmount,
            "paycheck_day": paycheckDay,
            "allocation_percent": allocationPercent
        ]
        return try await postAny(url: APIEndpoints.plannerSetup, body: body)
    }

    // MARK: - Predator

    func fetchPredatorAlerts() async throws -> [PredatorAlert] {
        struct AlertsWrapper: Decodable { let alerts: [PredatorAlert] }
        let wrapper: AlertsWrapper = try await get(url: APIEndpoints.predatorAlerts)
        return wrapper.alerts
    }

    func fetchPredatorWatchlist() async throws -> [PredatorAlert] {
        struct WatchlistWrapper: Decodable { let watchlist: [PredatorAlert] }
        let wrapper: WatchlistWrapper = try await get(url: APIEndpoints.predatorWatchlist)
        return wrapper.watchlist
    }

    // MARK: - Market Data

    func fetchMarketData() async throws -> MarketData {
        return try await get(url: APIEndpoints.market)
    }

    // MARK: - Operator / Alpha

    func fetchBackendHealth() async throws -> BackendHealth {
        return try await getV1(url: APIEndpoints.v1Health)
    }

    func fetchRootHealth() async throws -> RootHealth {
        return try await get(url: "\(APIEndpoints.base)/health")
    }

    func fetchAlphaTop() async throws -> AlphaTopResponse {
        return try await getV1(url: APIEndpoints.alphaTop)
    }

    func fetchAlphaReport() async throws -> AlphaReport {
        return try await getV1(url: APIEndpoints.alphaReport)
    }

    func fetchAlphaOutcomes() async throws -> AlphaOutcomesResponse {
        return try await getV1(url: APIEndpoints.alphaOutcomes)
    }

    func fetchAlphaLearning() async throws -> AlphaLearning {
        return try await getV1(url: APIEndpoints.alphaLearning)
    }

    func fetchAlphaLearningRecommendations() async throws -> AlphaLearningRecommendations {
        return try await getV1(url: APIEndpoints.alphaLearningRecommendations)
    }

    func fetchAlphaShadowPolicy() async throws -> AlphaShadowPolicy {
        return try await getV1(url: APIEndpoints.alphaShadowPolicy)
    }

    func fetchAlphaValidationSummary() async throws -> AlphaValidationSummary {
        return try await getV1(url: APIEndpoints.alphaValidationSummary)
    }

    func fetchAlphaValidations() async throws -> AlphaValidationResponse {
        return try await getV1(url: APIEndpoints.alphaValidation)
    }

    func fetchAlphaAlertCandidates() async throws -> AlphaAlertCandidatesResponse {
        return try await getV1(url: APIEndpoints.alphaAlertCandidates)
    }

    func fetchAlphaAlertGateSummary() async throws -> AlphaAlertGateSummary {
        return try await getV1(url: APIEndpoints.alphaAlertGateSummary)
    }

    func fetchAlphaNotificationDryRuns() async throws -> AlphaDryRunListResponse {
        return try await getV1(url: APIEndpoints.alphaNotificationDryRun)
    }

    func generateAlphaDryRuns(secret: String) async throws -> AlphaDryRunGenerateResponse {
        return try await postV1Auth(url: APIEndpoints.alphaNotificationDryRunGenerate, body: [:], secret: secret)
    }

    func reviewAlphaDryRun(id: String, note: String?, secret: String) async throws -> AlphaDryRunActionResponse {
        var body: [String: Any] = [:]
        if let note { body["note"] = note }
        return try await postV1Auth(url: APIEndpoints.alphaNotificationDryRunReview(id), body: body, secret: secret)
    }

    func dismissAlphaDryRun(id: String, reason: String?, secret: String) async throws -> AlphaDryRunActionResponse {
        var body: [String: Any] = [:]
        if let reason { body["reason"] = reason }
        return try await postV1Auth(url: APIEndpoints.alphaNotificationDryRunDismiss(id), body: body, secret: secret)
    }

    func fetchAlphaNotificationQC() async throws -> AlphaNotificationQCResponse {
        return try await getV1(url: APIEndpoints.alphaNotificationQC)
    }

    func fetchAlphaNotificationQCSummary() async throws -> AlphaNotificationQCSummary {
        return try await getV1(url: APIEndpoints.alphaNotificationQCSummary)
    }

    func fetchAlphaNotificationDeliveryLog() async throws -> AlphaNotificationDeliveryLogResponse {
        return try await getV1(url: APIEndpoints.alphaNotificationDeliveryLog)
    }

    func sendAlphaNotification(dryRunId: String, secret: String) async throws -> AlphaNotificationSendResponse {
        return try await postV1Auth(url: APIEndpoints.alphaNotificationSend(dryRunId), body: [:], secret: secret)
    }

    func fetchCanonicalPortfolio() async throws -> CanonicalPortfolioResponse {
        return try await getV1(url: APIEndpoints.canonicalPortfolio)
    }

    func fetchPortfolioReconciliation() async throws -> PortfolioReconciliationResponse {
        return try await getV1(url: APIEndpoints.portfolioReconciliation)
    }

    func fetchPortfolioSnapshots() async throws -> PortfolioSnapshotsResponse {
        return try await getV1(url: APIEndpoints.portfolioSnapshots)
    }

    func reconcilePortfolio(secret: String) async throws -> PortfolioReconcileResponse {
        return try await postV1Auth(url: APIEndpoints.portfolioReconcile, body: [:], secret: secret)
    }

    func fetchManualPortfolio() async throws -> ManualPortfolioResponse {
        return try await getV1(url: APIEndpoints.manualPortfolio)
    }

    func upsertManualPosition(
        ticker: String,
        quantity: Double,
        avgCost: Double,
        realizedPnL: Double,
        accountType: String,
        currency: String,
        note: String,
        secret: String
    ) async throws -> ManualPositionActionResponse {
        let body: [String: Any] = [
            "ticker": ticker,
            "quantity": quantity,
            "avg_cost": avgCost,
            "realized_pnl": realizedPnL,
            "account_type": accountType,
            "currency": currency,
            "note": note
        ]
        return try await postV1Auth(url: APIEndpoints.manualPortfolioUpsert, body: body, secret: secret)
    }

    func deactivateManualPosition(ticker: String, secret: String) async throws -> ManualPositionActionResponse {
        return try await postV1Auth(url: APIEndpoints.manualPortfolioDeactivate(ticker), body: [:], secret: secret)
    }

    func updateManualAccount(
        accountName: String,
        accountType: String,
        baseCurrency: String,
        availableCash: Double,
        contributionRoom: Double?,
        notes: String,
        secret: String
    ) async throws -> ManualAccountUpdateResponse {
        var body: [String: Any] = [
            "account_name": accountName,
            "account_type": accountType,
            "base_currency": baseCurrency,
            "available_cash": availableCash,
            "notes": notes
        ]
        if let contributionRoom { body["contribution_room"] = contributionRoom }
        return try await postV1Auth(url: APIEndpoints.manualPortfolioAccount, body: body, secret: secret)
    }

    func reconcileManualPortfolio(secret: String) async throws -> PortfolioReconcileResponse {
        return try await postV1Auth(url: APIEndpoints.manualPortfolioReconcile, body: [:], secret: secret)
    }

    func fetchPortfolioTheses() async throws -> PortfolioThesisListResponse {
        return try await getV1(url: APIEndpoints.portfolioThesis)
    }

    func fetchPortfolioThesisDetail(ticker: String) async throws -> PortfolioThesisDetailResponse {
        return try await getV1(url: APIEndpoints.portfolioThesisDetail(ticker))
    }

    func upsertPortfolioThesis(
        ticker: String,
        thesisTitle: String,
        thesisText: String,
        setupType: String,
        convictionLevel: String,
        timeHorizon: String,
        entryReason: String,
        expectedCatalysts: String,
        riskFactors: String,
        invalidationLevel: Double?,
        targetLevel: Double?,
        exitPlan: String,
        reviewFrequencyDays: Int,
        nextReviewAt: String?,
        status: String,
        secret: String
    ) async throws -> PortfolioThesisActionResponse {
        var body: [String: Any] = [
            "thesis_title": thesisTitle,
            "thesis_text": thesisText,
            "setup_type": setupType,
            "conviction_level": convictionLevel,
            "time_horizon": timeHorizon,
            "entry_reason": entryReason,
            "expected_catalysts": expectedCatalysts,
            "risk_factors": riskFactors,
            "exit_plan": exitPlan,
            "review_frequency_days": reviewFrequencyDays,
            "status": status
        ]
        if let invalidationLevel { body["invalidation_level"] = invalidationLevel }
        if let targetLevel { body["target_level"] = targetLevel }
        if let nextReviewAt, !nextReviewAt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["next_review_at"] = nextReviewAt
        }
        return try await postV1Auth(url: APIEndpoints.portfolioThesisUpsert(ticker), body: body, secret: secret)
    }

    func appendThesisJournalEntry(
        ticker: String,
        entryType: String,
        text: String,
        tags: [String],
        confidenceChange: String?,
        secret: String
    ) async throws -> PortfolioJournalActionResponse {
        var body: [String: Any] = [
            "entry_type": entryType,
            "text": text,
            "tags": tags
        ]
        if let confidenceChange, !confidenceChange.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["confidence_change"] = confidenceChange
        }
        return try await postV1Auth(url: APIEndpoints.portfolioThesisJournal(ticker), body: body, secret: secret)
    }

    func fetchPortfolioReviews() async throws -> PortfolioReviewsResponse {
        return try await getV1(url: APIEndpoints.portfolioReviews)
    }

    func fetchDecisionChecklists() async throws -> DecisionChecklistListResponse {
        return try await getV1(url: APIEndpoints.decisionChecklists)
    }

    func fetchDecisionChecklistDetail(id: String) async throws -> DecisionChecklist {
        return try await getV1(url: APIEndpoints.decisionChecklistDetail(id))
    }

    func createDecisionChecklist(
        ticker: String,
        decisionType: String,
        linkedAlphaCandidateId: String?,
        linkedThesisId: Int?,
        secret: String
    ) async throws -> DecisionChecklistActionResponse {
        var body: [String: Any] = [
            "ticker": ticker,
            "decision_type": decisionType
        ]
        if let linkedAlphaCandidateId, !linkedAlphaCandidateId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["linked_alpha_candidate_id"] = linkedAlphaCandidateId
        }
        if let linkedThesisId {
            body["linked_thesis_id"] = linkedThesisId
        }
        return try await postV1Auth(url: APIEndpoints.decisionChecklistCreate, body: body, secret: secret)
    }

    func updateDecisionChecklistItem(
        checklistId: String,
        itemKey: String,
        passed: Bool?,
        note: String,
        secret: String
    ) async throws -> DecisionChecklistItemActionResponse {
        var body: [String: Any] = [
            "item_key": itemKey,
            "note": note
        ]
        body["passed"] = passed ?? NSNull()
        return try await postV1Auth(url: APIEndpoints.decisionChecklistItem(checklistId), body: body, secret: secret)
    }

    func approveDecisionChecklist(id: String, secret: String) async throws -> DecisionChecklistActionResponse {
        return try await postV1Auth(url: APIEndpoints.decisionChecklistApprove(id), body: ["actor": "ios"], secret: secret)
    }

    func rejectDecisionChecklist(id: String, reason: String, secret: String) async throws -> DecisionChecklistActionResponse {
        return try await postV1Auth(url: APIEndpoints.decisionChecklistReject(id), body: ["actor": "ios", "reason": reason], secret: secret)
    }

    func fetchDecisionSummary() async throws -> DecisionSummaryResponse {
        return try await getV1(url: APIEndpoints.decisionSummary)
    }

    func fetchPortfolioRisk() async throws -> PortfolioRiskReport {
        return try await getV1(url: APIEndpoints.portfolioRisk)
    }

    func fetchDecisionSizeCheck(ticker: String, decisionType: String) async throws -> DecisionSizeCheckResponse {
        return try await getV1(url: APIEndpoints.decisionSizeCheck(ticker: ticker, decisionType: decisionType))
    }

    func fetchMarketRegime() async throws -> MarketRegimeResponse {
        return try await getV1(url: APIEndpoints.marketRegime)
    }

    func fetchMarketRegimeHistory() async throws -> MarketRegimeHistoryResponse {
        return try await getV1(url: APIEndpoints.marketRegimeHistory)
    }

    func refreshMarketRegime(secret: String) async throws -> MarketRegimeRefreshResponse {
        return try await postV1Auth(url: APIEndpoints.marketRegimeRefresh, body: [:], secret: secret)
    }

    func fetchReplayRuns(limit: Int = 20) async throws -> ReplayRunsResponse {
        return try await getV1(url: "\(APIEndpoints.replayRuns)?limit=\(limit)")
    }

    func fetchReplayRunDetail(id: String) async throws -> ReplayRunDetailResponse {
        return try await getV1(url: APIEndpoints.replayRunDetail(id))
    }

    func fetchReplayRunEvents(id: String, limit: Int = 200) async throws -> ReplayEventsResponse {
        return try await getV1(url: APIEndpoints.replayRunEvents(id, limit: limit))
    }

    func createReplayRun(
        startDate: String,
        endDate: String,
        ticker: String?,
        source: String?,
        setupType: String?,
        maxRows: Int,
        secret: String
    ) async throws -> ReplayRunCreateResponse {
        var body: [String: Any] = [
            "start_date": startDate,
            "end_date": endDate,
            "max_rows": maxRows
        ]
        if let ticker, !ticker.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["ticker_filter"] = [ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()]
        }
        if let source, !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["source_filter"] = source.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let setupType, !setupType.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["setup_type_filter"] = setupType.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return try await postV1Auth(url: APIEndpoints.replayRunCreate, body: body, secret: secret)
    }

    func fetchPortfolioStress() async throws -> PortfolioStressLatestResponse {
        return try await getV1(url: APIEndpoints.portfolioStress)
    }

    func fetchPortfolioStressHistory(limit: Int = 20) async throws -> PortfolioStressHistoryResponse {
        return try await getV1(url: "\(APIEndpoints.portfolioStressHistory)?limit=\(limit)")
    }

    func runPortfolioStress(secret: String) async throws -> PortfolioStressRunResponse {
        return try await postV1Auth(url: APIEndpoints.portfolioStressRun, body: [:], secret: secret)
    }

    func fetchStrategyScorecards() async throws -> StrategyScorecardsResponse {
        return try await getV1(url: APIEndpoints.strategyScorecards)
    }

    func fetchStrategySummary() async throws -> StrategySummaryResponse {
        return try await getV1(url: APIEndpoints.strategySummary)
    }

    func fetchStrategyDetail(strategy: String) async throws -> StrategyScorecardDetailResponse {
        return try await getV1(url: APIEndpoints.strategyDetail(strategy))
    }

    func fetchPlannerSummary() async throws -> PlannerSummaryResponse {
        return try await getV1(url: APIEndpoints.plannerSummary)
    }

    func fetchPlannerProjections() async throws -> PlannerProjectionsResponse {
        return try await getV1(url: APIEndpoints.plannerProjections)
    }

    func refreshPlanner(monthlyContribution: Double? = nil, secret: String) async throws -> PlannerRefreshResponse {
        var body: [String: Any] = [:]
        if let monthlyContribution {
            body["monthly_contribution"] = monthlyContribution
        }
        return try await postV1Auth(url: APIEndpoints.plannerRefresh, body: body, secret: secret)
    }

    func fetchDailyBriefCompact() async throws -> DailyBriefCompactResponse {
        return try await getV1(url: APIEndpoints.dailyBrief(mode: "compact"))
    }

    func fetchDailyBriefDetailed() async throws -> DailyBriefDetailedResponse {
        return try await getV1(url: APIEndpoints.dailyBrief(mode: "detailed"))
    }

    func fetchDailyBriefDebug() async throws -> DailyBriefDebugResponse {
        return try await getV1(url: APIEndpoints.dailyBrief(mode: "debug"))
    }

    func fetchEODBriefCompact() async throws -> DailyBriefCompactResponse {
        return try await getV1(url: APIEndpoints.eodBrief(mode: "compact"))
    }

    func fetchEODBriefDetailed() async throws -> DailyBriefDetailedResponse {
        return try await getV1(url: APIEndpoints.eodBrief(mode: "detailed"))
    }

    func fetchEODBriefDebug() async throws -> DailyBriefDebugResponse {
        return try await getV1(url: APIEndpoints.eodBrief(mode: "debug"))
    }

    func fetchMarketPulse(period: String) async throws -> MarketPulseResponse {
        return try await getV1(url: APIEndpoints.marketPulse(period: period))
    }

    func fetchSectorPerformance(period: String) async throws -> SectorPerformanceResponse {
        return try await getV1(url: APIEndpoints.sectorPerformance(period: period))
    }

    func fetchStockResearch(ticker: String, period: String) async throws -> StockResearchResponse {
        return try await getV1(url: APIEndpoints.stockResearch(ticker, period: period))
    }

    func fetchETFResearch(ticker: String, period: String) async throws -> ETFResearchResponse {
        return try await getV1(url: APIEndpoints.etfResearch(ticker, period: period))
    }

    func fetchMacroResearch() async throws -> MacroResearchResponse {
        return try await getV1(url: APIEndpoints.macroResearch)
    }

    func fetchMarketNews() async throws -> ResearchNewsResponse {
        return try await getV1(url: APIEndpoints.marketNews)
    }

    func fetchTickerNews(ticker: String) async throws -> ResearchNewsResponse {
        return try await getV1(url: APIEndpoints.tickerNews(ticker))
    }

    func generateAIStockResearch(ticker: String) async throws -> ResearchAIResponse {
        return try await postV1(url: APIEndpoints.aiStockResearch(ticker), body: [:])
    }

    func generateAIETFResearch(ticker: String) async throws -> ResearchAIResponse {
        return try await postV1(url: APIEndpoints.aiETFResearch(ticker), body: [:])
    }

    func generateAIMacroResearch() async throws -> ResearchAIResponse {
        return try await postV1(url: APIEndpoints.aiMacroResearch, body: [:])
    }

    func fetchResearchWatchlist() async throws -> ResearchWatchlistResponse {
        return try await getV1(url: APIEndpoints.researchWatchlist)
    }

    func fetchResearchWatchlistSuggestions() async throws -> ResearchWatchlistSuggestionsResponse {
        return try await getV1(url: APIEndpoints.researchWatchlistSuggestions)
    }

    func fetchResearchWatchlistDetail(ticker: String) async throws -> ResearchWatchlistDetail {
        return try await getV1(url: APIEndpoints.researchWatchlistDetail(ticker))
    }

    func upsertResearchWatchlistItem(
        ticker: String,
        name: String?,
        assetType: String,
        category: String,
        status: String,
        priority: String,
        reason: String,
        nextReviewAt: String?,
        linkedAlphaCandidateId: Int?,
        linkedThesisId: Int?,
        secret: String
    ) async throws -> ResearchWatchlistItem {
        var body: [String: Any] = [
            "ticker": ticker,
            "asset_type": assetType,
            "category": category,
            "status": status,
            "priority": priority,
            "reason": reason
        ]
        if let name, !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { body["name"] = name }
        if let nextReviewAt, !nextReviewAt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { body["next_review_at"] = nextReviewAt }
        if let linkedAlphaCandidateId { body["linked_alpha_candidate_id"] = linkedAlphaCandidateId }
        if let linkedThesisId { body["linked_thesis_id"] = linkedThesisId }
        return try await postV1Auth(url: APIEndpoints.researchWatchlistUpsert, body: body, secret: secret)
    }

    func appendResearchWatchlistNote(
        ticker: String,
        noteType: String,
        text: String,
        tags: [String],
        secret: String
    ) async throws -> ResearchWatchlistNote {
        let body: [String: Any] = [
            "note_type": noteType,
            "text": text,
            "tags": tags
        ]
        return try await postV1Auth(url: APIEndpoints.researchWatchlistNote(ticker), body: body, secret: secret)
    }

    func archiveResearchWatchlistItem(ticker: String, secret: String) async throws -> ResearchWatchlistItem {
        return try await postV1Auth(url: APIEndpoints.researchWatchlistArchive(ticker), body: [:], secret: secret)
    }

    func fetchResearchWorkflowQueue() async throws -> ResearchWorkflowQueueResponse {
        return try await getV1(url: APIEndpoints.researchWorkflowQueue)
    }

    func fetchResearchWorkflowSummary() async throws -> ResearchWorkflowSummary {
        return try await getV1(url: APIEndpoints.researchWorkflowSummary)
    }

    func startResearchWorkflowItem(itemId: String, secret: String) async throws -> ResearchWorkflowItem {
        return try await postV1Auth(url: APIEndpoints.researchWorkflowAction(itemId, action: "start"), body: [:], secret: secret)
    }

    func completeResearchWorkflowItem(itemId: String, secret: String) async throws -> ResearchWorkflowItem {
        return try await postV1Auth(url: APIEndpoints.researchWorkflowAction(itemId, action: "done"), body: [:], secret: secret)
    }

    func snoozeResearchWorkflowItem(itemId: String, hours: Int, secret: String) async throws -> ResearchWorkflowItem {
        return try await postV1Auth(url: APIEndpoints.researchWorkflowAction(itemId, action: "snooze"), body: ["hours": hours], secret: secret)
    }

    func archiveResearchWorkflowItem(itemId: String, secret: String) async throws -> ResearchWorkflowItem {
        return try await postV1Auth(url: APIEndpoints.researchWorkflowAction(itemId, action: "archive"), body: [:], secret: secret)
    }

    func appendResearchWorkflowNote(itemId: String, text: String, secret: String) async throws -> ResearchWorkflowNote {
        return try await postV1Auth(url: APIEndpoints.researchWorkflowAction(itemId, action: "note"), body: ["text": text], secret: secret)
    }

    func fetchWeeklyReviewCompact() async throws -> WeeklyReviewCompactResponse {
        return try await getV1(url: APIEndpoints.weeklyReview(mode: "compact"))
    }

    func fetchWeeklyReviewDetailed() async throws -> WeeklyReviewDetailedResponse {
        return try await getV1(url: APIEndpoints.weeklyReview(mode: "detailed"))
    }

    func fetchWeeklyReviewDebug() async throws -> WeeklyReviewDebugResponse {
        return try await getV1(url: APIEndpoints.weeklyReview(mode: "debug"))
    }

    func fetchWeeklyReviewHistory() async throws -> WeeklyReviewHistoryResponse {
        return try await getV1(url: APIEndpoints.weeklyReviewHistory)
    }

    func fetchCatalysts(ticker: String? = nil) async throws -> CatalystListResponse {
        return try await getV1(url: APIEndpoints.catalysts(ticker: ticker))
    }

    func fetchCatalystSummary() async throws -> CatalystSummaryResponse {
        return try await getV1(url: APIEndpoints.catalystsSummary)
    }

    func fetchCatalystDetail(id: String) async throws -> Catalyst {
        return try await getV1(url: APIEndpoints.catalystDetail(id))
    }

    func upsertCatalyst(
        ticker: String?,
        title: String,
        description: String,
        catalystType: String,
        date: String,
        confidence: String,
        importance: String,
        linkedEntityType: String?,
        linkedEntityId: String?,
        secret: String
    ) async throws -> Catalyst {
        var body: [String: Any] = [
            "title": title,
            "description": description,
            "catalyst_type": catalystType,
            "date": date,
            "confidence": confidence,
            "importance": importance,
            "source": "manual"
        ]
        if let ticker, !ticker.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["ticker"] = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        }
        if let linkedEntityType, !linkedEntityType.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["linked_entity_type"] = linkedEntityType.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        }
        if let linkedEntityId, !linkedEntityId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["linked_entity_id"] = linkedEntityId.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return try await postV1Auth(url: APIEndpoints.catalystUpsert, body: body, secret: secret)
    }

    func completeCatalyst(id: String, secret: String) async throws -> Catalyst {
        return try await postV1Auth(url: APIEndpoints.catalystComplete(id), body: [:], secret: secret)
    }

    func archiveCatalyst(id: String, secret: String) async throws -> Catalyst {
        return try await postV1Auth(url: APIEndpoints.catalystArchive(id), body: [:], secret: secret)
    }

    func fetchNotifications() async throws -> [InAppNotification] {
        let response: NotificationListResponse = try await getV1(url: APIEndpoints.notifications)
        return response.notifications
    }

    func fetchNotificationSummary() async throws -> NotificationSummaryResponse {
        return try await getV1(url: APIEndpoints.notificationsSummary)
    }

    func fetchNotificationDetail(id: String) async throws -> InAppNotification {
        return try await getV1(url: APIEndpoints.notificationDetail(id))
    }

    func generateNotifications(secret: String) async throws -> NotificationGenerateResponse {
        return try await postV1Auth(url: APIEndpoints.notificationsGenerate, body: [:], secret: secret)
    }

    func markNotificationRead(id: String, secret: String) async throws -> InAppNotification {
        return try await postV1Auth(url: APIEndpoints.notificationAction(id, action: "read"), body: [:], secret: secret)
    }

    func markNotificationUnread(id: String, secret: String) async throws -> InAppNotification {
        return try await postV1Auth(url: APIEndpoints.notificationAction(id, action: "unread"), body: [:], secret: secret)
    }

    func dismissNotification(id: String, secret: String) async throws -> InAppNotification {
        return try await postV1Auth(url: APIEndpoints.notificationAction(id, action: "dismiss"), body: [:], secret: secret)
    }

    func archiveNotification(id: String, secret: String) async throws -> InAppNotification {
        return try await postV1Auth(url: APIEndpoints.notificationAction(id, action: "archive"), body: [:], secret: secret)
    }

    func markAllNotificationsRead(secret: String) async throws -> NotificationMarkAllReadResponse {
        return try await postV1Auth(url: APIEndpoints.notificationsMarkAllRead, body: [:], secret: secret)
    }

    func archiveReadNotifications(secret: String) async throws -> NotificationArchiveReadResponse {
        return try await postV1Auth(url: APIEndpoints.notificationsArchiveRead, body: [:], secret: secret)
    }

    func fetchNotificationPreferences() async throws -> NotificationPreferences {
        return try await getV1(url: APIEndpoints.notificationPreferences)
    }

    func updateNotificationPreferences(_ preferences: NotificationPreferences, secret: String) async throws -> NotificationPreferences {
        let body: [String: Any] = [
            "enabled_categories": preferences.enabledCategories,
            "minimum_severity": preferences.minimumSeverity,
            "quiet_hours_enabled": preferences.quietHoursEnabled,
            "quiet_hours_start": preferences.quietHoursStart,
            "quiet_hours_end": preferences.quietHoursEnd,
            "timezone": preferences.timezone,
            "digest_mode": preferences.digestMode,
            "max_notifications_per_digest": preferences.maxNotificationsPerDigest,
            "include_read_items": preferences.includeReadItems,
            "auto_archive_after_days": preferences.autoArchiveAfterDays
        ]
        return try await postV1Auth(url: APIEndpoints.notificationPreferences, body: body, secret: secret)
    }

    func fetchNotificationCategoryOverrides() async throws -> [NotificationCategoryPreference] {
        return try await getV1(url: APIEndpoints.notificationPreferenceCategories)
    }

    func updateNotificationCategoryOverride(_ override: NotificationCategoryPreference, secret: String) async throws -> NotificationCategoryPreference {
        var body: [String: Any] = [
            "enabled": override.enabled,
            "digest_only": override.digestOnly
        ]
        if let minimumSeverity = override.minimumSeverity { body["minimum_severity"] = minimumSeverity }
        if let quietHoursOverride = override.quietHoursOverride { body["quiet_hours_override"] = quietHoursOverride }
        return try await postV1Auth(url: APIEndpoints.notificationPreferenceCategory(override.category), body: body, secret: secret)
    }

    func fetchNotificationDigest(mode: String) async throws -> NotificationDigestResponse {
        return try await getV1(url: APIEndpoints.notificationDigest(mode: mode))
    }

    func fetchSystemReleaseCheck(mode: String) async throws -> SystemReleaseCheckResponse {
        return try await getV1(url: APIEndpoints.systemReleaseCheck(mode: mode))
    }

    func fetchSystemRoutes() async throws -> SystemRoutesResponse {
        return try await getV1(url: APIEndpoints.systemRoutes)
    }

    func fetchSystemFlags() async throws -> SystemFlagsResponse {
        return try await getV1(url: APIEndpoints.systemFlags)
    }

    // MARK: - Backups (Phase I30 / A29)

    func fetchBackupList(limit: Int = 20) async throws -> BackupListResponse {
        return try await getV1(url: "\(APIEndpoints.backupList)?limit=\(limit)")
    }

    func fetchBackupDetail(id: String) async throws -> BackupManifestEntry {
        return try await getV1(url: APIEndpoints.backupDetail(id))
    }

    func createBackup(backupType: String, notes: String?, secret: String) async throws -> BackupManifestEntry {
        var body: [String: Any] = ["backup_type": backupType]
        if let notes { body["notes"] = notes }
        return try await postV1Auth(url: APIEndpoints.backupCreate, body: body, secret: secret)
    }

    func verifyBackup(id: String, secret: String) async throws -> BackupVerifyResponse {
        return try await postV1Auth(url: APIEndpoints.backupVerify(id), body: [:], secret: secret)
    }

    func fetchBackupDownloadInfo(id: String) async throws -> BackupManifestEntry {
        return try await getV1(url: APIEndpoints.backupDownloadInfo(id))
    }

    func fetchBackupRestorePreview(id: String, secret: String) async throws -> BackupRestorePreviewResponse {
        return try await postV1Auth(url: APIEndpoints.backupRestorePreview(id), body: [:], secret: secret)
    }

    // MARK: - Operator / Alpha L3 Proposals

    func fetchAlphaProposals(includeHistorical: Bool = false) async throws -> AlphaProposalsResponse {
        var url = APIEndpoints.alphaProposals
        if includeHistorical { url += "?include_historical=true" }
        return try await getV1(url: url)
    }

    func generateAlphaProposals(secret: String) async throws -> AlphaProposalsGenerateResponse {
        return try await postV1Auth(url: APIEndpoints.alphaProposalsGenerate, body: [:], secret: secret)
    }

    func approveAlphaProposal(id: String, note: String?, secret: String) async throws -> AlphaProposalActionResponse {
        var body: [String: Any] = [:]
        if let note { body["note"] = note }
        return try await postV1Auth(url: APIEndpoints.alphaProposalsApproveShadow(id), body: body, secret: secret)
    }

    func rejectAlphaProposal(id: String, reason: String?, secret: String) async throws -> AlphaProposalActionResponse {
        var body: [String: Any] = [:]
        if let reason { body["reason"] = reason }
        return try await postV1Auth(url: APIEndpoints.alphaProposalsReject(id), body: body, secret: secret)
    }

    func fetchAlphaProposalShadowResults(id: String) async throws -> AlphaProposalShadowResults {
        return try await getV1(url: APIEndpoints.alphaProposalShadowResults(id))
    }

    // MARK: - Cash

    func fetchCash() async throws -> Double {
        struct CashResponse: Decodable { let availableCash: Double }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let reqURL = URL(string: APIEndpoints.cash) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw NetworkError.serverError(0, "Failed to fetch cash")
        }
        return (try decoder.decode(CashResponse.self, from: data)).availableCash
    }

    func updateCash(amount: Double) async throws {
        let body: [String: Any] = ["cash": amount]
        struct OKResponse: Decodable { let success: Bool; let error: String? }
        let response: OKResponse = try await postAny(url: APIEndpoints.cash, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to update cash")
        }
    }

    // MARK: - Generic GET

    private func get<T: Decodable>(url: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(request: request)
    }

    private func getV1<T: Decodable>(url: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let envelope: APIEnvelope<T> = try await perform(request: request)
        guard envelope.ok, let data = envelope.data else {
            throw NetworkError.serverError(envelope.error?.code ?? 500, envelope.error?.message ?? "API request failed")
        }
        return data
    }

    // MARK: - Generic POST (Encodable body)

    private func post<T: Decodable, B: Encodable>(url: String, body: B) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(body)
        return try await perform(request: request)
    }

    // MARK: - Authenticated POST for v1 envelope endpoints (auth = Bearer token)

    private func postV1Auth<T: Decodable>(url: String, body: [String: Any], secret: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
        request.httpBody = body.isEmpty ? "{}".data(using: .utf8) : try JSONSerialization.data(withJSONObject: body)
        let envelope: APIEnvelope<T> = try await perform(request: request)
        guard envelope.ok, let data = envelope.data else {
            throw NetworkError.serverError(envelope.error?.code ?? 500, envelope.error?.message ?? "API request failed")
        }
        return data
    }

    private func postV1<T: Decodable>(url: String, body: [String: Any]) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = body.isEmpty ? "{}".data(using: .utf8) : try JSONSerialization.data(withJSONObject: body)
        let envelope: APIEnvelope<T> = try await perform(request: request)
        guard envelope.ok, let data = envelope.data else {
            throw NetworkError.serverError(envelope.error?.code ?? 500, envelope.error?.message ?? "API request failed")
        }
        return data
    }

    // MARK: - Generic POST (Any body)

    private func postAny<T: Decodable>(url: String, body: [String: Any]) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await perform(request: request)
    }

    // MARK: - Perform

    private func perform<T: Decodable>(request: URLRequest) async throws -> T {
        do {
            return try await performOnce(request: request)
        } catch {
            guard shouldRetry(error) else { throw error }
            try? await Task.sleep(nanoseconds: 350_000_000)
            return try await performOnce(request: request)
        }
    }

    private func performOnce<T: Decodable>(request: URLRequest) async throws -> T {
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw NetworkError.noData }
            guard (200...299).contains(http.statusCode) else {
                let message = String(data: data, encoding: .utf8) ?? "Unknown error"
                throw NetworkError.serverError(http.statusCode, message)
            }
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw NetworkError.decodingError(error)
            }
        } catch let error as NetworkError {
            throw error
        } catch let error as URLError where error.code == .timedOut {
            throw NetworkError.timeout
        } catch let error as URLError where error.code == .notConnectedToInternet || error.code == .networkConnectionLost {
            throw NetworkError.offline
        } catch {
            throw NetworkError.networkError(error)
        }
    }

    private func shouldRetry(_ error: Error) -> Bool {
        if case NetworkError.timeout = error { return true }
        if case NetworkError.offline = error { return false }
        if case NetworkError.networkError = error { return true }
        if case NetworkError.serverError(let code, _) = error { return code == 408 || code == 429 || code >= 500 }
        return false
    }
}

struct APIEnvelope<T: Decodable>: Decodable {
    let ok: Bool
    let data: T?
    let error: APIEnvelopeError?
}

struct APIEnvelopeError: Decodable {
    let code: Int
    let message: String
}
