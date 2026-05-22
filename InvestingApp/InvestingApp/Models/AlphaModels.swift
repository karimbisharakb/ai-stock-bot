import Foundation

// Alpha domain models split from AlphaCandidate.swift.
import Foundation

struct AlphaTopResponse: Codable {
    let results: [AlphaCandidate]
    let total: Int
}

struct AlphaCandidate: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let alphaScore: Double?
    let alphaTier: String?
    let setupType: String?
    let predatorTier: String?
    let predatorScore: Double?
    let tierMatch: Bool
    let filterReason: String?
    let explanation: String
    let scanTime: String?
    let components: [String: Double]

    enum CodingKeys: String, CodingKey {
        case ticker
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case predatorTier = "predator_tier"
        case predatorScore = "predator_score"
        case tierMatch = "tier_match"
        case filterReason = "filter_reason"
        case explanation
        case scanTime = "scan_time"
        case components
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        alphaScore = try container.decodeIfPresent(Double.self, forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        predatorTier = try container.decodeIfPresent(String.self, forKey: .predatorTier)
        predatorScore = try container.decodeIfPresent(Double.self, forKey: .predatorScore)
        tierMatch = try container.decodeIfPresent(Bool.self, forKey: .tierMatch) ?? false
        filterReason = try container.decodeIfPresent(String.self, forKey: .filterReason)
        explanation = try container.decodeIfPresent(String.self, forKey: .explanation) ?? ""
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime)
        components = (try? container.decode([String: FlexibleDouble].self, forKey: .components).mapValues(\.value)) ?? [:]
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ticker, forKey: .ticker)
        try container.encodeIfPresent(alphaScore, forKey: .alphaScore)
        try container.encodeIfPresent(alphaTier, forKey: .alphaTier)
        try container.encodeIfPresent(setupType, forKey: .setupType)
        try container.encodeIfPresent(predatorTier, forKey: .predatorTier)
        try container.encodeIfPresent(predatorScore, forKey: .predatorScore)
        try container.encode(tierMatch, forKey: .tierMatch)
        try container.encodeIfPresent(filterReason, forKey: .filterReason)
        try container.encode(explanation, forKey: .explanation)
        try container.encodeIfPresent(scanTime, forKey: .scanTime)
        try container.encode(components, forKey: .components)
    }

    var topComponents: [(name: String, value: Double)] {
        components
            .sorted { lhs, rhs in
                if lhs.value == rhs.value { return lhs.key < rhs.key }
                return lhs.value > rhs.value
            }
            .prefix(3)
            .map { ($0.key, $0.value) }
    }
}

struct FlexibleDouble: Decodable {
    let value: Double

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let double = try? container.decode(Double.self) {
            value = double
        } else if let int = try? container.decode(Int.self) {
            value = Double(int)
        } else if let string = try? container.decode(String.self), let double = Double(string) {
            value = double
        } else {
            value = 0
        }
    }
}

struct BackendHealth: Codable {
    let status: String
    let dbConnected: Bool
    let predatorTickersScanned: Int
    let latestScanTime: String?

    enum CodingKeys: String, CodingKey {
        case status
        case dbConnected = "db_connected"
        case predatorTickersScanned = "predator_tickers_scanned"
        case latestScanTime = "latest_scan_time"
    }
}

struct RootHealth: Codable {
    let status: String
}

struct AlphaReport: Codable {
    let generatedAt: String?
    let errors: [String]
    let summary: AlphaReportSummary
    let dataQuality: AlphaDataQuality
    let diagnosis: [AlphaIssue]
    let recommendations: [String]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case errors
        case summary
        case dataQuality = "data_quality"
        case diagnosis
        case recommendations
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        errors = try container.decodeIfPresent([String].self, forKey: .errors) ?? []
        summary = try container.decodeIfPresent(AlphaReportSummary.self, forKey: .summary) ?? AlphaReportSummary.empty
        dataQuality = try container.decodeIfPresent(AlphaDataQuality.self, forKey: .dataQuality) ?? AlphaDataQuality.empty
        diagnosis = try container.decodeIfPresent([AlphaIssue].self, forKey: .diagnosis) ?? []
        recommendations = try container.decodeIfPresent([String].self, forKey: .recommendations) ?? []
    }
}

struct AlphaReportSummary: Codable {
    let totalUniqueScored: Int
    let tierDistribution: [String: Int]

    static let empty = AlphaReportSummary(totalUniqueScored: 0, tierDistribution: [:])

    enum CodingKeys: String, CodingKey {
        case totalUniqueScored = "total_unique_scored"
        case tierDistribution = "tier_distribution"
    }

    init(totalUniqueScored: Int, tierDistribution: [String: Int]) {
        self.totalUniqueScored = totalUniqueScored
        self.tierDistribution = tierDistribution
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalUniqueScored = try container.decodeIfPresent(Int.self, forKey: .totalUniqueScored) ?? 0
        let raw = try container.decodeIfPresent([String: FlexibleInt].self, forKey: .tierDistribution) ?? [:]
        tierDistribution = raw.mapValues(\.value)
    }
}

struct AlphaDataQuality: Codable {
    let totalScored: Int
    let missingCatalystRate: Double?
    let missingOptionsRate: Double?
    let missingRiskRewardRate: Double?
    let staleCount: Int
    let staleTickers: [String]

    static let empty = AlphaDataQuality(
        totalScored: 0,
        missingCatalystRate: nil,
        missingOptionsRate: nil,
        missingRiskRewardRate: nil,
        staleCount: 0,
        staleTickers: []
    )

    enum CodingKeys: String, CodingKey {
        case totalScored = "total_scored"
        case missingCatalystRate = "missing_catalyst_rate"
        case missingOptionsRate = "missing_options_rate"
        case missingRiskRewardRate = "missing_risk_reward_rate"
        case staleCount = "stale_count"
        case staleTickers = "stale_tickers"
    }
}

struct AlphaIssue: Codable, Identifiable {
    var id: String { code + message }

    let code: String
    let severity: String
    let message: String
}

struct AlphaOutcomesResponse: Codable {
    let results: [AlphaOutcome]
    let total: Int
    let statusFilter: String?

    enum CodingKeys: String, CodingKey {
        case results
        case total
        case statusFilter = "status_filter"
    }
}

struct AlphaOutcome: Codable, Identifiable {
    var id: String { "\(numericID ?? 0)-\(ticker)-\(scanTime)" }

    let numericID: Int?
    let ticker: String
    let scanTime: String
    let alphaScore: Double?
    let alphaTier: String?
    let setupType: String?
    let status: String
    let return1d: Double?
    let return3d: Double?
    let return5d: Double?
    let return10d: Double?
    let return20d: Double?
    let maxGain: Double?
    let maxDrawdown: Double?

    enum CodingKeys: String, CodingKey {
        case numericID = "id"
        case ticker
        case scanTime = "scan_time"
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case status
        case return1d = "return_1d"
        case return3d = "return_3d"
        case return5d = "return_5d"
        case return10d = "return_10d"
        case return20d = "return_20d"
        case maxGain = "max_gain"
        case maxDrawdown = "max_drawdown"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericID = try container.decodeIfPresent(Int.self, forKey: .numericID)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime) ?? ""
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        return1d = try container.decodeFlexibleDoubleIfPresent(forKey: .return1d)
        return3d = try container.decodeFlexibleDoubleIfPresent(forKey: .return3d)
        return5d = try container.decodeFlexibleDoubleIfPresent(forKey: .return5d)
        return10d = try container.decodeFlexibleDoubleIfPresent(forKey: .return10d)
        return20d = try container.decodeFlexibleDoubleIfPresent(forKey: .return20d)
        maxGain = try container.decodeFlexibleDoubleIfPresent(forKey: .maxGain)
        maxDrawdown = try container.decodeFlexibleDoubleIfPresent(forKey: .maxDrawdown)
    }
}

struct AlphaLearning: Codable {
    let totalComplete: Int
    let setupEffectiveness: [String: AlphaEffectiveness]
    let tierEffectiveness: [String: AlphaEffectiveness]
    let sourceEffectiveness: [String: AlphaEffectiveness]
    let falsePositiveRate: Double?
    let note: String?

    enum CodingKeys: String, CodingKey {
        case totalComplete = "total_complete"
        case setupEffectiveness = "setup_effectiveness"
        case tierEffectiveness = "tier_effectiveness"
        case sourceEffectiveness = "source_effectiveness"
        case falsePositiveRate = "false_positive_rate"
        case note
    }

    var bestSetupTypes: [(String, AlphaEffectiveness)] {
        setupEffectiveness.sorted { lhs, rhs in
            if lhs.value.avgReturn5d == rhs.value.avgReturn5d { return lhs.key < rhs.key }
            return (lhs.value.avgReturn5d ?? -.infinity) > (rhs.value.avgReturn5d ?? -.infinity)
        }
    }

    var worstSetupTypes: [(String, AlphaEffectiveness)] {
        setupEffectiveness.sorted { lhs, rhs in
            if lhs.value.avgReturn5d == rhs.value.avgReturn5d { return lhs.key < rhs.key }
            return (lhs.value.avgReturn5d ?? .infinity) < (rhs.value.avgReturn5d ?? .infinity)
        }
    }

    var componentEffectiveness: [(String, AlphaEffectiveness)] {
        tierEffectiveness.sorted { lhs, rhs in
            if lhs.value.count == rhs.value.count { return lhs.key < rhs.key }
            return lhs.value.count > rhs.value.count
        }
    }

    var falsePositivePatterns: [(String, AlphaEffectiveness)] {
        setupEffectiveness
            .filter { $0.value.falsePositiveRate != nil }
            .sorted { lhs, rhs in
                if lhs.value.falsePositiveRate == rhs.value.falsePositiveRate { return lhs.key < rhs.key }
                return (lhs.value.falsePositiveRate ?? 0) > (rhs.value.falsePositiveRate ?? 0)
            }
    }
}

struct AlphaEffectiveness: Codable {
    let count: Int
    let avgReturn5d: Double?
    let winRate: Double?
    let falsePositiveRate: Double?

    enum CodingKeys: String, CodingKey {
        case count
        case avgReturn5d = "avg_return_5d"
        case winRate = "win_rate"
        case falsePositiveRate = "false_positive_rate"
    }
}

struct AlphaLearningRecommendations: Codable {
    let generatedAt: String?
    let note: String
    let totalCompleteOutcomes: Int
    let sampleSizeWarning: String?
    let errors: [String]
    let currentWeights: [String: Double]
    let currentTierThresholds: [String: Double]
    let weightRecommendations: [AlphaWeightRecommendation]
    let thresholdRecommendations: [AlphaThresholdRecommendation]
    let topChanges: [AlphaWeightRecommendation]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case note
        case totalCompleteOutcomes = "total_complete_outcomes"
        case sampleSizeWarning = "sample_size_warning"
        case errors
        case currentWeights = "current_weights"
        case currentTierThresholds = "current_tier_thresholds"
        case weightRecommendations = "weight_recommendations"
        case thresholdRecommendations = "threshold_recommendations"
        case topChanges = "top_changes"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? "Shadow mode only"
        totalCompleteOutcomes = try container.decodeIfPresent(Int.self, forKey: .totalCompleteOutcomes) ?? 0
        sampleSizeWarning = try container.decodeIfPresent(String.self, forKey: .sampleSizeWarning)
        errors = try container.decodeIfPresent([String].self, forKey: .errors) ?? []
        currentWeights = try container.decodeFlexibleDoubleMap(forKey: .currentWeights)
        currentTierThresholds = try container.decodeFlexibleDoubleMap(forKey: .currentTierThresholds)
        weightRecommendations = try container.decodeIfPresent([AlphaWeightRecommendation].self, forKey: .weightRecommendations) ?? []
        thresholdRecommendations = try container.decodeIfPresent([AlphaThresholdRecommendation].self, forKey: .thresholdRecommendations) ?? []
        topChanges = try container.decodeIfPresent([AlphaWeightRecommendation].self, forKey: .topChanges) ?? []
    }
}

struct AlphaWeightRecommendation: Codable, Identifiable {
    var id: String { component + action }

    let component: String
    let action: String
    let currentWeight: Double?
    let rawDelta: Double?
    let shrunkDelta: Double?
    let confidence: String
    let reason: String
    let risk: String
    let sampleSize: Int

    enum CodingKeys: String, CodingKey {
        case component
        case action
        case currentWeight = "current_weight"
        case rawDelta = "raw_delta"
        case shrunkDelta = "shrunk_delta"
        case confidence
        case reason
        case risk
        case sampleSize = "sample_size"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        component = try container.decodeIfPresent(String.self, forKey: .component) ?? "unknown"
        action = try container.decodeIfPresent(String.self, forKey: .action) ?? "KEEP"
        currentWeight = try container.decodeFlexibleDoubleIfPresent(forKey: .currentWeight)
        rawDelta = try container.decodeFlexibleDoubleIfPresent(forKey: .rawDelta)
        shrunkDelta = try container.decodeFlexibleDoubleIfPresent(forKey: .shrunkDelta)
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? "No reason provided"
        risk = try container.decodeIfPresent(String.self, forKey: .risk) ?? "Unknown risk"
        sampleSize = try container.decodeIfPresent(Int.self, forKey: .sampleSize) ?? 0
    }
}

struct AlphaThresholdRecommendation: Codable, Identifiable {
    var id: String { tier + action }

    let tier: String
    let action: String
    let currentThreshold: Double?
    let suggestedDelta: Double?
    let confidence: String
    let reason: String
    let risk: String

    enum CodingKeys: String, CodingKey {
        case tier
        case action
        case currentThreshold = "current_threshold"
        case suggestedDelta = "suggested_delta"
        case confidence
        case reason
        case risk
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        tier = try container.decodeIfPresent(String.self, forKey: .tier) ?? "UNKNOWN"
        action = try container.decodeIfPresent(String.self, forKey: .action) ?? "KEEP"
        currentThreshold = try container.decodeFlexibleDoubleIfPresent(forKey: .currentThreshold)
        suggestedDelta = try container.decodeFlexibleDoubleIfPresent(forKey: .suggestedDelta)
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? "No reason provided"
        risk = try container.decodeIfPresent(String.self, forKey: .risk) ?? "Unknown risk"
    }
}

struct AlphaShadowPolicy: Codable {
    let generatedAt: String?
    let note: String
    let errors: [String]
    let currentWeights: [String: Double]
    let shadowWeights: [String: Double]
    let weightDeltas: [String: Double]
    let shadowWeightsSumToOne: Bool
    let replayStats: AlphaShadowReplayStats

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case note
        case errors
        case currentWeights = "current_weights"
        case shadowWeights = "shadow_weights"
        case weightDeltas = "weight_deltas"
        case shadowWeightsSumToOne = "shadow_weights_sum_to_one"
        case replayStats = "replay_stats"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? "Shadow policy simulation"
        errors = try container.decodeIfPresent([String].self, forKey: .errors) ?? []
        currentWeights = try container.decodeFlexibleDoubleMap(forKey: .currentWeights)
        shadowWeights = try container.decodeFlexibleDoubleMap(forKey: .shadowWeights)
        weightDeltas = try container.decodeFlexibleDoubleMap(forKey: .weightDeltas)
        shadowWeightsSumToOne = try container.decodeIfPresent(Bool.self, forKey: .shadowWeightsSumToOne) ?? false
        replayStats = try container.decodeIfPresent(AlphaShadowReplayStats.self, forKey: .replayStats) ?? .empty
    }
}

struct AlphaShadowReplayStats: Codable {
    let totalReplayed: Int
    let tierUnchanged: Int
    let tierUpgraded: Int
    let tierDowngraded: Int
    let expectedFalsePositiveReduction: Double?
    let expectedMissedWinnerRisk: Double?
    let changedCandidates: [AlphaShadowCandidateChange]

    static let empty = AlphaShadowReplayStats(
        totalReplayed: 0,
        tierUnchanged: 0,
        tierUpgraded: 0,
        tierDowngraded: 0,
        expectedFalsePositiveReduction: nil,
        expectedMissedWinnerRisk: nil,
        changedCandidates: []
    )

    enum CodingKeys: String, CodingKey {
        case totalReplayed = "total_replayed"
        case tierUnchanged = "tier_unchanged"
        case tierUpgraded = "tier_upgraded"
        case tierDowngraded = "tier_downgraded"
        case expectedFalsePositiveReduction = "expected_fp_reduction"
        case expectedMissedWinnerRisk = "expected_missed_winner_risk"
        case changedCandidates = "changed_candidates"
    }
}

struct AlphaShadowCandidateChange: Codable, Identifiable {
    var id: String { "\(ticker)-\(scanTime)-\(oldTier)-\(shadowTier)" }

    let ticker: String
    let scanTime: String
    let oldTier: String
    let shadowTier: String
    let oldScore: Double?
    let shadowScore: Double?
    let return5d: Double?
    let isWinner: Bool?
    let isFalsePositive: Bool?

    enum CodingKeys: String, CodingKey {
        case ticker
        case scanTime = "scan_time"
        case oldTier = "old_tier"
        case shadowTier = "shadow_tier"
        case oldScore = "old_score"
        case shadowScore = "shadow_score"
        case return5d = "return_5d"
        case isWinner = "is_winner"
        case isFalsePositive = "is_fp"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime) ?? ""
        oldTier = try container.decodeIfPresent(String.self, forKey: .oldTier) ?? "UNKNOWN"
        shadowTier = try container.decodeIfPresent(String.self, forKey: .shadowTier) ?? "UNKNOWN"
        oldScore = try container.decodeFlexibleDoubleIfPresent(forKey: .oldScore)
        shadowScore = try container.decodeFlexibleDoubleIfPresent(forKey: .shadowScore)
        return5d = try container.decodeFlexibleDoubleIfPresent(forKey: .return5d)
        isWinner = try container.decodeIfPresent(Bool.self, forKey: .isWinner)
        isFalsePositive = try container.decodeIfPresent(Bool.self, forKey: .isFalsePositive)
    }
}

// MARK: - L3 Proposal Models

struct AlphaProposalsResponse: Codable {
    let proposals: [AlphaProposal]
    let total: Int
    let active: Int
    let statusFilter: String?

    enum CodingKeys: String, CodingKey {
        case proposals
        case total
        case active
        case statusFilter = "status_filter"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposals = try container.decodeIfPresent([AlphaProposal].self, forKey: .proposals) ?? []
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? 0
        active = try container.decodeIfPresent(Int.self, forKey: .active) ?? 0
        statusFilter = try container.decodeIfPresent(String.self, forKey: .statusFilter)
    }
}

struct AlphaProposal: Codable, Identifiable {
    var id: String { proposalId }

    let proposalId: String
    let status: String
    let kind: String
    let evidenceSummary: String?
    let sampleSize: Int
    let confidence: String
    let riskWarning: String?
    let expectedBenefit: String?
    let expectedDownside: String?
    let createdAt: String
    let expiresAt: String
    let reviewedAt: String?
    let reviewedBy: String?
    let reviewNote: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case status, kind
        case evidenceSummary = "evidence_summary"
        case sampleSize = "sample_size"
        case confidence
        case riskWarning = "risk_warning"
        case expectedBenefit = "expected_benefit"
        case expectedDownside = "expected_downside"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case reviewedAt = "reviewed_at"
        case reviewedBy = "reviewed_by"
        case reviewNote = "review_note"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decodeIfPresent(String.self, forKey: .proposalId) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? "UNKNOWN"
        evidenceSummary = try container.decodeIfPresent(String.self, forKey: .evidenceSummary)
        sampleSize = try container.decodeIfPresent(Int.self, forKey: .sampleSize) ?? 0
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        riskWarning = try container.decodeIfPresent(String.self, forKey: .riskWarning)
        expectedBenefit = try container.decodeIfPresent(String.self, forKey: .expectedBenefit)
        expectedDownside = try container.decodeIfPresent(String.self, forKey: .expectedDownside)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt) ?? ""
        reviewedAt = try container.decodeIfPresent(String.self, forKey: .reviewedAt)
        reviewedBy = try container.decodeIfPresent(String.self, forKey: .reviewedBy)
        reviewNote = try container.decodeIfPresent(String.self, forKey: .reviewNote)
    }

    var isActive: Bool { status == "PROPOSED" || status == "APPROVED_FOR_SHADOW" }
    var isActionable: Bool { status == "PROPOSED" }
}

struct AlphaProposalShadowResults: Codable {
    let proposalId: String
    let status: String
    let kind: String
    let confidence: String
    let sampleSize: Int
    let shadowWeights: [String: Double]
    let replayStats: AlphaShadowReplayStats

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case status, kind, confidence
        case sampleSize = "sample_size"
        case shadowWeights = "shadow_weights"
        case replayStats = "replay_stats"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decodeIfPresent(String.self, forKey: .proposalId) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? ""
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? ""
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        sampleSize = try container.decodeIfPresent(Int.self, forKey: .sampleSize) ?? 0
        shadowWeights = try container.decodeFlexibleDoubleMap(forKey: .shadowWeights)
        replayStats = try container.decodeIfPresent(AlphaShadowReplayStats.self, forKey: .replayStats) ?? .empty
    }
}

struct AlphaProposalActionResponse: Codable {
    let proposalId: String
    let status: String
    let reviewedAt: String?
    let reviewedBy: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case status
        case reviewedAt = "reviewed_at"
        case reviewedBy = "reviewed_by"
    }
}

struct AlphaProposalsGenerateResponse: Codable {
    let generated: Int
    let proposals: [AlphaProposal]
    let note: String?
}

// MARK: - A6 Validation Models

struct AlphaValidationResponse: Codable {
    let results: [AlphaValidationRecord]
    let total: Int
    let setupTypeFilter: String?
    let behaviorClassFilter: String?

    enum CodingKeys: String, CodingKey {
        case results
        case total
        case setupTypeFilter = "setup_type_filter"
        case behaviorClassFilter = "behavior_class_filter"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        results = try container.decodeIfPresent([AlphaValidationRecord].self, forKey: .results) ?? []
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? results.count
        setupTypeFilter = try container.decodeIfPresent(String.self, forKey: .setupTypeFilter)
        behaviorClassFilter = try container.decodeIfPresent(String.self, forKey: .behaviorClassFilter)
    }
}

struct AlphaValidationRecord: Codable, Identifiable {
    var id: String { "\(outcomeId ?? 0)-\(ticker)-\(computedAt ?? scanTime)" }

    let outcomeId: Int?
    let ticker: String
    let scanTime: String
    let setupType: String?
    let alphaTier: String?
    let behaviorClass: String
    let validationScore: Double?
    let confidence: String
    let keySuccessReason: String?
    let keyFailureReason: String?
    let evidenceSummary: String?
    let computedAt: String?

    enum CodingKeys: String, CodingKey {
        case outcomeId = "outcome_id"
        case ticker
        case scanTime = "scan_time"
        case setupType = "setup_type"
        case alphaTier = "alpha_tier"
        case behaviorClass = "behavior_class"
        case validationScore = "validation_score"
        case confidence
        case keySuccessReason = "key_success_reason"
        case keyFailureReason = "key_failure_reason"
        case evidenceSummary = "evidence_summary"
        case computedAt = "computed_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        outcomeId = try container.decodeIfPresent(Int.self, forKey: .outcomeId)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime) ?? ""
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        behaviorClass = try container.decodeIfPresent(String.self, forKey: .behaviorClass) ?? "INCONCLUSIVE"
        validationScore = try container.decodeFlexibleDoubleIfPresent(forKey: .validationScore)
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        keySuccessReason = try container.decodeIfPresent(String.self, forKey: .keySuccessReason)
        keyFailureReason = try container.decodeIfPresent(String.self, forKey: .keyFailureReason)
        evidenceSummary = try container.decodeIfPresent(String.self, forKey: .evidenceSummary)
        computedAt = try container.decodeIfPresent(String.self, forKey: .computedAt)
    }

    var behaviorLabel: String {
        AlphaValidationBehavior.label(for: behaviorClass)
    }
}

struct AlphaValidationSummary: Codable {
    let totalValidated: Int
    let behaviorDistribution: [String: Int]
    let overallTrapRate: Double?
    let overallSustainabilityRate: Double?
    let avgValidationScore: Double?
    let bestValidatedSetups: [AlphaValidationSetupScore]
    let worstTrapProneSetups: [AlphaValidationSetupTrap]
    let validationByTier: [String: AlphaValidationTierScore]
    let note: String?

    enum CodingKeys: String, CodingKey {
        case totalValidated = "total_validated"
        case behaviorDistribution = "behavior_distribution"
        case overallTrapRate = "overall_trap_rate"
        case overallSustainabilityRate = "overall_sustainability_rate"
        case avgValidationScore = "avg_validation_score"
        case bestValidatedSetups = "best_validated_setups"
        case worstTrapProneSetups = "worst_trap_prone_setups"
        case validationByTier = "validation_by_tier"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalValidated = try container.decodeIfPresent(Int.self, forKey: .totalValidated) ?? 0
        let behaviorRaw = try container.decodeIfPresent([String: FlexibleInt].self, forKey: .behaviorDistribution) ?? [:]
        behaviorDistribution = behaviorRaw.mapValues(\.value)
        overallTrapRate = try container.decodeFlexibleDoubleIfPresent(forKey: .overallTrapRate)
        overallSustainabilityRate = try container.decodeFlexibleDoubleIfPresent(forKey: .overallSustainabilityRate)
        avgValidationScore = try container.decodeFlexibleDoubleIfPresent(forKey: .avgValidationScore)
        bestValidatedSetups = try container.decodeIfPresent([AlphaValidationSetupScore].self, forKey: .bestValidatedSetups) ?? []
        worstTrapProneSetups = try container.decodeIfPresent([AlphaValidationSetupTrap].self, forKey: .worstTrapProneSetups) ?? []
        validationByTier = try container.decodeIfPresent([String: AlphaValidationTierScore].self, forKey: .validationByTier) ?? [:]
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaValidationSetupScore: Codable, Identifiable {
    var id: String { setupType }

    let setupType: String
    let avgValidationScore: Double?

    enum CodingKeys: String, CodingKey {
        case setupType = "setup_type"
        case avgValidationScore = "avg_validation_score"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType) ?? "UNKNOWN"
        avgValidationScore = try container.decodeFlexibleDoubleIfPresent(forKey: .avgValidationScore)
    }
}

struct AlphaValidationSetupTrap: Codable, Identifiable {
    var id: String { setupType }

    let setupType: String
    let trapRate: Double?

    enum CodingKeys: String, CodingKey {
        case setupType = "setup_type"
        case trapRate = "trap_rate"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType) ?? "UNKNOWN"
        trapRate = try container.decodeFlexibleDoubleIfPresent(forKey: .trapRate)
    }
}

struct AlphaValidationTierScore: Codable {
    let count: Int
    let avgValidationScore: Double?

    enum CodingKeys: String, CodingKey {
        case count
        case avgValidationScore = "avg_validation_score"
    }
}

enum AlphaValidationBehavior {
    static let positive = Set(["SUSTAINED_TREND", "VALID_BREAKOUT", "INSTITUTIONAL_ACCUMULATION"])
    static let trapLike = Set(["SHORT_LIVED_SPIKE", "VOLATILITY_TRAP", "FAILED_SQUEEZE", "FAILED_BREAKOUT", "MEAN_REVERSION"])

    static func label(for behavior: String) -> String {
        switch behavior.uppercased() {
        case "SUSTAINED_TREND": return "Held its move"
        case "SHORT_LIVED_SPIKE": return "Quick spike then faded"
        case "VOLATILITY_TRAP": return "Too volatile / messy"
        case "FAILED_SQUEEZE": return "Squeeze failed"
        case "FAILED_BREAKOUT": return "Breakout failed"
        case "MEAN_REVERSION": return "Reversed back"
        case "INSTITUTIONAL_ACCUMULATION": return "Steady accumulation"
        case "VALID_BREAKOUT": return "Clean breakout"
        case "INCONCLUSIVE": return "Not enough proof"
        default: return behavior.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

// MARK: - A7 Alert Readiness Models

struct AlphaAlertCandidatesResponse: Codable {
    let results: [AlphaAlertCandidate]
    let total: Int
    let alertReady: Int
    let note: String?

    enum CodingKeys: String, CodingKey {
        case results
        case total
        case alertReady = "alert_ready"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        results = try container.decodeIfPresent([AlphaAlertCandidate].self, forKey: .results) ?? []
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? results.count
        alertReady = try container.decodeIfPresent(Int.self, forKey: .alertReady) ?? results.filter(\.alertReady).count
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaAlertCandidate: Codable, Identifiable {
    var id: String { "\(ticker)-\(scanTime ?? readinessTier)" }

    let ticker: String
    let readinessScore: Double?
    let readinessTier: String
    let alertReady: Bool
    let alphaScore: Double?
    let alphaTier: String?
    let setupType: String?
    let reason: String
    let blockingFactors: [String]
    let confirmationNeeded: [String]
    let suggestedWaitWindow: String?
    let scanTime: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case readinessScore = "readiness_score"
        case readinessTier = "readiness_tier"
        case alertReady = "alert_ready"
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case reason
        case blockingFactors = "blocking_factors"
        case confirmationNeeded = "confirmation_needed"
        case suggestedWaitWindow = "suggested_wait_window"
        case scanTime = "scan_time"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessScore = try container.decodeFlexibleDoubleIfPresent(forKey: .readinessScore)
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier) ?? "NOT_READY"
        alertReady = try container.decodeIfPresent(Bool.self, forKey: .alertReady) ?? false
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? ""
        blockingFactors = try container.decodeIfPresent([String].self, forKey: .blockingFactors) ?? []
        confirmationNeeded = try container.decodeIfPresent([String].self, forKey: .confirmationNeeded) ?? []
        suggestedWaitWindow = try container.decodeIfPresent(String.self, forKey: .suggestedWaitWindow)
        scanTime = try container.decodeIfPresent(String.self, forKey: .scanTime)
    }
}

struct AlphaAlertGateSummary: Codable {
    let totalEvaluated: Int
    let readinessDistribution: [String: Int]
    let alertReadyCount: Int
    let nearAlertCount: Int
    let topAlertReady: [AlphaAlertReadySummary]
    let topBlockers: [AlphaAlertGateCount]
    let topConfirmationsNeeded: [AlphaAlertGateConfirmation]
    let rejectedDueToValidation: Int
    let rejectedDueToTrapRisk: Int
    let note: String?
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case totalEvaluated = "total_evaluated"
        case readinessDistribution = "readiness_distribution"
        case alertReadyCount = "alert_ready_count"
        case nearAlertCount = "near_alert_count"
        case topAlertReady = "top_alert_ready"
        case topBlockers = "top_blockers"
        case topConfirmationsNeeded = "top_confirmations_needed"
        case rejectedDueToValidation = "rejected_due_to_validation"
        case rejectedDueToTrapRisk = "rejected_due_to_trap_risk"
        case note
        case generatedAt = "generated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalEvaluated = try container.decodeIfPresent(Int.self, forKey: .totalEvaluated) ?? 0
        let distribution = try container.decodeIfPresent([String: FlexibleInt].self, forKey: .readinessDistribution) ?? [:]
        readinessDistribution = distribution.mapValues(\.value)
        alertReadyCount = try container.decodeIfPresent(Int.self, forKey: .alertReadyCount) ?? 0
        nearAlertCount = try container.decodeIfPresent(Int.self, forKey: .nearAlertCount) ?? 0
        topAlertReady = try container.decodeIfPresent([AlphaAlertReadySummary].self, forKey: .topAlertReady) ?? []
        topBlockers = try container.decodeIfPresent([AlphaAlertGateCount].self, forKey: .topBlockers) ?? []
        topConfirmationsNeeded = try container.decodeIfPresent([AlphaAlertGateConfirmation].self, forKey: .topConfirmationsNeeded) ?? []
        rejectedDueToValidation = try container.decodeIfPresent(Int.self, forKey: .rejectedDueToValidation) ?? 0
        rejectedDueToTrapRisk = try container.decodeIfPresent(Int.self, forKey: .rejectedDueToTrapRisk) ?? 0
        note = try container.decodeIfPresent(String.self, forKey: .note)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
    }
}

struct AlphaAlertReadySummary: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let readinessScore: Double?
    let alphaTier: String?
    let setupType: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case readinessScore = "readiness_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessScore = try container.decodeFlexibleDoubleIfPresent(forKey: .readinessScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
    }
}

struct AlphaAlertGateCount: Codable, Identifiable {
    var id: String { factor }

    let factor: String
    let count: Int
}

struct AlphaAlertGateConfirmation: Codable, Identifiable {
    var id: String { confirmation }

    let confirmation: String
    let count: Int
}

enum AlphaAlertReadiness {
    static func label(for tier: String) -> String {
        switch tier.uppercased() {
        case "NOT_READY": return "Not ready"
        case "MONITOR": return "Watch only"
        case "PRE_ALERT": return "Almost ready"
        case "ALERT_READY": return "Ready for alert"
        case "RARE_ALERT": return "Rare setup"
        default: return tier.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func alertLabel(_ ready: Bool) -> String {
        ready ? "Alert-worthy" : "Needs more proof"
    }
}

// MARK: - A8/A9 Notification Dry-Run and QC Models

struct AlphaDryRunListResponse: Codable {
    let results: [AlphaDryRunNotification]
    let total: Int
    let active: Int
    let statusFilter: String?
    let note: String?

    enum CodingKeys: String, CodingKey {
        case results
        case total
        case active
        case statusFilter = "status_filter"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        results = try container.decodeIfPresent([AlphaDryRunNotification].self, forKey: .results) ?? []
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? results.count
        active = try container.decodeIfPresent(Int.self, forKey: .active) ?? results.filter { $0.status == "DRY_RUN" || $0.status == "REVIEWED" }.count
        statusFilter = try container.decodeIfPresent(String.self, forKey: .statusFilter)
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaDryRunNotification: Codable, Identifiable {
    var id: String { dryRunId }

    let dryRunId: String
    let ticker: String
    let readinessTier: String
    let alphaScore: Double?
    let alphaTier: String?
    let setupType: String?
    let messageText: String
    let reason: String?
    let blockingFactors: [String]
    let confirmationNeeded: [String]
    let status: String
    let createdAt: String
    let expiresAt: String?
    let reviewedAt: String?
    let reviewedBy: String?
    let reviewNote: String?
    let dismissedAt: String?
    let dismissedBy: String?
    let dismissReason: String?

    enum CodingKeys: String, CodingKey {
        case dryRunId = "dry_run_id"
        case ticker
        case readinessTier = "readiness_tier"
        case alphaScore = "alpha_score"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case messageText = "message_text"
        case reason
        case blockingFactors = "blocking_factors"
        case blockingFactorsJSON = "blocking_factors_json"
        case confirmationNeeded = "confirmation_needed"
        case confirmationNeededJSON = "confirmation_needed_json"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case reviewedAt = "reviewed_at"
        case reviewedBy = "reviewed_by"
        case reviewNote = "review_note"
        case dismissedAt = "dismissed_at"
        case dismissedBy = "dismissed_by"
        case dismissReason = "dismiss_reason"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        dryRunId = try container.decodeIfPresent(String.self, forKey: .dryRunId) ?? ""
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier) ?? "NOT_READY"
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        messageText = try container.decodeIfPresent(String.self, forKey: .messageText) ?? ""
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        blockingFactors = Self.decodeStringArray(container, plainKey: .blockingFactors, jsonKey: .blockingFactorsJSON)
        confirmationNeeded = Self.decodeStringArray(container, plainKey: .confirmationNeeded, jsonKey: .confirmationNeededJSON)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "DRY_RUN"
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
        reviewedAt = try container.decodeIfPresent(String.self, forKey: .reviewedAt)
        reviewedBy = try container.decodeIfPresent(String.self, forKey: .reviewedBy)
        reviewNote = try container.decodeIfPresent(String.self, forKey: .reviewNote)
        dismissedAt = try container.decodeIfPresent(String.self, forKey: .dismissedAt)
        dismissedBy = try container.decodeIfPresent(String.self, forKey: .dismissedBy)
        dismissReason = try container.decodeIfPresent(String.self, forKey: .dismissReason)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(dryRunId, forKey: .dryRunId)
        try container.encode(ticker, forKey: .ticker)
        try container.encode(readinessTier, forKey: .readinessTier)
        try container.encodeIfPresent(alphaScore, forKey: .alphaScore)
        try container.encodeIfPresent(alphaTier, forKey: .alphaTier)
        try container.encodeIfPresent(setupType, forKey: .setupType)
        try container.encode(messageText, forKey: .messageText)
        try container.encodeIfPresent(reason, forKey: .reason)
        try container.encode(blockingFactors, forKey: .blockingFactors)
        try container.encode(confirmationNeeded, forKey: .confirmationNeeded)
        try container.encode(status, forKey: .status)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encodeIfPresent(expiresAt, forKey: .expiresAt)
        try container.encodeIfPresent(reviewedAt, forKey: .reviewedAt)
        try container.encodeIfPresent(reviewedBy, forKey: .reviewedBy)
        try container.encodeIfPresent(reviewNote, forKey: .reviewNote)
        try container.encodeIfPresent(dismissedAt, forKey: .dismissedAt)
        try container.encodeIfPresent(dismissedBy, forKey: .dismissedBy)
        try container.encodeIfPresent(dismissReason, forKey: .dismissReason)
    }

    private static func decodeStringArray(
        _ container: KeyedDecodingContainer<CodingKeys>,
        plainKey: CodingKeys,
        jsonKey: CodingKeys
    ) -> [String] {
        if let values = try? container.decodeIfPresent([String].self, forKey: plainKey) {
            return values
        }
        guard
            let json = try? container.decodeIfPresent(String.self, forKey: jsonKey),
            let data = json.data(using: .utf8),
            let values = try? JSONDecoder().decode([String].self, from: data)
        else { return [] }
        return values
    }
}

struct AlphaDryRunGenerateResponse: Codable {
    let generated: Int
    let total: Int
    let dryRuns: [AlphaDryRunNotification]
    let note: String?

    enum CodingKeys: String, CodingKey {
        case generated
        case total
        case dryRuns = "dry_runs"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generated = try container.decodeIfPresent(Int.self, forKey: .generated) ?? 0
        dryRuns = try container.decodeIfPresent([AlphaDryRunNotification].self, forKey: .dryRuns) ?? []
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? dryRuns.count
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaDryRunActionResponse: Codable {
    let dryRunId: String
    let status: String
    let reviewedAt: String?
    let reviewedBy: String?
    let dismissedAt: String?
    let dismissedBy: String?

    enum CodingKeys: String, CodingKey {
        case dryRunId = "dry_run_id"
        case status
        case reviewedAt = "reviewed_at"
        case reviewedBy = "reviewed_by"
        case dismissedAt = "dismissed_at"
        case dismissedBy = "dismissed_by"
    }
}

struct AlphaNotificationQCResponse: Codable {
    let count: Int
    let records: [AlphaNotificationQCRecord]
    let note: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        records = try container.decodeIfPresent([AlphaNotificationQCRecord].self, forKey: .records) ?? []
        count = try container.decodeIfPresent(Int.self, forKey: .count) ?? records.count
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaNotificationQCRecord: Codable, Identifiable {
    var id: String { "\(numericId ?? 0)-\(ticker)-\(evaluatedAt)" }

    let numericId: Int?
    let ticker: String
    let readinessTier: String
    let alphaTier: String?
    let setupType: String?
    let alphaScore: Double?
    let readinessScore: Double?
    let qcScore: Double?
    let qcTier: String
    let allowNotification: Bool
    let suppressionReason: String?
    let cooldownRemaining: Double?
    let qualityFlags: [String]
    let noveltyScore: Double?
    let stabilityScore: Double?
    let informationGainScore: Double?
    let behaviorClass: String?
    let dryRunId: String?
    let evaluatedAt: String

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case ticker
        case readinessTier = "readiness_tier"
        case alphaTier = "alpha_tier"
        case setupType = "setup_type"
        case alphaScore = "alpha_score"
        case readinessScore = "readiness_score"
        case qcScore = "qc_score"
        case qcTier = "qc_tier"
        case allowNotification = "allow_notification"
        case suppressionReason = "suppression_reason"
        case cooldownRemaining = "cooldown_remaining"
        case qualityFlags = "quality_flags"
        case qualityFlagsJSON = "quality_flags_json"
        case noveltyScore = "novelty_score"
        case stabilityScore = "stability_score"
        case informationGainScore = "information_gain_score"
        case behaviorClass = "behavior_class"
        case dryRunId = "dry_run_id"
        case evaluatedAt = "evaluated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(Int.self, forKey: .numericId)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier) ?? "NOT_READY"
        alphaTier = try container.decodeIfPresent(String.self, forKey: .alphaTier)
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType)
        alphaScore = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaScore)
        readinessScore = try container.decodeFlexibleDoubleIfPresent(forKey: .readinessScore)
        qcScore = try container.decodeFlexibleDoubleIfPresent(forKey: .qcScore)
        qcTier = try container.decodeIfPresent(String.self, forKey: .qcTier) ?? "BLOCK"
        allowNotification = try container.decodeIfPresent(FlexibleBool.self, forKey: .allowNotification)?.value ?? false
        suppressionReason = try container.decodeIfPresent(String.self, forKey: .suppressionReason)
        cooldownRemaining = try container.decodeFlexibleDoubleIfPresent(forKey: .cooldownRemaining)
        qualityFlags = Self.decodeQualityFlags(container)
        noveltyScore = try container.decodeFlexibleDoubleIfPresent(forKey: .noveltyScore)
        stabilityScore = try container.decodeFlexibleDoubleIfPresent(forKey: .stabilityScore)
        informationGainScore = try container.decodeFlexibleDoubleIfPresent(forKey: .informationGainScore)
        behaviorClass = try container.decodeIfPresent(String.self, forKey: .behaviorClass)
        dryRunId = try container.decodeIfPresent(String.self, forKey: .dryRunId)
        evaluatedAt = try container.decodeIfPresent(String.self, forKey: .evaluatedAt) ?? ""
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(numericId, forKey: .numericId)
        try container.encode(ticker, forKey: .ticker)
        try container.encode(readinessTier, forKey: .readinessTier)
        try container.encodeIfPresent(alphaTier, forKey: .alphaTier)
        try container.encodeIfPresent(setupType, forKey: .setupType)
        try container.encodeIfPresent(alphaScore, forKey: .alphaScore)
        try container.encodeIfPresent(readinessScore, forKey: .readinessScore)
        try container.encodeIfPresent(qcScore, forKey: .qcScore)
        try container.encode(qcTier, forKey: .qcTier)
        try container.encode(allowNotification, forKey: .allowNotification)
        try container.encodeIfPresent(suppressionReason, forKey: .suppressionReason)
        try container.encodeIfPresent(cooldownRemaining, forKey: .cooldownRemaining)
        try container.encode(qualityFlags, forKey: .qualityFlags)
        try container.encodeIfPresent(noveltyScore, forKey: .noveltyScore)
        try container.encodeIfPresent(stabilityScore, forKey: .stabilityScore)
        try container.encodeIfPresent(informationGainScore, forKey: .informationGainScore)
        try container.encodeIfPresent(behaviorClass, forKey: .behaviorClass)
        try container.encodeIfPresent(dryRunId, forKey: .dryRunId)
        try container.encode(evaluatedAt, forKey: .evaluatedAt)
    }

    private static func decodeQualityFlags(_ container: KeyedDecodingContainer<CodingKeys>) -> [String] {
        if let values = try? container.decodeIfPresent([String].self, forKey: .qualityFlags) {
            return values
        }
        guard
            let json = try? container.decodeIfPresent(String.self, forKey: .qualityFlagsJSON),
            let data = json.data(using: .utf8),
            let values = try? JSONDecoder().decode([String].self, from: data)
        else { return [] }
        return values
    }
}

struct AlphaNotificationQCSummary: Codable {
    let totalEvaluated: Int
    let allowedCount: Int
    let suppressedCount: Int
    let duplicateSuppressions: Int
    let unstableSuppressions: Int
    let lowQualitySuppressions: Int
    let priorityCandidates: Int
    let cooldownActiveCount: Int
    let avgQCScore: Double?
    let qcTierDistribution: [String: Int]
    let note: String?
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case totalEvaluated = "total_evaluated"
        case allowedCount = "allowed_count"
        case suppressedCount = "suppressed_count"
        case duplicateSuppressions = "duplicate_suppressions"
        case unstableSuppressions = "unstable_suppressions"
        case lowQualitySuppressions = "low_quality_suppressions"
        case priorityCandidates = "priority_candidates"
        case cooldownActiveCount = "cooldown_active_count"
        case avgQCScore = "avg_qc_score"
        case qcTierDistribution = "qc_tier_distribution"
        case note
        case generatedAt = "generated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalEvaluated = try container.decodeIfPresent(Int.self, forKey: .totalEvaluated) ?? 0
        allowedCount = try container.decodeIfPresent(Int.self, forKey: .allowedCount) ?? 0
        suppressedCount = try container.decodeIfPresent(Int.self, forKey: .suppressedCount) ?? 0
        duplicateSuppressions = try container.decodeIfPresent(Int.self, forKey: .duplicateSuppressions) ?? 0
        unstableSuppressions = try container.decodeIfPresent(Int.self, forKey: .unstableSuppressions) ?? 0
        lowQualitySuppressions = try container.decodeIfPresent(Int.self, forKey: .lowQualitySuppressions) ?? 0
        priorityCandidates = try container.decodeIfPresent(Int.self, forKey: .priorityCandidates) ?? 0
        cooldownActiveCount = try container.decodeIfPresent(Int.self, forKey: .cooldownActiveCount) ?? 0
        avgQCScore = try container.decodeFlexibleDoubleIfPresent(forKey: .avgQCScore)
        let distribution = try container.decodeIfPresent([String: FlexibleInt].self, forKey: .qcTierDistribution) ?? [:]
        qcTierDistribution = distribution.mapValues(\.value)
        note = try container.decodeIfPresent(String.self, forKey: .note)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
    }
}

enum AlphaNotificationQC {
    static func label(for tier: String) -> String {
        switch tier.uppercased() {
        case "BLOCK": return "Blocked"
        case "SUPPRESS": return "Suppressed"
        case "ALLOW": return "Allowed in test"
        case "PRIORITY": return "Priority in test"
        default: return tier.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func allowLabel(_ allowed: Bool) -> String {
        allowed ? "Would pass QC" : "Would not send"
    }
}

struct AlphaNotificationDeliveryLogResponse: Codable {
    let count: Int
    let entries: [AlphaNotificationDeliveryEntry]
    let featureFlags: AlphaNotificationDeliveryFlags?
    let note: String?

    enum CodingKeys: String, CodingKey {
        case count
        case entries
        case featureFlags = "feature_flags"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        entries = try container.decodeIfPresent([AlphaNotificationDeliveryEntry].self, forKey: .entries) ?? []
        count = try container.decodeIfPresent(Int.self, forKey: .count) ?? entries.count
        featureFlags = try container.decodeIfPresent(AlphaNotificationDeliveryFlags.self, forKey: .featureFlags)
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }
}

struct AlphaNotificationDeliveryEntry: Codable, Identifiable {
    var id: String { "\(numericId ?? 0)-\(dryRunId)-\(sentAt ?? createdAt ?? status)" }

    let numericId: Int?
    let dryRunId: String
    let ticker: String
    let readinessTier: String?
    let messageHash: String?
    let status: String
    let reason: String?
    let providerResponse: String?
    let sentAt: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case dryRunId = "dry_run_id"
        case ticker
        case readinessTier = "readiness_tier"
        case messageHash = "message_hash"
        case status
        case reason
        case providerResponse = "provider_response"
        case sentAt = "sent_at"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(Int.self, forKey: .numericId)
        dryRunId = try container.decodeIfPresent(String.self, forKey: .dryRunId) ?? ""
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier)
        messageHash = try container.decodeIfPresent(String.self, forKey: .messageHash)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        providerResponse = try container.decodeIfPresent(String.self, forKey: .providerResponse)
        sentAt = try container.decodeIfPresent(String.self, forKey: .sentAt)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

struct AlphaNotificationDeliveryFlags: Codable {
    let enabled: Bool
    let dryRunOnly: Bool
    let minQCTier: String
    let requireReviewed: Bool

    enum CodingKeys: String, CodingKey {
        case enabled
        case dryRunOnly = "dry_run_only"
        case minQCTier = "min_qc_tier"
        case requireReviewed = "require_reviewed"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try container.decodeIfPresent(FlexibleBool.self, forKey: .enabled)?.value ?? false
        dryRunOnly = try container.decodeIfPresent(FlexibleBool.self, forKey: .dryRunOnly)?.value ?? true
        minQCTier = try container.decodeIfPresent(String.self, forKey: .minQCTier) ?? "PRIORITY"
        requireReviewed = try container.decodeIfPresent(FlexibleBool.self, forKey: .requireReviewed)?.value ?? true
    }
}

struct AlphaNotificationSendResponse: Codable {
    let dryRunId: String
    let ticker: String
    let readinessTier: String?
    let status: String
    let reason: String?
    let sentAt: String?

    enum CodingKeys: String, CodingKey {
        case dryRunId = "dry_run_id"
        case ticker
        case readinessTier = "readiness_tier"
        case status
        case reason
        case sentAt = "sent_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        dryRunId = try container.decodeIfPresent(String.self, forKey: .dryRunId) ?? ""
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        readinessTier = try container.decodeIfPresent(String.self, forKey: .readinessTier)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ERROR"
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        sentAt = try container.decodeIfPresent(String.self, forKey: .sentAt)
    }
}

enum AlphaNotificationDelivery {
    static func label(for status: String) -> String {
        switch status.uppercased() {
        case "SENT": return "Sent"
        case "BLOCKED": return "Blocked"
        case "DRY_RUN_ONLY": return "Dry-run only"
        case "NOT_REVIEWED": return "Not reviewed"
        case "QC_BLOCKED": return "QC blocked"
        case "DUPLICATE": return "Duplicate"
        case "ERROR": return "Error"
        default: return status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

// MARK: - Private helpers (must come after AlphaProposalsGenerateResponse)

struct FlexibleBool: Decodable {
    let value: Bool

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let bool = try? container.decode(Bool.self) {
            value = bool
        } else if let int = try? container.decode(Int.self) {
            value = int != 0
        } else if let double = try? container.decode(Double.self) {
            value = double != 0
        } else if let string = try? container.decode(String.self) {
            value = ["1", "true", "yes"].contains(string.lowercased())
        } else {
            value = false
        }
    }
}

struct FlexibleInt: Decodable {
    let value: Int

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let int = try? container.decode(Int.self) {
            value = int
        } else if let double = try? container.decode(Double.self) {
            value = Int(double)
        } else if let string = try? container.decode(String.self), let int = Int(string) {
            value = int
        } else {
            value = 0
        }
    }
}

extension KeyedDecodingContainer {
    func decodeFlexibleDoubleIfPresent(forKey key: Key) throws -> Double? {
        try decodeIfPresent(FlexibleDouble.self, forKey: key)?.value
    }

    func decodeFlexibleDoubleMap(forKey key: Key) throws -> [String: Double] {
        let raw = try decodeIfPresent([String: FlexibleDouble].self, forKey: key) ?? [:]
        return raw.mapValues(\.value)
    }

    func decodeFlexibleIntMap(forKey key: Key) throws -> [String: Int] {
        let raw = try decodeIfPresent([String: FlexibleInt].self, forKey: key) ?? [:]
        return raw.mapValues(\.value)
    }

    func decodeFlexibleStringArrayIfPresent(forKey key: Key) throws -> [String]? {
        if let strings = try? decodeIfPresent([String].self, forKey: key) {
            return strings
        }
        if let string = try? decodeIfPresent(String.self, forKey: key) {
            return string.isEmpty ? [] : [string]
        }
        return nil
    }
}
