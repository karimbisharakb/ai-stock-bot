import Foundation

// Research workflow and watchlist models split from AlphaCandidate.swift.
struct ResearchSparkPoint: Codable, Identifiable {
    var id: String { t + String(v) }
    let t: String
    let v: Double

    enum CodingKeys: String, CodingKey { case t, v }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        t = try container.decodeIfPresent(String.self, forKey: .t) ?? ""
        v = try container.decodeFlexibleDoubleIfPresent(forKey: .v) ?? 0
    }
}

struct MarketPulseResponse: Codable {
    let period: String
    let market: [MarketTickerSnapshot]
    let sectors: [MarketTickerSnapshot]
    let disclaimer: String?
}

struct MarketTickerSnapshot: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let label: String?
    let sectorName: String?
    let period: String?
    let price: Double?
    let prevClose: Double?
    let changePct: Double?
    let sparkline: [ResearchSparkPoint]
    let disclaimer: String?

    enum CodingKeys: String, CodingKey {
        case ticker, label, period, price, sparkline, disclaimer
        case sectorName = "sector_name"
        case prevClose = "prev_close"
        case changePct = "change_pct"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        label = try container.decodeIfPresent(String.self, forKey: .label)
        sectorName = try container.decodeIfPresent(String.self, forKey: .sectorName)
        period = try container.decodeIfPresent(String.self, forKey: .period)
        price = try container.decodeFlexibleDoubleIfPresent(forKey: .price)
        prevClose = try container.decodeFlexibleDoubleIfPresent(forKey: .prevClose)
        changePct = try container.decodeFlexibleDoubleIfPresent(forKey: .changePct)
        sparkline = try container.decodeIfPresent([ResearchSparkPoint].self, forKey: .sparkline) ?? []
        disclaimer = try container.decodeIfPresent(String.self, forKey: .disclaimer)
    }
}

struct SectorPerformanceResponse: Codable {
    let period: String
    let sectors: [SectorPerformanceRow]
    let disclaimer: String?
}

struct SectorPerformanceRow: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let sectorName: String
    let changePct: Double?
    let price: Double?

    enum CodingKeys: String, CodingKey {
        case ticker, price
        case sectorName = "sector_name"
        case changePct = "change_pct"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        sectorName = try container.decodeIfPresent(String.self, forKey: .sectorName) ?? ticker
        changePct = try container.decodeFlexibleDoubleIfPresent(forKey: .changePct)
        price = try container.decodeFlexibleDoubleIfPresent(forKey: .price)
    }
}

struct StockResearchResponse: Codable {
    let ticker: String
    let period: String
    let name: String?
    let sector: String?
    let industry: String?
    let quote: ResearchQuote
    let fundamentals: StockFundamentals
    let technicals: StockTechnicals
    let sparkline: [ResearchSparkPoint]
    let disclaimer: String?
}

struct ResearchQuote: Codable {
    let price: Double?
    let prevClose: Double?
    let changePct: Double?
    let marketCap: Double?
    let volume: Double?
    let avgVolume: Double?

    enum CodingKeys: String, CodingKey {
        case price, volume
        case prevClose = "prev_close"
        case changePct = "change_pct"
        case marketCap = "market_cap"
        case avgVolume = "avg_volume"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        price = try container.decodeFlexibleDoubleIfPresent(forKey: .price)
        prevClose = try container.decodeFlexibleDoubleIfPresent(forKey: .prevClose)
        changePct = try container.decodeFlexibleDoubleIfPresent(forKey: .changePct)
        marketCap = try container.decodeFlexibleDoubleIfPresent(forKey: .marketCap)
        volume = try container.decodeFlexibleDoubleIfPresent(forKey: .volume)
        avgVolume = try container.decodeFlexibleDoubleIfPresent(forKey: .avgVolume)
    }
}

struct StockFundamentals: Codable {
    let peRatio: Double?
    let forwardPe: Double?
    let roePct: Double?
    let debtEquity: Double?
    let beta: Double?
    let dividendYield: Double?
    let roeTier: String?
    let deTier: String?
    let betaTier: String?
    let valuationTier: String?
    let fundamentalScore: Double?

    enum CodingKeys: String, CodingKey {
        case beta
        case peRatio = "pe_ratio"
        case forwardPe = "forward_pe"
        case roePct = "roe_pct"
        case debtEquity = "debt_equity"
        case dividendYield = "dividend_yield"
        case roeTier = "roe_tier"
        case deTier = "de_tier"
        case betaTier = "beta_tier"
        case valuationTier = "valuation_tier"
        case fundamentalScore = "fundamental_score"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        peRatio = try container.decodeFlexibleDoubleIfPresent(forKey: .peRatio)
        forwardPe = try container.decodeFlexibleDoubleIfPresent(forKey: .forwardPe)
        roePct = try container.decodeFlexibleDoubleIfPresent(forKey: .roePct)
        debtEquity = try container.decodeFlexibleDoubleIfPresent(forKey: .debtEquity)
        beta = try container.decodeFlexibleDoubleIfPresent(forKey: .beta)
        dividendYield = try container.decodeFlexibleDoubleIfPresent(forKey: .dividendYield)
        roeTier = try container.decodeIfPresent(String.self, forKey: .roeTier)
        deTier = try container.decodeIfPresent(String.self, forKey: .deTier)
        betaTier = try container.decodeIfPresent(String.self, forKey: .betaTier)
        valuationTier = try container.decodeIfPresent(String.self, forKey: .valuationTier)
        fundamentalScore = try container.decodeFlexibleDoubleIfPresent(forKey: .fundamentalScore)
    }
}

struct StockTechnicals: Codable {
    let rsi: Double?
    let rsiBucket: String?
    let sma50: Double?
    let sma200: Double?
    let week52Low: Double?
    let week52High: Double?
    let week52Position: Double?
    let annualizedVolatility: Double?
    let technicalScore: Double?

    enum CodingKeys: String, CodingKey {
        case rsi, sma50, sma200
        case rsiBucket = "rsi_bucket"
        case week52Low = "week52_low"
        case week52High = "week52_high"
        case week52Position = "week52_position"
        case annualizedVolatility = "annualized_volatility"
        case technicalScore = "technical_score"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rsi = try container.decodeFlexibleDoubleIfPresent(forKey: .rsi)
        rsiBucket = try container.decodeIfPresent(String.self, forKey: .rsiBucket)
        sma50 = try container.decodeFlexibleDoubleIfPresent(forKey: .sma50)
        sma200 = try container.decodeFlexibleDoubleIfPresent(forKey: .sma200)
        week52Low = try container.decodeFlexibleDoubleIfPresent(forKey: .week52Low)
        week52High = try container.decodeFlexibleDoubleIfPresent(forKey: .week52High)
        week52Position = try container.decodeFlexibleDoubleIfPresent(forKey: .week52Position)
        annualizedVolatility = try container.decodeFlexibleDoubleIfPresent(forKey: .annualizedVolatility)
        technicalScore = try container.decodeFlexibleDoubleIfPresent(forKey: .technicalScore)
    }
}

struct ETFResearchResponse: Codable {
    let ticker: String
    let period: String
    let name: String?
    let category: String?
    let expenseRatio: Double?
    let aum: Double?
    let quote: ResearchQuote
    let returns: [String: Double]
    let risk: ETFRisk
    let sectorBreakdown: [ResearchNamedWeight]
    let topHoldings: [ETFTopHolding]
    let peers: [String]
    let cheaperAlternatives: [String]
    let sparkline: [ResearchSparkPoint]
    let disclaimer: String?

    enum CodingKeys: String, CodingKey {
        case ticker, period, name, category, quote, returns, risk, peers, sparkline, disclaimer, aum
        case expenseRatio = "expense_ratio"
        case sectorBreakdown = "sector_breakdown"
        case topHoldings = "top_holdings"
        case cheaperAlternatives = "cheaper_alternatives"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        period = try container.decodeIfPresent(String.self, forKey: .period) ?? ""
        name = try container.decodeIfPresent(String.self, forKey: .name)
        category = try container.decodeIfPresent(String.self, forKey: .category)
        expenseRatio = try container.decodeFlexibleDoubleIfPresent(forKey: .expenseRatio)
        aum = try container.decodeFlexibleDoubleIfPresent(forKey: .aum)
        quote = try container.decodeIfPresent(ResearchQuote.self, forKey: .quote) ?? ResearchQuote.empty
        returns = try container.decodeFlexibleDoubleMap(forKey: .returns)
        risk = try container.decodeIfPresent(ETFRisk.self, forKey: .risk) ?? ETFRisk.empty
        sectorBreakdown = try container.decodeIfPresent([ResearchNamedWeight].self, forKey: .sectorBreakdown) ?? []
        topHoldings = try container.decodeIfPresent([ETFTopHolding].self, forKey: .topHoldings) ?? []
        peers = try container.decodeIfPresent([String].self, forKey: .peers) ?? []
        cheaperAlternatives = try container.decodeIfPresent([String].self, forKey: .cheaperAlternatives) ?? []
        sparkline = try container.decodeIfPresent([ResearchSparkPoint].self, forKey: .sparkline) ?? []
        disclaimer = try container.decodeIfPresent(String.self, forKey: .disclaimer)
    }
}

extension ResearchQuote {
    static let empty = ResearchQuote(price: nil, prevClose: nil, changePct: nil, marketCap: nil, volume: nil, avgVolume: nil)

    init(price: Double?, prevClose: Double?, changePct: Double?, marketCap: Double?, volume: Double?, avgVolume: Double?) {
        self.price = price
        self.prevClose = prevClose
        self.changePct = changePct
        self.marketCap = marketCap
        self.volume = volume
        self.avgVolume = avgVolume
    }
}

struct ETFRisk: Codable {
    let annualizedVolatility: Double?
    let riskScore: Double?

    static let empty = ETFRisk(annualizedVolatility: nil, riskScore: nil)

    enum CodingKeys: String, CodingKey {
        case annualizedVolatility = "annualized_volatility"
        case riskScore = "risk_score"
    }

    init(annualizedVolatility: Double?, riskScore: Double?) {
        self.annualizedVolatility = annualizedVolatility
        self.riskScore = riskScore
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        annualizedVolatility = try container.decodeFlexibleDoubleIfPresent(forKey: .annualizedVolatility)
        riskScore = try container.decodeFlexibleDoubleIfPresent(forKey: .riskScore)
    }
}

struct ResearchNamedWeight: Codable, Identifiable {
    var id: String { name }
    let name: String
    let weightPct: Double?

    enum CodingKeys: String, CodingKey {
        case name
        case weightPct = "weight_pct"
    }
}

struct ETFTopHolding: Codable, Identifiable {
    var id: String { (ticker ?? name ?? UUID().uuidString) }
    let ticker: String?
    let name: String?
    let weightPct: Double?

    enum CodingKeys: String, CodingKey {
        case ticker, name
        case weightPct = "weight_pct"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker)
        name = try container.decodeIfPresent(String.self, forKey: .name)
        weightPct = try container.decodeFlexibleDoubleIfPresent(forKey: .weightPct)
    }
}

struct MacroResearchResponse: Codable {
    let available: Bool
    let reason: String?
    let indicators: [String: MacroIndicator]
    let disclaimer: String?
}

struct MacroIndicator: Codable, Identifiable {
    var id: String { label + (date ?? "") }
    let label: String
    let value: Double?
    let date: String?

    enum CodingKeys: String, CodingKey { case label, value, date }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? ""
        value = try container.decodeFlexibleDoubleIfPresent(forKey: .value)
        date = try container.decodeIfPresent(String.self, forKey: .date)
    }
}

struct ResearchNewsResponse: Codable {
    let ticker: String?
    let items: [ResearchNewsItem]
    let disclaimer: String?
}

struct ResearchNewsItem: Codable, Identifiable {
    var id: String { (title ?? "") + (publishedAt ?? "") }
    let title: String?
    let publisher: String?
    let url: String?
    let publishedAt: String?
    let summary: String?

    enum CodingKeys: String, CodingKey {
        case title, publisher, url, summary
        case publishedAt = "published_at"
    }
}

struct ResearchAIResponse: Codable {
    let ticker: String?
    let commentary: String
    let disclaimer: String?
    let complianceOk: Bool

    enum CodingKeys: String, CodingKey {
        case ticker, commentary, disclaimer
        case complianceOk = "compliance_ok"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker)
        commentary = try container.decodeIfPresent(String.self, forKey: .commentary) ?? ""
        disclaimer = try container.decodeIfPresent(String.self, forKey: .disclaimer)
        complianceOk = (try container.decodeIfPresent(FlexibleBool.self, forKey: .complianceOk)?.value) ?? false
    }
}

struct ResearchWatchlistResponse: Codable {
    let items: [ResearchWatchlistItem]
    let count: Int
}

struct ResearchWatchlistItem: Codable, Identifiable {
    var id: String { ticker }
    let rowId: Int?
    let ticker: String
    let name: String?
    let assetType: String
    let category: String
    let status: String
    let priority: String
    let reason: String
    let linkedAlphaCandidateId: Int?
    let linkedThesisId: Int?
    let nextReviewAt: String?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker, name, category, status, priority, reason
        case rowId = "id"
        case assetType = "asset_type"
        case linkedAlphaCandidateId = "linked_alpha_candidate_id"
        case linkedThesisId = "linked_thesis_id"
        case nextReviewAt = "next_review_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        name = try container.decodeIfPresent(String.self, forKey: .name)
        assetType = try container.decodeIfPresent(String.self, forKey: .assetType) ?? "STOCK"
        category = try container.decodeIfPresent(String.self, forKey: .category) ?? "LEARNING"
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "WATCHING"
        priority = try container.decodeIfPresent(String.self, forKey: .priority) ?? "MEDIUM"
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? ""
        linkedAlphaCandidateId = try container.decodeIfPresent(FlexibleInt.self, forKey: .linkedAlphaCandidateId)?.value
        linkedThesisId = try container.decodeIfPresent(FlexibleInt.self, forKey: .linkedThesisId)?.value
        nextReviewAt = try container.decodeIfPresent(String.self, forKey: .nextReviewAt)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct ResearchWatchlistDetail: Codable {
    let item: ResearchWatchlistItem
    let notes: [ResearchWatchlistNote]

    init(from decoder: Decoder) throws {
        item = try ResearchWatchlistItem(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        notes = try container.decodeIfPresent([ResearchWatchlistNote].self, forKey: .notes) ?? []
    }

    enum CodingKeys: String, CodingKey {
        case notes
        case rowId = "id"
        case ticker, name, category, status, priority, reason
        case assetType = "asset_type"
        case linkedAlphaCandidateId = "linked_alpha_candidate_id"
        case linkedThesisId = "linked_thesis_id"
        case nextReviewAt = "next_review_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(item.rowId, forKey: .rowId)
        try container.encode(item.ticker, forKey: .ticker)
        try container.encodeIfPresent(item.name, forKey: .name)
        try container.encode(item.assetType, forKey: .assetType)
        try container.encode(item.category, forKey: .category)
        try container.encode(item.status, forKey: .status)
        try container.encode(item.priority, forKey: .priority)
        try container.encode(item.reason, forKey: .reason)
        try container.encodeIfPresent(item.linkedAlphaCandidateId, forKey: .linkedAlphaCandidateId)
        try container.encodeIfPresent(item.linkedThesisId, forKey: .linkedThesisId)
        try container.encodeIfPresent(item.nextReviewAt, forKey: .nextReviewAt)
        try container.encodeIfPresent(item.createdAt, forKey: .createdAt)
        try container.encodeIfPresent(item.updatedAt, forKey: .updatedAt)
        try container.encode(notes, forKey: .notes)
    }
}

struct ResearchWatchlistNote: Codable, Identifiable {
    var id: String { "\(rowId ?? 0)-\(ticker)-\(createdAt ?? "")" }
    let rowId: Int?
    let ticker: String
    let noteType: String
    let text: String
    let tags: [String]
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker, text, tags
        case rowId = "id"
        case noteType = "note_type"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        noteType = try container.decodeIfPresent(String.self, forKey: .noteType) ?? "OTHER"
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        tags = try container.decodeFlexibleStringArrayIfPresent(forKey: .tags) ?? []
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

struct ResearchWatchlistSuggestionsResponse: Codable {
    let alphaCandidates: [ResearchWatchlistSuggestion]
    let alertGate: [ResearchWatchlistSuggestion]
    let missedWinners: [ResearchWatchlistSuggestion]
    let validationTrends: [ResearchWatchlistSuggestion]
    let thesisWarnings: [ResearchWatchlistSuggestion]
    let scorecardGaps: [ResearchWatchlistSuggestion]
    let combined: [ResearchWatchlistSuggestion]
    let total: Int
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case combined, total
        case alphaCandidates = "alpha_candidates"
        case alertGate = "alert_gate"
        case missedWinners = "missed_winners"
        case validationTrends = "validation_trends"
        case thesisWarnings = "thesis_warnings"
        case scorecardGaps = "scorecard_gaps"
        case generatedAt = "generated_at"
    }
}

struct ResearchWatchlistSuggestion: Codable, Identifiable {
    var id: String { (ticker ?? source) + reason + priority }
    let ticker: String?
    let reason: String
    let source: String
    let category: String
    let priority: String

    enum CodingKeys: String, CodingKey {
        case ticker, reason, source, category, priority
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker)
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? ""
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? "unknown"
        category = try container.decodeIfPresent(String.self, forKey: .category) ?? "LEARNING"
        priority = try container.decodeIfPresent(String.self, forKey: .priority) ?? "LOW"
    }
}

struct ResearchWorkflowQueueResponse: Codable {
    let items: [ResearchWorkflowItem]
    let count: Int
}

struct ResearchWorkflowItem: Codable, Identifiable {
    var id: String { itemId }
    let itemId: String
    let ticker: String?
    let source: String
    let priority: String
    let reason: String
    let dueAt: String?
    let status: String
    let linkedEntityType: String
    let linkedEntityId: String?
    let urgencyScore: Double
    let opportunityScore: Double
    let riskScore: Double
    let staleScore: Double
    let priorityScore: Double
    let snoozedUntil: String?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker, source, priority, reason, status
        case itemId = "item_id"
        case dueAt = "due_at"
        case linkedEntityType = "linked_entity_type"
        case linkedEntityId = "linked_entity_id"
        case urgencyScore = "urgency_score"
        case opportunityScore = "opportunity_score"
        case riskScore = "risk_score"
        case staleScore = "stale_score"
        case priorityScore = "priority_score"
        case snoozedUntil = "snoozed_until"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        itemId = try container.decodeIfPresent(String.self, forKey: .itemId) ?? ""
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker)
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? ""
        priority = try container.decodeIfPresent(String.self, forKey: .priority) ?? "MEDIUM"
        reason = try container.decodeIfPresent(String.self, forKey: .reason) ?? ""
        dueAt = try container.decodeIfPresent(String.self, forKey: .dueAt)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "OPEN"
        linkedEntityType = try container.decodeIfPresent(String.self, forKey: .linkedEntityType) ?? "NONE"
        linkedEntityId = try container.decodeIfPresent(String.self, forKey: .linkedEntityId)
        urgencyScore = try container.decodeFlexibleDoubleIfPresent(forKey: .urgencyScore) ?? 0
        opportunityScore = try container.decodeFlexibleDoubleIfPresent(forKey: .opportunityScore) ?? 0
        riskScore = try container.decodeFlexibleDoubleIfPresent(forKey: .riskScore) ?? 0
        staleScore = try container.decodeFlexibleDoubleIfPresent(forKey: .staleScore) ?? 0
        priorityScore = try container.decodeFlexibleDoubleIfPresent(forKey: .priorityScore) ?? 0
        snoozedUntil = try container.decodeIfPresent(String.self, forKey: .snoozedUntil)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct ResearchWorkflowSummary: Codable {
    let topItems: [ResearchWorkflowItem]
    let overdue: [ResearchWorkflowItem]
    let snoozedReturning: [ResearchWorkflowItem]
    let newHighPriority: [ResearchWorkflowItem]
    let completedToday: [ResearchWorkflowItem]
    let bottlenecks: [ResearchWorkflowItem]
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case overdue, bottlenecks
        case topItems = "top_items"
        case snoozedReturning = "snoozed_returning"
        case newHighPriority = "new_high_priority"
        case completedToday = "completed_today"
        case generatedAt = "generated_at"
    }
}

struct ResearchWorkflowNote: Codable, Identifiable {
    var id: String { "\(rowId ?? 0)-\(itemId)-\(createdAt ?? "")" }
    let rowId: Int?
    let itemId: String
    let text: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case text
        case rowId = "id"
        case itemId = "item_id"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        itemId = try container.decodeIfPresent(String.self, forKey: .itemId) ?? ""
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

