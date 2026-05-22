import Foundation

// Brief, market research, and weekly review models split from AlphaCandidate.swift.
// MARK: - A21 Daily Operator Brief Models

struct DailyBriefCompactResponse: Codable {
    let brief: String
    let mode: String

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        brief = try container.decodeIfPresent(String.self, forKey: .brief) ?? ""
        mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "compact"
    }
}

struct DailyBriefDetailedResponse: Codable {
    let mode: String
    let generatedAt: String?
    let portfolioTruth: BriefPortfolioTruth
    let overnightChanges: [String]
    let alphaHighlights: [BriefAlphaHighlight]
    let dryrunHighlights: [BriefDryRunHighlight]
    let qcSuppressionSummary: BriefQCSummary
    let marketRegime: BriefMarketRegime
    let riskWarnings: [String]
    let stressWorstCase: BriefStressWorstCase
    let checklistsDue: [BriefChecklistDue]
    let thesisReviewsDue: BriefThesisReviewsDue
    let scorecardWarnings: [String]
    let plannerSummary: BriefPlannerSummary
    let cashTfsaNotes: BriefCashTfsaNotes
    let keyActions: [String]

    enum CodingKeys: String, CodingKey {
        case mode
        case generatedAt = "generated_at"
        case portfolioTruth = "portfolio_truth"
        case overnightChanges = "overnight_changes"
        case alphaHighlights = "alpha_highlights"
        case dryrunHighlights = "dryrun_highlights"
        case qcSuppressionSummary = "qc_suppression_summary"
        case marketRegime = "market_regime"
        case riskWarnings = "risk_warnings"
        case stressWorstCase = "stress_worst_case"
        case checklistsDue = "checklists_due"
        case thesisReviewsDue = "thesis_reviews_due"
        case scorecardWarnings = "scorecard_warnings"
        case plannerSummary = "planner_summary"
        case cashTfsaNotes = "cash_tfsa_notes"
        case keyActions = "key_actions"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "detailed"
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        portfolioTruth = try container.decodeIfPresent(BriefPortfolioTruth.self, forKey: .portfolioTruth) ?? .empty
        overnightChanges = try container.decodeIfPresent([String].self, forKey: .overnightChanges) ?? []
        alphaHighlights = try container.decodeIfPresent([BriefAlphaHighlight].self, forKey: .alphaHighlights) ?? []
        dryrunHighlights = try container.decodeIfPresent([BriefDryRunHighlight].self, forKey: .dryrunHighlights) ?? []
        qcSuppressionSummary = try container.decodeIfPresent(BriefQCSummary.self, forKey: .qcSuppressionSummary) ?? .empty
        marketRegime = try container.decodeIfPresent(BriefMarketRegime.self, forKey: .marketRegime) ?? .empty
        riskWarnings = try container.decodeIfPresent([String].self, forKey: .riskWarnings) ?? []
        stressWorstCase = try container.decodeIfPresent(BriefStressWorstCase.self, forKey: .stressWorstCase) ?? .empty
        checklistsDue = try container.decodeIfPresent([BriefChecklistDue].self, forKey: .checklistsDue) ?? []
        thesisReviewsDue = try container.decodeIfPresent(BriefThesisReviewsDue.self, forKey: .thesisReviewsDue) ?? .empty
        scorecardWarnings = try container.decodeIfPresent([String].self, forKey: .scorecardWarnings) ?? []
        plannerSummary = try container.decodeIfPresent(BriefPlannerSummary.self, forKey: .plannerSummary) ?? .empty
        cashTfsaNotes = try container.decodeIfPresent(BriefCashTfsaNotes.self, forKey: .cashTfsaNotes) ?? .empty
        keyActions = try container.decodeIfPresent([String].self, forKey: .keyActions) ?? []
    }
}

struct DailyBriefDebugResponse: Codable {
    let detailed: DailyBriefDetailedResponse
    let dataSources: BriefDataSources

    enum CodingKeys: String, CodingKey {
        case detailed
        case dataSources = "data_sources"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let cachedDetailed = try? container.decode(DailyBriefDetailedResponse.self, forKey: .detailed) {
            detailed = cachedDetailed
        } else {
            detailed = try DailyBriefDetailedResponse(from: decoder)
        }
        dataSources = try container.decodeIfPresent(BriefDataSources.self, forKey: .dataSources) ?? .empty
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(detailed, forKey: .detailed)
        try container.encode(dataSources, forKey: .dataSources)
    }
}

struct BriefPortfolioTruth: Codable {
    let positionCount: Int
    let totalValue: Double
    let cash: Double
    let unrealizedPnl: Double
    let unrealizedPnlPct: Double
    let positions: [BriefPosition]

    static let empty = BriefPortfolioTruth(positionCount: 0, totalValue: 0, cash: 0, unrealizedPnl: 0, unrealizedPnlPct: 0, positions: [])

    enum CodingKeys: String, CodingKey {
        case positionCount = "position_count"
        case totalValue = "total_value"
        case cash
        case unrealizedPnl = "unrealized_pnl"
        case unrealizedPnlPct = "unrealized_pnl_pct"
        case positions
    }

    init(positionCount: Int, totalValue: Double, cash: Double, unrealizedPnl: Double, unrealizedPnlPct: Double, positions: [BriefPosition]) {
        self.positionCount = positionCount
        self.totalValue = totalValue
        self.cash = cash
        self.unrealizedPnl = unrealizedPnl
        self.unrealizedPnlPct = unrealizedPnlPct
        self.positions = positions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        positionCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .positionCount)?.value ?? 0
        totalValue = try container.decodeFlexibleDoubleIfPresent(forKey: .totalValue) ?? 0
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash) ?? 0
        unrealizedPnl = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnl) ?? 0
        unrealizedPnlPct = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnlPct) ?? 0
        positions = try container.decodeIfPresent([BriefPosition].self, forKey: .positions) ?? []
    }
}

struct BriefPosition: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let quantity: Double
    let marketValue: Double
    let unrealizedPnl: Double
    let unrealizedPnlPct: Double

    enum CodingKeys: String, CodingKey {
        case ticker
        case quantity
        case marketValue = "market_value"
        case unrealizedPnl = "unrealized_pnl"
        case unrealizedPnlPct = "unrealized_pnl_pct"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        quantity = try container.decodeFlexibleDoubleIfPresent(forKey: .quantity) ?? 0
        marketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .marketValue) ?? 0
        unrealizedPnl = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnl) ?? 0
        unrealizedPnlPct = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnlPct) ?? 0
    }
}

struct BriefAlphaHighlight: Codable, Identifiable {
    var id: String { ticker + readinessTier }
    let ticker: String
    let alphaScore: Double?
    let alphaTier: String
    let setupType: String
    let readinessTier: String
    let reason: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case readinessTier = "readiness_tier"
        case reason
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier) ?? ""
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier) ?? ""
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? ""
    }
}

struct BriefDryRunHighlight: Codable, Identifiable {
    var id: String { ticker + status + messagePreview }
    let ticker: String
    let readinessTier: String
    let alphaScore: Double?
    let status: String
    let messagePreview: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case readinessTier = "readiness_tier"
        case alphaScore = "alpha_score"
        case status
        case messagePreview = "message_preview"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier) ?? ""
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? ""
        messagePreview = try container.decodeIfPresent(String.self, forKey: .messagePreview) ?? ""
    }
}

struct BriefQCSummary: Codable {
    let totalEvaluated: Int
    let allowedCount: Int
    let suppressedCount: Int
    let priorityCandidates: Int
    let avgQcScore: Double

    static let empty = BriefQCSummary(totalEvaluated: 0, allowedCount: 0, suppressedCount: 0, priorityCandidates: 0, avgQcScore: 0)

    enum CodingKeys: String, CodingKey {
        case totalEvaluated = "total_evaluated"
        case allowedCount = "allowed_count"
        case suppressedCount = "suppressed_count"
        case priorityCandidates = "priority_candidates"
        case avgQcScore = "avg_qc_score"
    }
}

struct BriefMarketRegime: Codable {
    let available: Bool
    let overallRegime: String
    let volatilityRegime: String?
    let breadthRegime: String?
    let speculativeRegime: String?
    let regimeScore: Double
    let warnings: [String]
    let capturedAt: String?

    static let empty = BriefMarketRegime(available: false, overallRegime: "NEUTRAL", volatilityRegime: nil, breadthRegime: nil, speculativeRegime: nil, regimeScore: 50, warnings: [], capturedAt: nil)

    enum CodingKeys: String, CodingKey {
        case available
        case overallRegime = "overall_regime"
        case volatilityRegime = "volatility_regime"
        case breadthRegime = "breadth_regime"
        case speculativeRegime = "speculative_regime"
        case regimeScore = "regime_score"
        case warnings
        case capturedAt = "captured_at"
    }
}

struct BriefStressWorstCase: Codable {
    let available: Bool
    let runId: String?
    let worstScenario: String?
    let worstLossPct: Double?
    let avgLossPct: Double?
    let warnings: [String]
    let createdAt: String?

    static let empty = BriefStressWorstCase(available: false, runId: nil, worstScenario: nil, worstLossPct: nil, avgLossPct: nil, warnings: [], createdAt: nil)

    enum CodingKeys: String, CodingKey {
        case available
        case runId = "run_id"
        case worstScenario = "worst_scenario"
        case worstLossPct = "worst_loss_pct"
        case avgLossPct = "avg_loss_pct"
        case warnings
        case createdAt = "created_at"
    }
}

struct BriefChecklistDue: Codable, Identifiable {
    var id: String { ticker + decisionType }
    let ticker: String
    let decisionType: String
    let checklistStatus: String
    let readiness: String
    let blockingItems: Int

    enum CodingKeys: String, CodingKey {
        case ticker
        case decisionType = "decision_type"
        case checklistStatus = "checklist_status"
        case readiness
        case blockingItems = "blocking_items"
    }
}

struct BriefThesisReviewsDue: Codable {
    let overdueCount: Int
    let overdue: [BriefThesisReview]
    let upcomingCount: Int
    let missingThesis: [String]
    let staleThesis: [String]
    let missingExitPlan: [String]

    static let empty = BriefThesisReviewsDue(overdueCount: 0, overdue: [], upcomingCount: 0, missingThesis: [], staleThesis: [], missingExitPlan: [])

    enum CodingKeys: String, CodingKey {
        case overdueCount = "overdue_count"
        case overdue
        case upcomingCount = "upcoming_count"
        case missingThesis = "missing_thesis"
        case staleThesis = "stale_thesis"
        case missingExitPlan = "missing_exit_plan"
    }
}

struct BriefThesisReview: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let nextReviewAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case nextReviewAt = "next_review_at"
    }
}

struct BriefPlannerSummary: Codable {
    let available: Bool
    let rebalanceUrgency: String
    let priorityAreas: [PlannerPriorityArea]
    let cashDeploymentGuidance: String
    let contributionGuidance: String
    let riskReductionGuidance: String
    let regime: String
    let createdAt: String?

    static let empty = BriefPlannerSummary(available: false, rebalanceUrgency: "NONE", priorityAreas: [], cashDeploymentGuidance: "", contributionGuidance: "", riskReductionGuidance: "", regime: "NEUTRAL", createdAt: nil)

    enum CodingKeys: String, CodingKey {
        case available
        case rebalanceUrgency = "rebalance_urgency"
        case priorityAreas = "priority_areas"
        case cashDeploymentGuidance = "cash_deployment_guidance"
        case contributionGuidance = "contribution_guidance"
        case riskReductionGuidance = "risk_reduction_guidance"
        case regime
        case createdAt = "created_at"
    }
}

struct BriefCashTfsaNotes: Codable {
    let cash: Double
    let tfsaRoom: Double

    static let empty = BriefCashTfsaNotes(cash: 0, tfsaRoom: 0)

    enum CodingKeys: String, CodingKey {
        case cash
        case tfsaRoom = "tfsa_room"
    }
}

struct BriefDataSources: Codable {
    let portfolioAvailable: Bool
    let alphaCandidatesCount: Int
    let dryRunsCount: Int
    let regimeAvailable: Bool
    let stressRunAvailable: Bool
    let plannerSnapshotAvailable: Bool
    let pendingChecklistsCount: Int
    let scorecardComputedAt: String?

    static let empty = BriefDataSources(portfolioAvailable: false, alphaCandidatesCount: 0, dryRunsCount: 0, regimeAvailable: false, stressRunAvailable: false, plannerSnapshotAvailable: false, pendingChecklistsCount: 0, scorecardComputedAt: nil)

    enum CodingKeys: String, CodingKey {
        case portfolioAvailable = "portfolio_available"
        case alphaCandidatesCount = "alpha_candidates_count"
        case dryRunsCount = "dry_runs_count"
        case regimeAvailable = "regime_available"
        case stressRunAvailable = "stress_run_available"
        case plannerSnapshotAvailable = "planner_snapshot_available"
        case pendingChecklistsCount = "pending_checklists_count"
        case scorecardComputedAt = "scorecard_computed_at"
    }
}

struct WeeklyReviewCompactResponse: Codable {
    let mode: String
    let text: String
}

struct WeeklyReviewDetailedResponse: Codable {
    let mode: String
    let grade: String
    let weekStart: String?
    let weekEnd: String?
    let weekLabel: String?
    let generatedAt: String?
    let accountabilityMetrics: WeeklyAccountabilityMetrics
    let portfolioWeeklyChange: WeeklyPortfolioChange
    let alphaGenerated: WeeklyAlphaGenerated
    let alphaImproved: WeeklyCountList
    let alphaFailed: WeeklyCountList
    let validationOutcomes: WeeklyValidationOutcomes
    let notificationActivity: WeeklyNotificationActivity
    let qcSuppressions: WeeklyQCSuppressions
    let deliveryAttempts: WeeklyDeliveryAttempts
    let checklistDiscipline: WeeklyChecklistDiscipline
    let workflowSummary: WeeklyWorkflowSummary
    let thesisSummary: WeeklyThesisSummary
    let watchlistChanges: WeeklyWatchlistChanges
    let scorecardChanges: WeeklyScorecardChanges
    let stressTestChanges: WeeklyStressTestChanges
    let plannerDriftChanges: WeeklyPlannerDriftChanges
    let regimeChanges: WeeklyRegimeChanges
    let keyMistakes: [WeeklyObservation]
    let bestDecisions: [WeeklyObservation]
    let missedOpportunities: [WeeklyObservation]
    let focusNextWeek: [String]

    enum CodingKeys: String, CodingKey {
        case mode, grade
        case weekStart = "week_start"
        case weekEnd = "week_end"
        case weekLabel = "week_label"
        case generatedAt = "generated_at"
        case accountabilityMetrics = "accountability_metrics"
        case portfolioWeeklyChange = "portfolio_weekly_change"
        case alphaGenerated = "alpha_generated"
        case alphaImproved = "alpha_improved"
        case alphaFailed = "alpha_failed"
        case validationOutcomes = "validation_outcomes"
        case notificationActivity = "notification_activity"
        case qcSuppressions = "qc_suppressions"
        case deliveryAttempts = "delivery_attempts"
        case checklistDiscipline = "checklist_discipline"
        case workflowSummary = "workflow_summary"
        case thesisSummary = "thesis_summary"
        case watchlistChanges = "watchlist_changes"
        case scorecardChanges = "scorecard_changes"
        case stressTestChanges = "stress_test_changes"
        case plannerDriftChanges = "planner_drift_changes"
        case regimeChanges = "regime_changes"
        case keyMistakes = "key_mistakes"
        case bestDecisions = "best_decisions"
        case missedOpportunities = "missed_opportunities"
        case focusNextWeek = "focus_next_week"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "detailed"
        grade = try c.decodeIfPresent(String.self, forKey: .grade) ?? "C"
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart)
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd)
        weekLabel = try c.decodeIfPresent(String.self, forKey: .weekLabel)
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        accountabilityMetrics = try c.decodeIfPresent(WeeklyAccountabilityMetrics.self, forKey: .accountabilityMetrics) ?? .empty
        portfolioWeeklyChange = try c.decodeIfPresent(WeeklyPortfolioChange.self, forKey: .portfolioWeeklyChange) ?? .empty
        alphaGenerated = try c.decodeIfPresent(WeeklyAlphaGenerated.self, forKey: .alphaGenerated) ?? .empty
        alphaImproved = try c.decodeIfPresent(WeeklyCountList.self, forKey: .alphaImproved) ?? .empty
        alphaFailed = try c.decodeIfPresent(WeeklyCountList.self, forKey: .alphaFailed) ?? .empty
        validationOutcomes = try c.decodeIfPresent(WeeklyValidationOutcomes.self, forKey: .validationOutcomes) ?? .empty
        notificationActivity = try c.decodeIfPresent(WeeklyNotificationActivity.self, forKey: .notificationActivity) ?? .empty
        qcSuppressions = try c.decodeIfPresent(WeeklyQCSuppressions.self, forKey: .qcSuppressions) ?? .empty
        deliveryAttempts = try c.decodeIfPresent(WeeklyDeliveryAttempts.self, forKey: .deliveryAttempts) ?? .empty
        checklistDiscipline = try c.decodeIfPresent(WeeklyChecklistDiscipline.self, forKey: .checklistDiscipline) ?? .empty
        workflowSummary = try c.decodeIfPresent(WeeklyWorkflowSummary.self, forKey: .workflowSummary) ?? .empty
        thesisSummary = try c.decodeIfPresent(WeeklyThesisSummary.self, forKey: .thesisSummary) ?? .empty
        watchlistChanges = try c.decodeIfPresent(WeeklyWatchlistChanges.self, forKey: .watchlistChanges) ?? .empty
        scorecardChanges = try c.decodeIfPresent(WeeklyScorecardChanges.self, forKey: .scorecardChanges) ?? .empty
        stressTestChanges = try c.decodeIfPresent(WeeklyStressTestChanges.self, forKey: .stressTestChanges) ?? .empty
        plannerDriftChanges = try c.decodeIfPresent(WeeklyPlannerDriftChanges.self, forKey: .plannerDriftChanges) ?? .empty
        regimeChanges = try c.decodeIfPresent(WeeklyRegimeChanges.self, forKey: .regimeChanges) ?? .empty
        keyMistakes = try c.decodeIfPresent([WeeklyObservation].self, forKey: .keyMistakes) ?? []
        bestDecisions = try c.decodeIfPresent([WeeklyObservation].self, forKey: .bestDecisions) ?? []
        missedOpportunities = try c.decodeIfPresent([WeeklyObservation].self, forKey: .missedOpportunities) ?? []
        focusNextWeek = try c.decodeFlexibleStringArrayIfPresent(forKey: .focusNextWeek) ?? []
    }
}

struct WeeklyReviewDebugResponse: Codable {
    let detailed: WeeklyReviewDetailedResponse
    let dataSources: WeeklyDataSources

    enum CodingKeys: String, CodingKey {
        case dataSources = "data_sources"
    }

    init(from decoder: Decoder) throws {
        detailed = try WeeklyReviewDetailedResponse(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dataSources = try c.decodeIfPresent(WeeklyDataSources.self, forKey: .dataSources) ?? .empty
    }

    func encode(to encoder: Encoder) throws {
        try detailed.encode(to: encoder)
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(dataSources, forKey: .dataSources)
    }
}

struct WeeklyAccountabilityMetrics: Codable {
    let reviewCompletionRate: Double
    let overdueReviewCount: Int
    let checklistDisciplineScore: Double
    let ignoredHighPriorityWorkflow: Int
    let unreviewedDryRuns: Int
    let staleTheses: Int
    let alphaFalsePositiveCount: Int
    let missedWinnerCount: Int
    let riskWarningsUnresolved: Int

    static let empty = WeeklyAccountabilityMetrics(reviewCompletionRate: 0, overdueReviewCount: 0, checklistDisciplineScore: 1, ignoredHighPriorityWorkflow: 0, unreviewedDryRuns: 0, staleTheses: 0, alphaFalsePositiveCount: 0, missedWinnerCount: 0, riskWarningsUnresolved: 0)

    enum CodingKeys: String, CodingKey {
        case reviewCompletionRate = "review_completion_rate"
        case overdueReviewCount = "overdue_review_count"
        case checklistDisciplineScore = "checklist_discipline_score"
        case ignoredHighPriorityWorkflow = "ignored_high_priority_workflow"
        case unreviewedDryRuns = "unreviewed_dry_runs"
        case staleTheses = "stale_theses"
        case alphaFalsePositiveCount = "alpha_false_positive_count"
        case missedWinnerCount = "missed_winner_count"
        case riskWarningsUnresolved = "risk_warnings_unresolved"
    }
}

struct WeeklyPortfolioChange: Codable {
    let available: Bool
    let startValue: Double?
    let endValue: Double?
    let changeCad: Double?
    let changePct: Double?

    static let empty = WeeklyPortfolioChange(available: false, startValue: nil, endValue: nil, changeCad: nil, changePct: nil)

    enum CodingKeys: String, CodingKey {
        case available
        case startValue = "start_value"
        case endValue = "end_value"
        case changeCad = "change_cad"
        case changePct = "change_pct"
    }

    init(available: Bool, startValue: Double?, endValue: Double?, changeCad: Double?, changePct: Double?) {
        self.available = available
        self.startValue = startValue
        self.endValue = endValue
        self.changeCad = changeCad
        self.changePct = changePct
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = (try c.decodeIfPresent(FlexibleBool.self, forKey: .available)?.value) ?? false
        startValue = try c.decodeFlexibleDoubleIfPresent(forKey: .startValue)
        endValue = try c.decodeFlexibleDoubleIfPresent(forKey: .endValue)
        changeCad = try c.decodeFlexibleDoubleIfPresent(forKey: .changeCad)
        changePct = try c.decodeFlexibleDoubleIfPresent(forKey: .changePct)
    }
}

struct WeeklyAlphaGenerated: Codable {
    let count: Int
    let tickers: [String]
    let tierDistribution: [String: Int]

    static let empty = WeeklyAlphaGenerated(count: 0, tickers: [], tierDistribution: [:])

    enum CodingKeys: String, CodingKey {
        case count, tickers
        case tierDistribution = "tier_distribution"
    }

    init(count: Int, tickers: [String], tierDistribution: [String: Int]) {
        self.count = count
        self.tickers = tickers
        self.tierDistribution = tierDistribution
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        count = try c.decodeIfPresent(FlexibleInt.self, forKey: .count)?.value ?? 0
        tickers = try c.decodeFlexibleStringArrayIfPresent(forKey: .tickers) ?? []
        tierDistribution = try c.decodeFlexibleIntMap(forKey: .tierDistribution)
    }
}

struct WeeklyCountList: Codable {
    let count: Int
    static let empty = WeeklyCountList(count: 0)
}

struct WeeklyValidationOutcomes: Codable {
    let completedCount: Int
    let falsePositiveCount: Int
    let positiveCount: Int
    let outcomes: [WeeklyOutcome]

    static let empty = WeeklyValidationOutcomes(completedCount: 0, falsePositiveCount: 0, positiveCount: 0, outcomes: [])

    enum CodingKeys: String, CodingKey {
        case outcomes
        case completedCount = "completed_count"
        case falsePositiveCount = "false_positive_count"
        case positiveCount = "positive_count"
    }
}

struct WeeklyOutcome: Codable, Identifiable {
    var id: String { ticker + status }
    let ticker: String
    let return5d: Double?
    let status: String

    enum CodingKeys: String, CodingKey {
        case ticker, status
        case return5d = "return_5d"
    }
}

struct WeeklyNotificationActivity: Codable {
    let createdThisWeek: Int
    let reviewedThisWeek: Int
    let dismissedThisWeek: Int
    let stillActive: Int

    static let empty = WeeklyNotificationActivity(createdThisWeek: 0, reviewedThisWeek: 0, dismissedThisWeek: 0, stillActive: 0)

    enum CodingKeys: String, CodingKey {
        case createdThisWeek = "created_this_week"
        case reviewedThisWeek = "reviewed_this_week"
        case dismissedThisWeek = "dismissed_this_week"
        case stillActive = "still_active"
    }
}

struct WeeklyQCSuppressions: Codable {
    let evaluatedThisWeek: Int
    let suppressedThisWeek: Int
    let allowedThisWeek: Int
    let suppressionRate: Double

    static let empty = WeeklyQCSuppressions(evaluatedThisWeek: 0, suppressedThisWeek: 0, allowedThisWeek: 0, suppressionRate: 0)

    enum CodingKeys: String, CodingKey {
        case evaluatedThisWeek = "evaluated_this_week"
        case suppressedThisWeek = "suppressed_this_week"
        case allowedThisWeek = "allowed_this_week"
        case suppressionRate = "suppression_rate"
    }
}

struct WeeklyDeliveryAttempts: Codable {
    let sentThisWeek: Int
    let byUrgency: [String: Int]

    static let empty = WeeklyDeliveryAttempts(sentThisWeek: 0, byUrgency: [:])

    enum CodingKeys: String, CodingKey {
        case sentThisWeek = "sent_this_week"
        case byUrgency = "by_urgency"
    }
}

struct WeeklyChecklistDiscipline: Codable {
    let createdThisWeek: Int
    let approvedThisWeek: Int
    let rejectedThisWeek: Int
    let pendingCount: Int

    static let empty = WeeklyChecklistDiscipline(createdThisWeek: 0, approvedThisWeek: 0, rejectedThisWeek: 0, pendingCount: 0)

    enum CodingKeys: String, CodingKey {
        case createdThisWeek = "created_this_week"
        case approvedThisWeek = "approved_this_week"
        case rejectedThisWeek = "rejected_this_week"
        case pendingCount = "pending_count"
    }
}

struct WeeklyWorkflowSummary: Codable {
    let completedThisWeek: Int
    let overdueCount: Int
    let openCount: Int
    let highOpenCount: Int
    let overdueItems: [WeeklyLinkedItem]

    static let empty = WeeklyWorkflowSummary(completedThisWeek: 0, overdueCount: 0, openCount: 0, highOpenCount: 0, overdueItems: [])

    enum CodingKeys: String, CodingKey {
        case completedThisWeek = "completed_this_week"
        case overdueCount = "overdue_count"
        case openCount = "open_count"
        case highOpenCount = "high_open_count"
        case overdueItems = "overdue_items"
    }
}

struct WeeklyLinkedItem: Codable, Identifiable {
    var id: String { (ticker ?? "") + reason }
    let ticker: String?
    let reason: String
}

struct WeeklyThesisSummary: Codable {
    let reviewsCompletedThisWeek: Int
    let overdueCount: Int
    let staleCount: Int
    static let empty = WeeklyThesisSummary(reviewsCompletedThisWeek: 0, overdueCount: 0, staleCount: 0)
    enum CodingKeys: String, CodingKey {
        case reviewsCompletedThisWeek = "reviews_completed_this_week"
        case overdueCount = "overdue_count"
        case staleCount = "stale_count"
    }
}

struct WeeklyWatchlistChanges: Codable {
    let updatedThisWeek: Int
    let archivedThisWeek: Int
    let totalActive: Int
    static let empty = WeeklyWatchlistChanges(updatedThisWeek: 0, archivedThisWeek: 0, totalActive: 0)
    enum CodingKeys: String, CodingKey {
        case updatedThisWeek = "updated_this_week"
        case archivedThisWeek = "archived_this_week"
        case totalActive = "total_active"
    }
}

struct WeeklyScorecardChanges: Codable {
    let computedThisWeek: Int
    let topStrategy: String?
    static let empty = WeeklyScorecardChanges(computedThisWeek: 0, topStrategy: nil)
    enum CodingKeys: String, CodingKey {
        case computedThisWeek = "computed_this_week"
        case topStrategy = "top_strategy"
    }
}

struct WeeklyStressTestChanges: Codable {
    let runsThisWeek: Int
    let worstLossPct: Double?
    static let empty = WeeklyStressTestChanges(runsThisWeek: 0, worstLossPct: nil)
    enum CodingKeys: String, CodingKey {
        case runsThisWeek = "runs_this_week"
        case worstLossPct = "worst_loss_pct"
    }
}

struct WeeklyPlannerDriftChanges: Codable {
    let runsThisWeek: Int
    let lastUrgency: String
    let driftChanged: Bool
    static let empty = WeeklyPlannerDriftChanges(runsThisWeek: 0, lastUrgency: "NONE", driftChanged: false)
    enum CodingKeys: String, CodingKey {
        case runsThisWeek = "runs_this_week"
        case lastUrgency = "last_urgency"
        case driftChanged = "drift_changed"
    }
}

struct WeeklyRegimeChanges: Codable {
    let snapshotsThisWeek: Int
    let openingRegime: String
    let closingRegime: String
    let regimeChanged: Bool
    static let empty = WeeklyRegimeChanges(snapshotsThisWeek: 0, openingRegime: "NEUTRAL", closingRegime: "NEUTRAL", regimeChanged: false)
    enum CodingKeys: String, CodingKey {
        case snapshotsThisWeek = "snapshots_this_week"
        case openingRegime = "opening_regime"
        case closingRegime = "closing_regime"
        case regimeChanged = "regime_changed"
    }
}

struct WeeklyObservation: Codable, Identifiable {
    var id: String { type + (ticker ?? "") + description }
    let type: String
    let ticker: String?
    let description: String
}

struct WeeklyDataSources: Codable {
    let portfolioAvailable: Bool
    let alphaGeneratedCount: Int
    let outcomesCompletedCount: Int
    let dryrunsCreatedCount: Int
    let qcEvaluatedCount: Int
    let deliveryCount: Int
    let checklistsCreatedCount: Int
    let workflowCompletedCount: Int
    let thesisReviewsCount: Int
    let regimeSnapshotsCount: Int
    let missedWinnersCount: Int
    let riskWarningsUnresolved: Int

    static let empty = WeeklyDataSources(portfolioAvailable: false, alphaGeneratedCount: 0, outcomesCompletedCount: 0, dryrunsCreatedCount: 0, qcEvaluatedCount: 0, deliveryCount: 0, checklistsCreatedCount: 0, workflowCompletedCount: 0, thesisReviewsCount: 0, regimeSnapshotsCount: 0, missedWinnersCount: 0, riskWarningsUnresolved: 0)

    enum CodingKeys: String, CodingKey {
        case portfolioAvailable = "portfolio_available"
        case alphaGeneratedCount = "alpha_generated_count"
        case outcomesCompletedCount = "outcomes_completed_count"
        case dryrunsCreatedCount = "dryruns_created_count"
        case qcEvaluatedCount = "qc_evaluated_count"
        case deliveryCount = "delivery_count"
        case checklistsCreatedCount = "checklists_created_count"
        case workflowCompletedCount = "workflow_completed_count"
        case thesisReviewsCount = "thesis_reviews_count"
        case regimeSnapshotsCount = "regime_snapshots_count"
        case missedWinnersCount = "missed_winners_count"
        case riskWarningsUnresolved = "risk_warnings_unresolved"
    }
}

struct WeeklyReviewHistoryResponse: Codable {
    let count: Int
    let history: [WeeklyReviewHistoryEntry]
}

struct WeeklyReviewHistoryEntry: Codable, Identifiable {
    var id: String { weekStart + (sentAt ?? "") }
    let weekStart: String
    let sentAt: String?
    let grade: String
    let mode: String?

    enum CodingKeys: String, CodingKey {
        case grade, mode
        case weekStart = "week_start"
        case sentAt = "sent_at"
    }
}

