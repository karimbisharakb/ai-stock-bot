import Foundation

// MARK: - Phase I30 / A29 Backup & Export Models

struct BackupManifestEntry: Codable, Identifiable {
    var id: String { backupId }
    let backupId: String
    let createdAt: String
    let backupType: String
    let tableCount: Int
    let rowCount: Int
    let sizeBytes: Int
    let checksumSha256: String
    let status: String
    let sourceDbPath: String
    let filePath: String
    let notes: String

    enum CodingKeys: String, CodingKey {
        case backupId = "backup_id"
        case createdAt = "created_at"
        case backupType = "backup_type"
        case tableCount = "table_count"
        case rowCount = "row_count"
        case sizeBytes = "size_bytes"
        case checksumSha256 = "checksum_sha256"
        case status
        case sourceDbPath = "source_db_path"
        case filePath = "file_path"
        case notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backupId        = try c.decodeIfPresent(String.self, forKey: .backupId) ?? ""
        createdAt       = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        backupType      = try c.decodeIfPresent(String.self, forKey: .backupType) ?? "FULL"
        tableCount      = try c.decodeIfPresent(FlexibleInt.self, forKey: .tableCount)?.value ?? 0
        rowCount        = try c.decodeIfPresent(FlexibleInt.self, forKey: .rowCount)?.value ?? 0
        sizeBytes       = try c.decodeIfPresent(FlexibleInt.self, forKey: .sizeBytes)?.value ?? 0
        checksumSha256  = try c.decodeIfPresent(String.self, forKey: .checksumSha256) ?? ""
        status          = try c.decodeIfPresent(String.self, forKey: .status) ?? "CREATED"
        sourceDbPath    = try c.decodeIfPresent(String.self, forKey: .sourceDbPath) ?? ""
        filePath        = try c.decodeIfPresent(String.self, forKey: .filePath) ?? ""
        notes           = try c.decodeIfPresent(String.self, forKey: .notes) ?? ""
    }

    var shortChecksum: String {
        let prefix = String(checksumSha256.prefix(16))
        return prefix.isEmpty ? "—" : prefix + "…"
    }

    var formattedSize: String {
        let kb = Double(sizeBytes) / 1024.0
        if kb < 1024 { return String(format: "%.1f KB", kb) }
        return String(format: "%.2f MB", kb / 1024.0)
    }

    var fileBasename: String {
        URL(fileURLWithPath: filePath).lastPathComponent
    }
}

// MARK: -

struct BackupListResponse: Codable {
    let backups: [BackupManifestEntry]
    let limit: Int

    enum CodingKeys: String, CodingKey {
        case backups, limit
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backups = try c.decodeIfPresent([BackupManifestEntry].self, forKey: .backups) ?? []
        limit   = try c.decodeIfPresent(FlexibleInt.self, forKey: .limit)?.value ?? 20
    }
}

// MARK: -

struct BackupVerifyCheck: Codable, Identifiable {
    var id: String { name }
    let name: String
    let passed: Bool
    let detail: String

    enum CodingKeys: String, CodingKey {
        case name, passed, detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name   = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        passed = try c.decodeIfPresent(FlexibleBool.self, forKey: .passed)?.value ?? false
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
    }
}

struct BackupVerifyResponse: Codable {
    let ok: Bool
    let backupId: String
    let checks: [BackupVerifyCheck]
    let manifestEntry: BackupManifestEntry?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, checks, error
        case backupId      = "backup_id"
        case manifestEntry = "manifest_entry"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok            = try c.decodeIfPresent(FlexibleBool.self, forKey: .ok)?.value ?? false
        backupId      = try c.decodeIfPresent(String.self, forKey: .backupId) ?? ""
        checks        = try c.decodeIfPresent([BackupVerifyCheck].self, forKey: .checks) ?? []
        manifestEntry = try c.decodeIfPresent(BackupManifestEntry.self, forKey: .manifestEntry)
        error         = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

// MARK: -

struct BackupTableDelta: Codable {
    let backupRows: Int
    let liveRows: Int
    let delta: Int
    let tableExistsInLive: Bool

    enum CodingKeys: String, CodingKey {
        case delta
        case backupRows        = "backup_rows"
        case liveRows          = "live_rows"
        case tableExistsInLive = "table_exists_in_live"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backupRows        = try c.decodeIfPresent(FlexibleInt.self, forKey: .backupRows)?.value ?? 0
        liveRows          = try c.decodeIfPresent(FlexibleInt.self, forKey: .liveRows)?.value ?? 0
        delta             = try c.decodeIfPresent(FlexibleInt.self, forKey: .delta)?.value ?? 0
        tableExistsInLive = try c.decodeIfPresent(FlexibleBool.self, forKey: .tableExistsInLive)?.value ?? true
    }
}

struct BackupRestorePreview: Codable {
    let backupType: String
    let backupCreatedAt: String
    let backupStatus: String
    let checksumSha256: String
    let tables: [String: BackupTableDelta]
    let tablesWithDelta: [String]
    let wouldChange: Bool
    let changeSummary: String
    let featureFlags: [String: FlexibleJSONValue]?
    let envSnapshot: [String: FlexibleJSONValue]?

    enum CodingKeys: String, CodingKey {
        case tables
        case backupType      = "backup_type"
        case backupCreatedAt = "backup_created_at"
        case backupStatus    = "backup_status"
        case checksumSha256  = "checksum_sha256"
        case tablesWithDelta = "tables_with_delta"
        case wouldChange     = "would_change"
        case changeSummary   = "change_summary"
        case featureFlags    = "feature_flags"
        case envSnapshot     = "env_snapshot"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backupType      = try c.decodeIfPresent(String.self, forKey: .backupType) ?? ""
        backupCreatedAt = try c.decodeIfPresent(String.self, forKey: .backupCreatedAt) ?? ""
        backupStatus    = try c.decodeIfPresent(String.self, forKey: .backupStatus) ?? ""
        checksumSha256  = try c.decodeIfPresent(String.self, forKey: .checksumSha256) ?? ""
        tables          = try c.decodeIfPresent([String: BackupTableDelta].self, forKey: .tables) ?? [:]
        tablesWithDelta = try c.decodeIfPresent([String].self, forKey: .tablesWithDelta) ?? []
        wouldChange     = try c.decodeIfPresent(FlexibleBool.self, forKey: .wouldChange)?.value ?? false
        changeSummary   = try c.decodeIfPresent(String.self, forKey: .changeSummary) ?? ""
        featureFlags    = try c.decodeIfPresent([String: FlexibleJSONValue].self, forKey: .featureFlags)
        envSnapshot     = try c.decodeIfPresent([String: FlexibleJSONValue].self, forKey: .envSnapshot)
    }
}

struct BackupRestorePreviewResponse: Codable {
    let ok: Bool
    let backupId: String
    let warning: String?
    let preview: BackupRestorePreview?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, warning, preview, error
        case backupId = "backup_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok       = try c.decodeIfPresent(FlexibleBool.self, forKey: .ok)?.value ?? false
        backupId = try c.decodeIfPresent(String.self, forKey: .backupId) ?? ""
        warning  = try c.decodeIfPresent(String.self, forKey: .warning)
        preview  = try c.decodeIfPresent(BackupRestorePreview.self, forKey: .preview)
        error    = try c.decodeIfPresent(String.self, forKey: .error)
    }
}
