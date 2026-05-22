import Foundation

// Planner models split from AlphaCandidate.swift.
// MARK: - A20 Long-Horizon Planner Models

struct PlannerSummaryResponse: Codable {
    let snapshot: PlannerSnapshot?
}

struct PlannerProjectionsResponse: Codable {
    let projections: PlannerProjections?
    let monthlyContribution: Double?
    let portfolioValue: Double?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case projections
        case monthlyContribution = "monthly_contribution"
        case portfolioValue = "portfolio_value"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        projections = try container.decodeIfPresent(PlannerProjections.self, forKey: .projections)
        monthlyContribution = try container.decodeFlexibleDoubleIfPresent(forKey: .monthlyContribution)
        portfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .portfolioValue)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

struct PlannerRefreshResponse: Codable {
    let planner: PlannerSnapshot
}

struct PlannerSnapshot: Codable, Identifiable {
    var id: String { snapshotId }

    let numericId: Int?
    let snapshotId: String
    let createdAt: String?
    let portfolioValue: Double
    let cash: Double
    let regime: String
    let riskScore: Double
    let rebalanceUrgency: String
    let currentAllocation: [String: Double]
    let targetAllocation: [String: Double]
    let drift: [String: Double]
    let priorityAreas: [PlannerPriorityArea]
    let cashDeploymentGuidance: String
    let contributionGuidance: String
    let riskReductionGuidance: String
    let strategyAlignmentNotes: [String]
    let monthlyContribution: Double?
    let projections: PlannerProjections?

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case snapshotId = "snapshot_id"
        case createdAt = "created_at"
        case portfolioValue = "portfolio_value"
        case cash
        case regime
        case riskScore = "risk_score"
        case rebalanceUrgency = "rebalance_urgency"
        case currentAllocation = "current_allocation"
        case targetAllocation = "target_allocation"
        case drift
        case priorityAreas = "priority_areas"
        case cashDeploymentGuidance = "cash_deployment_guidance"
        case contributionGuidance = "contribution_guidance"
        case riskReductionGuidance = "risk_reduction_guidance"
        case strategyAlignmentNotes = "strategy_alignment_notes"
        case monthlyContribution = "monthly_contribution"
        case projections
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(FlexibleInt.self, forKey: .numericId)?.value
        snapshotId = try container.decodeIfPresent(String.self, forKey: .snapshotId) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        portfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .portfolioValue) ?? 0
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash) ?? 0
        regime = try container.decodeIfPresent(String.self, forKey: .regime) ?? "NEUTRAL"
        riskScore = try container.decodeFlexibleDoubleIfPresent(forKey: .riskScore) ?? 50
        rebalanceUrgency = try container.decodeIfPresent(String.self, forKey: .rebalanceUrgency) ?? "NONE"
        currentAllocation = try container.decodeFlexibleDoubleMap(forKey: .currentAllocation)
        targetAllocation = try container.decodeFlexibleDoubleMap(forKey: .targetAllocation)
        drift = try container.decodeFlexibleDoubleMap(forKey: .drift)
        priorityAreas = try container.decodeIfPresent([PlannerPriorityArea].self, forKey: .priorityAreas) ?? []
        cashDeploymentGuidance = try container.decodeIfPresent(String.self, forKey: .cashDeploymentGuidance) ?? ""
        contributionGuidance = try container.decodeIfPresent(String.self, forKey: .contributionGuidance) ?? ""
        riskReductionGuidance = try container.decodeIfPresent(String.self, forKey: .riskReductionGuidance) ?? ""
        strategyAlignmentNotes = try container.decodeIfPresent([String].self, forKey: .strategyAlignmentNotes) ?? []
        monthlyContribution = try container.decodeFlexibleDoubleIfPresent(forKey: .monthlyContribution)
        projections = try container.decodeIfPresent(PlannerProjections.self, forKey: .projections)
    }

    func allocationRows() -> [PlannerAllocationRow] {
        PlannerLabels.buckets.map { bucket in
            PlannerAllocationRow(
                bucket: bucket,
                currentPct: currentAllocation[bucket] ?? 0,
                targetPct: targetAllocation[bucket] ?? 0,
                driftPct: drift[bucket] ?? 0
            )
        }
    }
}

struct PlannerPriorityArea: Codable, Identifiable {
    var id: String { bucket }

    let bucket: String
    let driftPct: Double
    let action: String

    enum CodingKeys: String, CodingKey {
        case bucket
        case driftPct = "drift_pct"
        case action
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        bucket = try container.decodeIfPresent(String.self, forKey: .bucket) ?? ""
        driftPct = try container.decodeFlexibleDoubleIfPresent(forKey: .driftPct) ?? 0
        action = try container.decodeIfPresent(String.self, forKey: .action) ?? ""
    }
}

struct PlannerAllocationRow: Identifiable {
    var id: String { bucket }

    let bucket: String
    let currentPct: Double
    let targetPct: Double
    let driftPct: Double

    var status: String {
        let absDrift = abs(driftPct)
        if absDrift < 3 { return "On target" }
        if absDrift < 8 { return driftPct > 0 ? "Slightly high" : "Slightly low" }
        return driftPct > 0 ? "Over target" : "Under target"
    }

    var explanation: String {
        if abs(driftPct) < 3 {
            return "Current allocation is close to target."
        }
        let direction = driftPct > 0 ? "above" : "below"
        return "\(PlannerLabels.bucket(bucket)) is \(String(format: "%.1f", abs(driftPct))) pp \(direction) target. Review gradually; no automatic action."
    }
}

struct PlannerProjections: Codable {
    let monthlyContribution: Double
    let startingValue: Double
    let conservative: [PlannerProjection]
    let base: [PlannerProjection]
    let aggressive: [PlannerProjection]
    let downside: [PlannerProjection]

    enum CodingKeys: String, CodingKey {
        case monthlyContribution = "monthly_contribution"
        case startingValue = "starting_value"
        case conservative
        case base
        case aggressive
        case downside
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        monthlyContribution = try container.decodeFlexibleDoubleIfPresent(forKey: .monthlyContribution) ?? 0
        startingValue = try container.decodeFlexibleDoubleIfPresent(forKey: .startingValue) ?? 0
        conservative = try container.decodeIfPresent([PlannerProjection].self, forKey: .conservative) ?? []
        base = try container.decodeIfPresent([PlannerProjection].self, forKey: .base) ?? []
        aggressive = try container.decodeIfPresent([PlannerProjection].self, forKey: .aggressive) ?? []
        downside = try container.decodeIfPresent([PlannerProjection].self, forKey: .downside) ?? []
    }

    var allScenarios: [(name: String, rows: [PlannerProjection])] {
        [
            ("conservative", conservative),
            ("base", base),
            ("aggressive", aggressive),
            ("downside", downside)
        ]
    }
}

struct PlannerProjection: Codable, Identifiable {
    var id: String { "\(scenario)-\(years)" }

    let scenario: String
    let years: Int
    let startingValue: Double
    let projectedValue: Double
    let totalContributed: Double
    let contributionImpact: Double
    let compoundingImpact: Double
    let bucketValues: [String: Double]

    enum CodingKeys: String, CodingKey {
        case scenario
        case years
        case startingValue = "starting_value"
        case projectedValue = "projected_value"
        case totalContributed = "total_contributed"
        case contributionImpact = "contribution_impact"
        case compoundingImpact = "compounding_impact"
        case bucketValues = "bucket_values"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scenario = try container.decodeIfPresent(String.self, forKey: .scenario) ?? ""
        years = try container.decodeIfPresent(FlexibleInt.self, forKey: .years)?.value ?? 0
        startingValue = try container.decodeFlexibleDoubleIfPresent(forKey: .startingValue) ?? 0
        projectedValue = try container.decodeFlexibleDoubleIfPresent(forKey: .projectedValue) ?? 0
        totalContributed = try container.decodeFlexibleDoubleIfPresent(forKey: .totalContributed) ?? 0
        contributionImpact = try container.decodeFlexibleDoubleIfPresent(forKey: .contributionImpact) ?? 0
        compoundingImpact = try container.decodeFlexibleDoubleIfPresent(forKey: .compoundingImpact) ?? 0
        bucketValues = try container.decodeFlexibleDoubleMap(forKey: .bucketValues)
    }
}

enum PlannerLabels {
    static let buckets = [
        "CORE_INDEX",
        "QUALITY_GROWTH",
        "ALPHA_OPPORTUNITY",
        "SPECULATIVE",
        "CASH_RESERVE",
        "CANADIAN_EXPOSURE",
        "USD_EXPOSURE"
    ]

    static func urgency(_ value: String) -> String {
        switch value.uppercased() {
        case "NONE": return "No rebalance needed"
        case "LOW": return "Minor rebalance"
        case "MEDIUM": return "Moderate rebalance"
        case "HIGH": return "High rebalance urgency"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func bucket(_ value: String) -> String {
        switch value.uppercased() {
        case "CORE_INDEX": return "Core index"
        case "QUALITY_GROWTH": return "Quality growth"
        case "ALPHA_OPPORTUNITY": return "Alpha opportunities"
        case "SPECULATIVE": return "Speculative"
        case "CASH_RESERVE": return "Cash reserve"
        case "CANADIAN_EXPOSURE": return "Canadian exposure"
        case "USD_EXPOSURE": return "USD exposure"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

