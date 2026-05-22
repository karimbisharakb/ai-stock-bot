import Foundation

// Checklist models split from AlphaCandidate.swift.
// MARK: - A14 Decision Checklist Models

struct DecisionChecklistListResponse: Codable {
    let checklists: [DecisionChecklist]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        checklists = try container.decodeIfPresent([DecisionChecklist].self, forKey: .checklists) ?? []
    }
}

struct DecisionChecklist: Codable, Identifiable {
    var id: String { checklistId }

    let rowId: Int?
    let checklistId: String
    let ticker: String
    let decisionType: String
    let linkedAlphaCandidateId: String?
    let linkedThesisId: Int?
    let checklistStatus: String
    let checklistCompletion: Double
    let blockingItems: Int
    let readiness: String
    let notes: String
    let createdAt: String?
    let reviewedAt: String?
    let updatedAt: String?
    let items: [DecisionChecklistItem]
    let auditTrail: [DecisionChecklistAuditEntry]

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case checklistId = "checklist_id"
        case ticker
        case decisionType = "decision_type"
        case linkedAlphaCandidateId = "linked_alpha_candidate_id"
        case linkedThesisId = "linked_thesis_id"
        case checklistStatus = "checklist_status"
        case checklistCompletion = "checklist_completion"
        case blockingItems = "blocking_items"
        case readiness
        case notes
        case createdAt = "created_at"
        case reviewedAt = "reviewed_at"
        case updatedAt = "updated_at"
        case items
        case auditTrail = "audit_trail"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        checklistId = try container.decodeIfPresent(String.self, forKey: .checklistId) ?? UUID().uuidString
        ticker = try container.decodeIfPresent(String.self, forKey: .ticker) ?? ""
        decisionType = try container.decodeIfPresent(String.self, forKey: .decisionType) ?? "ENTER"
        linkedAlphaCandidateId = try container.decodeIfPresent(String.self, forKey: .linkedAlphaCandidateId)
        linkedThesisId = try container.decodeIfPresent(FlexibleInt.self, forKey: .linkedThesisId)?.value
        checklistStatus = try container.decodeIfPresent(String.self, forKey: .checklistStatus) ?? "DRAFT"
        checklistCompletion = try container.decodeFlexibleDoubleIfPresent(forKey: .checklistCompletion) ?? 0
        blockingItems = try container.decodeIfPresent(FlexibleInt.self, forKey: .blockingItems)?.value ?? 0
        readiness = try container.decodeIfPresent(String.self, forKey: .readiness) ?? "NOT_READY"
        notes = try container.decodeIfPresent(String.self, forKey: .notes) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        reviewedAt = try container.decodeIfPresent(String.self, forKey: .reviewedAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        items = try container.decodeIfPresent([DecisionChecklistItem].self, forKey: .items) ?? []
        auditTrail = try container.decodeIfPresent([DecisionChecklistAuditEntry].self, forKey: .auditTrail) ?? []
    }
}

struct DecisionChecklistItem: Codable, Identifiable {
    var id: String { itemKey }

    let rowId: Int?
    let checklistId: String
    let itemKey: String
    let label: String
    let passed: Bool?
    let note: String
    let required: Bool
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case checklistId = "checklist_id"
        case itemKey = "item_key"
        case label
        case passed
        case note
        case required
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rowId = try container.decodeIfPresent(FlexibleInt.self, forKey: .rowId)?.value
        checklistId = try container.decodeIfPresent(String.self, forKey: .checklistId) ?? ""
        itemKey = try container.decodeIfPresent(String.self, forKey: .itemKey) ?? ""
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? itemKey
        passed = try container.decodeIfPresent(FlexibleBool.self, forKey: .passed)?.value
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? ""
        required = try container.decodeIfPresent(FlexibleBool.self, forKey: .required)?.value ?? true
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct DecisionChecklistAuditEntry: Codable, Identifiable {
    var id: String { "\(rowId ?? 0)-\(performedAt ?? action)" }

    let rowId: Int?
    let checklistId: String
    let action: String
    let fromStatus: String?
    let toStatus: String?
    let actor: String?
    let detailJson: String?
    let performedAt: String?

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case checklistId = "checklist_id"
        case action
        case fromStatus = "from_status"
        case toStatus = "to_status"
        case actor
        case detailJson = "detail_json"
        case performedAt = "performed_at"
    }
}

struct DecisionChecklistActionResponse: Codable {
    let ok: Bool
    let checklistId: String?
    let checklist: DecisionChecklist?
    let errors: [String]?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case checklistId = "checklist_id"
        case checklist
        case errors
        case error
    }
}

struct DecisionChecklistItemActionResponse: Codable {
    let ok: Bool
    let checklistId: String?
    let item: DecisionChecklistItem?
    let scoring: DecisionChecklistScoring?
    let errors: [String]?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case checklistId = "checklist_id"
        case item
        case scoring
        case errors
        case error
    }
}

struct DecisionChecklistScoring: Codable {
    let checklistCompletion: Double
    let blockingItems: Int
    let readiness: String

    enum CodingKeys: String, CodingKey {
        case checklistCompletion = "checklist_completion"
        case blockingItems = "blocking_items"
        case readiness
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        checklistCompletion = try container.decodeFlexibleDoubleIfPresent(forKey: .checklistCompletion) ?? 0
        blockingItems = try container.decodeIfPresent(FlexibleInt.self, forKey: .blockingItems)?.value ?? 0
        readiness = try container.decodeIfPresent(String.self, forKey: .readiness) ?? "NOT_READY"
    }
}

struct DecisionSummaryResponse: Codable {
    let pendingCount: Int
    let approvedCount: Int
    let rejectedCount: Int
    let archivedCount: Int
    let byDecisionType: [String: Int]
    let pendingChecklists: [DecisionChecklist]

    enum CodingKeys: String, CodingKey {
        case pendingCount = "pending_count"
        case approvedCount = "approved_count"
        case rejectedCount = "rejected_count"
        case archivedCount = "archived_count"
        case byDecisionType = "by_decision_type"
        case pendingChecklists = "pending_checklists"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        pendingCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .pendingCount)?.value ?? 0
        approvedCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .approvedCount)?.value ?? 0
        rejectedCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .rejectedCount)?.value ?? 0
        archivedCount = try container.decodeIfPresent(FlexibleInt.self, forKey: .archivedCount)?.value ?? 0
        let rawTypes = try container.decodeIfPresent([String: FlexibleInt].self, forKey: .byDecisionType) ?? [:]
        byDecisionType = rawTypes.mapValues(\.value)
        pendingChecklists = try container.decodeIfPresent([DecisionChecklist].self, forKey: .pendingChecklists) ?? []
    }
}

