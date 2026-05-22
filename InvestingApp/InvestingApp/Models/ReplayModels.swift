import Foundation

// Replay models split from AlphaCandidate.swift.

struct ReplayRunsResponse: Codable {
    let runs: [ReplayRun]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runs = try container.decodeIfPresent([ReplayRun].self, forKey: .runs) ?? []
    }
}

struct ReplayRunDetailResponse: Codable {
    let run: ReplayRun
}

struct ReplayEventsResponse: Codable {
    let events: [ReplayEvent]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        events = try container.decodeIfPresent([ReplayEvent].self, forKey: .events) ?? []
    }
}

struct ReplayRunCreateResponse: Codable {
    let runId: String
    let status: String
    let eventCount: Int
    let summary: ReplaySummary

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case status
        case eventCount = "event_count"
        case summary
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runId = try container.decodeIfPresent(String.self, forKey: .runId) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        eventCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .eventCount)?.value ?? 0
        summary = try container.decodeIfPresent(ReplaySummary.self, forKey: .summary) ?? .empty
    }
}

struct ReplayRun: Codable, Identifiable {
    var id: String { runId }

    let numericId: Int?
    let runId: String
    let createdAt: String?
    let startDate: String
    let endDate: String
    let tickerFilter: [String]
    let sourceFilter: String?
    let setupTypeFilter: String?
    let maxRows: Int
    let status: String
    let eventCount: Int
    let completedAt: String?
    let summary: ReplaySummary

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case runId = "run_id"
        case createdAt = "created_at"
        case startDate = "start_date"
        case endDate = "end_date"
        case tickerFilter = "ticker_filter"
        case sourceFilter = "source_filter"
        case setupTypeFilter = "setup_type_filter"
        case maxRows = "max_rows"
        case status
        case eventCount = "event_count"
        case completedAt = "completed_at"
        case summary
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decodedSummary = try container.decodeIfPresent(ReplaySummary.self, forKey: .summary) ?? .empty
        numericId = try container.decodeIfPresent(FlexibleInt.self, forKey: .numericId)?.value
        runId = try container.decodeIfPresent(String.self, forKey: .runId) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        startDate = try container.decodeIfPresent(String.self, forKey: .startDate) ?? decodedSummary.replayPeriod.startDate
        endDate = try container.decodeIfPresent(String.self, forKey: .endDate) ?? decodedSummary.replayPeriod.endDate
        tickerFilter = try container.decodeFlexibleStringArrayIfPresent(forKey: .tickerFilter) ?? []
        sourceFilter = try container.decodeIfPresent(String.self, forKey: .sourceFilter)
        setupTypeFilter = try container.decodeIfPresent(String.self, forKey: .setupTypeFilter)
        maxRows = try container.decodeIfPresent(FlexibleInt.self, forKey: .maxRows)?.value ?? 0
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        eventCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .eventCount)?.value ?? 0
        completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
        summary = decodedSummary
    }
}

struct ReplaySummary: Codable {
    let replayPeriod: ReplayPeriod
    let eventCount: Int
    let simulatedAlertCount: Int
    let simulatedMonitorCount: Int
    let simulatedPrepareCount: Int
    let simulatedBlockCount: Int
    let simulatedIgnoreCount: Int
    let simulatedRejectCount: Int
    let missedWinners: Int
    let avoidedLosers: Int
    let falsePositives: Int
    let correctIgnores: Int
    let outcomeBreakdown: [String: Int]
    let decisionBreakdown: [String: Int]
    let regimeBreakdown: [String: Int]
    let setupBreakdown: [String: Int]
    let sourceBreakdown: [String: Int]
    let bestSimulatedOpportunities: [ReplayOpportunity]
    let worstSimulatedAlerts: [ReplayOpportunity]
    let error: String?

    static let empty = ReplaySummary(
        replayPeriod: ReplayPeriod(startDate: "", endDate: ""),
        eventCount: 0,
        simulatedAlertCount: 0,
        simulatedMonitorCount: 0,
        simulatedPrepareCount: 0,
        simulatedBlockCount: 0,
        simulatedIgnoreCount: 0,
        simulatedRejectCount: 0,
        missedWinners: 0,
        avoidedLosers: 0,
        falsePositives: 0,
        correctIgnores: 0,
        outcomeBreakdown: [:],
        decisionBreakdown: [:],
        regimeBreakdown: [:],
        setupBreakdown: [:],
        sourceBreakdown: [:],
        bestSimulatedOpportunities: [],
        worstSimulatedAlerts: [],
        error: nil
    )

    enum CodingKeys: String, CodingKey {
        case replayPeriod = "replay_period"
        case eventCount = "event_count"
        case simulatedAlertCount = "simulated_alert_count"
        case simulatedMonitorCount = "simulated_monitor_count"
        case simulatedPrepareCount = "simulated_prepare_count"
        case simulatedBlockCount = "simulated_block_count"
        case simulatedIgnoreCount = "simulated_ignore_count"
        case simulatedRejectCount = "simulated_reject_count"
        case missedWinners = "missed_winners"
        case avoidedLosers = "avoided_losers"
        case falsePositives = "false_positives"
        case correctIgnores = "correct_ignores"
        case outcomeBreakdown = "outcome_breakdown"
        case decisionBreakdown = "decision_breakdown"
        case regimeBreakdown = "regime_breakdown"
        case setupBreakdown = "setup_breakdown"
        case sourceBreakdown = "source_breakdown"
        case bestSimulatedOpportunities = "best_simulated_opportunities"
        case worstSimulatedAlerts = "worst_simulated_alerts"
        case error
    }

    init(
        replayPeriod: ReplayPeriod,
        eventCount: Int,
        simulatedAlertCount: Int,
        simulatedMonitorCount: Int,
        simulatedPrepareCount: Int,
        simulatedBlockCount: Int,
        simulatedIgnoreCount: Int,
        simulatedRejectCount: Int,
        missedWinners: Int,
        avoidedLosers: Int,
        falsePositives: Int,
        correctIgnores: Int,
        outcomeBreakdown: [String: Int],
        decisionBreakdown: [String: Int],
        regimeBreakdown: [String: Int],
        setupBreakdown: [String: Int],
        sourceBreakdown: [String: Int],
        bestSimulatedOpportunities: [ReplayOpportunity],
        worstSimulatedAlerts: [ReplayOpportunity],
        error: String?
    ) {
        self.replayPeriod = replayPeriod
        self.eventCount = eventCount
        self.simulatedAlertCount = simulatedAlertCount
        self.simulatedMonitorCount = simulatedMonitorCount
        self.simulatedPrepareCount = simulatedPrepareCount
        self.simulatedBlockCount = simulatedBlockCount
        self.simulatedIgnoreCount = simulatedIgnoreCount
        self.simulatedRejectCount = simulatedRejectCount
        self.missedWinners = missedWinners
        self.avoidedLosers = avoidedLosers
        self.falsePositives = falsePositives
        self.correctIgnores = correctIgnores
        self.outcomeBreakdown = outcomeBreakdown
        self.decisionBreakdown = decisionBreakdown
        self.regimeBreakdown = regimeBreakdown
        self.setupBreakdown = setupBreakdown
        self.sourceBreakdown = sourceBreakdown
        self.bestSimulatedOpportunities = bestSimulatedOpportunities
        self.worstSimulatedAlerts = worstSimulatedAlerts
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        replayPeriod = try container.decodeIfPresent(ReplayPeriod.self, forKey: .replayPeriod) ?? ReplayPeriod(startDate: "", endDate: "")
        eventCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .eventCount)?.value ?? 0
        simulatedAlertCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedAlertCount)?.value ?? 0
        simulatedMonitorCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedMonitorCount)?.value ?? 0
        simulatedPrepareCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedPrepareCount)?.value ?? 0
        simulatedBlockCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedBlockCount)?.value ?? 0
        simulatedIgnoreCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedIgnoreCount)?.value ?? 0
        simulatedRejectCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .simulatedRejectCount)?.value ?? 0
        missedWinners = try container.decodeIfPresent(FlexibleInt.self, forKey: .missedWinners)?.value ?? 0
        avoidedLosers = try container.decodeIfPresent(FlexibleInt.self, forKey: .avoidedLosers)?.value ?? 0
        falsePositives = try container.decodeIfPresent(FlexibleInt.self, forKey: .falsePositives)?.value ?? 0
        correctIgnores = try container.decodeIfPresent(FlexibleInt.self, forKey: .correctIgnores)?.value ?? 0
        outcomeBreakdown = try container.decodeFlexibleIntMap(forKey: .outcomeBreakdown)
        decisionBreakdown = try container.decodeFlexibleIntMap(forKey: .decisionBreakdown)
        regimeBreakdown = try container.decodeFlexibleIntMap(forKey: .regimeBreakdown)
        setupBreakdown = try container.decodeFlexibleIntMap(forKey: .setupBreakdown)
        sourceBreakdown = try container.decodeFlexibleIntMap(forKey: .sourceBreakdown)
        bestSimulatedOpportunities = try container.decodeIfPresent([ReplayOpportunity].self, forKey: .bestSimulatedOpportunities) ?? []
        worstSimulatedAlerts = try container.decodeIfPresent([ReplayOpportunity].self, forKey: .worstSimulatedAlerts) ?? []
        error = try container.decodeIfPresent(String.self, forKey: .error)
    }
}

struct ReplayPeriod: Codable {
    let startDate: String
    let endDate: String

    enum CodingKeys: String, CodingKey {
        case startDate = "start_date"
        case endDate = "end_date"
    }
}

struct ReplayOpportunity: Codable, Identifiable {
    var id: String { "\(ticker)-\(scanTime ?? "")-\(return5d ?? 0)" }

    let ticker: String
    let return5d: Double?
    let alphaTier: String?
    let scanTime: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case return5d = "return_5d"
        case alphaTier = "alpha_tier"
        case scanTime = "scan_time"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        return5d = try container.decodeFlexibleDoubleIfPresent(forKey: .return5d)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime)
    }
}

struct ReplayEvent: Codable, Identifiable {
    var id: String { "\(numericId ?? 0)-\(runId)-\(ticker)-\(scanTime)" }

    let numericId: Int?
    let runId: String
    let shadowLogId: Int?
    let ticker: String
    let scanTime: String
    let alphaScore: Double?
    let alphaTier: String?
    let setupType: String?
    let source: String?
    let filterReason: String?
    let readinessTier: String?
    let readinessScore: Double?
    let alertReady: Bool
    let qcTier: String?
    let qcScore: Double?
    let allowNotification: Bool
    let regimeOverall: String?
    let regimeScore: Double?
    let regimeCapturedAt: String?
    let simulatedDecision: String
    let outcomeStatus: String?
    let return5d: Double?
    let return10d: Double?
    let maxGain: Double?
    let maxDrawdown: Double?
    let outcomeClassification: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case runId = "run_id"
        case shadowLogId = "shadow_log_id"
        case ticker
        case scanTime = "scan_time"
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case source
        case filterReason = "filter_reason"
        case readinessTier = "readiness_tier"
        case readinessScore = "readiness_score"
        case alertReady = "alert_ready"
        case qcTier = "qc_tier"
        case qcScore = "qc_score"
        case allowNotification = "allow_notification"
        case regimeOverall = "regime_overall"
        case regimeScore = "regime_score"
        case regimeCapturedAt = "regime_captured_at"
        case simulatedDecision = "simulated_decision"
        case outcomeStatus = "outcome_status"
        case return5d = "return_5d"
        case return10d = "return_10d"
        case maxGain = "max_gain"
        case maxDrawdown = "max_drawdown"
        case outcomeClassification = "outcome_classification"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(FlexibleInt.self, forKey: .numericId)?.value
        runId = try container.decodeIfPresent(String.self, forKey: .runId) ?? ""
        shadowLogId = try container.decodeIfPresent(FlexibleInt.self, forKey: .shadowLogId)?.value
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime) ?? ""
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        filterReason = try container.decodeIfPresent(String.self, forKey: .filterReason)
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier)
        readinessScore = try container.decodeFlexibleDoubleIfPresent(forKey: .readinessScore)
        alertReady = try container.decodeIfPresent(FlexibleBool.self, forKey: .alertReady)?.value ?? false
        qcTier = try container.decodeIfPresent(String.self, forKey: .qcTier)
        qcScore = try container.decodeFlexibleDoubleIfPresent(forKey: .qcScore)
        allowNotification = try container.decodeIfPresent(FlexibleBool.self, forKey: .allowNotification)?.value ?? false
        regimeOverall = try container.decodeIfPresent(String.self, forKey: .regimeOverall)
        regimeScore = try container.decodeFlexibleDoubleIfPresent(forKey: .regimeScore)
        regimeCapturedAt = try container.decodeIfPresent(String.self, forKey: .regimeCapturedAt)
        simulatedDecision = try container.decodeIfPresent(String.self, forKey: .simulatedDecision) ?? "WOULD_IGNORE"
        outcomeStatus = try container.decodeIfPresent(String.self, forKey: .outcomeStatus)
        return5d = try container.decodeFlexibleDoubleIfPresent(forKey: .return5d)
        return10d = try container.decodeFlexibleDoubleIfPresent(forKey: .return10d)
        maxGain = try container.decodeFlexibleDoubleIfPresent(forKey: .maxGain)
        maxDrawdown = try container.decodeFlexibleDoubleIfPresent(forKey: .maxDrawdown)
        outcomeClassification = try container.decodeIfPresent(String.self, forKey: .outcomeClassification)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

enum ReplayLabels {
    static func decision(_ value: String?) -> String {
        switch (value ?? "").uppercased() {
        case "WOULD_ALERT": return "Would alert"
        case "WOULD_PREPARE": return "Prepare/watch"
        case "WOULD_MONITOR": return "Monitor"
        case "WOULD_BLOCK": return "Blocked"
        case "WOULD_IGNORE": return "Ignore"
        case "WOULD_REJECT": return "Reject"
        default: return (value ?? "Unknown").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func outcome(_ value: String?) -> String {
        switch (value ?? "").lowercased() {
        case "missed_winner": return "Missed winner"
        case "avoided_loser": return "Avoided loser"
        case "false_positive": return "False positive"
        case "early_but_valid": return "Early but valid"
        case "too_late": return "Too late"
        case "correct_ignore": return "Correct ignore"
        case "inconclusive": return "Inconclusive"
        default: return (value ?? "Unknown").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

