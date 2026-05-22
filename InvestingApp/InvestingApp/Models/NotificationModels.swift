import Foundation

// Notification center models split from AlphaCandidate.swift.
struct CatalystListResponse: Codable {
    let count: Int
    let days: Int
    let catalysts: [Catalyst]
}

struct Catalyst: Codable, Identifiable {
    var id: String { catalystId }
    let catalystId: String
    let ticker: String?
    let title: String
    let description: String
    let catalystType: String
    let date: String
    let confidence: String
    let importance: String
    let source: String
    let status: String
    let linkedEntityType: String?
    let linkedEntityId: String?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case ticker, title, description, date, confidence, importance, source, status
        case catalystId = "catalyst_id"
        case catalystType = "catalyst_type"
        case linkedEntityType = "linked_entity_type"
        case linkedEntityId = "linked_entity_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        catalystId = try c.decodeIfPresent(String.self, forKey: .catalystId) ?? UUID().uuidString
        ticker = try c.decodeIfPresent(String.self, forKey: .ticker)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        catalystType = try c.decodeIfPresent(String.self, forKey: .catalystType) ?? "OTHER"
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        confidence = try c.decodeIfPresent(String.self, forKey: .confidence) ?? "MEDIUM"
        importance = try c.decodeIfPresent(String.self, forKey: .importance) ?? "MEDIUM"
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "manual"
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "UPCOMING"
        linkedEntityType = try c.decodeIfPresent(String.self, forKey: .linkedEntityType)
        linkedEntityId = try c.decodeIfPresent(String.self, forKey: .linkedEntityId)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct CatalystSummaryResponse: Codable {
    let thisWeekCount: Int
    let nextWeekCount: Int
    let highImportanceCount: Int
    let portfolioCatalysts: [CatalystSummaryItem]
    let alphaCatalysts: [CatalystAlphaSummaryItem]
    let overdueCount: Int
    let overdueCatalysts: [CatalystSummaryItem]
    let missingThesisDates: [String]

    enum CodingKeys: String, CodingKey {
        case thisWeekCount = "this_week_count"
        case nextWeekCount = "next_week_count"
        case highImportanceCount = "high_importance_count"
        case portfolioCatalysts = "portfolio_catalysts"
        case alphaCatalysts = "alpha_catalysts"
        case overdueCount = "overdue_count"
        case overdueCatalysts = "overdue_catalysts"
        case missingThesisDates = "missing_thesis_dates"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        thisWeekCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .thisWeekCount)?.value ?? 0
        nextWeekCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .nextWeekCount)?.value ?? 0
        highImportanceCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .highImportanceCount)?.value ?? 0
        portfolioCatalysts = try c.decodeIfPresent([CatalystSummaryItem].self, forKey: .portfolioCatalysts) ?? []
        alphaCatalysts = try c.decodeIfPresent([CatalystAlphaSummaryItem].self, forKey: .alphaCatalysts) ?? []
        let overdueItems = try c.decodeIfPresent([CatalystSummaryItem].self, forKey: .overdueCatalysts) ?? []
        overdueCatalysts = overdueItems
        overdueCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .overdueCount)?.value ?? overdueItems.count
        missingThesisDates = try c.decodeFlexibleStringArrayIfPresent(forKey: .missingThesisDates) ?? []
    }
}

struct CatalystSummaryItem: Codable, Identifiable {
    var id: String { catalystId ?? "\(ticker ?? "none")-\(title)-\(date)" }
    let catalystId: String?
    let ticker: String?
    let title: String
    let date: String
    let importance: String

    enum CodingKeys: String, CodingKey {
        case ticker, title, date, importance
        case catalystId = "catalyst_id"
    }
}

struct CatalystAlphaSummaryItem: Codable, Identifiable {
    var id: String { ticker + readinessTier }
    let ticker: String
    let readinessTier: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case readinessTier = "readiness_tier"
    }
}

struct NotificationListResponse: Codable {
    let notifications: [InAppNotification]
    let suppressedCount: Int
    let filtered: [InAppNotification]
    let quietHoursActive: Bool

    enum CodingKeys: String, CodingKey {
        case notifications, filtered
        case suppressedCount = "suppressed_count"
        case quietHoursActive = "quiet_hours_active"
    }

    init(from decoder: Decoder) throws {
        if let array = try? [InAppNotification](from: decoder) {
            notifications = array
            suppressedCount = 0
            filtered = []
            quietHoursActive = false
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        notifications = try c.decodeIfPresent([InAppNotification].self, forKey: .notifications) ?? []
        suppressedCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .suppressedCount)?.value ?? 0
        filtered = try c.decodeIfPresent([InAppNotification].self, forKey: .filtered) ?? []
        quietHoursActive = try c.decodeIfPresent(FlexibleBool.self, forKey: .quietHoursActive)?.value ?? false
    }
}

struct InAppNotification: Codable, Identifiable {
    var id: String { notificationId }
    let notificationId: String
    let category: String
    let severity: String
    let title: String
    let body: String
    let entityType: String?
    let entityId: String?
    let source: String
    let status: String
    let actionURL: String?
    let metadata: [String: FlexibleJSONValue]
    let createdAt: String
    let updatedAt: String?
    let expiresAt: String?

    var ticker: String? {
        guard entityType?.lowercased().contains("ticker") == true
            || entityType?.lowercased().contains("holding") == true
            || entityType?.lowercased().contains("alpha") == true
        else { return nil }
        return entityId
    }

    var shortBody: String {
        if let value = metadata["short_body"]?.stringValue, !value.isEmpty { return value }
        if let value = metadata["shortBody"]?.stringValue, !value.isEmpty { return value }
        if body.count <= 120 { return body }
        return String(body.prefix(117)) + "..."
    }

    enum CodingKeys: String, CodingKey {
        case category, severity, title, body, source, status, metadata
        case notificationId = "notification_id"
        case entityType = "entity_type"
        case entityId = "entity_id"
        case actionURL = "action_url"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case expiresAt = "expires_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        notificationId = try c.decodeIfPresent(String.self, forKey: .notificationId) ?? UUID().uuidString
        category = try c.decodeIfPresent(String.self, forKey: .category) ?? "SYSTEM"
        severity = try c.decodeIfPresent(String.self, forKey: .severity) ?? "INFO"
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        entityType = try c.decodeIfPresent(String.self, forKey: .entityType)
        entityId = try c.decodeIfPresent(String.self, forKey: .entityId)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "system"
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "UNREAD"
        actionURL = try c.decodeIfPresent(String.self, forKey: .actionURL)
        metadata = try c.decodeIfPresent([String: FlexibleJSONValue].self, forKey: .metadata) ?? [:]
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
        expiresAt = try c.decodeIfPresent(String.self, forKey: .expiresAt)
    }
}

struct NotificationSummaryResponse: Codable {
    let unreadCount: Int
    let criticalCount: Int
    let warningCount: Int
    let byCategory: [String: Int]
    let bySeverity: [String: Int]
    let topNotifications: [InAppNotification]
    let staleNotificationCount: Int
    let generatedAt: String
    let visibleUnreadCount: Int
    let filteredCount: Int
    let suppressedByPreferencesCount: Int
    let quietHoursActive: Bool

    enum CodingKeys: String, CodingKey {
        case unreadCount = "unread_count"
        case criticalCount = "critical_count"
        case warningCount = "warning_count"
        case byCategory = "by_category"
        case bySeverity = "by_severity"
        case topNotifications = "top_notifications"
        case staleNotificationCount = "stale_notification_count"
        case generatedAt = "generated_at"
        case visibleUnreadCount = "visible_unread_count"
        case filteredCount = "filtered_count"
        case suppressedByPreferencesCount = "suppressed_by_preferences_count"
        case quietHoursActive = "quiet_hours_active"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        unreadCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .unreadCount)?.value ?? 0
        criticalCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .criticalCount)?.value ?? 0
        warningCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .warningCount)?.value ?? 0
        let categories = try c.decodeIfPresent([String: FlexibleInt].self, forKey: .byCategory) ?? [:]
        byCategory = categories.mapValues(\.value)
        let severities = try c.decodeIfPresent([String: FlexibleInt].self, forKey: .bySeverity) ?? [:]
        bySeverity = severities.mapValues(\.value)
        topNotifications = try c.decodeIfPresent([InAppNotification].self, forKey: .topNotifications) ?? []
        staleNotificationCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .staleNotificationCount)?.value ?? 0
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        visibleUnreadCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .visibleUnreadCount)?.value ?? unreadCount
        filteredCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .filteredCount)?.value ?? 0
        suppressedByPreferencesCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .suppressedByPreferencesCount)?.value ?? filteredCount
        quietHoursActive = try c.decodeIfPresent(FlexibleBool.self, forKey: .quietHoursActive)?.value ?? false
    }
}

struct NotificationGenerateResponse: Codable {
    let generated: Int
    let errors: Int
    let generatedAt: String?
    let skipped: Bool

    enum CodingKeys: String, CodingKey {
        case generated, errors, skipped
        case generatedAt = "generated_at"
    }
}

struct NotificationMarkAllReadResponse: Codable {
    let markedRead: Int

    enum CodingKeys: String, CodingKey {
        case markedRead = "marked_read"
    }
}

struct NotificationArchiveReadResponse: Codable {
    let archived: Int
}

struct NotificationPreferences: Codable {
    let enabledCategories: [String]
    let minimumSeverity: String
    let quietHoursEnabled: Bool
    let quietHoursStart: String
    let quietHoursEnd: String
    let timezone: String
    let digestMode: String
    let maxNotificationsPerDigest: Int
    let includeReadItems: Bool
    let autoArchiveAfterDays: Int
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case enabledCategories = "enabled_categories"
        case minimumSeverity = "minimum_severity"
        case quietHoursEnabled = "quiet_hours_enabled"
        case quietHoursStart = "quiet_hours_start"
        case quietHoursEnd = "quiet_hours_end"
        case timezone
        case digestMode = "digest_mode"
        case maxNotificationsPerDigest = "max_notifications_per_digest"
        case includeReadItems = "include_read_items"
        case autoArchiveAfterDays = "auto_archive_after_days"
        case updatedAt = "updated_at"
    }
}

struct NotificationCategoryPreference: Codable, Identifiable {
    var id: String { category }
    let category: String
    var enabled: Bool
    var minimumSeverity: String?
    var digestOnly: Bool
    var quietHoursOverride: Bool?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case category, enabled
        case minimumSeverity = "minimum_severity"
        case digestOnly = "digest_only"
        case quietHoursOverride = "quiet_hours_override"
        case updatedAt = "updated_at"
    }

    init(category: String, enabled: Bool = true, minimumSeverity: String? = nil, digestOnly: Bool = false, quietHoursOverride: Bool? = nil, updatedAt: String? = nil) {
        self.category = category
        self.enabled = enabled
        self.minimumSeverity = minimumSeverity
        self.digestOnly = digestOnly
        self.quietHoursOverride = quietHoursOverride
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        category = try c.decodeIfPresent(String.self, forKey: .category) ?? "SYSTEM"
        enabled = try c.decodeIfPresent(FlexibleBool.self, forKey: .enabled)?.value ?? true
        minimumSeverity = try c.decodeIfPresent(String.self, forKey: .minimumSeverity)
        digestOnly = try c.decodeIfPresent(FlexibleBool.self, forKey: .digestOnly)?.value ?? false
        quietHoursOverride = try c.decodeIfPresent(FlexibleBool.self, forKey: .quietHoursOverride)?.value
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct NotificationDigestResponse: Codable {
    let title: String
    let mode: String
    let generatedAt: String
    let includedCount: Int
    let omittedCount: Int
    let byCategory: [String: Int]
    let bySeverity: [String: Int]
    let topCriticalWarning: [InAppNotification]
    let topAlpha: [InAppNotification]
    let topRisk: [InAppNotification]
    let topResearchCatalystChecklist: [InAppNotification]
    let notifications: [InAppNotification]

    enum CodingKeys: String, CodingKey {
        case title, mode, notifications
        case generatedAt = "generated_at"
        case includedCount = "included_count"
        case omittedCount = "omitted_count"
        case byCategory = "by_category"
        case bySeverity = "by_severity"
        case topCriticalWarning = "top_critical_warning"
        case topAlpha = "top_alpha"
        case topRisk = "top_risk"
        case topResearchCatalystChecklist = "top_research_catalyst_checklist"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Digest"
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "daily"
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        includedCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .includedCount)?.value ?? 0
        omittedCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .omittedCount)?.value ?? 0
        byCategory = (try c.decodeIfPresent([String: FlexibleInt].self, forKey: .byCategory) ?? [:]).mapValues(\.value)
        bySeverity = (try c.decodeIfPresent([String: FlexibleInt].self, forKey: .bySeverity) ?? [:]).mapValues(\.value)
        topCriticalWarning = try c.decodeIfPresent([InAppNotification].self, forKey: .topCriticalWarning) ?? []
        topAlpha = try c.decodeIfPresent([InAppNotification].self, forKey: .topAlpha) ?? []
        topRisk = try c.decodeIfPresent([InAppNotification].self, forKey: .topRisk) ?? []
        topResearchCatalystChecklist = try c.decodeIfPresent([InAppNotification].self, forKey: .topResearchCatalystChecklist) ?? []
        notifications = try c.decodeIfPresent([InAppNotification].self, forKey: .notifications) ?? []
    }
}

struct FlexibleJSONValue: Codable {
    let stringValue: String?

    var boolValue: Bool? {
        guard let stringValue else { return nil }
        switch stringValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "true", "1", "yes", "on": return true
        case "false", "0", "no", "off": return false
        default: return nil
        }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let string = try? c.decode(String.self) {
            stringValue = string
        } else if let int = try? c.decode(Int.self) {
            stringValue = String(int)
        } else if let double = try? c.decode(Double.self) {
            stringValue = String(double)
        } else if let bool = try? c.decode(Bool.self) {
            stringValue = bool.description
        } else {
            stringValue = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(stringValue)
    }
}

// MARK: - System Diagnostics Models

struct SystemReleaseCheckResponse: Codable {
    let overallStatus: String
    let checksPassed: Int
    let checksWarned: Int
    let checksFailed: Int
    let checksTotal: Int
    let warnings: [SystemCheckMessage]
    let failures: [SystemCheckMessage]
    let recommendedFixes: [String]
    let sections: [String: [SystemCheckResult]]
    let environment: [String: FlexibleJSONValue]
    let generatedAt: String
    let mode: String

    var orderedSections: [(name: String, results: [SystemCheckResult])] {
        let preferred = ["core", "routes", "notification_safety", "data_health", "brief_safety", "alpha_safety"]
        var output = preferred.compactMap { key -> (String, [SystemCheckResult])? in
            guard let results = sections[key] else { return nil }
            return (key, results)
        }
        let extras = sections.keys
            .filter { !preferred.contains($0) }
            .sorted()
            .map { ($0, sections[$0] ?? []) }
        output.append(contentsOf: extras)
        return output
    }

    enum CodingKeys: String, CodingKey {
        case warnings, failures, sections, environment, mode
        case overallStatus = "overall_status"
        case checksPassed = "checks_passed"
        case checksWarned = "checks_warned"
        case checksFailed = "checks_failed"
        case checksTotal = "checks_total"
        case recommendedFixes = "recommended_fixes"
        case generatedAt = "generated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        overallStatus = try c.decodeIfPresent(String.self, forKey: .overallStatus) ?? "WATCH"
        checksPassed = try c.decodeIfPresent(FlexibleInt.self, forKey: .checksPassed)?.value ?? 0
        checksWarned = try c.decodeIfPresent(FlexibleInt.self, forKey: .checksWarned)?.value ?? 0
        checksFailed = try c.decodeIfPresent(FlexibleInt.self, forKey: .checksFailed)?.value ?? 0
        checksTotal = try c.decodeIfPresent(FlexibleInt.self, forKey: .checksTotal)?.value ?? checksPassed + checksWarned + checksFailed
        warnings = try c.decodeIfPresent([SystemCheckMessage].self, forKey: .warnings) ?? []
        failures = try c.decodeIfPresent([SystemCheckMessage].self, forKey: .failures) ?? []
        recommendedFixes = try c.decodeFlexibleStringArrayIfPresent(forKey: .recommendedFixes) ?? []
        sections = try c.decodeIfPresent([String: [SystemCheckResult]].self, forKey: .sections) ?? [:]
        environment = try c.decodeIfPresent([String: FlexibleJSONValue].self, forKey: .environment) ?? [:]
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "full"
    }
}

struct SystemCheckMessage: Codable, Identifiable {
    var id: String { "\(name)-\(detail)" }
    let name: String
    let detail: String

    enum CodingKeys: String, CodingKey {
        case name, detail
    }

    init(from decoder: Decoder) throws {
        if let string = try? decoder.singleValueContainer().decode(String.self) {
            name = "message"
            detail = string
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "message"
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
    }
}

struct SystemCheckResult: Codable, Identifiable {
    var id: String { name }
    let name: String
    let status: String
    let detail: String
    let fix: String?

    enum CodingKeys: String, CodingKey {
        case name, status, detail, fix
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? UUID().uuidString
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "WARN"
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
        fix = try c.decodeIfPresent(String.self, forKey: .fix)
    }
}

struct SystemRoutesResponse: Codable {
    let routes: [SystemRouteInfo]
    let count: Int
    let required: [String]
    let requiredCount: Int
    let error: String?

    var missingCriticalRoutes: [SystemRouteInfo] {
        let registered = Set(routes.map { $0.path })
        return required
            .filter { path in
                let normalized = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                return !registered.contains(path) && !registered.contains(where: { $0.trimmingCharacters(in: CharacterSet(charactersIn: "/")).hasPrefix(normalized) })
            }
            .map { SystemRouteInfo(path: $0, method: "GET", group: SystemRouteInfo.groupName(for: $0), registered: false, critical: true) }
    }

    enum CodingKeys: String, CodingKey {
        case routes, count, required, error
        case requiredCount = "required_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let richRoutes = try? c.decode([SystemRouteInfo].self, forKey: .routes) {
            routes = richRoutes
        } else {
            let routePaths = try c.decodeIfPresent([String].self, forKey: .routes) ?? []
            routes = routePaths.map { SystemRouteInfo(path: $0, method: "GET", group: SystemRouteInfo.groupName(for: $0), registered: true, critical: false) }
        }
        count = try c.decodeIfPresent(FlexibleInt.self, forKey: .count)?.value ?? routes.count
        required = try c.decodeFlexibleStringArrayIfPresent(forKey: .required) ?? []
        requiredCount = try c.decodeIfPresent(FlexibleInt.self, forKey: .requiredCount)?.value ?? required.count
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

struct SystemRouteInfo: Codable, Identifiable {
    var id: String { "\(method)-\(path)" }
    let method: String
    let path: String
    let group: String?
    let category: String?
    let registered: Bool
    let critical: Bool

    var isMissing: Bool { !registered }
    var groupLabel: String { group ?? category ?? Self.groupName(for: path) ?? "System" }

    enum CodingKeys: String, CodingKey {
        case method, path, group, category, registered, critical, missing
    }

    init(path: String, method: String, group: String?, registered: Bool, critical: Bool) {
        self.path = path
        self.method = method
        self.group = group
        self.category = group
        self.registered = registered
        self.critical = critical
    }

    init(from decoder: Decoder) throws {
        if let string = try? decoder.singleValueContainer().decode(String.self) {
            path = string
            method = "GET"
            group = Self.groupName(for: string)
            category = group
            registered = true
            critical = false
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decodeIfPresent(String.self, forKey: .path) ?? ""
        method = try c.decodeIfPresent(String.self, forKey: .method) ?? "GET"
        group = try c.decodeIfPresent(String.self, forKey: .group)
        category = try c.decodeIfPresent(String.self, forKey: .category)
        let missing = try c.decodeIfPresent(FlexibleBool.self, forKey: .missing)?.value ?? false
        registered = try c.decodeIfPresent(FlexibleBool.self, forKey: .registered)?.value ?? !missing
        critical = try c.decodeIfPresent(FlexibleBool.self, forKey: .critical)?.value ?? false
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(method, forKey: .method)
        try c.encode(path, forKey: .path)
        try c.encodeIfPresent(group, forKey: .group)
        try c.encodeIfPresent(category, forKey: .category)
        try c.encode(registered, forKey: .registered)
        try c.encode(critical, forKey: .critical)
        try c.encode(!registered, forKey: .missing)
    }

    static func groupName(for path: String) -> String? {
        let parts = path.split(separator: "/").map(String.init)
        guard let index = parts.firstIndex(of: "v1"), parts.indices.contains(index + 1) else { return nil }
        return parts[index + 1].replacingOccurrences(of: "-", with: " ").capitalized
    }
}

struct SystemFlagsResponse: Codable {
    let flags: [String: FlexibleJSONValue]
    let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case flags
        case generatedAt = "generated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        flags = try c.decodeIfPresent([String: FlexibleJSONValue].self, forKey: .flags) ?? [:]
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
    }

    func boolFlag(_ key: String) -> Bool? {
        let normalized = key.lowercased()
        return flags[normalized]?.boolValue ?? flags[key]?.boolValue
    }

    func textFlag(_ key: String) -> String {
        let normalized = key.lowercased()
        return flags[normalized]?.stringValue ?? flags[key]?.stringValue ?? "unknown"
    }
}
