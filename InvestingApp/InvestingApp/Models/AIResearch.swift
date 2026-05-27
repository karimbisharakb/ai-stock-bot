import Foundation

// MARK: - Persona

struct PersonaInfo: Codable, Identifiable {
    var id: String { key }
    let key: String
    let name: String
    let emoji: String
    let color: String
    let intro: String
}

struct PersonasResponse: Codable {
    let personas: [PersonaInfo]
}

// MARK: - Chat

struct ChatMessage: Codable, Identifiable {
    var id: String { "\(dbId ?? 0)-\(role)-\(timestamp ?? UUID().uuidString)" }
    let dbId: Int?
    let role: String          // "user" | "assistant"
    let content: String
    let persona: String?
    let personaName: String?
    let personaEmoji: String?
    let ticker: String?
    let timestamp: String?

    enum CodingKeys: String, CodingKey {
        case role, content, persona, ticker, timestamp
        case dbId = "id"
        case personaName = "persona_name"
        case personaEmoji = "persona_emoji"
    }
}

struct ChatResponse: Codable {
    let response: String
    let persona: String
    let personaName: String
    let personaEmoji: String
    let sessionId: String

    enum CodingKeys: String, CodingKey {
        case response, persona
        case personaName = "persona_name"
        case personaEmoji = "persona_emoji"
        case sessionId = "session_id"
    }
}

struct ChatHistoryResponse: Codable {
    let messages: [ChatMessage]
    let sessionId: String

    enum CodingKeys: String, CodingKey {
        case messages
        case sessionId = "session_id"
    }
}

// MARK: - Ticker Comparison

struct CompareResult: Codable {
    let tickers: [String]
    let perspective: String
    let rows: [CompareRow]
    let verdict: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tickers = try c.decodeIfPresent([String].self, forKey: .tickers) ?? []
        perspective = try c.decodeIfPresent(String.self, forKey: .perspective) ?? "ALL"
        rows = try c.decodeIfPresent([CompareRow].self, forKey: .rows) ?? []
        verdict = try c.decodeIfPresent(String.self, forKey: .verdict) ?? ""
    }

    enum CodingKeys: String, CodingKey { case tickers, perspective, rows, verdict }
}

struct CompareRow: Codable, Identifiable {
    var id: String { metric }
    let metric: String
    let values: [String: String]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        metric = try c.decodeIfPresent(String.self, forKey: .metric) ?? ""
        values = try c.decodeIfPresent([String: String].self, forKey: .values) ?? [:]
    }

    enum CodingKeys: String, CodingKey { case metric, values }
}

// MARK: - Social Trending

struct SocialTrend: Codable, Identifiable {
    var id: String { ticker + (subreddit ?? "") + (scannedAt ?? "") }
    let ticker: String
    let mentionCount: Int
    let sentimentScore: Double
    let sentimentLabel: String
    let samplePostUrl: String?
    let subreddit: String?
    let scannedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case mentionCount = "mention_count"
        case sentimentScore = "sentiment_score"
        case sentimentLabel = "sentiment_label"
        case samplePostUrl = "sample_post_url"
        case subreddit
        case scannedAt = "scanned_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try c.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        if let fi = try? c.decodeIfPresent(FlexibleInt.self, forKey: .mentionCount) { mentionCount = fi.value } else { mentionCount = 0 }
        sentimentScore = (try? c.decodeFlexibleDoubleIfPresent(forKey: .sentimentScore)) ?? 0
        sentimentLabel = try c.decodeIfPresent(String.self, forKey: .sentimentLabel) ?? "NEUTRAL"
        samplePostUrl = try c.decodeIfPresent(String.self, forKey: .samplePostUrl)
        subreddit = try c.decodeIfPresent(String.self, forKey: .subreddit)
        scannedAt = try c.decodeIfPresent(String.self, forKey: .scannedAt)
    }
}

struct SocialTrendingResponse: Codable {
    let trending: [SocialTrend]
    let fromCache: Bool
    let scannedAt: String?

    enum CodingKeys: String, CodingKey {
        case trending
        case fromCache = "from_cache"
        case scannedAt = "scanned_at"
    }
}

// MARK: - News Impact

struct NewsImpactAffected: Codable, Identifiable {
    var id: String { ticker + direction }
    let ticker: String
    let direction: String     // "POSITIVE" | "NEGATIVE" | "NEUTRAL"
    let confidence: String    // "HIGH" | "MEDIUM" | "LOW"
    let reason: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try c.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        direction = try c.decodeIfPresent(String.self, forKey: .direction) ?? "NEUTRAL"
        confidence = try c.decodeIfPresent(String.self, forKey: .confidence) ?? "LOW"
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
    }

    enum CodingKeys: String, CodingKey { case ticker, direction, confidence, reason }
}

struct NewsImpactItem: Codable, Identifiable {
    var id: String { "\(dbId ?? 0)-\(timestamp ?? "")" }
    let dbId: Int?
    let headline: String
    let source: String
    let affected: [NewsImpactAffected]
    let impactAnalysis: String
    let timestamp: String?

    enum CodingKeys: String, CodingKey {
        case headline, source, affected, timestamp
        case dbId = "id"
        case impactAnalysis = "impact_analysis"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let fi = try? c.decodeIfPresent(FlexibleInt.self, forKey: .dbId) { dbId = fi.value } else { dbId = nil }
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        affected = try c.decodeIfPresent([NewsImpactAffected].self, forKey: .affected) ?? []
        impactAnalysis = try c.decodeIfPresent(String.self, forKey: .impactAnalysis) ?? ""
        timestamp = try c.decodeIfPresent(String.self, forKey: .timestamp)
    }
}

struct NewsImpactListResponse: Codable {
    let items: [NewsImpactItem]
}

// MARK: - Market Brief

struct MarketBriefData: Codable {
    let briefText: String
    let keyMetrics: [String: String]
    let generatedAt: String
    let fromCache: Bool

    enum CodingKeys: String, CodingKey {
        case briefText = "brief_text"
        case keyMetrics = "key_metrics"
        case generatedAt = "generated_at"
        case fromCache = "from_cache"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        briefText = try c.decodeIfPresent(String.self, forKey: .briefText) ?? ""
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        fromCache = try c.decodeIfPresent(Bool.self, forKey: .fromCache) ?? false

        // key_metrics may be [String: Double] or [String: String] — decode flexibly
        if let strMap = try? c.decodeIfPresent([String: String].self, forKey: .keyMetrics) {
            keyMetrics = strMap ?? [:]
        } else if let dblMap = try? c.decodeIfPresent([String: Double].self, forKey: .keyMetrics) {
            keyMetrics = (dblMap ?? [:]).mapValues { String(format: "%.2f", $0) }
        } else {
            keyMetrics = [:]
        }
    }
}

// MARK: - Sector Heatmap

struct SectorData: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let sectorName: String
    let changePct: Double
    let price: Double?

    enum CodingKeys: String, CodingKey {
        case ticker, price
        case sectorName = "sector_name"
        case changePct = "change_pct"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let t = try c.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        ticker = t
        sectorName = try c.decodeIfPresent(String.self, forKey: .sectorName) ?? t
        changePct = (try? c.decodeFlexibleDoubleIfPresent(forKey: .changePct)) ?? 0
        price = try? c.decodeFlexibleDoubleIfPresent(forKey: .price)
    }
}

struct SectorHeatmapResponse: Codable {
    let sectors: [SectorData]
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case sectors
        case generatedAt = "generated_at"
    }
}

// MARK: - Saved Research

struct SavedResearchItem: Codable, Identifiable {
    var id: String { "\(dbId ?? 0)" }
    let dbId: Int?
    let title: String
    let snippet: String
    let content: String
    let tickers: [String]
    let persona: String
    let personaName: String
    let personaEmoji: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case title, snippet, content, tickers, persona
        case dbId = "id"
        case personaName = "persona_name"
        case personaEmoji = "persona_emoji"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dbId = try c.decodeIfPresent(FlexibleInt.self, forKey: .dbId)?.value
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        snippet = try c.decodeIfPresent(String.self, forKey: .snippet) ?? ""
        content = try c.decodeIfPresent(String.self, forKey: .content) ?? ""
        tickers = try c.decodeIfPresent([String].self, forKey: .tickers) ?? []
        persona = try c.decodeIfPresent(String.self, forKey: .persona) ?? "VALUE"
        personaName = try c.decodeIfPresent(String.self, forKey: .personaName) ?? "Value Investor"
        personaEmoji = try c.decodeIfPresent(String.self, forKey: .personaEmoji) ?? "🏰"
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

struct SavedResearchListResponse: Codable {
    let items: [SavedResearchItem]
}
