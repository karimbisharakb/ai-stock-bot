import Foundation

// Market regime models split from AlphaCandidate.swift.
// MARK: - A16 Market Regime Models

struct MarketRegimeResponse: Codable {
    let regime: MarketRegimeSnapshot?
}

struct MarketRegimeHistoryResponse: Codable {
    let history: [MarketRegimeSnapshot]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        history = try container.decodeIfPresent([MarketRegimeSnapshot].self, forKey: .history) ?? []
    }
}

struct MarketRegimeRefreshResponse: Codable {
    let regime: MarketRegimeSnapshot?
}

struct MarketRegimeSnapshot: Codable, Identifiable {
    var id: String { snapshotId.map(String.init) ?? capturedAt ?? UUID().uuidString }

    let snapshotId: Int?
    let capturedAt: String?
    let overallRegime: String
    let volatilityRegime: String
    let breadthRegime: String
    let speculativeRegime: String
    let regimeScore: Double
    let riskMultiplier: Double
    let sizingMultiplier: Double
    let alphaThresholdAdjustment: Double
    let confidenceAdjustment: Double
    let explanation: String
    let warnings: [String]
    let dataQuality: String
    let points: Double?

    enum CodingKeys: String, CodingKey {
        case snapshotId = "id"
        case capturedAt = "captured_at"
        case overallRegime = "overall_regime"
        case volatilityRegime = "volatility_regime"
        case breadthRegime = "breadth_regime"
        case speculativeRegime = "speculative_regime"
        case regimeScore = "regime_score"
        case riskMultiplier = "risk_multiplier"
        case sizingMultiplier = "sizing_multiplier"
        case alphaThresholdAdjustment = "alpha_threshold_adjustment"
        case confidenceAdjustment = "confidence_adjustment"
        case explanation
        case warnings
        case dataQuality = "data_quality"
        case points
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        snapshotId = try container.decodeIfPresent(FlexibleInt.self, forKey: .snapshotId)?.value
        capturedAt = try container.decodeIfPresent(String.self, forKey: .capturedAt)
        overallRegime = try container.decodeIfPresent(String.self, forKey: .overallRegime) ?? "NEUTRAL"
        volatilityRegime = try container.decodeIfPresent(String.self, forKey: .volatilityRegime) ?? "ELEVATED"
        breadthRegime = try container.decodeIfPresent(String.self, forKey: .breadthRegime) ?? "MIXED"
        speculativeRegime = try container.decodeIfPresent(String.self, forKey: .speculativeRegime) ?? "SELECTIVE"
        regimeScore = try container.decodeFlexibleDoubleIfPresent(forKey: .regimeScore) ?? 50
        riskMultiplier = try container.decodeFlexibleDoubleIfPresent(forKey: .riskMultiplier) ?? 1
        sizingMultiplier = try container.decodeFlexibleDoubleIfPresent(forKey: .sizingMultiplier) ?? 1
        alphaThresholdAdjustment = try container.decodeFlexibleDoubleIfPresent(forKey: .alphaThresholdAdjustment) ?? 0
        confidenceAdjustment = try container.decodeFlexibleDoubleIfPresent(forKey: .confidenceAdjustment) ?? 0
        explanation = try container.decodeIfPresent(String.self, forKey: .explanation) ?? ""
        warnings = try container.decodeIfPresent([String].self, forKey: .warnings) ?? []
        dataQuality = try container.decodeIfPresent(String.self, forKey: .dataQuality) ?? "UNKNOWN"
        points = try container.decodeFlexibleDoubleIfPresent(forKey: .points)
    }
}

enum MarketRegimeLabels {
    static func label(_ value: String) -> String {
        switch value.uppercased() {
        case "RISK_ON": return "Risk-on"
        case "NEUTRAL": return "Neutral"
        case "RISK_OFF": return "Risk-off"
        case "PANIC": return "Panic"
        case "CALM": return "Calm"
        case "ELEVATED": return "Elevated"
        case "HIGH": return "High volatility"
        case "EXTREME": return "Extreme volatility"
        case "SPECULATION_ACTIVE": return "Speculation active"
        case "SELECTIVE": return "Selective"
        case "DEFENSIVE": return "Defensive"
        case "SPECULATION_DEAD": return "Speculation dead"
        case "BROAD_STRENGTH": return "Broad strength"
        case "NARROW_STRENGTH": return "Narrow strength"
        case "MIXED": return "Mixed"
        case "WEAK": return "Weak"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

// MARK: - A17 Historical Replay Models
