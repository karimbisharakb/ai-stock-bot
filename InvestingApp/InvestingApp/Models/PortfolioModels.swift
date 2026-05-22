import Foundation

// Portfolio, thesis, risk, and stress models split from AlphaCandidate.swift.
// MARK: - A11 Canonical Portfolio Models

struct CanonicalPortfolioResponse: Codable {
    let positions: [CanonicalPosition]
    let aggregates: CanonicalPortfolioAggregates

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        positions = try container.decodeIfPresent([CanonicalPosition].self, forKey: .positions) ?? []
        aggregates = try container.decodeIfPresent(CanonicalPortfolioAggregates.self, forKey: .aggregates) ?? .empty
    }
}

struct CanonicalPosition: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let quantity: Double
    let avgCost: Double?
    let marketPrice: Double?
    let marketValue: Double?
    let costBasis: Double?
    let unrealizedPnL: Double?
    let unrealizedPnLPercent: Double?
    let realizedPnL: Double?
    let source: String?
    let isStale: Bool
    let concentrationPercent: Double?
    let priceFetchedAt: String?
    let reconciledAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case quantity
        case avgCost = "avg_cost"
        case marketPrice = "market_price"
        case marketValue = "market_value"
        case costBasis = "cost_basis"
        case unrealizedPnL = "unrealized_pnl"
        case unrealizedPnLPercent = "unrealized_pnl_pct"
        case realizedPnL = "realized_pnl"
        case source
        case isStale = "is_stale"
        case concentrationPercent = "concentration_pct"
        case priceFetchedAt = "price_fetched_at"
        case reconciledAt = "reconciled_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        quantity = try container.decodeFlexibleDoubleIfPresent(forKey: .quantity) ?? 0
        avgCost = try container.decodeFlexibleDoubleIfPresent(forKey: .avgCost)
        marketPrice = try container.decodeFlexibleDoubleIfPresent(forKey: .marketPrice)
        marketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .marketValue)
        costBasis = try container.decodeFlexibleDoubleIfPresent(forKey: .costBasis)
        unrealizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnL)
        unrealizedPnLPercent = try container.decodeFlexibleDoubleIfPresent(forKey: .unrealizedPnLPercent)
        realizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .realizedPnL)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        isStale = try container.decodeIfPresent(FlexibleBool.self, forKey: .isStale)?.value ?? false
        concentrationPercent = try container.decodeFlexibleDoubleIfPresent(forKey: .concentrationPercent)
        priceFetchedAt = try container.decodeIfPresent(String.self, forKey: .priceFetchedAt)
        reconciledAt = try container.decodeIfPresent(String.self, forKey: .reconciledAt)
    }
}

struct CanonicalPortfolioAggregates: Codable {
    let totalMarketValue: Double?
    let totalCostBasis: Double?
    let totalUnrealizedPnL: Double?
    let totalRealizedPnL: Double?
    let cash: Double?
    let totalPortfolioValue: Double?
    let positionCount: Int
    let staleCount: Int
    let reconciledAt: String?

    static let empty = CanonicalPortfolioAggregates(
        totalMarketValue: nil,
        totalCostBasis: nil,
        totalUnrealizedPnL: nil,
        totalRealizedPnL: nil,
        cash: nil,
        totalPortfolioValue: nil,
        positionCount: 0,
        staleCount: 0,
        reconciledAt: nil
    )

    enum CodingKeys: String, CodingKey {
        case totalMarketValue = "total_market_value"
        case totalCostBasis = "total_cost_basis"
        case totalUnrealizedPnL = "total_unrealized_pnl"
        case totalRealizedPnL = "total_realized_pnl"
        case cash
        case totalPortfolioValue = "total_portfolio_value"
        case positionCount = "position_count"
        case staleCount = "stale_count"
        case reconciledAt = "reconciled_at"
    }

    init(
        totalMarketValue: Double?,
        totalCostBasis: Double?,
        totalUnrealizedPnL: Double?,
        totalRealizedPnL: Double?,
        cash: Double?,
        totalPortfolioValue: Double?,
        positionCount: Int,
        staleCount: Int,
        reconciledAt: String?
    ) {
        self.totalMarketValue = totalMarketValue
        self.totalCostBasis = totalCostBasis
        self.totalUnrealizedPnL = totalUnrealizedPnL
        self.totalRealizedPnL = totalRealizedPnL
        self.cash = cash
        self.totalPortfolioValue = totalPortfolioValue
        self.positionCount = positionCount
        self.staleCount = staleCount
        self.reconciledAt = reconciledAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        totalMarketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .totalMarketValue)
        totalCostBasis = try container.decodeFlexibleDoubleIfPresent(forKey: .totalCostBasis)
        totalUnrealizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .totalUnrealizedPnL)
        totalRealizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .totalRealizedPnL)
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash)
        totalPortfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .totalPortfolioValue)
        positionCount = try container.decodeIfPresent(Int.self, forKey: .positionCount) ?? 0
        staleCount = try container.decodeIfPresent(Int.self, forKey: .staleCount) ?? 0
        reconciledAt = try container.decodeIfPresent(String.self, forKey: .reconciledAt)
    }
}

struct PortfolioReconciliationResponse: Codable {
    let count: Int
    let runs: [PortfolioReconciliationRun]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runs = try container.decodeIfPresent([PortfolioReconciliationRun].self, forKey: .runs) ?? []
        count = try container.decodeIfPresent(Int.self, forKey: .count) ?? runs.count
    }
}

struct PortfolioReconciliationRun: Codable, Identifiable {
    var id: String { runId }

    let runId: String
    let trigger: String?
    let status: String
    let positionCount: Int
    let issues: [String]
    let durationMs: Double?
    let reconciledAt: String?

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case trigger
        case status
        case positionCount = "position_count"
        case issues
        case issuesJSON = "issues_json"
        case durationMs = "duration_ms"
        case reconciledAt = "reconciled_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runId = try container.decodeIfPresent(String.self, forKey: .runId) ?? UUID().uuidString
        trigger = try container.decodeIfPresent(String.self, forKey: .trigger)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        positionCount = try container.decodeIfPresent(Int.self, forKey: .positionCount) ?? 0
        issues = Self.decodeIssues(container)
        durationMs = try container.decodeFlexibleDoubleIfPresent(forKey: .durationMs)
        reconciledAt = try container.decodeIfPresent(String.self, forKey: .reconciledAt)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(runId, forKey: .runId)
        try container.encodeIfPresent(trigger, forKey: .trigger)
        try container.encode(status, forKey: .status)
        try container.encode(positionCount, forKey: .positionCount)
        try container.encode(issues, forKey: .issues)
        try container.encodeIfPresent(durationMs, forKey: .durationMs)
        try container.encodeIfPresent(reconciledAt, forKey: .reconciledAt)
    }

    private static func decodeIssues(_ container: KeyedDecodingContainer<CodingKeys>) -> [String] {
        if let values = try? container.decodeIfPresent([String].self, forKey: .issues) {
            return values
        }
        guard
            let json = try? container.decodeIfPresent(String.self, forKey: .issuesJSON),
            let data = json.data(using: .utf8),
            let values = try? JSONDecoder().decode([String].self, from: data)
        else { return [] }
        return values
    }
}

struct PortfolioSnapshotsResponse: Codable {
    let count: Int
    let snapshots: [PortfolioSnapshot]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        snapshots = try container.decodeIfPresent([PortfolioSnapshot].self, forKey: .snapshots) ?? []
        count = try container.decodeIfPresent(Int.self, forKey: .count) ?? snapshots.count
    }
}

struct PortfolioSnapshot: Codable, Identifiable {
    var id: String { snapshotId }

    let snapshotId: String
    let trigger: String?
    let totalMarketValue: Double?
    let totalCostBasis: Double?
    let totalUnrealizedPnL: Double?
    let totalRealizedPnL: Double?
    let cash: Double?
    let totalPortfolioValue: Double?
    let positionCount: Int
    let staleCount: Int
    let takenAt: String?

    enum CodingKeys: String, CodingKey {
        case snapshotId = "snapshot_id"
        case trigger
        case totalMarketValue = "total_market_value"
        case totalCostBasis = "total_cost_basis"
        case totalUnrealizedPnL = "total_unrealized_pnl"
        case totalRealizedPnL = "total_realized_pnl"
        case cash
        case totalPortfolioValue = "total_portfolio_value"
        case positionCount = "position_count"
        case staleCount = "stale_count"
        case takenAt = "taken_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        snapshotId = try container.decodeIfPresent(String.self, forKey: .snapshotId) ?? UUID().uuidString
        trigger = try container.decodeIfPresent(String.self, forKey: .trigger)
        totalMarketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .totalMarketValue)
        totalCostBasis = try container.decodeFlexibleDoubleIfPresent(forKey: .totalCostBasis)
        totalUnrealizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .totalUnrealizedPnL)
        totalRealizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .totalRealizedPnL)
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash)
        totalPortfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .totalPortfolioValue)
        positionCount = try container.decodeIfPresent(Int.self, forKey: .positionCount) ?? 0
        staleCount = try container.decodeIfPresent(Int.self, forKey: .staleCount) ?? 0
        takenAt = try container.decodeIfPresent(String.self, forKey: .takenAt)
    }
}

struct PortfolioReconcileResponse: Codable {
    let status: String
    let runId: String?
    let positions: [CanonicalPosition]
    let aggregates: CanonicalPortfolioAggregates
    let issues: [String]
    let positionCount: Int
    let reconciledAt: String?
    let durationMs: Double?

    enum CodingKeys: String, CodingKey {
        case status
        case runId = "run_id"
        case positions
        case aggregates
        case issues
        case positionCount = "position_count"
        case reconciledAt = "reconciled_at"
        case durationMs = "duration_ms"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "UNKNOWN"
        runId = try container.decodeIfPresent(String.self, forKey: .runId)
        positions = try container.decodeIfPresent([CanonicalPosition].self, forKey: .positions) ?? []
        aggregates = try container.decodeIfPresent(CanonicalPortfolioAggregates.self, forKey: .aggregates) ?? .empty
        issues = try container.decodeIfPresent([String].self, forKey: .issues) ?? []
        positionCount = try container.decodeIfPresent(Int.self, forKey: .positionCount) ?? positions.count
        reconciledAt = try container.decodeIfPresent(String.self, forKey: .reconciledAt)
        durationMs = try container.decodeFlexibleDoubleIfPresent(forKey: .durationMs)
    }
}

// MARK: - A12 Manual Portfolio Models

struct ManualPortfolioResponse: Codable {
    let positions: [ManualPortfolioPosition]
    let accountSettings: ManualAccountSettings

    enum CodingKeys: String, CodingKey {
        case positions
        case accountSettings = "account_settings"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        positions = try container.decodeIfPresent([ManualPortfolioPosition].self, forKey: .positions) ?? []
        accountSettings = try container.decodeIfPresent(ManualAccountSettings.self, forKey: .accountSettings) ?? .empty
    }
}

struct ManualPortfolioPosition: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let quantity: Double
    let avgCost: Double
    let realizedPnL: Double
    let accountType: String
    let currency: String
    let note: String
    let active: Bool
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case quantity
        case avgCost = "avg_cost"
        case realizedPnL = "realized_pnl"
        case accountType = "account_type"
        case currency
        case note
        case active
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        quantity = try container.decodeFlexibleDoubleIfPresent(forKey: .quantity) ?? 0
        avgCost = try container.decodeFlexibleDoubleIfPresent(forKey: .avgCost) ?? 0
        realizedPnL = try container.decodeFlexibleDoubleIfPresent(forKey: .realizedPnL) ?? 0
        accountType = try container.decodeIfPresent(String.self, forKey: .accountType) ?? "TFSA"
        currency = try container.decodeIfPresent(String.self, forKey: .currency) ?? "CAD"
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? ""
        active = try container.decodeIfPresent(FlexibleBool.self, forKey: .active)?.value ?? true
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct ManualAccountSettings: Codable {
    let accountName: String
    let accountType: String
    let baseCurrency: String
    let availableCash: Double
    let contributionRoom: Double?
    let notes: String
    let updatedAt: String?

    static let empty = ManualAccountSettings(
        accountName: "",
        accountType: "TFSA",
        baseCurrency: "CAD",
        availableCash: 0,
        contributionRoom: nil,
        notes: "",
        updatedAt: nil
    )

    enum CodingKeys: String, CodingKey {
        case accountName = "account_name"
        case accountType = "account_type"
        case baseCurrency = "base_currency"
        case availableCash = "available_cash"
        case contributionRoom = "contribution_room"
        case notes
        case updatedAt = "updated_at"
    }

    init(
        accountName: String,
        accountType: String,
        baseCurrency: String,
        availableCash: Double,
        contributionRoom: Double?,
        notes: String,
        updatedAt: String?
    ) {
        self.accountName = accountName
        self.accountType = accountType
        self.baseCurrency = baseCurrency
        self.availableCash = availableCash
        self.contributionRoom = contributionRoom
        self.notes = notes
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accountName = try container.decodeIfPresent(String.self, forKey: .accountName) ?? ""
        accountType = try container.decodeIfPresent(String.self, forKey: .accountType) ?? "TFSA"
        baseCurrency = try container.decodeIfPresent(String.self, forKey: .baseCurrency) ?? "CAD"
        availableCash = try container.decodeFlexibleDoubleIfPresent(forKey: .availableCash) ?? 0
        contributionRoom = try container.decodeFlexibleDoubleIfPresent(forKey: .contributionRoom)
        notes = try container.decodeIfPresent(String.self, forKey: .notes) ?? ""
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct ManualPositionActionResponse: Codable {
    let ok: Bool
    let ticker: String?
    let position: ManualPortfolioPosition?
    let errors: [String]?
    let error: String?
}

struct ManualAccountUpdateResponse: Codable {
    let ok: Bool
    let settings: ManualAccountSettings?
    let errors: [String]?
}

// MARK: - A13 Position Thesis and Journal Models

struct PortfolioThesisListResponse: Codable {
    let theses: [PositionThesis]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        theses = try container.decodeIfPresent([PositionThesis].self, forKey: .theses) ?? []
    }
}

struct PortfolioThesisDetailResponse: Codable {
    let thesis: PositionThesis
    let journal: [PositionJournalEntry]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        thesis = try container.decode(PositionThesis.self, forKey: .thesis)
        journal = try container.decodeIfPresent([PositionJournalEntry].self, forKey: .journal) ?? []
    }
}

struct PositionThesis: Codable, Identifiable {
    var id: String { ticker }

    let rowId: Int?
    let ticker: String
    let thesisTitle: String
    let thesisText: String
    let setupType: String
    let convictionLevel: String
    let timeHorizon: String
    let entryReason: String
    let expectedCatalysts: String
    let riskFactors: String
    let invalidationLevel: Double?
    let targetLevel: Double?
    let exitPlan: String
    let reviewFrequencyDays: Int
    let nextReviewAt: String?
    let status: String
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case ticker
        case thesisTitle = "thesis_title"
        case thesisText = "thesis_text"
        case setupType = "setup_type"
        case convictionLevel = "conviction_level"
        case timeHorizon = "time_horizon"
        case entryReason = "entry_reason"
        case expectedCatalysts = "expected_catalysts"
        case riskFactors = "risk_factors"
        case invalidationLevel = "invalidation_level"
        case targetLevel = "target_level"
        case exitPlan = "exit_plan"
        case reviewFrequencyDays = "review_frequency_days"
        case nextReviewAt = "next_review_at"
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        thesisTitle = try container.decodeIfPresent(String.self, forKey: .thesisTitle) ?? ""
        thesisText = try container.decodeIfPresent(String.self, forKey: .thesisText) ?? ""
        setupType = try container.decodeIfPresent(String.self, forKey: .setupType) ?? ""
        convictionLevel = try container.decodeIfPresent(String.self, forKey: .convictionLevel) ?? "MEDIUM"
        timeHorizon = try container.decodeIfPresent(String.self, forKey: .timeHorizon) ?? "MEDIUM"
        entryReason = try container.decodeIfPresent(String.self, forKey: .entryReason) ?? ""
        expectedCatalysts = try container.decodeIfPresent(String.self, forKey: .expectedCatalysts) ?? ""
        riskFactors = try container.decodeIfPresent(String.self, forKey: .riskFactors) ?? ""
        invalidationLevel = try container.decodeFlexibleDoubleIfPresent(forKey: .invalidationLevel)
        targetLevel = try container.decodeFlexibleDoubleIfPresent(forKey: .targetLevel)
        exitPlan = try container.decodeIfPresent(String.self, forKey: .exitPlan) ?? ""
        reviewFrequencyDays = try container.decodeIfPresent(FlexibleInt.self, forKey: .reviewFrequencyDays)?.value ?? 30
        nextReviewAt = try container.decodeIfPresent(String.self, forKey: .nextReviewAt)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ACTIVE"
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct PositionJournalEntry: Codable, Identifiable {
    var id: String { rowId.map(String.init) ?? "\(ticker)-\(createdAt ?? UUID().uuidString)" }

    let rowId: Int?
    let ticker: String
    let entryType: String
    let text: String
    let tags: [String]
    let confidenceChange: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case ticker
        case entryType = "entry_type"
        case text
        case tagsJson = "tags_json"
        case tags
        case confidenceChange = "confidence_change"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        entryType = try container.decodeIfPresent(String.self, forKey: .entryType) ?? "NOTE"
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        if let rawTags = try container.decodeIfPresent([String].self, forKey: .tags) {
            tags = rawTags
        } else if let json = try container.decodeIfPresent(String.self, forKey: .tagsJson),
                  let data = json.data(using: .utf8),
                  let decoded = try? JSONDecoder().decode([String].self, from: data) {
            tags = decoded
        } else {
            tags = []
        }
        confidenceChange = try container.decodeIfPresent(String.self, forKey: .confidenceChange)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(rowId, forKey: .rowId)
        try container.encode(ticker, forKey: .ticker)
        try container.encode(entryType, forKey: .entryType)
        try container.encode(text, forKey: .text)
        try container.encode(tags, forKey: .tags)
        try container.encodeIfPresent(confidenceChange, forKey: .confidenceChange)
        try container.encodeIfPresent(createdAt, forKey: .createdAt)
    }
}

struct PortfolioThesisActionResponse: Codable {
    let ok: Bool
    let ticker: String?
    let thesis: PositionThesis?
    let errors: [String]?
    let error: String?
}

struct PortfolioJournalActionResponse: Codable {
    let ok: Bool
    let ticker: String?
    let entry: PositionJournalEntry?
    let errors: [String]?
    let error: String?
}

struct PortfolioReviewsResponse: Codable {
    let reviews: ThesisReviewBucket
    let warnings: ThesisWarnings

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        reviews = try container.decodeIfPresent(ThesisReviewBucket.self, forKey: .reviews) ?? .empty
        warnings = try container.decodeIfPresent(ThesisWarnings.self, forKey: .warnings) ?? .empty
    }
}

struct ThesisReviewBucket: Codable {
    let due: [ThesisReviewRow]
    let overdue: [ThesisReviewRow]
    let upcoming: [ThesisReviewRow]
    let dueCount: Int
    let overdueCount: Int
    let upcomingCount: Int

    static let empty = ThesisReviewBucket(due: [], overdue: [], upcoming: [], dueCount: 0, overdueCount: 0, upcomingCount: 0)

    enum CodingKeys: String, CodingKey {
        case due
        case overdue
        case upcoming
        case dueCount = "due_count"
        case overdueCount = "overdue_count"
        case upcomingCount = "upcoming_count"
    }

    init(due: [ThesisReviewRow], overdue: [ThesisReviewRow], upcoming: [ThesisReviewRow], dueCount: Int, overdueCount: Int, upcomingCount: Int) {
        self.due = due
        self.overdue = overdue
        self.upcoming = upcoming
        self.dueCount = dueCount
        self.overdueCount = overdueCount
        self.upcomingCount = upcomingCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        due = try container.decodeIfPresent([ThesisReviewRow].self, forKey: .due) ?? []
        overdue = try container.decodeIfPresent([ThesisReviewRow].self, forKey: .overdue) ?? []
        upcoming = try container.decodeIfPresent([ThesisReviewRow].self, forKey: .upcoming) ?? []
        dueCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .dueCount)?.value ?? due.count
        overdueCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .overdueCount)?.value ?? overdue.count
        upcomingCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .upcomingCount)?.value ?? upcoming.count
    }
}

struct ThesisReviewRow: Codable, Identifiable {
    var id: String { "\(ticker)-\(nextReviewAt ?? "")" }

    let ticker: String
    let thesisTitle: String
    let convictionLevel: String
    let nextReviewAt: String?
    let reviewFrequencyDays: Int
    let status: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case thesisTitle = "thesis_title"
        case convictionLevel = "conviction_level"
        case nextReviewAt = "next_review_at"
        case reviewFrequencyDays = "review_frequency_days"
        case status
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        thesisTitle = try container.decodeIfPresent(String.self, forKey: .thesisTitle) ?? ""
        convictionLevel = try container.decodeIfPresent(String.self, forKey: .convictionLevel) ?? "MEDIUM"
        nextReviewAt = try container.decodeIfPresent(String.self, forKey: .nextReviewAt)
        reviewFrequencyDays = try container.decodeIfPresent(FlexibleInt.self, forKey: .reviewFrequencyDays)?.value ?? 30
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ACTIVE"
    }
}

struct ThesisWarnings: Codable {
    let missingThesis: [String]
    let staleThesis: [String]
    let missingExitPlan: [String]
    let hasWarnings: Bool

    static let empty = ThesisWarnings(missingThesis: [], staleThesis: [], missingExitPlan: [], hasWarnings: false)

    enum CodingKeys: String, CodingKey {
        case missingThesis = "missing_thesis"
        case staleThesis = "stale_thesis"
        case missingExitPlan = "missing_exit_plan"
        case hasWarnings = "has_warnings"
    }

    init(missingThesis: [String], staleThesis: [String], missingExitPlan: [String], hasWarnings: Bool) {
        self.missingThesis = missingThesis
        self.staleThesis = staleThesis
        self.missingExitPlan = missingExitPlan
        self.hasWarnings = hasWarnings
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        missingThesis = try container.decodeIfPresent([String].self, forKey: .missingThesis) ?? []
        staleThesis = try container.decodeIfPresent([String].self, forKey: .staleThesis) ?? []
        missingExitPlan = try container.decodeIfPresent([String].self, forKey: .missingExitPlan) ?? []
        hasWarnings = try container.decodeIfPresent(FlexibleBool.self, forKey: .hasWarnings)?.value ?? false
    }
}

// MARK: - A15 Portfolio Risk and Sizing Models

struct PortfolioRiskReport: Codable {
    let portfolioRiskScore: Double
    let concentrationWarnings: [String]
    let sizingWarnings: [String]
    let cashWarning: String?
    let drawdownWarning: String?
    let speculativePct: Double
    let cashPct: Double
    let cadPct: Double
    let usdPct: Double
    let themeExposure: [String: Double]
    let themeWarnings: [String]
    let tickerRiskTable: [TickerRiskRow]
    let recommendedActions: [String]
    let policy: RiskPolicy
    let checkedAt: String?

    enum CodingKeys: String, CodingKey {
        case portfolioRiskScore = "portfolio_risk_score"
        case concentrationWarnings = "concentration_warnings"
        case sizingWarnings = "sizing_warnings"
        case cashWarning = "cash_warning"
        case drawdownWarning = "drawdown_warning"
        case speculativePct = "speculative_pct"
        case cashPct = "cash_pct"
        case cadPct = "cad_pct"
        case usdPct = "usd_pct"
        case themeExposure = "theme_exposure"
        case themeWarnings = "theme_warnings"
        case tickerRiskTable = "ticker_risk_table"
        case recommendedActions = "recommended_actions"
        case policy
        case checkedAt = "checked_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        portfolioRiskScore = try container.decodeFlexibleDoubleIfPresent(forKey: .portfolioRiskScore) ?? 0
        concentrationWarnings = try container.decodeIfPresent([String].self, forKey: .concentrationWarnings) ?? []
        sizingWarnings = try container.decodeIfPresent([String].self, forKey: .sizingWarnings) ?? []
        cashWarning = try container.decodeIfPresent(String.self, forKey: .cashWarning)
        drawdownWarning = try container.decodeIfPresent(String.self, forKey: .drawdownWarning)
        speculativePct = try container.decodeFlexibleDoubleIfPresent(forKey: .speculativePct) ?? 0
        cashPct = try container.decodeFlexibleDoubleIfPresent(forKey: .cashPct) ?? 0
        cadPct = try container.decodeFlexibleDoubleIfPresent(forKey: .cadPct) ?? 0
        usdPct = try container.decodeFlexibleDoubleIfPresent(forKey: .usdPct) ?? 0
        themeExposure = try container.decodeFlexibleDoubleMap(forKey: .themeExposure)
        themeWarnings = try container.decodeIfPresent([String].self, forKey: .themeWarnings) ?? []
        tickerRiskTable = try container.decodeIfPresent([TickerRiskRow].self, forKey: .tickerRiskTable) ?? []
        recommendedActions = try container.decodeIfPresent([String].self, forKey: .recommendedActions) ?? []
        policy = try container.decodeIfPresent(RiskPolicy.self, forKey: .policy) ?? .empty
        checkedAt = try container.decodeIfPresent(String.self, forKey: .checkedAt)
    }
}

struct TickerRiskRow: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let marketValue: Double
    let concentrationPct: Double
    let theme: String
    let isSpeculative: Bool
    let isCad: Bool
    let riskFlags: [String]

    enum CodingKeys: String, CodingKey {
        case ticker
        case marketValue = "market_value"
        case concentrationPct = "concentration_pct"
        case theme
        case isSpeculative = "is_speculative"
        case isCad = "is_cad"
        case riskFlags = "risk_flags"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        marketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .marketValue) ?? 0
        concentrationPct = try container.decodeFlexibleDoubleIfPresent(forKey: .concentrationPct) ?? 0
        theme = try container.decodeIfPresent(String.self, forKey: .theme) ?? "OTHER"
        isSpeculative = try container.decodeIfPresent(FlexibleBool.self, forKey: .isSpeculative)?.value ?? false
        isCad = try container.decodeIfPresent(FlexibleBool.self, forKey: .isCad)?.value ?? false
        riskFlags = try container.decodeIfPresent([String].self, forKey: .riskFlags) ?? []
    }

    var volatilityTier: String {
        isSpeculative ? "HIGH" : "NORMAL"
    }

    func suggestedSizingTier(policy: RiskPolicy) -> String {
        if concentrationPct >= policy.maxSinglePositionPct { return "TOO_RISKY" }
        if concentrationPct >= policy.maxSinglePositionPct * 0.75 { return "HIGH_CONVICTION_ONLY" }
        if isSpeculative { return "SMALL_ONLY" }
        return "NORMAL"
    }

    func maxExpectedLoss(policy: RiskPolicy) -> Double {
        marketValue * policy.maxExpectedLossPct / 100
    }
}

struct RiskPolicy: Codable {
    let maxSinglePositionPct: Double
    let maxSpeculativePct: Double
    let minCashReservePct: Double
    let maxExpectedLossPct: Double
    let highVolatilityHaircut: Double
    let riskOffHaircut: Double
    let riskOffMode: Bool

    static let empty = RiskPolicy(
        maxSinglePositionPct: 0,
        maxSpeculativePct: 0,
        minCashReservePct: 0,
        maxExpectedLossPct: 0,
        highVolatilityHaircut: 0,
        riskOffHaircut: 0,
        riskOffMode: false
    )

    enum CodingKeys: String, CodingKey {
        case maxSinglePositionPct = "max_single_position_pct"
        case maxSpeculativePct = "max_speculative_pct"
        case minCashReservePct = "min_cash_reserve_pct"
        case maxExpectedLossPct = "max_expected_loss_pct"
        case highVolatilityHaircut = "high_volatility_haircut"
        case riskOffHaircut = "risk_off_haircut"
        case riskOffMode = "risk_off_mode"
    }

    init(
        maxSinglePositionPct: Double,
        maxSpeculativePct: Double,
        minCashReservePct: Double,
        maxExpectedLossPct: Double,
        highVolatilityHaircut: Double,
        riskOffHaircut: Double,
        riskOffMode: Bool
    ) {
        self.maxSinglePositionPct = maxSinglePositionPct
        self.maxSpeculativePct = maxSpeculativePct
        self.minCashReservePct = minCashReservePct
        self.maxExpectedLossPct = maxExpectedLossPct
        self.highVolatilityHaircut = highVolatilityHaircut
        self.riskOffHaircut = riskOffHaircut
        self.riskOffMode = riskOffMode
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        maxSinglePositionPct = try container.decodeFlexibleDoubleIfPresent(forKey: .maxSinglePositionPct) ?? 0
        maxSpeculativePct = try container.decodeFlexibleDoubleIfPresent(forKey: .maxSpeculativePct) ?? 0
        minCashReservePct = try container.decodeFlexibleDoubleIfPresent(forKey: .minCashReservePct) ?? 0
        maxExpectedLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .maxExpectedLossPct) ?? 0
        highVolatilityHaircut = try container.decodeFlexibleDoubleIfPresent(forKey: .highVolatilityHaircut) ?? 0
        riskOffHaircut = try container.decodeFlexibleDoubleIfPresent(forKey: .riskOffHaircut) ?? 0
        riskOffMode = try container.decodeIfPresent(FlexibleBool.self, forKey: .riskOffMode)?.value ?? false
    }
}

struct DecisionSizeCheckResponse: Codable {
    let ticker: String
    let decisionType: String
    let sizingGuidance: PositionSizingGuidance
    let blockers: [String]
    let warnings: [String]
    let checklistItemSuggestions: [String: Bool?]
    let checkedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case decisionType = "decision_type"
        case sizingGuidance = "sizing_guidance"
        case blockers
        case warnings
        case checklistItemSuggestions = "checklist_item_suggestions"
        case checkedAt = "checked_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        decisionType = try container.decodeIfPresent(String.self, forKey: .decisionType) ?? "ENTER"
        sizingGuidance = try container.decodeIfPresent(PositionSizingGuidance.self, forKey: .sizingGuidance) ?? .empty
        blockers = try container.decodeIfPresent([String].self, forKey: .blockers) ?? []
        warnings = try container.decodeIfPresent([String].self, forKey: .warnings) ?? []
        let rawSuggestions = try container.decodeIfPresent([String: FlexibleBool?].self, forKey: .checklistItemSuggestions) ?? [:]
        checklistItemSuggestions = rawSuggestions.mapValues { $0?.value }
        checkedAt = try container.decodeIfPresent(String.self, forKey: .checkedAt)
    }
}

struct PositionSizingGuidance: Codable {
    let ticker: String
    let decisionType: String
    let currentPositionValue: Double
    let currentConcentrationPct: Double
    let maxPositionSizeCad: Double
    let remainingBudgetCad: Double
    let suggestedSizeCad: Double
    let maxLossAmountCad: Double
    let maxPortfolioRiskPct: Double
    let sizingTier: String
    let haircutApplied: Bool
    let haircutReason: String?
    let stopDistancePct: Double?
    let riskRewardNote: String

    static let empty = PositionSizingGuidance(
        ticker: "",
        decisionType: "ENTER",
        currentPositionValue: 0,
        currentConcentrationPct: 0,
        maxPositionSizeCad: 0,
        remainingBudgetCad: 0,
        suggestedSizeCad: 0,
        maxLossAmountCad: 0,
        maxPortfolioRiskPct: 0,
        sizingTier: "NOT_READY",
        haircutApplied: false,
        haircutReason: nil,
        stopDistancePct: nil,
        riskRewardNote: "No sizing guidance available"
    )

    enum CodingKeys: String, CodingKey {
        case ticker
        case decisionType = "decision_type"
        case currentPositionValue = "current_position_value"
        case currentConcentrationPct = "current_concentration_pct"
        case maxPositionSizeCad = "max_position_size_cad"
        case remainingBudgetCad = "remaining_budget_cad"
        case suggestedSizeCad = "suggested_size_cad"
        case maxLossAmountCad = "max_loss_amount_cad"
        case maxPortfolioRiskPct = "max_portfolio_risk_pct"
        case sizingTier = "sizing_tier"
        case haircutApplied = "haircut_applied"
        case haircutReason = "haircut_reason"
        case stopDistancePct = "stop_distance_pct"
        case riskRewardNote = "risk_reward_note"
    }

    init(
        ticker: String,
        decisionType: String,
        currentPositionValue: Double,
        currentConcentrationPct: Double,
        maxPositionSizeCad: Double,
        remainingBudgetCad: Double,
        suggestedSizeCad: Double,
        maxLossAmountCad: Double,
        maxPortfolioRiskPct: Double,
        sizingTier: String,
        haircutApplied: Bool,
        haircutReason: String?,
        stopDistancePct: Double?,
        riskRewardNote: String
    ) {
        self.ticker = ticker
        self.decisionType = decisionType
        self.currentPositionValue = currentPositionValue
        self.currentConcentrationPct = currentConcentrationPct
        self.maxPositionSizeCad = maxPositionSizeCad
        self.remainingBudgetCad = remainingBudgetCad
        self.suggestedSizeCad = suggestedSizeCad
        self.maxLossAmountCad = maxLossAmountCad
        self.maxPortfolioRiskPct = maxPortfolioRiskPct
        self.sizingTier = sizingTier
        self.haircutApplied = haircutApplied
        self.haircutReason = haircutReason
        self.stopDistancePct = stopDistancePct
        self.riskRewardNote = riskRewardNote
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        decisionType = try container.decodeIfPresent(String.self, forKey: .decisionType) ?? "ENTER"
        currentPositionValue = try container.decodeFlexibleDoubleIfPresent(forKey: .currentPositionValue) ?? 0
        currentConcentrationPct = try container.decodeFlexibleDoubleIfPresent(forKey: .currentConcentrationPct) ?? 0
        maxPositionSizeCad = try container.decodeFlexibleDoubleIfPresent(forKey: .maxPositionSizeCad) ?? 0
        remainingBudgetCad = try container.decodeFlexibleDoubleIfPresent(forKey: .remainingBudgetCad) ?? 0
        suggestedSizeCad = try container.decodeFlexibleDoubleIfPresent(forKey: .suggestedSizeCad) ?? 0
        maxLossAmountCad = try container.decodeFlexibleDoubleIfPresent(forKey: .maxLossAmountCad) ?? 0
        maxPortfolioRiskPct = try container.decodeFlexibleDoubleIfPresent(forKey: .maxPortfolioRiskPct) ?? 0
        sizingTier = try container.decodeIfPresent(String.self, forKey: .sizingTier) ?? "NOT_READY"
        haircutApplied = try container.decodeIfPresent(FlexibleBool.self, forKey: .haircutApplied)?.value ?? false
        haircutReason = try container.decodeIfPresent(String.self, forKey: .haircutReason)
        stopDistancePct = try container.decodeFlexibleDoubleIfPresent(forKey: .stopDistancePct)
        riskRewardNote = try container.decodeIfPresent(String.self, forKey: .riskRewardNote) ?? "No sizing guidance available"
    }
}

// MARK: - A18 Portfolio Stress Models

struct PortfolioStressLatestResponse: Codable {
    let run: PortfolioStressRun?
}

struct PortfolioStressHistoryResponse: Codable {
    let runs: [PortfolioStressRun]
    let total: Int

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runs = try container.decodeIfPresent([PortfolioStressRun].self, forKey: .runs) ?? []
        total = try container.decodeIfPresent(FlexibleInt.self, forKey: .total)?.value ?? runs.count
    }
}

struct PortfolioStressRunResponse: Codable {
    let report: PortfolioStressReport
}

struct PortfolioStressRun: Codable, Identifiable {
    var id: String { runId }

    let numericId: Int?
    let runId: String
    let createdAt: String?
    let portfolioValue: Double
    let cash: Double
    let positionCount: Int
    let scenarioCount: Int
    let worstScenario: String?
    let worstLossPct: Double?
    let avgLossPct: Double?
    let warnings: [String]
    let summary: PortfolioStressSummary
    let scenarioEvents: [PortfolioStressScenario]

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case runId = "run_id"
        case createdAt = "created_at"
        case portfolioValue = "portfolio_value"
        case cash
        case positionCount = "position_count"
        case scenarioCount = "scenario_count"
        case worstScenario = "worst_scenario"
        case worstLossPct = "worst_loss_pct"
        case avgLossPct = "avg_loss_pct"
        case warnings
        case summary
        case scenarioEvents = "scenario_events"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(FlexibleInt.self, forKey: .numericId)?.value
        runId = try container.decodeIfPresent(String.self, forKey: .runId) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        portfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .portfolioValue) ?? 0
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash) ?? 0
        positionCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .positionCount)?.value ?? 0
        scenarioCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .scenarioCount)?.value ?? 0
        worstScenario = try container.decodeIfPresent(String.self, forKey: .worstScenario)
        worstLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .worstLossPct)
        avgLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .avgLossPct)
        warnings = try container.decodeIfPresent([String].self, forKey: .warnings) ?? []
        summary = try container.decodeIfPresent(PortfolioStressSummary.self, forKey: .summary) ?? .empty
        scenarioEvents = try container.decodeIfPresent([PortfolioStressScenario].self, forKey: .scenarioEvents) ?? []
    }
}

struct PortfolioStressReport: Codable {
    let runId: String?
    let createdAt: String?
    let portfolioValue: Double
    let cash: Double
    let positionCount: Int
    let scenarioCount: Int
    let worstScenario: String?
    let worstLossPct: Double?
    let avgLossPct: Double?
    let scenarios: [PortfolioStressScenario]
    let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case createdAt = "created_at"
        case portfolioValue = "portfolio_value"
        case cash
        case positionCount = "position_count"
        case scenarioCount = "scenario_count"
        case worstScenario = "worst_scenario"
        case worstLossPct = "worst_loss_pct"
        case avgLossPct = "avg_loss_pct"
        case scenarios
        case warnings
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runId = try container.decodeIfPresent(String.self, forKey: .runId)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        portfolioValue = try container.decodeFlexibleDoubleIfPresent(forKey: .portfolioValue) ?? 0
        cash = try container.decodeFlexibleDoubleIfPresent(forKey: .cash) ?? 0
        positionCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .positionCount)?.value ?? 0
        scenarioCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .scenarioCount)?.value ?? 0
        worstScenario = try container.decodeIfPresent(String.self, forKey: .worstScenario)
        worstLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .worstLossPct)
        avgLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .avgLossPct)
        scenarios = try container.decodeIfPresent([PortfolioStressScenario].self, forKey: .scenarios) ?? []
        warnings = try container.decodeIfPresent([String].self, forKey: .warnings) ?? []
    }
}

struct PortfolioStressSummary: Codable {
    let scenarioCount: Int
    let worstScenario: String?
    let worstLossPct: Double?
    let avgLossPct: Double?

    static let empty = PortfolioStressSummary(scenarioCount: 0, worstScenario: nil, worstLossPct: nil, avgLossPct: nil)

    enum CodingKeys: String, CodingKey {
        case scenarioCount = "scenario_count"
        case worstScenario = "worst_scenario"
        case worstLossPct = "worst_loss_pct"
        case avgLossPct = "avg_loss_pct"
    }

    init(scenarioCount: Int, worstScenario: String?, worstLossPct: Double?, avgLossPct: Double?) {
        self.scenarioCount = scenarioCount
        self.worstScenario = worstScenario
        self.worstLossPct = worstLossPct
        self.avgLossPct = avgLossPct
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scenarioCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .scenarioCount)?.value ?? 0
        worstScenario = try container.decodeIfPresent(String.self, forKey: .worstScenario)
        worstLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .worstLossPct)
        avgLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .avgLossPct)
    }
}

struct PortfolioStressScenario: Codable, Identifiable {
    var id: String { "\(numericId ?? 0)-\(scenarioType)-\(createdAt ?? "")" }

    let numericId: Int?
    let runId: String?
    let scenarioType: String
    let scenarioLabel: String?
    let estimatedLossPct: Double
    let estimatedLossAmount: Double
    let riskLevel: String
    let positionResults: [PortfolioStressPosition]
    let recommendedActions: [String]
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case numericId = "id"
        case runId = "run_id"
        case scenarioType = "scenario_type"
        case scenarioLabel = "scenario_label"
        case estimatedLossPct = "estimated_loss_pct"
        case estimatedLossAmount = "estimated_loss_amount"
        case riskLevel = "risk_level"
        case positionResults = "position_results"
        case recommendedActions = "recommended_actions"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        numericId = try container.decodeIfPresent(FlexibleInt.self, forKey: .numericId)?.value
        runId = try container.decodeIfPresent(String.self, forKey: .runId)
        scenarioType = try container.decodeIfPresent(String.self, forKey: .scenarioType) ?? "UNKNOWN"
        scenarioLabel = try container.decodeIfPresent(String.self, forKey: .scenarioLabel)
        estimatedLossPct = try container.decodeFlexibleDoubleIfPresent(forKey: .estimatedLossPct) ?? 0
        estimatedLossAmount = try container.decodeFlexibleDoubleIfPresent(forKey: .estimatedLossAmount) ?? 0
        riskLevel = try container.decodeIfPresent(String.self, forKey: .riskLevel) ?? "LOW"
        positionResults = try container.decodeIfPresent([PortfolioStressPosition].self, forKey: .positionResults) ?? []
        recommendedActions = try container.decodeIfPresent([String].self, forKey: .recommendedActions) ?? []
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
    }

    var estimatedPortfolioValue: Double {
        positionResults.reduce(0) { $0 + $1.stressedValue }
    }

    var worstImpactedHoldings: [PortfolioStressPosition] {
        positionResults.sorted { $0.estimatedLoss < $1.estimatedLoss }.prefix(3).map { $0 }
    }

    var concentrationContribution: Double {
        guard estimatedLossAmount != 0 else { return 0 }
        let largestLoss = abs(worstImpactedHoldings.first?.estimatedLoss ?? 0)
        return largestLoss / abs(estimatedLossAmount) * 100
    }

    var cashBufferAfterStress: Double {
        positionResults.reduce(0) { $0 + $1.estimatedLoss }
    }
}

struct PortfolioStressPosition: Codable, Identifiable {
    var id: String { ticker }

    let ticker: String
    let marketValue: Double
    let shockPct: Double
    let estimatedLoss: Double
    let stressedValue: Double

    enum CodingKeys: String, CodingKey {
        case ticker
        case marketValue = "market_value"
        case shockPct = "shock_pct"
        case estimatedLoss = "estimated_loss"
        case stressedValue = "stressed_value"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        marketValue = try container.decodeFlexibleDoubleIfPresent(forKey: .marketValue) ?? 0
        shockPct = try container.decodeFlexibleDoubleIfPresent(forKey: .shockPct) ?? 0
        estimatedLoss = try container.decodeFlexibleDoubleIfPresent(forKey: .estimatedLoss) ?? 0
        stressedValue = try container.decodeFlexibleDoubleIfPresent(forKey: .stressedValue) ?? 0
    }

    var lossContributionPct: Double {
        guard marketValue != 0 else { return 0 }
        return estimatedLoss / marketValue * 100
    }

    var sensitivity: String {
        let absShock = abs(shockPct)
        if absShock >= 30 { return "Severe" }
        if absShock >= 20 { return "High" }
        if absShock >= 10 { return "Moderate" }
        return "Low"
    }
}

enum PortfolioStressLabels {
    static func risk(_ value: String) -> String {
        switch value.uppercased() {
        case "LOW": return "Low"
        case "MODERATE": return "Moderate"
        case "HIGH": return "High"
        case "SEVERE": return "Severe"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func scenario(_ value: String?) -> String {
        switch (value ?? "").uppercased() {
        case "MARKET_PULLBACK_5": return "Market pullback"
        case "MARKET_CORRECTION_10": return "Market correction"
        case "MARKET_CRASH_20": return "Market crash"
        case "TECH_SELL_OFF": return "Tech sell-off"
        case "AI_SEMI_REVERSAL": return "AI/semi reversal"
        case "CRYPTO_RISK_OFF": return "Crypto risk-off"
        case "CANADA_UNDERPERFORMANCE": return "Canada underperforms"
        case "USD_CAD_MOVE": return "USD/CAD move"
        case "VOLATILITY_SPIKE": return "Volatility spike"
        case "ALPHA_FALSE_POSITIVE_CLUSTER": return "Alpha false positives"
        case "CUSTOM": return "Custom"
        default: return (value ?? "Unknown").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

