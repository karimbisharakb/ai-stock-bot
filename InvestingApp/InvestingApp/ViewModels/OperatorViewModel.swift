import Foundation
import UIKit
import UserNotifications

struct CacheEntryStatus: Identifiable {
    let id: String
    let title: String
    let hasData: Bool
    let bytes: Int
}

private enum NotificationPlainLabels {
    static func category(_ value: String) -> String {
        switch value.uppercased() {
        case "BRIEF": return "Brief"
        case "ALPHA", "ALPHA_SIGNAL": return "Alpha"
        case "PORTFOLIO": return "Portfolio"
        case "RISK": return "Risk"
        case "REGIME", "MARKET": return "Regime"
        case "RESEARCH": return "Research"
        case "CATALYST": return "Catalyst"
        case "CHECKLIST", "COMPLIANCE": return "Checklist"
        case "WEEKLY_REVIEW", "PERFORMANCE": return "Weekly review"
        case "SYSTEM": return "System"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func severity(_ value: String) -> String {
        switch value.uppercased() {
        case "INFO", "DEBUG": return "Info"
        case "WATCH": return "Watch"
        case "WARNING": return "Warning"
        case "CRITICAL": return "Critical"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

@MainActor
final class OperatorViewModel: ObservableObject {
    @Published var backendHealth: BackendHealth?
    @Published var rootHealth: RootHealth?
    @Published var alphaCandidates: [AlphaCandidate] = []
    @Published var alphaReport: AlphaReport?
    @Published var alphaOutcomes: [AlphaOutcome] = []
    @Published var alphaLearning: AlphaLearning?
    @Published var alphaLearningRecommendations: AlphaLearningRecommendations?
    @Published var alphaShadowPolicy: AlphaShadowPolicy?
    @Published var alphaProposals: [AlphaProposal] = []
    @Published var alphaValidationSummary: AlphaValidationSummary?
    @Published var alphaValidations: [AlphaValidationRecord] = []
    @Published var alphaAlertCandidates: [AlphaAlertCandidate] = []
    @Published var alphaAlertGateSummary: AlphaAlertGateSummary?
    @Published var alphaDryRunNotifications: [AlphaDryRunNotification] = []
    @Published var alphaNotificationQCRecords: [AlphaNotificationQCRecord] = []
    @Published var alphaNotificationQCSummary: AlphaNotificationQCSummary?
    @Published var alphaDeliveryLog: [AlphaNotificationDeliveryEntry] = []
    @Published var alphaDeliveryFlags: AlphaNotificationDeliveryFlags?
    @Published var canonicalPortfolio: CanonicalPortfolioResponse?
    @Published var portfolioReconciliationRuns: [PortfolioReconciliationRun] = []
    @Published var portfolioSnapshots: [PortfolioSnapshot] = []
    @Published var manualPortfolio: ManualPortfolioResponse?
    @Published var portfolioTheses: [PositionThesis] = []
    @Published var selectedThesisDetail: PortfolioThesisDetailResponse?
    @Published var portfolioReviews: PortfolioReviewsResponse?
    @Published var decisionChecklists: [DecisionChecklist] = []
    @Published var selectedDecisionChecklist: DecisionChecklist?
    @Published var decisionSummary: DecisionSummaryResponse?
    @Published var portfolioRisk: PortfolioRiskReport?
    @Published var sizeCheck: DecisionSizeCheckResponse?
    @Published var marketRegime: MarketRegimeSnapshot?
    @Published var marketRegimeHistory: [MarketRegimeSnapshot] = []
    @Published var replayRuns: [ReplayRun] = []
    @Published var selectedReplayRun: ReplayRun?
    @Published var replayEvents: [ReplayEvent] = []
    @Published var portfolioStressRun: PortfolioStressRun?
    @Published var portfolioStressHistory: [PortfolioStressRun] = []
    @Published var selectedStressScenario: PortfolioStressScenario?
    @Published var strategyScorecards: [StrategyScorecard] = []
    @Published var strategySummary: StrategySummaryResponse?
    @Published var selectedStrategyScorecard: StrategyScorecard?
    @Published var plannerSnapshot: PlannerSnapshot?
    @Published var plannerProjections: PlannerProjections?
    @Published var compactBrief: DailyBriefCompactResponse?
    @Published var detailedBrief: DailyBriefDetailedResponse?
    @Published var debugBrief: DailyBriefDebugResponse?
    @Published var briefLastRefresh: Date?
    @Published var eodCompactBrief: DailyBriefCompactResponse?
    @Published var eodDetailedBrief: DailyBriefDetailedResponse?
    @Published var eodDebugBrief: DailyBriefDebugResponse?
    @Published var eodBriefLastRefresh: Date?
    @Published var marketPulse: MarketPulseResponse?
    @Published var sectorPerformance: SectorPerformanceResponse?
    @Published var stockResearch: StockResearchResponse?
    @Published var etfResearch: ETFResearchResponse?
    @Published var macroResearch: MacroResearchResponse?
    @Published var marketNews: ResearchNewsResponse?
    @Published var tickerNews: ResearchNewsResponse?
    @Published var stockAIAnalysis: ResearchAIResponse?
    @Published var etfAIAnalysis: ResearchAIResponse?
    @Published var macroAIAnalysis: ResearchAIResponse?
    @Published var researchWatchlist: [ResearchWatchlistItem] = []
    @Published var researchWatchlistSuggestions: ResearchWatchlistSuggestionsResponse?
    @Published var selectedResearchWatchlistDetail: ResearchWatchlistDetail?
    @Published var researchWorkflowQueue: [ResearchWorkflowItem] = []
    @Published var researchWorkflowSummary: ResearchWorkflowSummary?
    @Published var weeklyReviewCompact: WeeklyReviewCompactResponse?
    @Published var weeklyReviewDetailed: WeeklyReviewDetailedResponse?
    @Published var weeklyReviewDebug: WeeklyReviewDebugResponse?
    @Published var weeklyReviewHistory: [WeeklyReviewHistoryEntry] = []
    @Published var weeklyReviewLastRefresh: Date?
    @Published var catalysts: [Catalyst] = []
    @Published var catalystSummary: CatalystSummaryResponse?
    @Published var selectedCatalyst: Catalyst?
    @Published var notifications: [InAppNotification] = []
    @Published var notificationSummary: NotificationSummaryResponse?
    @Published var selectedNotification: InAppNotification?
    @Published var notificationFilter = "all"
    @Published var notificationPreferences: NotificationPreferences?
    @Published var notificationCategoryPreferences: [NotificationCategoryPreference] = []
    @Published var notificationDigest: NotificationDigestResponse?
    @Published var notificationDigestMode = "daily"
    @Published var systemReleaseCompact: SystemReleaseCheckResponse?
    @Published var systemReleaseFull: SystemReleaseCheckResponse?
    @Published var systemRoutes: SystemRoutesResponse?
    @Published var systemFlags: SystemFlagsResponse?
    @Published var backupList: BackupListResponse?
    @Published var selectedBackup: BackupManifestEntry?
    @Published var backupVerifyResult: BackupVerifyResponse?
    @Published var backupRestorePreview: BackupRestorePreviewResponse?
    @Published var backupActionMessage: String?
    @Published var backupActionSuccess = false
    @Published var backupActionInProgress = false
    @Published var selectedBackupTypeForCreate = "FULL"
    @Published var backupCreateNotes = ""
    @Published var preferenceEnabledCategories: Set<String> = Set(["BRIEF", "ALPHA", "PORTFOLIO", "RISK", "REGIME", "RESEARCH", "CATALYST", "CHECKLIST", "WEEKLY_REVIEW", "SYSTEM"])
    @Published var preferenceMinimumSeverity = "INFO"
    @Published var preferenceQuietHoursEnabled = false
    @Published var preferenceQuietHoursStart = "22:00"
    @Published var preferenceQuietHoursEnd = "07:00"
    @Published var preferenceTimezone = "America/Toronto"
    @Published var preferenceDigestMode = "OFF"
    @Published var preferenceMaxNotificationsPerDigest = "20"
    @Published var preferenceIncludeReadItems = false
    @Published var preferenceAutoArchiveAfterDays = "7"
    @Published var localNotificationPermissionStatus = "not_requested"
    @Published var localNotificationsEnabled = false
    @Published var notifyCriticalOnly = true
    @Published var notifyCriticalWarning = true
    @Published var notifyUnreadAlpha = true
    @Published var notifyDailyBriefAvailable = true
    @Published var notifyResearchWorkflowDue = true
    @Published var notifyCatalystDueSoon = true
    @Published var showHistoricalProposals = false
    @Published var proposalShadowResults: [String: AlphaProposalShadowResults] = [:]
    @Published var proposalActionInProgress = false
    @Published var proposalActionMessage: String?
    @Published var proposalActionSuccess = false
    @Published var notificationActionInProgress = false
    @Published var notificationActionMessage: String?
    @Published var notificationActionSuccess = false
    @Published var deliveryActionInProgress = false
    @Published var deliveryActionMessage: String?
    @Published var deliveryActionSuccess = false
    @Published var portfolioActionInProgress = false
    @Published var portfolioActionMessage: String?
    @Published var portfolioActionSuccess = false
    @Published var manualActionInProgress = false
    @Published var manualActionMessage: String?
    @Published var manualActionSuccess = false
    @Published var thesisActionInProgress = false
    @Published var thesisActionMessage: String?
    @Published var thesisActionSuccess = false
    @Published var decisionActionInProgress = false
    @Published var decisionActionMessage: String?
    @Published var decisionActionSuccess = false
    @Published var regimeActionInProgress = false
    @Published var regimeActionMessage: String?
    @Published var regimeActionSuccess = false
    @Published var replayActionInProgress = false
    @Published var replayActionMessage: String?
    @Published var replayActionSuccess = false
    @Published var stressActionInProgress = false
    @Published var stressActionMessage: String?
    @Published var stressActionSuccess = false
    @Published var plannerActionInProgress = false
    @Published var plannerActionMessage: String?
    @Published var plannerActionSuccess = false
    @Published var researchActionInProgress = false
    @Published var researchActionMessage: String?
    @Published var researchActionSuccess = false
    @Published var plannerMonthlyContribution = "500"
    @Published var manualTicker = ""
    @Published var manualQuantity = ""
    @Published var manualAvgCost = ""
    @Published var manualRealizedPnL = ""
    @Published var manualAccountType = "TFSA"
    @Published var manualCurrency = "CAD"
    @Published var manualNote = ""
    @Published var accountName = ""
    @Published var accountType = "TFSA"
    @Published var accountBaseCurrency = "CAD"
    @Published var accountAvailableCash = ""
    @Published var accountContributionRoom = ""
    @Published var accountNotes = ""
    @Published var thesisTicker = ""
    @Published var thesisTitle = ""
    @Published var thesisText = ""
    @Published var thesisSetupType = ""
    @Published var thesisConviction = "MEDIUM"
    @Published var thesisTimeHorizon = "MEDIUM"
    @Published var thesisEntryReason = ""
    @Published var thesisExpectedCatalysts = ""
    @Published var thesisRiskFactors = ""
    @Published var thesisInvalidationLevel = ""
    @Published var thesisTargetLevel = ""
    @Published var thesisExitPlan = ""
    @Published var thesisReviewFrequencyDays = "30"
    @Published var thesisNextReviewAt = ""
    @Published var thesisStatus = "ACTIVE"
    @Published var journalEntryType = "NOTE"
    @Published var journalText = ""
    @Published var journalTags = ""
    @Published var journalConfidenceChange = ""
    @Published var decisionTicker = ""
    @Published var decisionType = "ENTER"
    @Published var decisionAlphaCandidateId = ""
    @Published var decisionThesisId = ""
    @Published var decisionRejectReason = ""
    @Published var sizeCheckTicker = ""
    @Published var sizeCheckDecisionType = "ENTER"
    @Published var replayStartDate = ""
    @Published var replayEndDate = ""
    @Published var replayTicker = ""
    @Published var replaySource = "Any"
    @Published var replaySetupType = ""
    @Published var replayMaxRows = "500"
    @Published var marketResearchPeriod = "1D"
    @Published var sectorResearchPeriod = "1D"
    @Published var stockResearchTicker = "AAPL"
    @Published var stockResearchPeriod = "1Y"
    @Published var etfResearchTicker = "QQQ"
    @Published var etfResearchPeriod = "1Y"
    @Published var newsTicker = "NVDA"
    @Published var watchlistTicker = ""
    @Published var watchlistName = ""
    @Published var watchlistAssetType = "STOCK"
    @Published var watchlistCategory = "LEARNING"
    @Published var watchlistStatus = "WATCHING"
    @Published var watchlistPriority = "MEDIUM"
    @Published var watchlistReason = ""
    @Published var watchlistNextReviewAt = ""
    @Published var watchlistLinkedAlphaCandidateId = ""
    @Published var watchlistLinkedThesisId = ""
    @Published var watchlistNoteType = "RESEARCH"
    @Published var watchlistNoteText = ""
    @Published var watchlistNoteTags = ""
    @Published var workflowSelectedItemId = ""
    @Published var workflowSnoozeValue = "24"
    @Published var workflowSnoozeUnit = "hours"
    @Published var workflowNoteText = ""
    @Published var catalystTickerFilter = ""
    @Published var catalystLookupId = ""
    @Published var catalystTicker = ""
    @Published var catalystTitle = ""
    @Published var catalystDescription = ""
    @Published var catalystType = "OTHER"
    @Published var catalystDate = ""
    @Published var catalystConfidence = "MEDIUM"
    @Published var catalystImportance = "MEDIUM"
    @Published var catalystLinkedEntityType = ""
    @Published var catalystLinkedEntityId = ""
    @Published var notificationLookupId = ""
    @Published var checklistItemNotes: [String: String] = [:]
    @Published var isLoading = false
    @Published var lastSync: Date?
    @Published var lastError: String?
    @Published var cacheStatus: [CacheEntryStatus] = []

    private let alphaCacheKey = "cached_alpha_top"
    private let alphaReportCacheKey = "cached_alpha_report"
    private let alphaOutcomesCacheKey = "cached_alpha_outcomes"
    private let alphaLearningCacheKey = "cached_alpha_learning"
    private let alphaLearningRecommendationsCacheKey = "cached_alpha_learning_recommendations"
    private let alphaShadowPolicyCacheKey = "cached_alpha_shadow_policy"
    private let alphaProposalsCacheKey = "cached_alpha_proposals"
    private let alphaValidationSummaryCacheKey = "cached_alpha_validation_summary"
    private let alphaValidationsCacheKey = "cached_alpha_validations"
    private let alphaAlertCandidatesCacheKey = "cached_alpha_alert_candidates"
    private let alphaAlertGateSummaryCacheKey = "cached_alpha_alert_gate_summary"
    private let alphaDryRunNotificationsCacheKey = "cached_alpha_dry_run_notifications"
    private let alphaNotificationQCRecordsCacheKey = "cached_alpha_notification_qc_records"
    private let alphaNotificationQCSummaryCacheKey = "cached_alpha_notification_qc_summary"
    private let alphaDeliveryLogCacheKey = "cached_alpha_delivery_log"
    private let alphaDeliveryFlagsCacheKey = "cached_alpha_delivery_flags"
    private let canonicalPortfolioCacheKey = "cached_canonical_portfolio"
    private let portfolioReconciliationCacheKey = "cached_portfolio_reconciliation"
    private let portfolioSnapshotsCacheKey = "cached_portfolio_snapshots"
    private let manualPortfolioCacheKey = "cached_manual_portfolio"
    private let portfolioThesesCacheKey = "cached_portfolio_theses"
    private let selectedThesisDetailCacheKey = "cached_portfolio_thesis_detail"
    private let portfolioReviewsCacheKey = "cached_portfolio_reviews"
    private let decisionChecklistsCacheKey = "cached_decision_checklists"
    private let selectedDecisionChecklistCacheKey = "cached_decision_checklist_detail"
    private let decisionSummaryCacheKey = "cached_decision_summary"
    private let portfolioRiskCacheKey = "cached_portfolio_risk"
    private let sizeCheckCacheKey = "cached_size_check"
    private let marketRegimeCacheKey = "cached_market_regime"
    private let marketRegimeHistoryCacheKey = "cached_market_regime_history"
    private let replayRunsCacheKey = "cached_replay_runs"
    private let selectedReplayRunCacheKey = "cached_replay_run_detail"
    private let replayEventsCacheKey = "cached_replay_events"
    private let portfolioStressCacheKey = "cached_portfolio_stress"
    private let portfolioStressHistoryCacheKey = "cached_portfolio_stress_history"
    private let selectedStressScenarioCacheKey = "cached_portfolio_stress_scenario"
    private let strategyScorecardsCacheKey = "cached_strategy_scorecards"
    private let strategySummaryCacheKey = "cached_strategy_summary"
    private let selectedStrategyScorecardCacheKey = "cached_strategy_detail"
    private let plannerSnapshotCacheKey = "cached_planner_snapshot"
    private let plannerProjectionsCacheKey = "cached_planner_projections"
    private let compactBriefCacheKey = "cached_daily_brief_compact"
    private let detailedBriefCacheKey = "cached_daily_brief_detailed"
    private let debugBriefCacheKey = "cached_daily_brief_debug"
    private let briefLastRefreshKey = "daily_brief_last_refresh"
    private let eodCompactBriefCacheKey = "cached_eod_brief_compact"
    private let eodDetailedBriefCacheKey = "cached_eod_brief_detailed"
    private let eodDebugBriefCacheKey = "cached_eod_brief_debug"
    private let eodBriefLastRefreshKey = "eod_brief_last_refresh"
    private let marketPulseCacheKey = "cached_market_pulse"
    private let sectorPerformanceCacheKey = "cached_sector_performance"
    private let stockResearchCacheKey = "cached_stock_research"
    private let etfResearchCacheKey = "cached_etf_research"
    private let macroResearchCacheKey = "cached_macro_research"
    private let marketNewsCacheKey = "cached_market_news"
    private let tickerNewsCacheKey = "cached_ticker_news"
    private let stockAIAnalysisCacheKey = "cached_stock_ai_analysis"
    private let etfAIAnalysisCacheKey = "cached_etf_ai_analysis"
    private let macroAIAnalysisCacheKey = "cached_macro_ai_analysis"
    private let researchWatchlistCacheKey = "cached_research_watchlist"
    private let researchWatchlistSuggestionsCacheKey = "cached_research_watchlist_suggestions"
    private let selectedResearchWatchlistDetailCacheKey = "cached_research_watchlist_detail"
    private let researchWorkflowQueueCacheKey = "cached_research_workflow_queue"
    private let researchWorkflowSummaryCacheKey = "cached_research_workflow_summary"
    private let weeklyReviewCompactCacheKey = "cached_weekly_review_compact"
    private let weeklyReviewDetailedCacheKey = "cached_weekly_review_detailed"
    private let weeklyReviewDebugCacheKey = "cached_weekly_review_debug"
    private let weeklyReviewHistoryCacheKey = "cached_weekly_review_history"
    private let weeklyReviewLastRefreshKey = "weekly_review_last_refresh"
    private let catalystsCacheKey = "cached_catalysts"
    private let catalystSummaryCacheKey = "cached_catalyst_summary"
    private let selectedCatalystCacheKey = "cached_catalyst_detail"
    private let notificationsCacheKey = "cached_notifications"
    private let notificationSummaryCacheKey = "cached_notification_summary"
    private let selectedNotificationCacheKey = "cached_notification_detail"
    private let notificationPreferencesCacheKey = "cached_notification_preferences"
    private let notificationCategoryPreferencesCacheKey = "cached_notification_category_preferences"
    private let notificationDigestCacheKey = "cached_notification_digest"
    private let systemReleaseCompactCacheKey = "cached_system_release_compact"
    private let systemReleaseFullCacheKey = "cached_system_release_full"
    private let systemRoutesCacheKey = "cached_system_routes"
    private let systemFlagsCacheKey = "cached_system_flags"
    private let backupListCacheKey = "cached_backup_list"
    private let localNotificationPermissionRequestedKey = "local_notifications_permission_requested"
    private let localNotificationsEnabledKey = "local_notifications_enabled"
    private let notifyCriticalOnlyKey = "local_notify_critical_only"
    private let notifyCriticalWarningKey = "local_notify_critical_warning"
    private let notifyUnreadAlphaKey = "local_notify_unread_alpha"
    private let notifyDailyBriefAvailableKey = "local_notify_daily_brief"
    private let notifyResearchWorkflowDueKey = "local_notify_research_workflow"
    private let notifyCatalystDueSoonKey = "local_notify_catalyst_due"
    private let localNotifiedIdsKey = "local_notified_notification_ids"
    private let lastSyncKey = "operator_last_successful_sync"
    private let signingReminderKey = "operator_signing_reminder_date"
    private let cacheKeys = [
        "cached_portfolio": "Portfolio",
        "cached_market": "Market",
        "cached_opportunities": "Opportunities",
        "cached_signals": "Feed",
        "cached_alpha_top": "Alpha",
        "cached_alpha_report": "Alpha Report",
        "cached_alpha_outcomes": "Alpha Outcomes",
        "cached_alpha_learning": "Alpha Learning",
        "cached_alpha_learning_recommendations": "Learning Recs",
        "cached_alpha_shadow_policy": "Shadow Policy",
        "cached_alpha_proposals": "Proposals",
        "cached_alpha_validation_summary": "Validation Summary",
        "cached_alpha_validations": "Validations",
        "cached_alpha_alert_candidates": "Alert Candidates",
        "cached_alpha_alert_gate_summary": "Alert Gate",
        "cached_alpha_dry_run_notifications": "Dry Runs",
        "cached_alpha_notification_qc_records": "Notification QC",
        "cached_alpha_notification_qc_summary": "QC Summary",
        "cached_alpha_delivery_log": "Delivery Log",
        "cached_alpha_delivery_flags": "Delivery Flags",
        "cached_canonical_portfolio": "Portfolio Truth",
        "cached_portfolio_reconciliation": "Reconciliation",
        "cached_portfolio_snapshots": "Snapshots",
        "cached_manual_portfolio": "Manual Portfolio",
        "cached_portfolio_theses": "Theses",
        "cached_portfolio_thesis_detail": "Thesis Detail",
        "cached_portfolio_reviews": "Reviews",
        "cached_decision_checklists": "Decision Checklists",
        "cached_decision_checklist_detail": "Checklist Detail",
        "cached_decision_summary": "Decision Summary",
        "cached_portfolio_risk": "Portfolio Risk",
        "cached_size_check": "Size Check",
        "cached_market_regime": "Market Regime",
        "cached_market_regime_history": "Regime History",
        "cached_replay_runs": "Replay Runs",
        "cached_replay_run_detail": "Replay Detail",
        "cached_replay_events": "Replay Events",
        "cached_portfolio_stress": "Stress Test",
        "cached_portfolio_stress_history": "Stress History",
        "cached_portfolio_stress_scenario": "Stress Scenario",
        "cached_strategy_scorecards": "Strategy Cards",
        "cached_strategy_summary": "Strategy Summary",
        "cached_strategy_detail": "Strategy Detail",
        "cached_planner_snapshot": "Planner",
        "cached_planner_projections": "Planner Projections",
        "cached_daily_brief_compact": "Brief Compact",
        "cached_daily_brief_detailed": "Brief Detailed",
        "cached_daily_brief_debug": "Brief Debug",
        "cached_eod_brief_compact": "EOD Brief",
        "cached_eod_brief_detailed": "EOD Detailed",
        "cached_eod_brief_debug": "EOD Debug",
        "cached_market_pulse": "Market Pulse",
        "cached_sector_performance": "Sectors",
        "cached_stock_research": "Stock Research",
        "cached_etf_research": "ETF Research",
        "cached_macro_research": "Macro",
        "cached_market_news": "Market News",
        "cached_ticker_news": "Ticker News",
        "cached_stock_ai_analysis": "Stock AI",
        "cached_etf_ai_analysis": "ETF AI",
        "cached_macro_ai_analysis": "Macro AI",
        "cached_research_watchlist": "Research Watchlist",
        "cached_research_watchlist_suggestions": "Watch Suggestions",
        "cached_research_watchlist_detail": "Watch Detail",
        "cached_research_workflow_queue": "Workflow Queue",
        "cached_research_workflow_summary": "Workflow Summary",
        "cached_weekly_review_compact": "Weekly Compact",
        "cached_weekly_review_detailed": "Weekly Detailed",
        "cached_weekly_review_debug": "Weekly Debug",
        "cached_weekly_review_history": "Weekly History",
        "cached_catalysts": "Catalysts",
        "cached_catalyst_summary": "Catalyst Summary",
        "cached_catalyst_detail": "Catalyst Detail",
        "cached_notifications": "Notifications",
        "cached_notification_summary": "Notification Summary",
        "cached_notification_detail": "Notification Detail",
        "cached_notification_preferences": "Notification Preferences",
        "cached_notification_category_preferences": "Notification Categories",
        "cached_notification_digest": "Notification Digest",
        "cached_system_release_compact": "System Health",
        "cached_system_release_full": "System Health Full",
        "cached_system_routes": "System Routes",
        "cached_system_flags": "System Flags",
        "cached_backup_list": "Backup List",
        "cached_watchlist": "Watchlist"
    ]

    init() {
        loadLocalState()
    }

    var apiSecretConfigured: Bool {
        !(UserDefaults.standard.string(forKey: "api_secret") ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var signingReminderDate: Date {
        get { UserDefaults.standard.object(forKey: signingReminderKey) as? Date ?? Calendar.current.date(byAdding: .day, value: 5, to: Date()) ?? Date() }
        set { UserDefaults.standard.set(newValue, forKey: signingReminderKey) }
    }

    var signingStatusText: String {
        let days = Calendar.current.dateComponents([.day], from: Date(), to: signingReminderDate).day ?? 0
        if days <= 0 { return "Rebuild in Xcode soon" }
        if days == 1 { return "Rebuild tomorrow" }
        return "Rebuild in \(days) days"
    }

    var buildInfo: String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "local"
        return "\(version) (\(build))"
    }

    var activeProposalCount: Int { alphaProposals.filter { $0.isActive }.count }
    var proposedCount: Int { alphaProposals.filter { $0.status == "PROPOSED" }.count }
    var validationRecordCount: Int { alphaValidations.count }
    var alertReadyCount: Int { alphaAlertCandidates.filter(\.alertReady).count }
    var activeDryRunCount: Int { alphaDryRunNotifications.filter { $0.status == "DRY_RUN" }.count }
    var qcAllowedCount: Int { alphaNotificationQCRecords.filter(\.allowNotification).count }
    var reviewedDryRunCount: Int { alphaDryRunNotifications.filter { $0.status == "REVIEWED" }.count }
    var canonicalPositions: [CanonicalPosition] { canonicalPortfolio?.positions ?? [] }
    var manualPositions: [ManualPortfolioPosition] { manualPortfolio?.positions ?? [] }
    var thesisJournalEntries: [PositionJournalEntry] { selectedThesisDetail?.journal ?? [] }
    var reviewDueCount: Int { portfolioReviews?.reviews.dueCount ?? 0 }
    var reviewOverdueCount: Int { portfolioReviews?.reviews.overdueCount ?? 0 }
    var thesisWarningCount: Int {
        (portfolioReviews?.warnings.missingThesis.count ?? 0) +
        (portfolioReviews?.warnings.staleThesis.count ?? 0) +
        (portfolioReviews?.warnings.missingExitPlan.count ?? 0)
    }
    var readyDecisionCount: Int {
        decisionChecklists.filter { $0.readiness == "READY_FOR_MANUAL_DECISION" || $0.checklistStatus == "READY" }.count
    }
    var averageChecklistCompletion: Double? {
        guard !decisionChecklists.isEmpty else { return nil }
        return decisionChecklists.map(\.checklistCompletion).reduce(0, +) / Double(decisionChecklists.count)
    }
    var commonBlockingItems: [(label: String, count: Int)] {
        let failedRequiredItems = decisionChecklists.flatMap(\.items).filter { $0.required && $0.passed == false }
        return Dictionary(grouping: failedRequiredItems, by: \.label)
            .map { ($0.key, $0.value.count) }
            .sorted { lhs, rhs in
                if lhs.1 == rhs.1 { return lhs.0 < rhs.0 }
                return lhs.1 > rhs.1
            }
    }
    var largestRiskPosition: TickerRiskRow? {
        portfolioRisk?.tickerRiskTable.max { $0.concentrationPct < $1.concentrationPct }
    }
    var largestCanonicalPosition: CanonicalPosition? {
        canonicalPositions.max { ($0.marketValue ?? 0) < ($1.marketValue ?? 0) }
    }

    var pendingOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "PENDING" }.count
    }

    var completedOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "COMPLETE" }.count
    }

    var staleOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "STALE" }.count
    }

    var sustainabilityLeaderboard: [(setup: String, rate: Double, count: Int)] {
        groupedValidationRates(matching: AlphaValidationBehavior.positive)
    }

    var fakeBreakoutLeaderboard: [(setup: String, rate: Double, count: Int)] {
        groupedValidationRates(matching: ["FAILED_BREAKOUT"])
    }

    func refreshAll() async {
        isLoading = true
        lastError = nil
        defer { isLoading = false }

        do {
            async let rootTask = NetworkManager.shared.fetchRootHealth()
            async let healthTask = NetworkManager.shared.fetchBackendHealth()
            async let alphaTask = NetworkManager.shared.fetchAlphaTop()
            async let reportTask = NetworkManager.shared.fetchAlphaReport()
            async let outcomesTask = NetworkManager.shared.fetchAlphaOutcomes()
            async let learningTask = NetworkManager.shared.fetchAlphaLearning()
            async let recommendationsTask = NetworkManager.shared.fetchAlphaLearningRecommendations()
            async let shadowPolicyTask = NetworkManager.shared.fetchAlphaShadowPolicy()
            async let proposalsTask = NetworkManager.shared.fetchAlphaProposals(includeHistorical: showHistoricalProposals)
            async let validationSummaryTask = NetworkManager.shared.fetchAlphaValidationSummary()
            async let validationsTask = NetworkManager.shared.fetchAlphaValidations()
            async let alertCandidatesTask = NetworkManager.shared.fetchAlphaAlertCandidates()
            async let alertGateSummaryTask = NetworkManager.shared.fetchAlphaAlertGateSummary()
            async let dryRunTask = NetworkManager.shared.fetchAlphaNotificationDryRuns()
            async let qcTask = NetworkManager.shared.fetchAlphaNotificationQC()
            async let qcSummaryTask = NetworkManager.shared.fetchAlphaNotificationQCSummary()
            async let deliveryLogTask = NetworkManager.shared.fetchAlphaNotificationDeliveryLog()
            async let canonicalPortfolioTask = NetworkManager.shared.fetchCanonicalPortfolio()
            async let portfolioReconciliationTask = NetworkManager.shared.fetchPortfolioReconciliation()
            async let portfolioSnapshotsTask = NetworkManager.shared.fetchPortfolioSnapshots()
            async let manualPortfolioTask = NetworkManager.shared.fetchManualPortfolio()
            async let portfolioThesesTask = NetworkManager.shared.fetchPortfolioTheses()
            async let portfolioReviewsTask = NetworkManager.shared.fetchPortfolioReviews()
            async let decisionChecklistsTask = NetworkManager.shared.fetchDecisionChecklists()
            async let decisionSummaryTask = NetworkManager.shared.fetchDecisionSummary()
            async let portfolioRiskTask = NetworkManager.shared.fetchPortfolioRisk()
            async let marketRegimeTask = NetworkManager.shared.fetchMarketRegime()
            async let marketRegimeHistoryTask = NetworkManager.shared.fetchMarketRegimeHistory()
            async let replayRunsTask = NetworkManager.shared.fetchReplayRuns()
            async let stressTask = NetworkManager.shared.fetchPortfolioStress()
            async let stressHistoryTask = NetworkManager.shared.fetchPortfolioStressHistory()
            async let strategyScorecardsTask = NetworkManager.shared.fetchStrategyScorecards()
            async let strategySummaryTask = NetworkManager.shared.fetchStrategySummary()
            async let plannerSummaryTask = NetworkManager.shared.fetchPlannerSummary()
            async let plannerProjectionsTask = NetworkManager.shared.fetchPlannerProjections()
            async let briefCompactTask = NetworkManager.shared.fetchDailyBriefCompact()
            async let briefDetailedTask = NetworkManager.shared.fetchDailyBriefDetailed()
            async let briefDebugTask = NetworkManager.shared.fetchDailyBriefDebug()

            let (root, health, alpha, report, outcomes, learning, recommendations, shadowPolicy, proposalsResponse, validationSummary, validationsResponse, alertCandidates, alertGateSummary, dryRuns, qc, qcSummary, deliveryLog, canonicalPortfolioResponse, reconciliationResponse, snapshotsResponse, manualPortfolioResponse, thesisResponse, reviewsResponse, decisionListResponse, decisionSummaryResponse, riskResponse, regimeResponse, regimeHistoryResponse, replayRunsResponse, stressResponse, stressHistoryResponse, strategyScorecardsResponse, strategySummaryResponse, plannerSummaryResponse, plannerProjectionsResponse, briefCompactResponse, briefDetailedResponse, briefDebugResponse) = try await (
                rootTask,
                healthTask,
                alphaTask,
                reportTask,
                outcomesTask,
                learningTask,
                recommendationsTask,
                shadowPolicyTask,
                proposalsTask,
                validationSummaryTask,
                validationsTask,
                alertCandidatesTask,
                alertGateSummaryTask,
                dryRunTask,
                qcTask,
                qcSummaryTask,
                deliveryLogTask,
                canonicalPortfolioTask,
                portfolioReconciliationTask,
                portfolioSnapshotsTask,
                manualPortfolioTask,
                portfolioThesesTask,
                portfolioReviewsTask,
                decisionChecklistsTask,
                decisionSummaryTask,
                portfolioRiskTask,
                marketRegimeTask,
                marketRegimeHistoryTask,
                replayRunsTask,
                stressTask,
                stressHistoryTask,
                strategyScorecardsTask,
                strategySummaryTask,
                plannerSummaryTask,
                plannerProjectionsTask,
                briefCompactTask,
                briefDetailedTask,
                briefDebugTask
            )
            rootHealth = root
            backendHealth = health
            alphaCandidates = alpha.results
            alphaReport = report
            alphaOutcomes = outcomes.results
            alphaLearning = learning
            alphaLearningRecommendations = recommendations
            alphaShadowPolicy = shadowPolicy
            alphaProposals = proposalsResponse.proposals
            alphaValidationSummary = validationSummary
            alphaValidations = validationsResponse.results
            alphaAlertCandidates = alertCandidates.results
            alphaAlertGateSummary = alertGateSummary
            alphaDryRunNotifications = dryRuns.results
            alphaNotificationQCRecords = qc.records
            alphaNotificationQCSummary = qcSummary
            alphaDeliveryLog = deliveryLog.entries
            alphaDeliveryFlags = deliveryLog.featureFlags
            canonicalPortfolio = canonicalPortfolioResponse
            portfolioReconciliationRuns = reconciliationResponse.runs
            portfolioSnapshots = snapshotsResponse.snapshots
            manualPortfolio = manualPortfolioResponse
            portfolioTheses = thesisResponse.theses
            portfolioReviews = reviewsResponse
            decisionChecklists = decisionListResponse.checklists
            decisionSummary = decisionSummaryResponse
            portfolioRisk = riskResponse
            marketRegime = regimeResponse.regime
            marketRegimeHistory = regimeHistoryResponse.history
            replayRuns = replayRunsResponse.runs
            if selectedReplayRun == nil {
                selectedReplayRun = replayRunsResponse.runs.first
            }
            portfolioStressRun = stressResponse.run
            portfolioStressHistory = stressHistoryResponse.runs
            if let scenarios = stressResponse.run?.scenarioEvents,
               !scenarios.contains(where: { $0.scenarioType == selectedStressScenario?.scenarioType }) {
                selectedStressScenario = scenarios.first
            } else if stressResponse.run?.scenarioEvents.isEmpty == true {
                selectedStressScenario = nil
            }
            strategyScorecards = strategyScorecardsResponse.scorecards
            strategySummary = strategySummaryResponse
            if let selected = selectedStrategyScorecard,
               !strategyScorecardsResponse.scorecards.contains(where: { $0.strategy == selected.strategy }) {
                selectedStrategyScorecard = strategyScorecardsResponse.scorecards.first
            } else if selectedStrategyScorecard == nil {
                selectedStrategyScorecard = strategyScorecardsResponse.scorecards.first
            }
            plannerSnapshot = plannerSummaryResponse.snapshot
            plannerProjections = plannerProjectionsResponse.projections
            compactBrief = briefCompactResponse
            detailedBrief = briefDetailedResponse
            debugBrief = briefDebugResponse
            briefLastRefresh = Date()
            syncAccountForm(from: manualPortfolioResponse.accountSettings)
            lastSync = Date()
            persistAlpha(alpha.results)
            persist(report, key: alphaReportCacheKey)
            persist(outcomes.results, key: alphaOutcomesCacheKey)
            persist(learning, key: alphaLearningCacheKey)
            persist(recommendations, key: alphaLearningRecommendationsCacheKey)
            persist(shadowPolicy, key: alphaShadowPolicyCacheKey)
            persist(proposalsResponse.proposals, key: alphaProposalsCacheKey)
            persist(validationSummary, key: alphaValidationSummaryCacheKey)
            persist(validationsResponse.results, key: alphaValidationsCacheKey)
            persist(alertCandidates.results, key: alphaAlertCandidatesCacheKey)
            persist(alertGateSummary, key: alphaAlertGateSummaryCacheKey)
            persist(dryRuns.results, key: alphaDryRunNotificationsCacheKey)
            persist(qc.records, key: alphaNotificationQCRecordsCacheKey)
            persist(qcSummary, key: alphaNotificationQCSummaryCacheKey)
            persist(deliveryLog.entries, key: alphaDeliveryLogCacheKey)
            persist(deliveryLog.featureFlags, key: alphaDeliveryFlagsCacheKey)
            persist(canonicalPortfolioResponse, key: canonicalPortfolioCacheKey)
            persist(reconciliationResponse.runs, key: portfolioReconciliationCacheKey)
            persist(snapshotsResponse.snapshots, key: portfolioSnapshotsCacheKey)
            persist(manualPortfolioResponse, key: manualPortfolioCacheKey)
            persist(thesisResponse.theses, key: portfolioThesesCacheKey)
            persist(reviewsResponse, key: portfolioReviewsCacheKey)
            persist(decisionListResponse.checklists, key: decisionChecklistsCacheKey)
            persist(decisionSummaryResponse, key: decisionSummaryCacheKey)
            persist(riskResponse, key: portfolioRiskCacheKey)
            persist(regimeResponse.regime, key: marketRegimeCacheKey)
            persist(regimeHistoryResponse.history, key: marketRegimeHistoryCacheKey)
            persist(replayRunsResponse.runs, key: replayRunsCacheKey)
            if let selectedReplayRun {
                persist(selectedReplayRun, key: selectedReplayRunCacheKey)
            }
            persist(stressResponse.run, key: portfolioStressCacheKey)
            persist(stressHistoryResponse.runs, key: portfolioStressHistoryCacheKey)
            if let selectedStressScenario {
                persist(selectedStressScenario, key: selectedStressScenarioCacheKey)
            }
            persist(strategyScorecardsResponse.scorecards, key: strategyScorecardsCacheKey)
            persist(strategySummaryResponse, key: strategySummaryCacheKey)
            if let selectedStrategyScorecard {
                persist(selectedStrategyScorecard, key: selectedStrategyScorecardCacheKey)
            }
            persist(plannerSummaryResponse.snapshot, key: plannerSnapshotCacheKey)
            persist(plannerProjectionsResponse.projections, key: plannerProjectionsCacheKey)
            persist(briefCompactResponse, key: compactBriefCacheKey)
            persist(briefDetailedResponse, key: detailedBriefCacheKey)
            persist(briefDebugResponse, key: debugBriefCacheKey)
            UserDefaults.standard.set(briefLastRefresh, forKey: briefLastRefreshKey)
            UserDefaults.standard.set(lastSync, forKey: lastSyncKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            lastError = error.localizedDescription
            loadAlphaCaches()
            updateCacheStatus()
            HapticManager.notification(.error)
        }
    }

    func refreshProposals() async {
        do {
            let response = try await NetworkManager.shared.fetchAlphaProposals(includeHistorical: showHistoricalProposals)
            alphaProposals = response.proposals
            persist(response.proposals, key: alphaProposalsCacheKey)
            updateCacheStatus()
        } catch {
            alphaProposals = load([AlphaProposal].self, key: alphaProposalsCacheKey) ?? alphaProposals
        }
    }

    func generateProposals() async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.generateAlphaProposals(secret: secret)
            proposalActionMessage = response.generated == 0
                ? "No new proposals — recommendations unchanged"
                : "\(response.generated) proposal(s) generated"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func approveProposal(id: String, note: String?) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            _ = try await NetworkManager.shared.approveAlphaProposal(id: id, note: note, secret: secret)
            proposalActionMessage = "Approved for shadow — live weights unchanged"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func rejectProposal(id: String, reason: String?) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            _ = try await NetworkManager.shared.rejectAlphaProposal(id: id, reason: reason, secret: secret)
            proposalActionMessage = "Proposal rejected"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func loadShadowResults(proposalId: String) async {
        do {
            let results = try await NetworkManager.shared.fetchAlphaProposalShadowResults(id: proposalId)
            proposalShadowResults[proposalId] = results
        } catch {
            // silently fail — view will show "not loaded" state
        }
    }

    func refreshNotificationDryRunsAndQC() async {
        do {
            async let dryRunTask = NetworkManager.shared.fetchAlphaNotificationDryRuns()
            async let qcTask = NetworkManager.shared.fetchAlphaNotificationQC()
            async let qcSummaryTask = NetworkManager.shared.fetchAlphaNotificationQCSummary()
            async let deliveryLogTask = NetworkManager.shared.fetchAlphaNotificationDeliveryLog()
            let (dryRuns, qc, qcSummary, deliveryLog) = try await (dryRunTask, qcTask, qcSummaryTask, deliveryLogTask)
            alphaDryRunNotifications = dryRuns.results
            alphaNotificationQCRecords = qc.records
            alphaNotificationQCSummary = qcSummary
            alphaDeliveryLog = deliveryLog.entries
            alphaDeliveryFlags = deliveryLog.featureFlags
            persist(dryRuns.results, key: alphaDryRunNotificationsCacheKey)
            persist(qc.records, key: alphaNotificationQCRecordsCacheKey)
            persist(qcSummary, key: alphaNotificationQCSummaryCacheKey)
            persist(deliveryLog.entries, key: alphaDeliveryLogCacheKey)
            persist(deliveryLog.featureFlags, key: alphaDeliveryFlagsCacheKey)
            updateCacheStatus()
        } catch {
            alphaDryRunNotifications = load([AlphaDryRunNotification].self, key: alphaDryRunNotificationsCacheKey) ?? alphaDryRunNotifications
            alphaNotificationQCRecords = load([AlphaNotificationQCRecord].self, key: alphaNotificationQCRecordsCacheKey) ?? alphaNotificationQCRecords
            alphaNotificationQCSummary = load(AlphaNotificationQCSummary.self, key: alphaNotificationQCSummaryCacheKey) ?? alphaNotificationQCSummary
            alphaDeliveryLog = load([AlphaNotificationDeliveryEntry].self, key: alphaDeliveryLogCacheKey) ?? alphaDeliveryLog
            alphaDeliveryFlags = load(AlphaNotificationDeliveryFlags.self, key: alphaDeliveryFlagsCacheKey) ?? alphaDeliveryFlags
        }
    }

    func refreshDeliveryLog() async {
        do {
            let response = try await NetworkManager.shared.fetchAlphaNotificationDeliveryLog()
            alphaDeliveryLog = response.entries
            alphaDeliveryFlags = response.featureFlags
            persist(response.entries, key: alphaDeliveryLogCacheKey)
            persist(response.featureFlags, key: alphaDeliveryFlagsCacheKey)
            updateCacheStatus()
        } catch {
            alphaDeliveryLog = load([AlphaNotificationDeliveryEntry].self, key: alphaDeliveryLogCacheKey) ?? alphaDeliveryLog
            alphaDeliveryFlags = load(AlphaNotificationDeliveryFlags.self, key: alphaDeliveryFlagsCacheKey) ?? alphaDeliveryFlags
        }
    }

    func generateDryRunNotifications() async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            notificationActionMessage = "API_SECRET not configured in Settings"
            notificationActionSuccess = false
            return
        }
        notificationActionInProgress = true
        notificationActionMessage = nil
        defer { notificationActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.generateAlphaDryRuns(secret: secret)
            notificationActionMessage = response.generated == 0
                ? "No new dry-run notifications"
                : "\(response.generated) dry-run notification(s) generated"
            notificationActionSuccess = true
            await refreshNotificationDryRunsAndQC()
            HapticManager.notification(.success)
        } catch {
            notificationActionMessage = error.localizedDescription
            notificationActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func reviewDryRun(id: String) async {
        await runDryRunAction {
            _ = try await NetworkManager.shared.reviewAlphaDryRun(id: id, note: "Reviewed in iOS app", secret: $0)
            return "Dry-run marked reviewed"
        }
    }

    func dismissDryRun(id: String) async {
        await runDryRunAction {
            _ = try await NetworkManager.shared.dismissAlphaDryRun(id: id, reason: "Dismissed in iOS app", secret: $0)
            return "Dry-run dismissed"
        }
    }

    private func runDryRunAction(_ action: (String) async throws -> String) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            notificationActionMessage = "API_SECRET not configured in Settings"
            notificationActionSuccess = false
            return
        }
        notificationActionInProgress = true
        notificationActionMessage = nil
        defer { notificationActionInProgress = false }
        do {
            notificationActionMessage = try await action(secret)
            notificationActionSuccess = true
            await refreshNotificationDryRunsAndQC()
            HapticManager.notification(.success)
        } catch {
            notificationActionMessage = error.localizedDescription
            notificationActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func sendReviewedDryRun(id: String) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            deliveryActionMessage = "API_SECRET not configured in Settings"
            deliveryActionSuccess = false
            return
        }
        deliveryActionInProgress = true
        deliveryActionMessage = nil
        defer { deliveryActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.sendAlphaNotification(dryRunId: id, secret: secret)
            deliveryActionMessage = "\(AlphaNotificationDelivery.label(for: response.status)): \(response.reason ?? "No reason")"
            deliveryActionSuccess = response.status == "SENT"
            await refreshDeliveryLog()
            HapticManager.notification(response.status == "SENT" ? .success : .warning)
        } catch {
            deliveryActionMessage = error.localizedDescription
            deliveryActionSuccess = false
            await refreshDeliveryLog()
            HapticManager.notification(.error)
        }
    }

    func refreshCanonicalPortfolio() async {
        do {
            async let portfolioTask = NetworkManager.shared.fetchCanonicalPortfolio()
            async let reconciliationTask = NetworkManager.shared.fetchPortfolioReconciliation()
            async let snapshotsTask = NetworkManager.shared.fetchPortfolioSnapshots()
            let (portfolio, reconciliation, snapshots) = try await (portfolioTask, reconciliationTask, snapshotsTask)
            canonicalPortfolio = portfolio
            portfolioReconciliationRuns = reconciliation.runs
            portfolioSnapshots = snapshots.snapshots
            persist(portfolio, key: canonicalPortfolioCacheKey)
            persist(reconciliation.runs, key: portfolioReconciliationCacheKey)
            persist(snapshots.snapshots, key: portfolioSnapshotsCacheKey)
            updateCacheStatus()
        } catch {
            canonicalPortfolio = load(CanonicalPortfolioResponse.self, key: canonicalPortfolioCacheKey) ?? canonicalPortfolio
            portfolioReconciliationRuns = load([PortfolioReconciliationRun].self, key: portfolioReconciliationCacheKey) ?? portfolioReconciliationRuns
            portfolioSnapshots = load([PortfolioSnapshot].self, key: portfolioSnapshotsCacheKey) ?? portfolioSnapshots
        }
    }

    func reconcilePortfolio() async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            portfolioActionMessage = "API_SECRET not configured in Settings"
            portfolioActionSuccess = false
            return
        }
        portfolioActionInProgress = true
        portfolioActionMessage = nil
        defer { portfolioActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.reconcilePortfolio(secret: secret)
            portfolioActionMessage = response.issues.isEmpty
                ? "Reconciled \(response.positionCount) position(s)"
                : "Reconciled with \(response.issues.count) issue(s)"
            portfolioActionSuccess = response.status == "OK"
            await refreshCanonicalPortfolio()
            HapticManager.notification(response.status == "OK" ? .success : .warning)
        } catch {
            portfolioActionMessage = error.localizedDescription
            portfolioActionSuccess = false
            await refreshCanonicalPortfolio()
            HapticManager.notification(.error)
        }
    }

    func refreshManualPortfolio() async {
        do {
            let response = try await NetworkManager.shared.fetchManualPortfolio()
            manualPortfolio = response
            syncAccountForm(from: response.accountSettings)
            persist(response, key: manualPortfolioCacheKey)
            updateCacheStatus()
        } catch {
            manualPortfolio = load(ManualPortfolioResponse.self, key: manualPortfolioCacheKey) ?? manualPortfolio
            if let settings = manualPortfolio?.accountSettings {
                syncAccountForm(from: settings)
            }
        }
    }

    func populateManualPositionForm(_ position: ManualPortfolioPosition) {
        manualTicker = position.ticker
        manualQuantity = String(format: "%.6g", position.quantity)
        manualAvgCost = String(format: "%.4f", position.avgCost)
        manualRealizedPnL = String(format: "%.2f", position.realizedPnL)
        manualAccountType = position.accountType
        manualCurrency = position.currency
        manualNote = position.note
    }

    func clearManualPositionForm() {
        manualTicker = ""
        manualQuantity = ""
        manualAvgCost = ""
        manualRealizedPnL = ""
        manualAccountType = accountType
        manualCurrency = accountBaseCurrency
        manualNote = ""
    }

    func upsertManualPosition() async {
        guard let secret = storedSecret() else {
            setManualError("API_SECRET not configured in Settings")
            return
        }
        let ticker = manualTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setManualError("Ticker is required")
            return
        }
        guard let quantity = Double(manualQuantity), quantity >= 0 else {
            setManualError("Quantity must be a non-negative number")
            return
        }
        guard let avgCost = Double(manualAvgCost), avgCost >= 0 else {
            setManualError("Average cost must be a non-negative number")
            return
        }
        let realized = Double(manualRealizedPnL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "0" : manualRealizedPnL)
        guard let realizedPnL = realized else {
            setManualError("Realized P&L must be numeric")
            return
        }
        guard ["TFSA", "CASH", "RRSP", "OTHER"].contains(manualAccountType) else {
            setManualError("Unsupported account type")
            return
        }
        guard ["CAD", "USD"].contains(manualCurrency) else {
            setManualError("Unsupported currency")
            return
        }
        manualActionInProgress = true
        manualActionMessage = nil
        defer { manualActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.upsertManualPosition(
                ticker: ticker,
                quantity: quantity,
                avgCost: avgCost,
                realizedPnL: realizedPnL,
                accountType: manualAccountType,
                currency: manualCurrency,
                note: manualNote,
                secret: secret
            )
            guard response.ok else {
                setManualError((response.errors ?? [response.error ?? "Validation failed"]).joined(separator: ", "))
                return
            }
            manualActionMessage = "Saved \(response.ticker ?? ticker)"
            manualActionSuccess = true
            clearManualPositionForm()
            await refreshManualPortfolio()
            HapticManager.notification(.success)
        } catch {
            setManualError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func deactivateManualPosition(ticker: String) async {
        guard let secret = storedSecret() else {
            setManualError("API_SECRET not configured in Settings")
            return
        }
        manualActionInProgress = true
        manualActionMessage = nil
        defer { manualActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.deactivateManualPosition(ticker: ticker, secret: secret)
            guard response.ok else {
                setManualError(response.error ?? response.errors?.joined(separator: ", ") ?? "Deactivate failed")
                return
            }
            manualActionMessage = "Deactivated \(ticker)"
            manualActionSuccess = true
            await refreshManualPortfolio()
            HapticManager.notification(.success)
        } catch {
            setManualError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func updateManualAccount() async {
        guard let secret = storedSecret() else {
            setManualError("API_SECRET not configured in Settings")
            return
        }
        guard let cash = Double(accountAvailableCash), cash >= 0 else {
            setManualError("Available cash must be a non-negative number")
            return
        }
        let roomText = accountContributionRoom.trimmingCharacters(in: .whitespacesAndNewlines)
        let room = roomText.isEmpty ? nil : Double(roomText)
        if roomText.isEmpty == false && room == nil {
            setManualError("Contribution room must be numeric")
            return
        }
        manualActionInProgress = true
        manualActionMessage = nil
        defer { manualActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.updateManualAccount(
                accountName: accountName,
                accountType: accountType,
                baseCurrency: accountBaseCurrency,
                availableCash: cash,
                contributionRoom: room,
                notes: accountNotes,
                secret: secret
            )
            guard response.ok else {
                setManualError(response.errors?.joined(separator: ", ") ?? "Account update failed")
                return
            }
            manualActionMessage = "Account settings updated"
            manualActionSuccess = true
            if let settings = response.settings {
                syncAccountForm(from: settings)
            }
            await refreshManualPortfolio()
            HapticManager.notification(.success)
        } catch {
            setManualError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func reconcileManualPortfolio() async {
        guard let secret = storedSecret() else {
            setManualError("API_SECRET not configured in Settings")
            return
        }
        manualActionInProgress = true
        manualActionMessage = nil
        defer { manualActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.reconcileManualPortfolio(secret: secret)
            manualActionMessage = response.issues.isEmpty
                ? "Manual reconcile updated \(response.positionCount) position(s)"
                : "Manual reconcile finished with \(response.issues.count) issue(s)"
            manualActionSuccess = response.status == "OK"
            await refreshManualPortfolio()
            await refreshCanonicalPortfolio()
            HapticManager.notification(response.status == "OK" ? .success : .warning)
        } catch {
            setManualError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshThesisSystem() async {
        do {
            async let thesesTask = NetworkManager.shared.fetchPortfolioTheses()
            async let reviewsTask = NetworkManager.shared.fetchPortfolioReviews()
            let (theses, reviews) = try await (thesesTask, reviewsTask)
            portfolioTheses = theses.theses
            portfolioReviews = reviews
            persist(theses.theses, key: portfolioThesesCacheKey)
            persist(reviews, key: portfolioReviewsCacheKey)
            updateCacheStatus()
        } catch {
            portfolioTheses = load([PositionThesis].self, key: portfolioThesesCacheKey) ?? portfolioTheses
            portfolioReviews = load(PortfolioReviewsResponse.self, key: portfolioReviewsCacheKey) ?? portfolioReviews
        }
    }

    func loadThesisDetail(ticker: String) async {
        let normalized = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !normalized.isEmpty else { return }
        thesisActionMessage = nil
        do {
            let detail = try await NetworkManager.shared.fetchPortfolioThesisDetail(ticker: normalized)
            selectedThesisDetail = detail
            persist(detail, key: selectedThesisDetailCacheKey)
            populateThesisForm(from: detail.thesis)
            updateCacheStatus()
        } catch {
            selectedThesisDetail = load(PortfolioThesisDetailResponse.self, key: selectedThesisDetailCacheKey)
            clearThesisForm(ticker: normalized)
            thesisActionMessage = error.localizedDescription
            thesisActionSuccess = false
        }
    }

    func populateThesisForm(from thesis: PositionThesis) {
        thesisTicker = thesis.ticker
        thesisTitle = thesis.thesisTitle
        thesisText = thesis.thesisText
        thesisSetupType = thesis.setupType
        thesisConviction = thesis.convictionLevel
        thesisTimeHorizon = thesis.timeHorizon
        thesisEntryReason = thesis.entryReason
        thesisExpectedCatalysts = thesis.expectedCatalysts
        thesisRiskFactors = thesis.riskFactors
        thesisInvalidationLevel = thesis.invalidationLevel.map { String(format: "%.4f", $0) } ?? ""
        thesisTargetLevel = thesis.targetLevel.map { String(format: "%.4f", $0) } ?? ""
        thesisExitPlan = thesis.exitPlan
        thesisReviewFrequencyDays = "\(thesis.reviewFrequencyDays)"
        thesisNextReviewAt = thesis.nextReviewAt ?? ""
        thesisStatus = thesis.status
    }

    func clearThesisForm(ticker: String = "") {
        thesisTicker = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        thesisTitle = ""
        thesisText = ""
        thesisSetupType = ""
        thesisConviction = "MEDIUM"
        thesisTimeHorizon = "MEDIUM"
        thesisEntryReason = ""
        thesisExpectedCatalysts = ""
        thesisRiskFactors = ""
        thesisInvalidationLevel = ""
        thesisTargetLevel = ""
        thesisExitPlan = ""
        thesisReviewFrequencyDays = "30"
        thesisNextReviewAt = ""
        thesisStatus = "ACTIVE"
        journalEntryType = "NOTE"
        journalText = ""
        journalTags = ""
        journalConfidenceChange = ""
    }

    func saveThesis() async {
        guard let secret = storedSecret() else {
            setThesisError("API_SECRET not configured in Settings")
            return
        }
        let ticker = thesisTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setThesisError("Ticker is required")
            return
        }
        guard ["LOW", "MEDIUM", "HIGH"].contains(thesisConviction) else {
            setThesisError("Unsupported conviction level")
            return
        }
        guard ["SHORT", "MEDIUM", "LONG"].contains(thesisTimeHorizon) else {
            setThesisError("Unsupported time horizon")
            return
        }
        guard ["ACTIVE", "WATCH", "CLOSED", "ARCHIVED"].contains(thesisStatus) else {
            setThesisError("Unsupported thesis status")
            return
        }
        guard let frequency = Int(thesisReviewFrequencyDays), frequency > 0 else {
            setThesisError("Review frequency must be a positive whole number")
            return
        }
        let invalidationText = thesisInvalidationLevel.trimmingCharacters(in: .whitespacesAndNewlines)
        let targetText = thesisTargetLevel.trimmingCharacters(in: .whitespacesAndNewlines)
        let invalidation = invalidationText.isEmpty ? nil : Double(invalidationText)
        let target = targetText.isEmpty ? nil : Double(targetText)
        if !invalidationText.isEmpty && invalidation == nil {
            setThesisError("Invalidation level must be numeric")
            return
        }
        if !targetText.isEmpty && target == nil {
            setThesisError("Target level must be numeric")
            return
        }

        thesisActionInProgress = true
        thesisActionMessage = nil
        defer { thesisActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.upsertPortfolioThesis(
                ticker: ticker,
                thesisTitle: thesisTitle,
                thesisText: thesisText,
                setupType: thesisSetupType,
                convictionLevel: thesisConviction,
                timeHorizon: thesisTimeHorizon,
                entryReason: thesisEntryReason,
                expectedCatalysts: thesisExpectedCatalysts,
                riskFactors: thesisRiskFactors,
                invalidationLevel: invalidation,
                targetLevel: target,
                exitPlan: thesisExitPlan,
                reviewFrequencyDays: frequency,
                nextReviewAt: thesisNextReviewAt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : thesisNextReviewAt,
                status: thesisStatus,
                secret: secret
            )
            guard response.ok else {
                setThesisError(response.errors?.joined(separator: ", ") ?? response.error ?? "Thesis save failed")
                return
            }
            thesisActionMessage = "Saved thesis for \(response.ticker ?? ticker)"
            thesisActionSuccess = true
            await refreshThesisSystem()
            await loadThesisDetail(ticker: ticker)
            HapticManager.notification(.success)
        } catch {
            setThesisError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func appendJournalEntry() async {
        guard let secret = storedSecret() else {
            setThesisError("API_SECRET not configured in Settings")
            return
        }
        let ticker = thesisTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setThesisError("Ticker is required")
            return
        }
        guard ["NOTE", "REVIEW", "THESIS_UPDATE", "RISK_UPDATE", "CATALYST_UPDATE", "EXIT_PLAN_UPDATE"].contains(journalEntryType) else {
            setThesisError("Unsupported journal entry type")
            return
        }
        let text = journalText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            setThesisError("Journal text is required")
            return
        }
        let tags = journalTags
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        thesisActionInProgress = true
        thesisActionMessage = nil
        defer { thesisActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.appendThesisJournalEntry(
                ticker: ticker,
                entryType: journalEntryType,
                text: text,
                tags: tags,
                confidenceChange: journalConfidenceChange,
                secret: secret
            )
            guard response.ok else {
                setThesisError(response.errors?.joined(separator: ", ") ?? response.error ?? "Journal submit failed")
                return
            }
            thesisActionMessage = "Journal entry added"
            thesisActionSuccess = true
            journalText = ""
            journalTags = ""
            journalConfidenceChange = ""
            await loadThesisDetail(ticker: ticker)
            await refreshThesisSystem()
            HapticManager.notification(.success)
        } catch {
            setThesisError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshDecisionSystem() async {
        do {
            async let listTask = NetworkManager.shared.fetchDecisionChecklists()
            async let summaryTask = NetworkManager.shared.fetchDecisionSummary()
            let (list, summary) = try await (listTask, summaryTask)
            decisionChecklists = list.checklists
            decisionSummary = summary
            persist(list.checklists, key: decisionChecklistsCacheKey)
            persist(summary, key: decisionSummaryCacheKey)
            updateCacheStatus()
        } catch {
            decisionChecklists = load([DecisionChecklist].self, key: decisionChecklistsCacheKey) ?? decisionChecklists
            decisionSummary = load(DecisionSummaryResponse.self, key: decisionSummaryCacheKey) ?? decisionSummary
        }
    }

    func loadDecisionChecklistDetail(id: String) async {
        decisionActionMessage = nil
        do {
            let checklist = try await NetworkManager.shared.fetchDecisionChecklistDetail(id: id)
            selectedDecisionChecklist = checklist
            persist(checklist, key: selectedDecisionChecklistCacheKey)
            syncChecklistItemNotes()
            updateCacheStatus()
        } catch {
            selectedDecisionChecklist = load(DecisionChecklist.self, key: selectedDecisionChecklistCacheKey)
            syncChecklistItemNotes()
            decisionActionMessage = error.localizedDescription
            decisionActionSuccess = false
        }
    }

    func clearDecisionChecklistForm() {
        decisionTicker = ""
        decisionType = "ENTER"
        decisionAlphaCandidateId = ""
        decisionThesisId = ""
        decisionRejectReason = ""
    }

    func createDecisionChecklist() async {
        guard let secret = storedSecret() else {
            setDecisionError("API_SECRET not configured in Settings")
            return
        }
        let ticker = decisionTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setDecisionError("Ticker is required")
            return
        }
        guard ["ENTER", "ADD", "REDUCE", "EXIT", "HOLD"].contains(decisionType) else {
            setDecisionError("Unsupported decision type")
            return
        }
        let thesisText = decisionThesisId.trimmingCharacters(in: .whitespacesAndNewlines)
        let thesisId = thesisText.isEmpty ? nil : Int(thesisText)
        if !thesisText.isEmpty && thesisId == nil {
            setDecisionError("Thesis id must be a whole number")
            return
        }

        decisionActionInProgress = true
        decisionActionMessage = nil
        defer { decisionActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.createDecisionChecklist(
                ticker: ticker,
                decisionType: decisionType,
                linkedAlphaCandidateId: decisionAlphaCandidateId,
                linkedThesisId: thesisId,
                secret: secret
            )
            guard response.ok else {
                setDecisionError(response.errors?.joined(separator: ", ") ?? response.error ?? "Checklist creation failed")
                return
            }
            decisionActionMessage = "Created checklist for \(ticker)"
            decisionActionSuccess = true
            clearDecisionChecklistForm()
            await refreshDecisionSystem()
            if let id = response.checklistId {
                await loadDecisionChecklistDetail(id: id)
            }
            HapticManager.notification(.success)
        } catch {
            setDecisionError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func updateDecisionChecklistItem(_ item: DecisionChecklistItem, passed: Bool?) async {
        guard let checklist = selectedDecisionChecklist else { return }
        guard let secret = storedSecret() else {
            setDecisionError("API_SECRET not configured in Settings")
            return
        }
        let note = checklistItemNotes[item.itemKey] ?? item.note
        decisionActionInProgress = true
        decisionActionMessage = nil
        defer { decisionActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.updateDecisionChecklistItem(
                checklistId: checklist.checklistId,
                itemKey: item.itemKey,
                passed: passed,
                note: note,
                secret: secret
            )
            guard response.ok else {
                setDecisionError(response.errors?.joined(separator: ", ") ?? response.error ?? "Checklist item update failed")
                return
            }
            decisionActionMessage = "Updated checklist item"
            decisionActionSuccess = true
            await loadDecisionChecklistDetail(id: checklist.checklistId)
            await refreshDecisionSystem()
            HapticManager.impact(.light)
        } catch {
            setDecisionError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func approveDecisionChecklist(id: String) async {
        await runDecisionStatusAction(id: id) { secret in
            _ = try await NetworkManager.shared.approveDecisionChecklist(id: id, secret: secret)
            return "Checklist approved. Approval does NOT place trades."
        }
    }

    func rejectDecisionChecklist(id: String) async {
        await runDecisionStatusAction(id: id) { secret in
            _ = try await NetworkManager.shared.rejectDecisionChecklist(id: id, reason: decisionRejectReason, secret: secret)
            return "Checklist rejected. No trades placed."
        }
    }

    func refreshPortfolioRisk() async {
        do {
            let risk = try await NetworkManager.shared.fetchPortfolioRisk()
            portfolioRisk = risk
            persist(risk, key: portfolioRiskCacheKey)
            updateCacheStatus()
        } catch {
            portfolioRisk = load(PortfolioRiskReport.self, key: portfolioRiskCacheKey) ?? portfolioRisk
        }
    }

    func runSizeCheck(ticker: String? = nil, decisionType: String? = nil) async {
        let resolvedTicker = (ticker ?? sizeCheckTicker).trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let resolvedType = (decisionType ?? sizeCheckDecisionType).trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !resolvedTicker.isEmpty else {
            setDecisionError("Ticker is required for size check")
            return
        }
        guard ["ENTER", "ADD", "REDUCE", "EXIT", "HOLD"].contains(resolvedType) else {
            setDecisionError("Unsupported decision type")
            return
        }
        decisionActionInProgress = true
        decisionActionMessage = nil
        defer { decisionActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.fetchDecisionSizeCheck(ticker: resolvedTicker, decisionType: resolvedType)
            sizeCheck = response
            sizeCheckTicker = response.ticker
            sizeCheckDecisionType = response.decisionType
            persist(response, key: sizeCheckCacheKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            sizeCheck = load(DecisionSizeCheckResponse.self, key: sizeCheckCacheKey) ?? sizeCheck
            setDecisionError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshMarketRegimeData() async {
        do {
            async let regimeTask = NetworkManager.shared.fetchMarketRegime()
            async let historyTask = NetworkManager.shared.fetchMarketRegimeHistory()
            let (regime, history) = try await (regimeTask, historyTask)
            marketRegime = regime.regime
            marketRegimeHistory = history.history
            persist(regime.regime, key: marketRegimeCacheKey)
            persist(history.history, key: marketRegimeHistoryCacheKey)
            updateCacheStatus()
        } catch {
            marketRegime = load(MarketRegimeSnapshot.self, key: marketRegimeCacheKey) ?? marketRegime
            marketRegimeHistory = load([MarketRegimeSnapshot].self, key: marketRegimeHistoryCacheKey) ?? marketRegimeHistory
        }
    }

    func runMarketRegimeRefresh() async {
        guard let secret = storedSecret() else {
            regimeActionMessage = "API_SECRET not configured in Settings"
            regimeActionSuccess = false
            return
        }
        regimeActionInProgress = true
        regimeActionMessage = nil
        defer { regimeActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.refreshMarketRegime(secret: secret)
            marketRegime = response.regime
            if let regime = response.regime {
                persist(regime, key: marketRegimeCacheKey)
                regimeActionMessage = "Regime refreshed: \(MarketRegimeLabels.label(regime.overallRegime))"
            } else {
                regimeActionMessage = "Regime refresh completed with no snapshot"
            }
            regimeActionSuccess = true
            await refreshMarketRegimeData()
            HapticManager.notification(.success)
        } catch {
            regimeActionMessage = error.localizedDescription
            regimeActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func refreshReplayRuns() async {
        do {
            let response = try await NetworkManager.shared.fetchReplayRuns()
            replayRuns = response.runs
            if selectedReplayRun == nil {
                selectedReplayRun = response.runs.first
            }
            persist(response.runs, key: replayRunsCacheKey)
            if let selectedReplayRun {
                persist(selectedReplayRun, key: selectedReplayRunCacheKey)
            }
            updateCacheStatus()
        } catch {
            replayRuns = load([ReplayRun].self, key: replayRunsCacheKey) ?? replayRuns
        }
    }

    func loadReplayRunDetail(id: String) async {
        do {
            async let detailTask = NetworkManager.shared.fetchReplayRunDetail(id: id)
            async let eventsTask = NetworkManager.shared.fetchReplayRunEvents(id: id)
            let (detail, events) = try await (detailTask, eventsTask)
            selectedReplayRun = detail.run
            replayEvents = events.events
            persist(detail.run, key: selectedReplayRunCacheKey)
            persist(events.events, key: replayEventsCacheKey)
            updateCacheStatus()
        } catch {
            selectedReplayRun = load(ReplayRun.self, key: selectedReplayRunCacheKey) ?? selectedReplayRun
            replayEvents = load([ReplayEvent].self, key: replayEventsCacheKey) ?? replayEvents
            replayActionMessage = error.localizedDescription
            replayActionSuccess = false
        }
    }

    func refreshReplayEvents() async {
        guard let runId = selectedReplayRun?.runId, !runId.isEmpty else {
            replayActionMessage = "Select a replay run first"
            replayActionSuccess = false
            return
        }
        do {
            let response = try await NetworkManager.shared.fetchReplayRunEvents(id: runId)
            replayEvents = response.events
            persist(response.events, key: replayEventsCacheKey)
            updateCacheStatus()
        } catch {
            replayEvents = load([ReplayEvent].self, key: replayEventsCacheKey) ?? replayEvents
            replayActionMessage = error.localizedDescription
            replayActionSuccess = false
        }
    }

    func createReplayRun() async {
        guard let secret = storedSecret() else {
            setReplayError("API_SECRET not configured in Settings")
            return
        }
        let start = replayStartDate.trimmingCharacters(in: .whitespacesAndNewlines)
        let end = replayEndDate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !start.isEmpty else {
            setReplayError("Start date is required")
            return
        }
        guard !end.isEmpty else {
            setReplayError("End date is required")
            return
        }
        guard start < end else {
            setReplayError("Start date must be before end date")
            return
        }
        guard let maxRows = Int(replayMaxRows.trimmingCharacters(in: .whitespacesAndNewlines)), maxRows > 0 else {
            setReplayError("Max rows must be a positive number")
            return
        }

        replayActionInProgress = true
        replayActionMessage = nil
        defer { replayActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.createReplayRun(
                startDate: start,
                endDate: end,
                ticker: replayTicker,
                source: replaySource == "Any" ? nil : replaySource,
                setupType: replaySetupType,
                maxRows: min(maxRows, 2000),
                secret: secret
            )
            replayActionMessage = "Replay \(response.runId) complete: \(response.eventCount) event(s)"
            replayActionSuccess = true
            await refreshReplayRuns()
            await loadReplayRunDetail(id: response.runId)
            HapticManager.notification(.success)
        } catch {
            setReplayError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshPortfolioStress() async {
        do {
            async let stressTask = NetworkManager.shared.fetchPortfolioStress()
            async let historyTask = NetworkManager.shared.fetchPortfolioStressHistory()
            let (stress, history) = try await (stressTask, historyTask)
            portfolioStressRun = stress.run
            portfolioStressHistory = history.runs
            if let scenarios = stress.run?.scenarioEvents,
               !scenarios.contains(where: { $0.scenarioType == selectedStressScenario?.scenarioType }) {
                selectedStressScenario = scenarios.first
            } else if stress.run?.scenarioEvents.isEmpty == true {
                selectedStressScenario = nil
            }
            persist(stress.run, key: portfolioStressCacheKey)
            persist(history.runs, key: portfolioStressHistoryCacheKey)
            if let selectedStressScenario {
                persist(selectedStressScenario, key: selectedStressScenarioCacheKey)
            }
            updateCacheStatus()
        } catch {
            portfolioStressRun = load(PortfolioStressRun.self, key: portfolioStressCacheKey) ?? portfolioStressRun
            portfolioStressHistory = load([PortfolioStressRun].self, key: portfolioStressHistoryCacheKey) ?? portfolioStressHistory
            selectedStressScenario = load(PortfolioStressScenario.self, key: selectedStressScenarioCacheKey) ?? selectedStressScenario
        }
    }

    func selectStressScenario(_ scenario: PortfolioStressScenario) {
        selectedStressScenario = scenario
        persist(scenario, key: selectedStressScenarioCacheKey)
        HapticManager.selection()
    }

    func runPortfolioStress() async {
        guard let secret = storedSecret() else {
            setStressError("API_SECRET not configured in Settings")
            return
        }
        stressActionInProgress = true
        stressActionMessage = nil
        defer { stressActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.runPortfolioStress(secret: secret)
            stressActionMessage = "Stress test complete: \(PortfolioStressLabels.scenario(response.report.worstScenario))"
            stressActionSuccess = true
            await refreshPortfolioStress()
            HapticManager.notification(.success)
        } catch {
            setStressError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshStrategies() async {
        do {
            async let cardsTask = NetworkManager.shared.fetchStrategyScorecards()
            async let summaryTask = NetworkManager.shared.fetchStrategySummary()
            let (cards, summary) = try await (cardsTask, summaryTask)
            strategyScorecards = cards.scorecards
            strategySummary = summary
            if let selected = selectedStrategyScorecard,
               !cards.scorecards.contains(where: { $0.strategy == selected.strategy }) {
                selectedStrategyScorecard = cards.scorecards.first
            } else if selectedStrategyScorecard == nil {
                selectedStrategyScorecard = cards.scorecards.first
            }
            persist(cards.scorecards, key: strategyScorecardsCacheKey)
            persist(summary, key: strategySummaryCacheKey)
            if let selectedStrategyScorecard {
                persist(selectedStrategyScorecard, key: selectedStrategyScorecardCacheKey)
            }
            updateCacheStatus()
        } catch {
            strategyScorecards = load([StrategyScorecard].self, key: strategyScorecardsCacheKey) ?? strategyScorecards
            strategySummary = load(StrategySummaryResponse.self, key: strategySummaryCacheKey) ?? strategySummary
            selectedStrategyScorecard = load(StrategyScorecard.self, key: selectedStrategyScorecardCacheKey) ?? selectedStrategyScorecard
        }
    }

    func loadStrategyDetail(strategy: String) async {
        do {
            let response = try await NetworkManager.shared.fetchStrategyDetail(strategy: strategy)
            selectedStrategyScorecard = response.scorecard
            persist(response.scorecard, key: selectedStrategyScorecardCacheKey)
            updateCacheStatus()
        } catch {
            selectedStrategyScorecard = load(StrategyScorecard.self, key: selectedStrategyScorecardCacheKey) ?? selectedStrategyScorecard
        }
    }

    func refreshPlannerData() async {
        do {
            async let summaryTask = NetworkManager.shared.fetchPlannerSummary()
            async let projectionsTask = NetworkManager.shared.fetchPlannerProjections()
            let (summary, projections) = try await (summaryTask, projectionsTask)
            plannerSnapshot = summary.snapshot
            plannerProjections = projections.projections
            persist(summary.snapshot, key: plannerSnapshotCacheKey)
            persist(projections.projections, key: plannerProjectionsCacheKey)
            if let monthly = projections.monthlyContribution ?? summary.snapshot?.monthlyContribution {
                plannerMonthlyContribution = String(format: "%.0f", monthly)
            }
            updateCacheStatus()
        } catch {
            plannerSnapshot = load(PlannerSnapshot.self, key: plannerSnapshotCacheKey) ?? plannerSnapshot
            plannerProjections = load(PlannerProjections.self, key: plannerProjectionsCacheKey) ?? plannerProjections
        }
    }

    func refreshPlannerSnapshot() async {
        guard let secret = storedSecret() else {
            setPlannerError("API_SECRET not configured in Settings")
            return
        }
        let trimmed = plannerMonthlyContribution.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let contribution = Double(trimmed), contribution >= 0 else {
            setPlannerError("Monthly contribution must be zero or higher")
            return
        }

        plannerActionInProgress = true
        plannerActionMessage = nil
        defer { plannerActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.refreshPlanner(monthlyContribution: contribution, secret: secret)
            plannerSnapshot = response.planner
            plannerProjections = response.planner.projections
            persist(response.planner, key: plannerSnapshotCacheKey)
            persist(response.planner.projections, key: plannerProjectionsCacheKey)
            plannerActionMessage = "Planner refreshed: \(PlannerLabels.urgency(response.planner.rebalanceUrgency))"
            plannerActionSuccess = true
            HapticManager.notification(.success)
            await refreshPlannerData()
        } catch {
            setPlannerError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func refreshDailyBrief() async {
        do {
            async let compactTask = NetworkManager.shared.fetchDailyBriefCompact()
            async let detailedTask = NetworkManager.shared.fetchDailyBriefDetailed()
            async let debugTask = NetworkManager.shared.fetchDailyBriefDebug()
            let (compact, detailed, debug) = try await (compactTask, detailedTask, debugTask)
            compactBrief = compact
            detailedBrief = detailed
            debugBrief = debug
            briefLastRefresh = Date()
            persist(compact, key: compactBriefCacheKey)
            persist(detailed, key: detailedBriefCacheKey)
            persist(debug, key: debugBriefCacheKey)
            UserDefaults.standard.set(briefLastRefresh, forKey: briefLastRefreshKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            compactBrief = load(DailyBriefCompactResponse.self, key: compactBriefCacheKey) ?? compactBrief
            detailedBrief = load(DailyBriefDetailedResponse.self, key: detailedBriefCacheKey) ?? detailedBrief
            debugBrief = load(DailyBriefDebugResponse.self, key: debugBriefCacheKey) ?? debugBrief
        }
    }

    func copyCompactBrief() {
        UIPasteboard.general.string = compactBrief?.brief ?? ""
        HapticManager.selection()
    }

    func refreshEODBrief() async {
        do {
            async let compactTask = NetworkManager.shared.fetchEODBriefCompact()
            async let detailedTask = NetworkManager.shared.fetchEODBriefDetailed()
            async let debugTask = NetworkManager.shared.fetchEODBriefDebug()
            let (compact, detailed, debug) = try await (compactTask, detailedTask, debugTask)
            eodCompactBrief = compact
            eodDetailedBrief = detailed
            eodDebugBrief = debug
            eodBriefLastRefresh = Date()
            persist(compact, key: eodCompactBriefCacheKey)
            persist(detailed, key: eodDetailedBriefCacheKey)
            persist(debug, key: eodDebugBriefCacheKey)
            UserDefaults.standard.set(eodBriefLastRefresh, forKey: eodBriefLastRefreshKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            eodCompactBrief = load(DailyBriefCompactResponse.self, key: eodCompactBriefCacheKey) ?? eodCompactBrief
            eodDetailedBrief = load(DailyBriefDetailedResponse.self, key: eodDetailedBriefCacheKey) ?? eodDetailedBrief
            eodDebugBrief = load(DailyBriefDebugResponse.self, key: eodDebugBriefCacheKey) ?? eodDebugBrief
            researchActionMessage = error.localizedDescription
            researchActionSuccess = false
        }
    }

    func copyEODCompactBrief() {
        UIPasteboard.general.string = eodCompactBrief?.brief ?? ""
        HapticManager.selection()
    }

    func refreshMarketPulse() async {
        await loadResearchData(cacheKey: marketPulseCacheKey, assign: { self.marketPulse = $0 }) {
            try await NetworkManager.shared.fetchMarketPulse(period: marketResearchPeriod)
        }
    }

    func refreshSectorPerformance() async {
        await loadResearchData(cacheKey: sectorPerformanceCacheKey, assign: { self.sectorPerformance = $0 }) {
            try await NetworkManager.shared.fetchSectorPerformance(period: sectorResearchPeriod)
        }
    }

    func refreshStockResearch() async {
        let ticker = stockResearchTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        stockResearchTicker = ticker
        await loadResearchData(cacheKey: stockResearchCacheKey, assign: { self.stockResearch = $0 }) {
            try await NetworkManager.shared.fetchStockResearch(ticker: ticker, period: stockResearchPeriod)
        }
    }

    func refreshETFResearch() async {
        let ticker = etfResearchTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("ETF ticker is required")
            return
        }
        etfResearchTicker = ticker
        await loadResearchData(cacheKey: etfResearchCacheKey, assign: { self.etfResearch = $0 }) {
            try await NetworkManager.shared.fetchETFResearch(ticker: ticker, period: etfResearchPeriod)
        }
    }

    func refreshMacroResearch() async {
        await loadResearchData(cacheKey: macroResearchCacheKey, assign: { self.macroResearch = $0 }) {
            try await NetworkManager.shared.fetchMacroResearch()
        }
    }

    func refreshMarketNews() async {
        await loadResearchData(cacheKey: marketNewsCacheKey, assign: { self.marketNews = $0 }) {
            try await NetworkManager.shared.fetchMarketNews()
        }
    }

    func refreshTickerNews() async {
        let ticker = newsTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        newsTicker = ticker
        await loadResearchData(cacheKey: tickerNewsCacheKey, assign: { self.tickerNews = $0 }) {
            try await NetworkManager.shared.fetchTickerNews(ticker: ticker)
        }
    }

    func generateStockAIAnalysis() async {
        let ticker = stockResearchTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        await runResearchAction(successMessage: "Educational stock analysis updated") {
            let response = try await NetworkManager.shared.generateAIStockResearch(ticker: ticker)
            stockAIAnalysis = response
            persist(response, key: stockAIAnalysisCacheKey)
        }
    }

    func generateETFAnalysis() async {
        let ticker = etfResearchTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("ETF ticker is required")
            return
        }
        await runResearchAction(successMessage: "Educational ETF analysis updated") {
            let response = try await NetworkManager.shared.generateAIETFResearch(ticker: ticker)
            etfAIAnalysis = response
            persist(response, key: etfAIAnalysisCacheKey)
        }
    }

    func generateMacroAIAnalysis() async {
        await runResearchAction(successMessage: "Educational macro analysis updated") {
            let response = try await NetworkManager.shared.generateAIMacroResearch()
            macroAIAnalysis = response
            persist(response, key: macroAIAnalysisCacheKey)
        }
    }

    func refreshResearchWatchlist() async {
        await loadResearchData(cacheKey: researchWatchlistCacheKey, assign: { self.researchWatchlist = $0 }) {
            let response = try await NetworkManager.shared.fetchResearchWatchlist()
            return response.items
        }
    }

    func refreshResearchWatchlistSuggestions() async {
        await loadResearchData(cacheKey: researchWatchlistSuggestionsCacheKey, assign: { self.researchWatchlistSuggestions = $0 }) {
            try await NetworkManager.shared.fetchResearchWatchlistSuggestions()
        }
    }

    func loadResearchWatchlistDetail(ticker: String) async {
        let normalized = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !normalized.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        await loadResearchData(cacheKey: selectedResearchWatchlistDetailCacheKey, assign: { detail in
            self.selectedResearchWatchlistDetail = detail
            self.populateResearchWatchlistForm(detail.item)
        }) {
            try await NetworkManager.shared.fetchResearchWatchlistDetail(ticker: normalized)
        }
    }

    func populateResearchWatchlistForm(_ item: ResearchWatchlistItem) {
        watchlistTicker = item.ticker
        watchlistName = item.name ?? ""
        watchlistAssetType = item.assetType
        watchlistCategory = item.category
        watchlistStatus = item.status
        watchlistPriority = item.priority
        watchlistReason = item.reason
        watchlistNextReviewAt = item.nextReviewAt ?? ""
        watchlistLinkedAlphaCandidateId = item.linkedAlphaCandidateId.map(String.init) ?? ""
        watchlistLinkedThesisId = item.linkedThesisId.map(String.init) ?? ""
    }

    func populateResearchWatchlistForm(_ suggestion: ResearchWatchlistSuggestion) {
        watchlistTicker = suggestion.ticker ?? ""
        watchlistName = ""
        watchlistAssetType = "STOCK"
        watchlistCategory = suggestion.category
        watchlistStatus = "WATCHING"
        watchlistPriority = suggestion.priority
        watchlistReason = suggestion.reason
        watchlistNextReviewAt = ""
        watchlistLinkedAlphaCandidateId = ""
        watchlistLinkedThesisId = ""
    }

    func clearResearchWatchlistForm() {
        watchlistTicker = ""
        watchlistName = ""
        watchlistAssetType = "STOCK"
        watchlistCategory = "LEARNING"
        watchlistStatus = "WATCHING"
        watchlistPriority = "MEDIUM"
        watchlistReason = ""
        watchlistNextReviewAt = ""
        watchlistLinkedAlphaCandidateId = ""
        watchlistLinkedThesisId = ""
    }

    func saveResearchWatchlistItem() async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        let ticker = watchlistTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        let alphaIdText = watchlistLinkedAlphaCandidateId.trimmingCharacters(in: .whitespacesAndNewlines)
        let thesisIdText = watchlistLinkedThesisId.trimmingCharacters(in: .whitespacesAndNewlines)
        let alphaId = alphaIdText.isEmpty ? nil : Int(alphaIdText)
        let thesisId = thesisIdText.isEmpty ? nil : Int(thesisIdText)
        if !alphaIdText.isEmpty && alphaId == nil {
            setResearchError("Linked Alpha candidate must be numeric")
            return
        }
        if !thesisIdText.isEmpty && thesisId == nil {
            setResearchError("Linked thesis must be numeric")
            return
        }

        await runResearchAction(successMessage: "Saved \(ticker)") {
            let item = try await NetworkManager.shared.upsertResearchWatchlistItem(
                ticker: ticker,
                name: watchlistName,
                assetType: watchlistAssetType,
                category: watchlistCategory,
                status: watchlistStatus,
                priority: watchlistPriority,
                reason: watchlistReason,
                nextReviewAt: watchlistNextReviewAt,
                linkedAlphaCandidateId: alphaId,
                linkedThesisId: thesisId,
                secret: secret
            )
            populateResearchWatchlistForm(item)
            await refreshResearchWatchlist()
            await loadResearchWatchlistDetail(ticker: item.ticker)
        }
    }

    func appendResearchWatchlistNote() async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        let ticker = (selectedResearchWatchlistDetail?.item.ticker ?? watchlistTicker).trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            setResearchError("Select a ticker first")
            return
        }
        let text = watchlistNoteText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            setResearchError("Note text is required")
            return
        }
        let tags = watchlistNoteTags
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        await runResearchAction(successMessage: "Note added to \(ticker)") {
            _ = try await NetworkManager.shared.appendResearchWatchlistNote(
                ticker: ticker,
                noteType: watchlistNoteType,
                text: text,
                tags: tags,
                secret: secret
            )
            watchlistNoteText = ""
            watchlistNoteTags = ""
            await loadResearchWatchlistDetail(ticker: ticker)
        }
    }

    func archiveResearchWatchlistItem(ticker: String? = nil) async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        let normalized = (ticker ?? selectedResearchWatchlistDetail?.item.ticker ?? watchlistTicker)
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased()
        guard !normalized.isEmpty else {
            setResearchError("Ticker is required")
            return
        }
        await runResearchAction(successMessage: "Archived \(normalized)") {
            let item = try await NetworkManager.shared.archiveResearchWatchlistItem(ticker: normalized, secret: secret)
            populateResearchWatchlistForm(item)
            await refreshResearchWatchlist()
            await loadResearchWatchlistDetail(ticker: normalized)
        }
    }

    func refreshResearchWorkflowQueue() async {
        await loadResearchData(cacheKey: researchWorkflowQueueCacheKey, assign: { self.researchWorkflowQueue = $0 }) {
            let response = try await NetworkManager.shared.fetchResearchWorkflowQueue()
            return response.items
        }
    }

    func refreshResearchWorkflowSummary() async {
        await loadResearchData(cacheKey: researchWorkflowSummaryCacheKey, assign: { self.researchWorkflowSummary = $0 }) {
            try await NetworkManager.shared.fetchResearchWorkflowSummary()
        }
    }

    func startResearchWorkflowItem(_ itemId: String) async {
        await runWorkflowItemAction(successMessage: "Started workflow item") { secret in
            _ = try await NetworkManager.shared.startResearchWorkflowItem(itemId: itemId, secret: secret)
        }
    }

    func completeResearchWorkflowItem(_ itemId: String) async {
        await runWorkflowItemAction(successMessage: "Marked done") { secret in
            _ = try await NetworkManager.shared.completeResearchWorkflowItem(itemId: itemId, secret: secret)
        }
    }

    func snoozeResearchWorkflowItem(_ itemId: String) async {
        let trimmed = workflowSnoozeValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let value = Int(trimmed), value > 0 else {
            setResearchError("Snooze value must be a positive number")
            return
        }
        let hours = workflowSnoozeUnit == "days" ? value * 24 : value
        await runWorkflowItemAction(successMessage: "Snoozed workflow item") { secret in
            _ = try await NetworkManager.shared.snoozeResearchWorkflowItem(itemId: itemId, hours: hours, secret: secret)
        }
    }

    func archiveResearchWorkflowItem(_ itemId: String) async {
        await runWorkflowItemAction(successMessage: "Archived workflow item") { secret in
            _ = try await NetworkManager.shared.archiveResearchWorkflowItem(itemId: itemId, secret: secret)
        }
    }

    func appendResearchWorkflowNote(_ itemId: String) async {
        let text = workflowNoteText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            setResearchError("Workflow note text is required")
            return
        }
        await runWorkflowItemAction(successMessage: "Workflow note added") { secret in
            _ = try await NetworkManager.shared.appendResearchWorkflowNote(itemId: itemId, text: text, secret: secret)
            workflowNoteText = ""
        }
    }

    private func runWorkflowItemAction(successMessage: String, action: (String) async throws -> Void) async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        await runResearchAction(successMessage: successMessage) {
            try await action(secret)
            await refreshResearchWorkflowQueue()
            await refreshResearchWorkflowSummary()
        }
    }

    func refreshWeeklyReview() async {
        do {
            async let compactTask = NetworkManager.shared.fetchWeeklyReviewCompact()
            async let detailedTask = NetworkManager.shared.fetchWeeklyReviewDetailed()
            async let debugTask = NetworkManager.shared.fetchWeeklyReviewDebug()
            let (compact, detailed, debug) = try await (compactTask, detailedTask, debugTask)
            weeklyReviewCompact = compact
            weeklyReviewDetailed = detailed
            weeklyReviewDebug = debug
            weeklyReviewLastRefresh = Date()
            persist(compact, key: weeklyReviewCompactCacheKey)
            persist(detailed, key: weeklyReviewDetailedCacheKey)
            persist(debug, key: weeklyReviewDebugCacheKey)
            UserDefaults.standard.set(weeklyReviewLastRefresh, forKey: weeklyReviewLastRefreshKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            weeklyReviewCompact = load(WeeklyReviewCompactResponse.self, key: weeklyReviewCompactCacheKey) ?? weeklyReviewCompact
            weeklyReviewDetailed = load(WeeklyReviewDetailedResponse.self, key: weeklyReviewDetailedCacheKey) ?? weeklyReviewDetailed
            weeklyReviewDebug = load(WeeklyReviewDebugResponse.self, key: weeklyReviewDebugCacheKey) ?? weeklyReviewDebug
            setResearchError(error.localizedDescription)
        }
    }

    func refreshWeeklyReviewHistory() async {
        await loadResearchData(cacheKey: weeklyReviewHistoryCacheKey, assign: { self.weeklyReviewHistory = $0 }) {
            let response = try await NetworkManager.shared.fetchWeeklyReviewHistory()
            return response.history
        }
    }

    func copyWeeklyReviewCompact() {
        UIPasteboard.general.string = weeklyReviewCompact?.text ?? ""
        HapticManager.selection()
    }

    func refreshCatalysts() async {
        let ticker = catalystTickerFilter.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        await loadResearchData(cacheKey: catalystsCacheKey, assign: { self.catalysts = $0 }) {
            let response = try await NetworkManager.shared.fetchCatalysts(ticker: ticker.isEmpty ? nil : ticker)
            return response.catalysts
        }
    }

    func refreshCatalystSummary() async {
        await loadResearchData(cacheKey: catalystSummaryCacheKey, assign: { self.catalystSummary = $0 }) {
            try await NetworkManager.shared.fetchCatalystSummary()
        }
    }

    func loadCatalystDetail(id: String) async {
        let normalized = id.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            setResearchError("Catalyst id is required")
            return
        }
        await loadResearchData(cacheKey: selectedCatalystCacheKey, assign: { item in
            self.selectedCatalyst = item
            self.catalystLookupId = item.catalystId
            self.populateCatalystForm(item)
        }) {
            try await NetworkManager.shared.fetchCatalystDetail(id: normalized)
        }
    }

    func populateCatalystForm(_ item: Catalyst) {
        catalystTicker = item.ticker ?? ""
        catalystTitle = item.title
        catalystDescription = item.description
        catalystType = item.catalystType
        catalystDate = String(item.date.prefix(10))
        catalystConfidence = item.confidence
        catalystImportance = item.importance
        catalystLinkedEntityType = item.linkedEntityType ?? ""
        catalystLinkedEntityId = item.linkedEntityId ?? ""
        selectedCatalyst = item
        catalystLookupId = item.catalystId
    }

    func clearCatalystForm() {
        catalystTicker = ""
        catalystTitle = ""
        catalystDescription = ""
        catalystType = "OTHER"
        catalystDate = ""
        catalystConfidence = "MEDIUM"
        catalystImportance = "MEDIUM"
        catalystLinkedEntityType = ""
        catalystLinkedEntityId = ""
        selectedCatalyst = nil
        catalystLookupId = ""
    }

    func saveCatalyst() async {
        let title = catalystTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        let date = catalystDate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else {
            setResearchError("Catalyst title is required")
            return
        }
        guard !date.isEmpty else {
            setResearchError("Catalyst date is required")
            return
        }
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        await runResearchAction(successMessage: "Catalyst saved") {
            let item = try await NetworkManager.shared.upsertCatalyst(
                ticker: catalystTicker,
                title: title,
                description: catalystDescription.trimmingCharacters(in: .whitespacesAndNewlines),
                catalystType: catalystType,
                date: date,
                confidence: catalystConfidence,
                importance: catalystImportance,
                linkedEntityType: catalystLinkedEntityType,
                linkedEntityId: catalystLinkedEntityId,
                secret: secret
            )
            selectedCatalyst = item
            populateCatalystForm(item)
            persist(item, key: selectedCatalystCacheKey)
            await refreshCatalysts()
            await refreshCatalystSummary()
        }
    }

    func completeCatalyst(id: String) async {
        await runCatalystAction(id: id, successMessage: "Catalyst completed") { secret in
            try await NetworkManager.shared.completeCatalyst(id: id, secret: secret)
        }
    }

    func archiveCatalyst(id: String) async {
        await runCatalystAction(id: id, successMessage: "Catalyst archived") { secret in
            try await NetworkManager.shared.archiveCatalyst(id: id, secret: secret)
        }
    }

    var filteredNotifications: [InAppNotification] {
        switch notificationFilter {
        case "unread":
            return notifications.filter { $0.status == "UNREAD" }
        case "critical_warning":
            return notifications.filter { ["CRITICAL", "WARNING"].contains($0.severity) }
        case "alpha":
            return notifications.filter { $0.category == "ALPHA" || $0.category == "ALPHA_SIGNAL" }
        case "portfolio":
            return notifications.filter { $0.category == "PORTFOLIO" }
        case "risk":
            return notifications.filter { $0.category == "RISK" }
        case "research":
            return notifications.filter { $0.category == "RESEARCH" }
        case "catalyst":
            return notifications.filter { $0.category == "CATALYST" }
        case "checklist":
            return notifications.filter { $0.category == "CHECKLIST" || $0.category == "COMPLIANCE" }
        case "system":
            return notifications.filter { $0.category == "SYSTEM" }
        default:
            return notifications
        }
    }

    func refreshNotifications() async {
        await loadResearchData(cacheKey: notificationsCacheKey, assign: { self.notifications = $0 }) {
            try await NetworkManager.shared.fetchNotifications()
        }
        await scheduleQualifyingLocalNotifications()
    }

    func refreshNotificationSummary() async {
        await loadResearchData(cacheKey: notificationSummaryCacheKey, assign: { self.notificationSummary = $0 }) {
            try await NetworkManager.shared.fetchNotificationSummary()
        }
        await updateAppBadgeCount()
        await scheduleQualifyingLocalNotifications()
    }

    func loadNotificationDetail(id: String) async {
        let normalized = id.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            setResearchError("Notification id is required")
            return
        }
        await loadResearchData(cacheKey: selectedNotificationCacheKey, assign: { item in
            self.selectedNotification = item
            self.notificationLookupId = item.notificationId
        }) {
            try await NetworkManager.shared.fetchNotificationDetail(id: normalized)
        }
    }

    func generateInAppNotifications() async {
        await runNotificationAction(successMessage: "Notifications generated") { secret in
            _ = try await NetworkManager.shared.generateNotifications(secret: secret)
        }
    }

    func markNotificationRead(id: String) async {
        await runNotificationAction(successMessage: "Marked read") { secret in
            _ = try await NetworkManager.shared.markNotificationRead(id: id, secret: secret)
        }
    }

    func markNotificationUnread(id: String) async {
        await runNotificationAction(successMessage: "Marked unread") { secret in
            _ = try await NetworkManager.shared.markNotificationUnread(id: id, secret: secret)
        }
    }

    func dismissNotification(id: String) async {
        await runNotificationAction(successMessage: "Notification dismissed") { secret in
            _ = try await NetworkManager.shared.dismissNotification(id: id, secret: secret)
        }
    }

    func archiveNotification(id: String) async {
        await runNotificationAction(successMessage: "Notification archived") { secret in
            _ = try await NetworkManager.shared.archiveNotification(id: id, secret: secret)
        }
    }

    func markAllNotificationsRead() async {
        await runNotificationAction(successMessage: "All notifications marked read") { secret in
            _ = try await NetworkManager.shared.markAllNotificationsRead(secret: secret)
        }
    }

    func archiveReadNotifications() async {
        await runNotificationAction(successMessage: "Read notifications archived") { secret in
            _ = try await NetworkManager.shared.archiveReadNotifications(secret: secret)
        }
    }

    func refreshNotificationPreferences() async {
        await loadResearchData(cacheKey: notificationPreferencesCacheKey, assign: { prefs in
            self.notificationPreferences = prefs
            self.syncNotificationPreferenceForm(prefs)
        }) {
            try await NetworkManager.shared.fetchNotificationPreferences()
        }
    }

    func saveNotificationPreferences() async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        let maxCount = Int(preferenceMaxNotificationsPerDigest.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 20
        let archiveDays = Int(preferenceAutoArchiveAfterDays.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 7
        let prefs = NotificationPreferences(
            enabledCategories: notificationPreferenceCategories.filter { preferenceEnabledCategories.contains($0) },
            minimumSeverity: preferenceMinimumSeverity,
            quietHoursEnabled: preferenceQuietHoursEnabled,
            quietHoursStart: preferenceQuietHoursStart,
            quietHoursEnd: preferenceQuietHoursEnd,
            timezone: preferenceTimezone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "America/Toronto" : preferenceTimezone,
            digestMode: preferenceDigestMode,
            maxNotificationsPerDigest: max(1, maxCount),
            includeReadItems: preferenceIncludeReadItems,
            autoArchiveAfterDays: max(1, archiveDays),
            updatedAt: nil
        )
        await runResearchAction(successMessage: "Notification preferences saved") {
            let saved = try await NetworkManager.shared.updateNotificationPreferences(prefs, secret: secret)
            notificationPreferences = saved
            syncNotificationPreferenceForm(saved)
            persist(saved, key: notificationPreferencesCacheKey)
            await refreshNotifications()
            await refreshNotificationSummary()
            await refreshNotificationDigest()
        }
    }

    func refreshNotificationCategoryPreferences() async {
        await loadResearchData(cacheKey: notificationCategoryPreferencesCacheKey, assign: { overrides in
            self.notificationCategoryPreferences = self.mergedNotificationCategoryPreferences(overrides)
        }) {
            try await NetworkManager.shared.fetchNotificationCategoryOverrides()
        }
    }

    func updateNotificationCategoryPreference(_ override: NotificationCategoryPreference) async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        await runResearchAction(successMessage: "\(override.category) preference saved") {
            let saved = try await NetworkManager.shared.updateNotificationCategoryOverride(override, secret: secret)
            if let idx = notificationCategoryPreferences.firstIndex(where: { $0.category == saved.category }) {
                notificationCategoryPreferences[idx] = saved
            } else {
                notificationCategoryPreferences.append(saved)
            }
            persist(notificationCategoryPreferences, key: notificationCategoryPreferencesCacheKey)
            await refreshNotifications()
            await refreshNotificationSummary()
            await refreshNotificationDigest()
        }
    }

    func refreshNotificationDigest() async {
        await loadResearchData(cacheKey: notificationDigestCacheKey, assign: { self.notificationDigest = $0 }) {
            try await NetworkManager.shared.fetchNotificationDigest(mode: notificationDigestMode)
        }
    }

    func refreshSystemReleaseCompact() async {
        await loadResearchData(cacheKey: systemReleaseCompactCacheKey, assign: { self.systemReleaseCompact = $0 }) {
            try await NetworkManager.shared.fetchSystemReleaseCheck(mode: "compact")
        }
    }

    func refreshSystemReleaseFull() async {
        await loadResearchData(cacheKey: systemReleaseFullCacheKey, assign: { self.systemReleaseFull = $0 }) {
            try await NetworkManager.shared.fetchSystemReleaseCheck(mode: "full")
        }
    }

    func refreshSystemRoutes() async {
        await loadResearchData(cacheKey: systemRoutesCacheKey, assign: { self.systemRoutes = $0 }) {
            try await NetworkManager.shared.fetchSystemRoutes()
        }
    }

    func refreshSystemFlags() async {
        await loadResearchData(cacheKey: systemFlagsCacheKey, assign: { self.systemFlags = $0 }) {
            try await NetworkManager.shared.fetchSystemFlags()
        }
    }

    func refreshSystemDiagnostics() async {
        await refreshSystemReleaseCompact()
        await refreshSystemRoutes()
        await refreshSystemFlags()
    }

    // MARK: - Backups (Phase I30 / A29)

    func refreshBackupList() async {
        await loadResearchData(cacheKey: backupListCacheKey, assign: { self.backupList = $0 }) {
            try await NetworkManager.shared.fetchBackupList()
        }
    }

    func createBackup() async {
        guard let secret = storedSecret() else {
            setBackupError("API_SECRET not configured in Settings")
            return
        }
        backupActionInProgress = true
        backupActionMessage = nil
        defer { backupActionInProgress = false }
        do {
            let notes = backupCreateNotes.trimmingCharacters(in: .whitespacesAndNewlines)
            let entry = try await NetworkManager.shared.createBackup(
                backupType: selectedBackupTypeForCreate,
                notes: notes.isEmpty ? nil : notes,
                secret: secret
            )
            selectedBackup = entry
            backupActionMessage = "Backup created: \(entry.backupId) (\(entry.status))"
            backupActionSuccess = true
            await refreshBackupList()
            HapticManager.notification(.success)
        } catch {
            setBackupError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func verifySelectedBackup() async {
        guard let id = selectedBackup?.backupId else {
            setBackupError("No backup selected — tap a backup in the Backups list first")
            return
        }
        guard let secret = storedSecret() else {
            setBackupError("API_SECRET not configured in Settings")
            return
        }
        backupActionInProgress = true
        backupActionMessage = nil
        defer { backupActionInProgress = false }
        do {
            let result = try await NetworkManager.shared.verifyBackup(id: id, secret: secret)
            backupVerifyResult = result
            if result.ok {
                backupActionMessage = "Verification passed"
                backupActionSuccess = true
            } else {
                setBackupError("Verification failed: \(result.error ?? "check results")")
            }
            if let updated = result.manifestEntry { selectedBackup = updated }
            await refreshBackupList()
            HapticManager.notification(result.ok ? .success : .error)
        } catch {
            setBackupError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func fetchRestorePreviewForSelectedBackup() async {
        guard let id = selectedBackup?.backupId else {
            setBackupError("No backup selected — tap a backup in the Backups list first")
            return
        }
        guard let secret = storedSecret() else {
            setBackupError("API_SECRET not configured in Settings")
            return
        }
        backupActionInProgress = true
        backupActionMessage = nil
        defer { backupActionInProgress = false }
        do {
            let result = try await NetworkManager.shared.fetchBackupRestorePreview(id: id, secret: secret)
            backupRestorePreview = result
            backupActionMessage = result.ok ? "Preview loaded — no restore performed" : "Preview failed"
            backupActionSuccess = result.ok
            HapticManager.notification(result.ok ? .success : .error)
        } catch {
            setBackupError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    private func setBackupError(_ message: String) {
        backupActionMessage = message
        backupActionSuccess = false
    }

    func refreshLocalNotificationPermissionStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        localNotificationPermissionStatus = localPermissionLabel(settings.authorizationStatus)
    }

    func requestLocalNotificationPermission() async {
        UserDefaults.standard.set(true, forKey: localNotificationPermissionRequestedKey)
        do {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            localNotificationsEnabled = allowed
            UserDefaults.standard.set(allowed, forKey: localNotificationsEnabledKey)
            await refreshLocalNotificationPermissionStatus()
            if allowed {
                await updateAppBadgeCount()
                await scheduleQualifyingLocalNotifications()
            }
        } catch {
            localNotificationsEnabled = false
            UserDefaults.standard.set(false, forKey: localNotificationsEnabledKey)
            localNotificationPermissionStatus = "denied"
            setResearchError(error.localizedDescription)
        }
    }

    func disableLocalNotifications() {
        localNotificationsEnabled = false
        UserDefaults.standard.set(false, forKey: localNotificationsEnabledKey)
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
    }

    func saveLocalNotificationSettings() {
        UserDefaults.standard.set(localNotificationsEnabled, forKey: localNotificationsEnabledKey)
        UserDefaults.standard.set(notifyCriticalOnly, forKey: notifyCriticalOnlyKey)
        UserDefaults.standard.set(notifyCriticalWarning, forKey: notifyCriticalWarningKey)
        UserDefaults.standard.set(notifyUnreadAlpha, forKey: notifyUnreadAlphaKey)
        UserDefaults.standard.set(notifyDailyBriefAvailable, forKey: notifyDailyBriefAvailableKey)
        UserDefaults.standard.set(notifyResearchWorkflowDue, forKey: notifyResearchWorkflowDueKey)
        UserDefaults.standard.set(notifyCatalystDueSoon, forKey: notifyCatalystDueSoonKey)
    }

    func sendTestLocalNotification() async {
        await refreshLocalNotificationPermissionStatus()
        guard localNotificationsEnabled, localNotificationPermissionStatus == "allowed" || localNotificationPermissionStatus == "provisional" else {
            setResearchError("Local notifications are not enabled or allowed")
            return
        }
        let content = UNMutableNotificationContent()
        content.title = "Operator test"
        content.subtitle = "System / Info"
        content.body = "Local notification test only. No backend send and no trades placed."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "operator-local-test-\(Date().timeIntervalSince1970)",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        )
        do {
            try await UNUserNotificationCenter.current().add(request)
            researchActionMessage = "Test local notification scheduled"
            researchActionSuccess = true
        } catch {
            setResearchError(error.localizedDescription)
        }
    }

    private func runCatalystAction(id: String, successMessage: String, action: (String) async throws -> Catalyst) async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        let normalized = id.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            setResearchError("Catalyst id is required")
            return
        }
        await runResearchAction(successMessage: successMessage) {
            let item = try await action(secret)
            selectedCatalyst = item
            populateCatalystForm(item)
            persist(item, key: selectedCatalystCacheKey)
            await refreshCatalysts()
            await refreshCatalystSummary()
        }
    }

    private func runNotificationAction(successMessage: String, action: (String) async throws -> Void) async {
        guard let secret = storedSecret() else {
            setResearchError("API_SECRET not configured in Settings")
            return
        }
        await runResearchAction(successMessage: successMessage) {
            try await action(secret)
            await refreshNotifications()
            await refreshNotificationSummary()
            if let id = selectedNotification?.notificationId {
                await loadNotificationDetail(id: id)
            }
        }
    }

    var notificationPreferenceCategories: [String] {
        ["BRIEF", "ALPHA", "PORTFOLIO", "RISK", "REGIME", "RESEARCH", "CATALYST", "CHECKLIST", "WEEKLY_REVIEW", "SYSTEM"]
    }

    private func syncNotificationPreferenceForm(_ prefs: NotificationPreferences) {
        preferenceEnabledCategories = Set(prefs.enabledCategories)
        preferenceMinimumSeverity = prefs.minimumSeverity
        preferenceQuietHoursEnabled = prefs.quietHoursEnabled
        preferenceQuietHoursStart = prefs.quietHoursStart
        preferenceQuietHoursEnd = prefs.quietHoursEnd
        preferenceTimezone = prefs.timezone
        preferenceDigestMode = prefs.digestMode
        preferenceMaxNotificationsPerDigest = "\(prefs.maxNotificationsPerDigest)"
        preferenceIncludeReadItems = prefs.includeReadItems
        preferenceAutoArchiveAfterDays = "\(prefs.autoArchiveAfterDays)"
    }

    private func mergedNotificationCategoryPreferences(_ overrides: [NotificationCategoryPreference]) -> [NotificationCategoryPreference] {
        notificationPreferenceCategories.map { category in
            overrides.first { $0.category == category } ?? NotificationCategoryPreference(category: category)
        }
    }

    private func scheduleQualifyingLocalNotifications() async {
        saveLocalNotificationSettings()
        guard localNotificationsEnabled else { return }
        await refreshLocalNotificationPermissionStatus()
        guard localNotificationPermissionStatus == "allowed" || localNotificationPermissionStatus == "provisional" else { return }

        var notified = Set(UserDefaults.standard.stringArray(forKey: localNotifiedIdsKey) ?? [])
        let activeIds = Set(notifications.map(\.notificationId))
        if notified.count > 500 {
            notified = notified.intersection(activeIds)
        }

        for item in notifications where shouldScheduleLocalNotification(for: item, notifiedIds: notified) {
            let content = UNMutableNotificationContent()
            content.title = sanitizedNotificationText(item.title.isEmpty ? "Operator notification" : item.title)
            content.subtitle = "\(NotificationPlainLabels.category(item.category)) / \(NotificationPlainLabels.severity(item.severity))"
            content.body = sanitizedNotificationText(item.shortBody.isEmpty ? item.body : item.shortBody)
            content.sound = .default
            let request = UNNotificationRequest(
                identifier: "operator-n4-\(item.notificationId)",
                content: content,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
            )
            do {
                try await UNUserNotificationCenter.current().add(request)
                notified.insert(item.notificationId)
            } catch {
                continue
            }
        }

        UserDefaults.standard.set(Array(notified), forKey: localNotifiedIdsKey)
    }

    private func shouldScheduleLocalNotification(for item: InAppNotification, notifiedIds: Set<String>) -> Bool {
        guard item.status == "UNREAD", !notifiedIds.contains(item.notificationId) else { return false }
        if notifyCriticalOnly && item.severity == "CRITICAL" { return true }
        if notifyCriticalWarning && ["CRITICAL", "WARNING"].contains(item.severity) { return true }
        if notifyUnreadAlpha && ["ALPHA", "ALPHA_SIGNAL"].contains(item.category) { return true }
        let source = item.source.lowercased()
        if notifyDailyBriefAvailable && (item.category == "BRIEF" || source.contains("brief")) { return true }
        if notifyResearchWorkflowDue && (source.contains("workflow") || item.entityType?.lowercased().contains("workflow") == true) { return true }
        if notifyCatalystDueSoon && item.category == "CATALYST" { return true }
        return false
    }

    private func updateAppBadgeCount() async {
        guard localNotificationsEnabled else { return }
        let count = notificationSummary?.unreadCount ?? notifications.filter { $0.status == "UNREAD" }.count
        do {
            try await UNUserNotificationCenter.current().setBadgeCount(count)
        } catch {
            return
        }
    }

    private func sanitizedNotificationText(_ value: String) -> String {
        var text = value
        let replacements = [
            "buy": "review",
            "sell": "review",
            "order": "action",
            "trade": "decision"
        ]
        for (word, replacement) in replacements {
            text = text.replacingOccurrences(of: word, with: replacement, options: [.caseInsensitive, .regularExpression])
        }
        return text
    }

    private func localPermissionLabel(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .notDetermined:
            return UserDefaults.standard.bool(forKey: localNotificationPermissionRequestedKey) ? "not_requested" : "not_requested"
        case .denied: return "denied"
        case .authorized: return "allowed"
        case .provisional: return "provisional"
        case .ephemeral: return "unknown"
        @unknown default: return "unknown"
        }
    }

    private func loadResearchData<T: Codable>(
        cacheKey: String,
        assign: (T) -> Void,
        fetch: () async throws -> T
    ) async {
        do {
            let response = try await fetch()
            assign(response)
            persist(response, key: cacheKey)
            updateCacheStatus()
        } catch {
            if let cached = load(T.self, key: cacheKey) {
                assign(cached)
            }
            setResearchError(error.localizedDescription)
        }
    }

    private func runResearchAction(successMessage: String, action: () async throws -> Void) async {
        researchActionInProgress = true
        researchActionMessage = nil
        defer { researchActionInProgress = false }
        do {
            try await action()
            researchActionMessage = successMessage
            researchActionSuccess = true
            updateCacheStatus()
            HapticManager.notification(.success)
        } catch {
            setResearchError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    private func runDecisionStatusAction(id: String, _ action: (String) async throws -> String) async {
        guard let secret = storedSecret() else {
            setDecisionError("API_SECRET not configured in Settings")
            return
        }
        decisionActionInProgress = true
        decisionActionMessage = nil
        defer { decisionActionInProgress = false }
        do {
            decisionActionMessage = try await action(secret)
            decisionActionSuccess = true
            await loadDecisionChecklistDetail(id: id)
            await refreshDecisionSystem()
            HapticManager.notification(.success)
        } catch {
            setDecisionError(error.localizedDescription)
            HapticManager.notification(.error)
        }
    }

    func copyDebugInfo() {
        let lines = [
            "base=\(APIEndpoints.base)",
            "root_health=\(rootHealth?.status ?? "unknown")",
            "api_v1_health=\(backendHealth?.status ?? "unknown")",
            "db_connected=\(backendHealth?.dbConnected.description ?? "unknown")",
            "latest_scan=\(backendHealth?.latestScanTime ?? "none")",
            "alpha_candidates=\(alphaCandidates.count)",
            "alpha_outcomes=\(alphaOutcomes.count)",
            "alpha_complete=\(alphaLearning?.totalComplete.description ?? "unknown")",
            "learning_recommendations=\(alphaLearningRecommendations?.weightRecommendations.count.description ?? "unknown")",
            "shadow_changed=\(alphaShadowPolicy?.replayStats.changedCandidates.count.description ?? "unknown")",
            "validation_records=\(alphaValidations.count)",
            "alert_candidates=\(alphaAlertCandidates.count)",
            "alert_ready=\(alertReadyCount)",
            "alert_gate_total=\(alphaAlertGateSummary?.totalEvaluated.description ?? "unknown")",
            "dry_run_notifications=\(alphaDryRunNotifications.count)",
            "qc_records=\(alphaNotificationQCRecords.count)",
            "qc_summary_total=\(alphaNotificationQCSummary?.totalEvaluated.description ?? "unknown")",
            "delivery_log=\(alphaDeliveryLog.count)",
            "delivery_enabled=\(alphaDeliveryFlags?.enabled.description ?? "unknown")",
            "delivery_dry_run_only=\(alphaDeliveryFlags?.dryRunOnly.description ?? "unknown")",
            "canonical_positions=\(canonicalPositions.count)",
            "portfolio_reconciliation_runs=\(portfolioReconciliationRuns.count)",
            "portfolio_snapshots=\(portfolioSnapshots.count)",
            "manual_positions=\(manualPositions.count)",
            "manual_cash=\(manualPortfolio?.accountSettings.availableCash.description ?? "unknown")",
            "portfolio_theses=\(portfolioTheses.count)",
            "thesis_reviews_due=\(reviewDueCount)",
            "thesis_reviews_overdue=\(reviewOverdueCount)",
            "thesis_warnings=\(thesisWarningCount)",
            "decision_checklists=\(decisionChecklists.count)",
            "decision_ready=\(readyDecisionCount)",
            "decision_pending=\(decisionSummary?.pendingCount.description ?? "unknown")",
            "portfolio_risk_score=\(portfolioRisk?.portfolioRiskScore.description ?? "unknown")",
            "size_check=\(sizeCheck?.ticker ?? "none")",
            "market_regime=\(marketRegime?.overallRegime ?? "unknown")",
            "market_regime_history=\(marketRegimeHistory.count)",
            "replay_runs=\(replayRuns.count)",
            "selected_replay=\(selectedReplayRun?.runId ?? "none")",
            "replay_events=\(replayEvents.count)",
            "portfolio_stress=\(portfolioStressRun?.runId ?? "none")",
            "stress_history=\(portfolioStressHistory.count)",
            "stress_scenario=\(selectedStressScenario?.scenarioType ?? "none")",
            "strategy_scorecards=\(strategyScorecards.count)",
            "selected_strategy=\(selectedStrategyScorecard?.strategy ?? "none")",
            "planner_snapshot=\(plannerSnapshot?.snapshotId ?? "none")",
            "planner_projection_start=\(plannerProjections?.startingValue.description ?? "unknown")",
            "daily_brief_compact=\(compactBrief?.brief.isEmpty == false)",
            "daily_brief_generated=\(detailedBrief?.generatedAt ?? "unknown")",
            "eod_brief_compact=\(eodCompactBrief?.brief.isEmpty == false)",
            "market_pulse=\(marketPulse?.market.count.description ?? "unknown")",
            "sectors=\(sectorPerformance?.sectors.count.description ?? "unknown")",
            "stock_research=\(stockResearch?.ticker ?? "none")",
            "etf_research=\(etfResearch?.ticker ?? "none")",
            "macro_available=\(macroResearch?.available.description ?? "unknown")",
            "market_news=\(marketNews?.items.count.description ?? "unknown")",
            "ticker_news=\(tickerNews?.ticker ?? "none")",
            "research_watchlist=\(researchWatchlist.count)",
            "research_watchlist_suggestions=\(researchWatchlistSuggestions?.total.description ?? "unknown")",
            "research_watchlist_detail=\(selectedResearchWatchlistDetail?.item.ticker ?? "none")",
            "research_workflow_queue=\(researchWorkflowQueue.count)",
            "research_workflow_summary=\(researchWorkflowSummary?.generatedAt ?? "unknown")",
            "weekly_review_grade=\(weeklyReviewDetailed?.grade ?? "unknown")",
            "weekly_review_history=\(weeklyReviewHistory.count)",
            "catalysts=\(catalysts.count)",
            "catalyst_summary_this_week=\(catalystSummary?.thisWeekCount.description ?? "unknown")",
            "selected_catalyst=\(selectedCatalyst?.catalystId ?? "none")",
            "notifications=\(notifications.count)",
            "notification_unread=\(notificationSummary?.unreadCount.description ?? "unknown")",
            "selected_notification=\(selectedNotification?.notificationId ?? "none")",
            "notification_preferences=\(notificationPreferences?.digestMode ?? "unknown")",
            "notification_category_preferences=\(notificationCategoryPreferences.count)",
            "notification_digest=\(notificationDigest?.mode ?? "none")",
            "system_health=\(systemReleaseCompact?.overallStatus ?? "unknown")",
            "system_health_full=\(systemReleaseFull?.overallStatus ?? "unknown")",
            "system_routes=\(systemRoutes?.count.description ?? "unknown")",
            "system_flags_generated=\(systemFlags?.generatedAt ?? "unknown")",
            "last_sync=\(lastSync.map { ISO8601DateFormatter().string(from: $0) } ?? "never")",
            "api_secret_configured=\(apiSecretConfigured)",
            "build=\(buildInfo)",
            "last_error=\(lastError ?? "none")"
        ]
        UIPasteboard.general.string = lines.joined(separator: "\n")
        HapticManager.selection()
    }

    func clearLocalCache() {
        for key in cacheKeys.keys {
            UserDefaults.standard.removeObject(forKey: key)
        }
        alphaCandidates = []
        alphaReport = nil
        alphaOutcomes = []
        alphaLearning = nil
        alphaLearningRecommendations = nil
        alphaShadowPolicy = nil
        alphaProposals = []
        alphaValidationSummary = nil
        alphaValidations = []
        alphaAlertCandidates = []
        alphaAlertGateSummary = nil
        alphaDryRunNotifications = []
        alphaNotificationQCRecords = []
        alphaNotificationQCSummary = nil
        alphaDeliveryLog = []
        alphaDeliveryFlags = nil
        canonicalPortfolio = nil
        portfolioReconciliationRuns = []
        portfolioSnapshots = []
        manualPortfolio = nil
        portfolioTheses = []
        selectedThesisDetail = nil
        portfolioReviews = nil
        decisionChecklists = []
        selectedDecisionChecklist = nil
        decisionSummary = nil
        portfolioRisk = nil
        sizeCheck = nil
        marketRegime = nil
        marketRegimeHistory = []
        replayRuns = []
        selectedReplayRun = nil
        replayEvents = []
        portfolioStressRun = nil
        portfolioStressHistory = []
        selectedStressScenario = nil
        strategyScorecards = []
        strategySummary = nil
        selectedStrategyScorecard = nil
        plannerSnapshot = nil
        plannerProjections = nil
        compactBrief = nil
        detailedBrief = nil
        debugBrief = nil
        briefLastRefresh = nil
        eodCompactBrief = nil
        eodDetailedBrief = nil
        eodDebugBrief = nil
        eodBriefLastRefresh = nil
        marketPulse = nil
        sectorPerformance = nil
        stockResearch = nil
        etfResearch = nil
        macroResearch = nil
        marketNews = nil
        tickerNews = nil
        stockAIAnalysis = nil
        etfAIAnalysis = nil
        macroAIAnalysis = nil
        researchWatchlist = []
        researchWatchlistSuggestions = nil
        selectedResearchWatchlistDetail = nil
        researchWorkflowQueue = []
        researchWorkflowSummary = nil
        weeklyReviewCompact = nil
        weeklyReviewDetailed = nil
        weeklyReviewDebug = nil
        weeklyReviewHistory = []
        weeklyReviewLastRefresh = nil
        catalysts = []
        catalystSummary = nil
        selectedCatalyst = nil
        catalystLookupId = ""
        notifications = []
        notificationSummary = nil
        selectedNotification = nil
        notificationLookupId = ""
        notificationPreferences = nil
        notificationCategoryPreferences = []
        notificationDigest = nil
        systemReleaseCompact = nil
        systemReleaseFull = nil
        systemRoutes = nil
        systemFlags = nil
        backupList = nil
        selectedBackup = nil
        backupVerifyResult = nil
        backupRestorePreview = nil
        proposalShadowResults = [:]
        updateCacheStatus()
        HapticManager.impact(.medium)
    }

    func openBackendHealth() {
        guard let url = URL(string: "\(APIEndpoints.base)/health") else { return }
        UIApplication.shared.open(url)
    }

    func setReminder(daysFromNow: Int) {
        signingReminderDate = Calendar.current.date(byAdding: .day, value: daysFromNow, to: Date()) ?? Date()
        objectWillChange.send()
    }

    private func loadLocalState() {
        lastSync = UserDefaults.standard.object(forKey: lastSyncKey) as? Date
        briefLastRefresh = UserDefaults.standard.object(forKey: briefLastRefreshKey) as? Date
        eodBriefLastRefresh = UserDefaults.standard.object(forKey: eodBriefLastRefreshKey) as? Date
        weeklyReviewLastRefresh = UserDefaults.standard.object(forKey: weeklyReviewLastRefreshKey) as? Date
        localNotificationsEnabled = UserDefaults.standard.bool(forKey: localNotificationsEnabledKey)
        notifyCriticalOnly = UserDefaults.standard.object(forKey: notifyCriticalOnlyKey) as? Bool ?? true
        notifyCriticalWarning = UserDefaults.standard.object(forKey: notifyCriticalWarningKey) as? Bool ?? true
        notifyUnreadAlpha = UserDefaults.standard.object(forKey: notifyUnreadAlphaKey) as? Bool ?? true
        notifyDailyBriefAvailable = UserDefaults.standard.object(forKey: notifyDailyBriefAvailableKey) as? Bool ?? true
        notifyResearchWorkflowDue = UserDefaults.standard.object(forKey: notifyResearchWorkflowDueKey) as? Bool ?? true
        notifyCatalystDueSoon = UserDefaults.standard.object(forKey: notifyCatalystDueSoonKey) as? Bool ?? true
        loadAlphaCaches()
        updateCacheStatus()
    }

    private func loadAlphaCaches() {
        alphaCandidates = load([AlphaCandidate].self, key: alphaCacheKey) ?? []
        alphaReport = load(AlphaReport.self, key: alphaReportCacheKey)
        alphaOutcomes = load([AlphaOutcome].self, key: alphaOutcomesCacheKey) ?? []
        alphaLearning = load(AlphaLearning.self, key: alphaLearningCacheKey)
        alphaLearningRecommendations = load(AlphaLearningRecommendations.self, key: alphaLearningRecommendationsCacheKey)
        alphaShadowPolicy = load(AlphaShadowPolicy.self, key: alphaShadowPolicyCacheKey)
        alphaProposals = load([AlphaProposal].self, key: alphaProposalsCacheKey) ?? []
        alphaValidationSummary = load(AlphaValidationSummary.self, key: alphaValidationSummaryCacheKey)
        alphaValidations = load([AlphaValidationRecord].self, key: alphaValidationsCacheKey) ?? []
        alphaAlertCandidates = load([AlphaAlertCandidate].self, key: alphaAlertCandidatesCacheKey) ?? []
        alphaAlertGateSummary = load(AlphaAlertGateSummary.self, key: alphaAlertGateSummaryCacheKey)
        alphaDryRunNotifications = load([AlphaDryRunNotification].self, key: alphaDryRunNotificationsCacheKey) ?? []
        alphaNotificationQCRecords = load([AlphaNotificationQCRecord].self, key: alphaNotificationQCRecordsCacheKey) ?? []
        alphaNotificationQCSummary = load(AlphaNotificationQCSummary.self, key: alphaNotificationQCSummaryCacheKey)
        alphaDeliveryLog = load([AlphaNotificationDeliveryEntry].self, key: alphaDeliveryLogCacheKey) ?? []
        alphaDeliveryFlags = load(AlphaNotificationDeliveryFlags.self, key: alphaDeliveryFlagsCacheKey)
        canonicalPortfolio = load(CanonicalPortfolioResponse.self, key: canonicalPortfolioCacheKey)
        portfolioReconciliationRuns = load([PortfolioReconciliationRun].self, key: portfolioReconciliationCacheKey) ?? []
        portfolioSnapshots = load([PortfolioSnapshot].self, key: portfolioSnapshotsCacheKey) ?? []
        manualPortfolio = load(ManualPortfolioResponse.self, key: manualPortfolioCacheKey)
        portfolioTheses = load([PositionThesis].self, key: portfolioThesesCacheKey) ?? []
        selectedThesisDetail = load(PortfolioThesisDetailResponse.self, key: selectedThesisDetailCacheKey)
        portfolioReviews = load(PortfolioReviewsResponse.self, key: portfolioReviewsCacheKey)
        decisionChecklists = load([DecisionChecklist].self, key: decisionChecklistsCacheKey) ?? []
        selectedDecisionChecklist = load(DecisionChecklist.self, key: selectedDecisionChecklistCacheKey)
        decisionSummary = load(DecisionSummaryResponse.self, key: decisionSummaryCacheKey)
        portfolioRisk = load(PortfolioRiskReport.self, key: portfolioRiskCacheKey)
        sizeCheck = load(DecisionSizeCheckResponse.self, key: sizeCheckCacheKey)
        marketRegime = load(MarketRegimeSnapshot.self, key: marketRegimeCacheKey)
        marketRegimeHistory = load([MarketRegimeSnapshot].self, key: marketRegimeHistoryCacheKey) ?? []
        replayRuns = load([ReplayRun].self, key: replayRunsCacheKey) ?? []
        selectedReplayRun = load(ReplayRun.self, key: selectedReplayRunCacheKey)
        replayEvents = load([ReplayEvent].self, key: replayEventsCacheKey) ?? []
        portfolioStressRun = load(PortfolioStressRun.self, key: portfolioStressCacheKey)
        portfolioStressHistory = load([PortfolioStressRun].self, key: portfolioStressHistoryCacheKey) ?? []
        selectedStressScenario = load(PortfolioStressScenario.self, key: selectedStressScenarioCacheKey)
        strategyScorecards = load([StrategyScorecard].self, key: strategyScorecardsCacheKey) ?? []
        strategySummary = load(StrategySummaryResponse.self, key: strategySummaryCacheKey)
        selectedStrategyScorecard = load(StrategyScorecard.self, key: selectedStrategyScorecardCacheKey)
        plannerSnapshot = load(PlannerSnapshot.self, key: plannerSnapshotCacheKey)
        plannerProjections = load(PlannerProjections.self, key: plannerProjectionsCacheKey)
        compactBrief = load(DailyBriefCompactResponse.self, key: compactBriefCacheKey)
        detailedBrief = load(DailyBriefDetailedResponse.self, key: detailedBriefCacheKey)
        debugBrief = load(DailyBriefDebugResponse.self, key: debugBriefCacheKey)
        eodCompactBrief = load(DailyBriefCompactResponse.self, key: eodCompactBriefCacheKey)
        eodDetailedBrief = load(DailyBriefDetailedResponse.self, key: eodDetailedBriefCacheKey)
        eodDebugBrief = load(DailyBriefDebugResponse.self, key: eodDebugBriefCacheKey)
        marketPulse = load(MarketPulseResponse.self, key: marketPulseCacheKey)
        sectorPerformance = load(SectorPerformanceResponse.self, key: sectorPerformanceCacheKey)
        stockResearch = load(StockResearchResponse.self, key: stockResearchCacheKey)
        etfResearch = load(ETFResearchResponse.self, key: etfResearchCacheKey)
        macroResearch = load(MacroResearchResponse.self, key: macroResearchCacheKey)
        marketNews = load(ResearchNewsResponse.self, key: marketNewsCacheKey)
        tickerNews = load(ResearchNewsResponse.self, key: tickerNewsCacheKey)
        stockAIAnalysis = load(ResearchAIResponse.self, key: stockAIAnalysisCacheKey)
        etfAIAnalysis = load(ResearchAIResponse.self, key: etfAIAnalysisCacheKey)
        macroAIAnalysis = load(ResearchAIResponse.self, key: macroAIAnalysisCacheKey)
        researchWatchlist = load([ResearchWatchlistItem].self, key: researchWatchlistCacheKey) ?? []
        researchWatchlistSuggestions = load(ResearchWatchlistSuggestionsResponse.self, key: researchWatchlistSuggestionsCacheKey)
        selectedResearchWatchlistDetail = load(ResearchWatchlistDetail.self, key: selectedResearchWatchlistDetailCacheKey)
        researchWorkflowQueue = load([ResearchWorkflowItem].self, key: researchWorkflowQueueCacheKey) ?? []
        researchWorkflowSummary = load(ResearchWorkflowSummary.self, key: researchWorkflowSummaryCacheKey)
        weeklyReviewCompact = load(WeeklyReviewCompactResponse.self, key: weeklyReviewCompactCacheKey)
        weeklyReviewDetailed = load(WeeklyReviewDetailedResponse.self, key: weeklyReviewDetailedCacheKey)
        weeklyReviewDebug = load(WeeklyReviewDebugResponse.self, key: weeklyReviewDebugCacheKey)
        weeklyReviewHistory = load([WeeklyReviewHistoryEntry].self, key: weeklyReviewHistoryCacheKey) ?? []
        catalysts = load([Catalyst].self, key: catalystsCacheKey) ?? []
        catalystSummary = load(CatalystSummaryResponse.self, key: catalystSummaryCacheKey)
        selectedCatalyst = load(Catalyst.self, key: selectedCatalystCacheKey)
        notifications = load([InAppNotification].self, key: notificationsCacheKey) ?? []
        notificationSummary = load(NotificationSummaryResponse.self, key: notificationSummaryCacheKey)
        selectedNotification = load(InAppNotification.self, key: selectedNotificationCacheKey)
        notificationPreferences = load(NotificationPreferences.self, key: notificationPreferencesCacheKey)
        notificationCategoryPreferences = mergedNotificationCategoryPreferences(load([NotificationCategoryPreference].self, key: notificationCategoryPreferencesCacheKey) ?? [])
        notificationDigest = load(NotificationDigestResponse.self, key: notificationDigestCacheKey)
        systemReleaseCompact = load(SystemReleaseCheckResponse.self, key: systemReleaseCompactCacheKey)
        systemReleaseFull = load(SystemReleaseCheckResponse.self, key: systemReleaseFullCacheKey)
        systemRoutes = load(SystemRoutesResponse.self, key: systemRoutesCacheKey)
        systemFlags = load(SystemFlagsResponse.self, key: systemFlagsCacheKey)
        backupList = load(BackupListResponse.self, key: backupListCacheKey)
        if let item = selectedResearchWatchlistDetail?.item {
            populateResearchWatchlistForm(item)
        }
        if let catalyst = selectedCatalyst {
            populateCatalystForm(catalyst)
        }
        if let notification = selectedNotification {
            notificationLookupId = notification.notificationId
        }
        if let prefs = notificationPreferences {
            syncNotificationPreferenceForm(prefs)
        }
        syncChecklistItemNotes()
        if let settings = manualPortfolio?.accountSettings {
            syncAccountForm(from: settings)
        }
    }

    private func storedSecret() -> String? {
        let secret = UserDefaults.standard.string(forKey: "api_secret") ?? ""
        let trimmed = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func setManualError(_ message: String) {
        manualActionMessage = message
        manualActionSuccess = false
    }

    private func setThesisError(_ message: String) {
        thesisActionMessage = message
        thesisActionSuccess = false
    }

    private func setDecisionError(_ message: String) {
        decisionActionMessage = message
        decisionActionSuccess = false
    }

    private func setReplayError(_ message: String) {
        replayActionMessage = message
        replayActionSuccess = false
    }

    private func setStressError(_ message: String) {
        stressActionMessage = message
        stressActionSuccess = false
    }

    private func setPlannerError(_ message: String) {
        plannerActionMessage = message
        plannerActionSuccess = false
    }

    private func setResearchError(_ message: String) {
        researchActionMessage = message
        researchActionSuccess = false
    }

    private func syncAccountForm(from settings: ManualAccountSettings) {
        accountName = settings.accountName
        accountType = settings.accountType
        accountBaseCurrency = settings.baseCurrency
        accountAvailableCash = String(format: "%.2f", settings.availableCash)
        accountContributionRoom = settings.contributionRoom.map { String(format: "%.2f", $0) } ?? ""
        accountNotes = settings.notes
        if manualAccountType.isEmpty { manualAccountType = settings.accountType }
        if manualCurrency.isEmpty { manualCurrency = settings.baseCurrency }
    }

    private func syncChecklistItemNotes() {
        checklistItemNotes = Dictionary(
            uniqueKeysWithValues: (selectedDecisionChecklist?.items ?? []).map { ($0.itemKey, $0.note) }
        )
    }

    private func groupedValidationRates(matching behaviors: Set<String>) -> [(setup: String, rate: Double, count: Int)] {
        let groups = Dictionary(grouping: alphaValidations) { $0.setupType ?? "UNKNOWN" }
        return groups.compactMap { setup, rows in
            guard !rows.isEmpty else { return nil }
            let hits = rows.filter { behaviors.contains($0.behaviorClass.uppercased()) }.count
            return (setup: setup, rate: Double(hits) / Double(rows.count), count: rows.count)
        }
        .sorted { lhs, rhs in
            if lhs.rate == rhs.rate { return lhs.setup < rhs.setup }
            return lhs.rate > rhs.rate
        }
    }

    private func persistAlpha(_ alpha: [AlphaCandidate]) {
        guard let data = try? JSONEncoder().encode(alpha) else { return }
        UserDefaults.standard.set(data, forKey: alphaCacheKey)
    }

    private func persist<T: Encodable>(_ value: T, key: String) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    private func load<T: Decodable>(_ type: T.Type, key: String) -> T? {
        guard let data = UserDefaults.standard.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    private func updateCacheStatus() {
        cacheStatus = cacheKeys
            .map { key, title in
                let bytes = UserDefaults.standard.data(forKey: key)?.count ?? 0
                return CacheEntryStatus(id: key, title: title, hasData: bytes > 0, bytes: bytes)
            }
            .sorted { $0.title < $1.title }
    }
}
