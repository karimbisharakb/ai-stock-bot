import Foundation

// Strategy scorecard models split from AlphaCandidate.swift.
// MARK: - A19 Strategy Scorecard Models

struct StrategyScorecardsResponse: Codable {
    let scorecards: [StrategyScorecard]
    let behaviorMetrics: StrategyBehaviorMetrics
    let computedAt: String?

    enum CodingKeys: String, CodingKey {
        case scorecards
        case behaviorMetrics = "behavior_metrics"
        case computedAt = "computed_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scorecards = try container.decodeIfPresent([StrategyScorecard].self, forKey: .scorecards) ?? []
        behaviorMetrics = try container.decodeIfPresent(StrategyBehaviorMetrics.self, forKey: .behaviorMetrics) ?? .empty
        computedAt = try container.decodeIfPresent(String.self, forKey: .computedAt)
    }
}

struct StrategyScorecardDetailResponse: Codable {
    let scorecard: StrategyScorecard
}

struct StrategySummaryResponse: Codable {
    let totalStrategies: Int
    let strategiesWithData: Int
    let topStrategies: [StrategySummaryRank]
    let bottomStrategies: [StrategySummaryRank]
    let behaviorMetrics: StrategyBehaviorMetrics
    let priorityRecommendations: [StrategyPriorityRecommendation]
    let computedAt: String?

    enum CodingKeys: String, CodingKey {
        case totalStrategies = "total_strategies"
        case strategiesWithData = "strategies_with_data"
        case topStrategies = "top_strategies"
        case bottomStrategies = "bottom_strategies"
        case behaviorMetrics = "behavior_metrics"
        case priorityRecommendations = "priority_recommendations"
        case computedAt = "computed_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalStrategies = try container.decodeIfPresent(FlexibleInt.self, forKey: .totalStrategies)?.value ?? 0
        strategiesWithData = try container.decodeIfPresent(FlexibleInt.self, forKey: .strategiesWithData)?.value ?? 0
        topStrategies = try container.decodeIfPresent([StrategySummaryRank].self, forKey: .topStrategies) ?? []
        bottomStrategies = try container.decodeIfPresent([StrategySummaryRank].self, forKey: .bottomStrategies) ?? []
        behaviorMetrics = try container.decodeIfPresent(StrategyBehaviorMetrics.self, forKey: .behaviorMetrics) ?? .empty
        priorityRecommendations = try container.decodeIfPresent([StrategyPriorityRecommendation].self, forKey: .priorityRecommendations) ?? []
        computedAt = try container.decodeIfPresent(String.self, forKey: .computedAt)
    }
}

struct StrategySummaryRank: Codable, Identifiable {
    var id: String { strategy }

    let strategy: String
    let riskAdjustedScore: Double?

    enum CodingKeys: String, CodingKey {
        case strategy
        case riskAdjustedScore = "risk_adjusted_score"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        strategy = try container.decodeIfPresent(String.self, forKey: .strategy) ?? ""
        riskAdjustedScore = try container.decodeFlexibleDoubleIfPresent(forKey: .riskAdjustedScore)
    }
}

struct StrategyPriorityRecommendation: Codable, Identifiable {
    var id: String { "\(strategy)-\(recommendation)" }

    let strategy: String
    let recommendation: String
    let description: String
}

struct StrategyBehaviorMetrics: Codable {
    let overused: [String]
    let underused: [String]
    let bestHistorical: [String]
    let worstDrawdowns: [String]
    let checklistNeglect: [String]
    let weakThesis: [String]
    let repeatedFalsePositives: [String]

    static let empty = StrategyBehaviorMetrics(
        overused: [],
        underused: [],
        bestHistorical: [],
        worstDrawdowns: [],
        checklistNeglect: [],
        weakThesis: [],
        repeatedFalsePositives: []
    )

    enum CodingKeys: String, CodingKey {
        case overused
        case underused
        case bestHistorical = "best_historical"
        case worstDrawdowns = "worst_drawdowns"
        case checklistNeglect = "checklist_neglect"
        case weakThesis = "weak_thesis"
        case repeatedFalsePositives = "repeated_false_positives"
    }

    init(
        overused: [String],
        underused: [String],
        bestHistorical: [String],
        worstDrawdowns: [String],
        checklistNeglect: [String],
        weakThesis: [String],
        repeatedFalsePositives: [String]
    ) {
        self.overused = overused
        self.underused = underused
        self.bestHistorical = bestHistorical
        self.worstDrawdowns = worstDrawdowns
        self.checklistNeglect = checklistNeglect
        self.weakThesis = weakThesis
        self.repeatedFalsePositives = repeatedFalsePositives
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        overused = try container.decodeIfPresent([String].self, forKey: .overused) ?? []
        underused = try container.decodeIfPresent([String].self, forKey: .underused) ?? []
        bestHistorical = try container.decodeIfPresent([String].self, forKey: .bestHistorical) ?? []
        worstDrawdowns = try container.decodeIfPresent([String].self, forKey: .worstDrawdowns) ?? []
        checklistNeglect = try container.decodeIfPresent([String].self, forKey: .checklistNeglect) ?? []
        weakThesis = try container.decodeIfPresent([String].self, forKey: .weakThesis) ?? []
        repeatedFalsePositives = try container.decodeIfPresent([String].self, forKey: .repeatedFalsePositives) ?? []
    }
}

struct StrategyScorecard: Codable, Identifiable {
    var id: String { strategy }

    let strategy: String
    let totalCandidates: Int
    let totalDecisions: Int
    let activePositions: Int
    let closedPositions: Int
    let winRate: Double?
    let avgReturn: Double?
    let avgMaxGain: Double?
    let avgMaxDrawdown: Double?
    let validationQuality: Double?
    let falsePositiveRate: Double?
    let stressSensitivity: Double?
    let thesisCompleteness: Double?
    let checklistDisciplineScore: Double?
    let riskAdjustedScore: Double?
    let confidenceScore: Double?
    let dataAvailable: Bool
    let recommendations: [String]
    let behaviorMetrics: StrategyBehaviorMetrics?
    let computedAt: String?

    enum CodingKeys: String, CodingKey {
        case strategy
        case totalCandidates = "total_candidates"
        case totalDecisions = "total_decisions"
        case activePositions = "active_positions"
        case closedPositions = "closed_positions"
        case winRate = "win_rate"
        case avgReturn = "avg_return"
        case avgMaxGain = "avg_max_gain"
        case avgMaxDrawdown = "avg_max_drawdown"
        case validationQuality = "validation_quality"
        case falsePositiveRate = "false_positive_rate"
        case stressSensitivity = "stress_sensitivity"
        case thesisCompleteness = "thesis_completeness"
        case checklistDisciplineScore = "checklist_discipline_score"
        case riskAdjustedScore = "risk_adjusted_score"
        case confidenceScore = "confidence_score"
        case dataAvailable = "data_available"
        case recommendations
        case behaviorMetrics = "behavior_metrics"
        case computedAt = "computed_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        strategy = try container.decodeIfPresent(String.self, forKey: .strategy) ?? ""
        totalCandidates = try container.decodeIfPresent(FlexibleInt.self, forKey: .totalCandidates)?.value ?? 0
        totalDecisions = try container.decodeIfPresent(FlexibleInt.self, forKey: .totalDecisions)?.value ?? 0
        activePositions = try container.decodeIfPresent(FlexibleInt.self, forKey: .activePositions)?.value ?? 0
        closedPositions = try container.decodeIfPresent(FlexibleInt.self, forKey: .closedPositions)?.value ?? 0
        winRate = try container.decodeFlexibleDoubleIfPresent(forKey: .winRate)
        avgReturn = try container.decodeFlexibleDoubleIfPresent(forKey: .avgReturn)
        avgMaxGain = try container.decodeFlexibleDoubleIfPresent(forKey: .avgMaxGain)
        avgMaxDrawdown = try container.decodeFlexibleDoubleIfPresent(forKey: .avgMaxDrawdown)
        validationQuality = try container.decodeFlexibleDoubleIfPresent(forKey: .validationQuality)
        falsePositiveRate = try container.decodeFlexibleDoubleIfPresent(forKey: .falsePositiveRate)
        stressSensitivity = try container.decodeFlexibleDoubleIfPresent(forKey: .stressSensitivity)
        thesisCompleteness = try container.decodeFlexibleDoubleIfPresent(forKey: .thesisCompleteness)
        checklistDisciplineScore = try container.decodeFlexibleDoubleIfPresent(forKey: .checklistDisciplineScore)
        riskAdjustedScore = try container.decodeFlexibleDoubleIfPresent(forKey: .riskAdjustedScore)
        confidenceScore = try container.decodeFlexibleDoubleIfPresent(forKey: .confidenceScore)
        dataAvailable = try container.decodeIfPresent(FlexibleBool.self, forKey: .dataAvailable)?.value ?? false
        recommendations = try container.decodeIfPresent([String].self, forKey: .recommendations) ?? []
        behaviorMetrics = try container.decodeIfPresent(StrategyBehaviorMetrics.self, forKey: .behaviorMetrics)
        computedAt = try container.decodeIfPresent(String.self, forKey: .computedAt)
    }

    var strengths: [String] {
        var items: [String] = []
        if let score = riskAdjustedScore, score >= 70 { items.append("Strong risk-adjusted behavior") }
        if let winRate, winRate >= 60 { items.append("Win rate is strong") }
        if let discipline = checklistDisciplineScore, discipline >= 70 { items.append("Checklist discipline is strong") }
        if let thesis = thesisCompleteness, thesis >= 70 { items.append("Thesis quality is strong") }
        if activePositions > 0 { items.append("\(activePositions) active position(s)") }
        return items
    }

    var weaknesses: [String] {
        var items: [String] = []
        if let drawdown = avgMaxDrawdown, drawdown <= -15 { items.append("Drawdowns need sizing control") }
        if let falsePositiveRate, falsePositiveRate >= 30 { items.append("False-positive rate is elevated") }
        if let discipline = checklistDisciplineScore, discipline < 50 { items.append("Checklist discipline is weak") }
        if let thesis = thesisCompleteness, thesis < 40 { items.append("Thesis quality needs work") }
        if !dataAvailable { items.append("Not enough personal history yet") }
        return items
    }
}

enum StrategyLabels {
    static func name(_ value: String?) -> String {
        switch (value ?? "").uppercased() {
        case "CORE_INDEX": return "Core index"
        case "GROWTH_COMPOUNDER": return "Growth compounder"
        case "AI_SEMI_MOMENTUM": return "AI/semi momentum"
        case "SPACE_DEFENSE": return "Space & defense"
        case "CRYPTO_BETA": return "Crypto beta"
        case "SHORT_SQUEEZE": return "Short squeeze"
        case "EVENT_CATALYST": return "Event catalyst"
        case "BREAKOUT_MOMENTUM": return "Breakout momentum"
        case "EARLY_ACCUMULATION": return "Early accumulation"
        case "SPECULATIVE_HIGH_VOL": return "Speculative high-vol"
        case "CASH_DEFENSIVE": return "Cash defensive"
        default: return (value ?? "Unknown").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func recommendation(_ value: String) -> String {
        switch value {
        case "promote_to_core": return "Strong long-term behavior"
        case "increase_focus": return "Historically effective"
        case "reduce_exposure": return "Reduce exposure"
        case "require_stricter_checklist": return "Needs stricter checklist"
        case "improve_thesis_quality": return "Improve thesis quality"
        case "use_smaller_sizing": return "Use smaller sizing"
        case "avoid_during_risk_off": return "Avoid during risk-off"
        case "monitor_only": return "Monitor only"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

