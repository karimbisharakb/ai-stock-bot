import SwiftUI

// MARK: - Phase I30 Backup confirm actions

enum BackupConfirmAction: Identifiable {
    case createBackup
    case verifyBackup(id: String)
    case restorePreview(id: String)

    var id: String {
        switch self {
        case .createBackup:              return "backup-create"
        case .verifyBackup(let id):      return "backup-verify-\(id)"
        case .restorePreview(let id):    return "backup-preview-\(id)"
        }
    }
}

// MARK: - Backup list page

struct BackupListPage: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                BackupSafetyBanner()
                OperatorCard(title: "Backups", icon: "externaldrive") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Count", value: "\(vm.backupList?.backups.count ?? 0)", color: .textPrimary)
                        AlphaMetric(title: "Selected", value: vm.selectedBackup.map { shortId($0.backupId) } ?? "None", color: vm.selectedBackup == nil ? .textSecondary : .accent)
                    }
                    ActionButton(title: "Refresh list", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshBackupList() }
                    }
                }
                if let list = vm.backupList, !list.backups.isEmpty {
                    ForEach(list.backups) { entry in
                        BackupEntryCard(entry: entry, isSelected: vm.selectedBackup?.backupId == entry.backupId) {
                            vm.selectedBackup = entry
                            vm.backupVerifyResult = nil
                            vm.backupRestorePreview = nil
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "externaldrive", title: "No backups yet")
                }
            }
            .padding(16)
        }
        .task {
            if vm.backupList == nil { await vm.refreshBackupList() }
        }
        .refreshable { await vm.refreshBackupList() }
    }
}

// MARK: - Backup detail page

struct BackupDetailPage: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                BackupSafetyBanner()
                if let entry = vm.selectedBackup {
                    OperatorCard(title: "Backup Detail", icon: "doc.text.magnifyingglass") {
                        StatusRow(label: "Backup ID", value: entry.backupId, color: .textPrimary)
                        StatusRow(label: "Status", value: entry.status, color: backupStatusColor(entry.status))
                        StatusRow(label: "Type", value: entry.backupType, color: .textPrimary)
                        StatusRow(label: "Created", value: shortDateTime(entry.createdAt), color: .textSecondary)
                        StatusRow(label: "Tables", value: "\(entry.tableCount)", color: .textPrimary)
                        StatusRow(label: "Rows", value: "\(entry.rowCount)", color: .textPrimary)
                        StatusRow(label: "Size", value: entry.formattedSize, color: .textPrimary)
                    }
                    OperatorCard(title: "Integrity", icon: "checkmark.shield") {
                        StatusRow(label: "Checksum (SHA256)", value: entry.shortChecksum, color: .textSecondary)
                        StatusRow(label: "File", value: entry.fileBasename.isEmpty ? "—" : entry.fileBasename, color: .textSecondary)
                    }
                    if !entry.notes.isEmpty {
                        OperatorCard(title: "Notes", icon: "note.text") {
                            Text(entry.notes)
                                .operatorBody(color: .textSecondary)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "doc.text.magnifyingglass", title: "No backup selected\nTap a backup in Backups")
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Create backup page

struct BackupCreatePage: View {
    @ObservedObject var vm: OperatorViewModel
    @Binding var pendingAction: BackupConfirmAction?

    private let backupTypes = ["FULL", "PORTFOLIO_ONLY", "ALPHA_ONLY",
                               "RESEARCH_ONLY", "NOTIFICATIONS_ONLY", "SYSTEM_CONFIG_ONLY"]

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                BackupSafetyBanner()

                if !vm.apiSecretConfigured {
                    StatusBanner(text: "API_SECRET not set. Configure it in Settings → API Secret before creating backups.",
                                 color: .warning, icon: "lock.trianglebadge.exclamationmark")
                }

                OperatorCard(title: "Create Backup", icon: "externaldrive.badge.plus") {
                    Text("Backup type")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    Picker("Type", selection: $vm.selectedBackupTypeForCreate) {
                        ForEach(backupTypes, id: \.self) { t in
                            Text(t.replacingOccurrences(of: "_", with: " ")).tag(t)
                        }
                    }
                    .pickerStyle(.menu)
                    .tint(.accent)
                    .frame(maxWidth: .infinity, alignment: .leading)

                    Text("Notes (optional)")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 4)

                    TextField("e.g. pre-deploy snapshot", text: $vm.backupCreateNotes)
                        .font(.system(size: 13))
                        .foregroundColor(.textPrimary)
                        .padding(10)
                        .background(Color.surfaceElevated)
                        .clipShape(RoundedRectangle(cornerRadius: 8))

                    ActionButton(title: "Create backup", icon: "externaldrive.badge.plus", color: .positive) {
                        if vm.apiSecretConfigured {
                            pendingAction = .createBackup
                        }
                    }
                }

                if vm.backupActionInProgress {
                    OperatorCard(title: "Working…", icon: "clock") {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    }
                }

                if let msg = vm.backupActionMessage {
                    StatusBanner(text: msg,
                                 color: vm.backupActionSuccess ? .positive : .warning,
                                 icon: vm.backupActionSuccess ? "checkmark.circle" : "exclamationmark.triangle")
                }

                if let entry = vm.selectedBackup, vm.backupActionSuccess {
                    BackupEntryCard(entry: entry, isSelected: true) {}
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Verify backup page

struct BackupVerifyPage: View {
    @ObservedObject var vm: OperatorViewModel
    @Binding var pendingAction: BackupConfirmAction?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                BackupSafetyBanner()

                if !vm.apiSecretConfigured {
                    StatusBanner(text: "API_SECRET not set. Configure it in Settings.",
                                 color: .warning, icon: "lock.trianglebadge.exclamationmark")
                }

                OperatorCard(title: "Verify Backup", icon: "checkmark.shield") {
                    if let entry = vm.selectedBackup {
                        StatusRow(label: "Target", value: shortId(entry.backupId), color: .textPrimary)
                        StatusRow(label: "Type", value: entry.backupType, color: .textSecondary)
                        StatusRow(label: "Current status", value: entry.status, color: backupStatusColor(entry.status))
                    } else {
                        Text("No backup selected — tap a backup in the Backups tab first.")
                            .operatorBody(color: .warning)
                    }
                    if vm.selectedBackup != nil {
                        ActionButton(title: "Verify backup", icon: "checkmark.shield", color: .positive) {
                            if vm.apiSecretConfigured, let id = vm.selectedBackup?.backupId {
                                pendingAction = .verifyBackup(id: id)
                            }
                        }
                    }
                }

                if vm.backupActionInProgress {
                    OperatorCard(title: "Verifying…", icon: "clock") {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }

                if let msg = vm.backupActionMessage {
                    StatusBanner(text: msg,
                                 color: vm.backupActionSuccess ? .positive : .warning,
                                 icon: vm.backupActionSuccess ? "checkmark.circle" : "exclamationmark.triangle")
                }

                if let result = vm.backupVerifyResult {
                    OperatorCard(title: "Verification Result", icon: "list.bullet.clipboard") {
                        StatusRow(label: "Overall", value: result.ok ? "PASSED" : "FAILED",
                                  color: result.ok ? .positive : .negative)
                        ForEach(result.checks) { check in
                            BackupCheckRow(check: check)
                        }
                    }
                    if let entry = result.manifestEntry {
                        OperatorCard(title: "Updated Manifest", icon: "doc.text") {
                            StatusRow(label: "Status", value: entry.status, color: backupStatusColor(entry.status))
                            StatusRow(label: "Checksum", value: entry.shortChecksum, color: .textSecondary)
                        }
                    }
                } else if vm.selectedBackup != nil {
                    OperatorEmptyState(icon: "checkmark.shield", title: "Tap Verify to run checks")
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Restore preview page

struct BackupRestorePreviewPage: View {
    @ObservedObject var vm: OperatorViewModel
    @Binding var pendingAction: BackupConfirmAction?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                // Prominent read-only warning — top of page
                OperatorCard(title: "Preview only — no restore performed", icon: "exclamationmark.triangle.fill") {
                    Text("THIS IS A READ-ONLY PREVIEW.\nNo data is written. No trades are placed. No notifications are sent. No restore is executed.")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.warning)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }

                BackupSafetyBanner()

                if !vm.apiSecretConfigured {
                    StatusBanner(text: "API_SECRET not set. Configure it in Settings.",
                                 color: .warning, icon: "lock.trianglebadge.exclamationmark")
                }

                OperatorCard(title: "Restore Preview", icon: "arrow.counterclockwise.circle") {
                    if let entry = vm.selectedBackup {
                        StatusRow(label: "Target", value: shortId(entry.backupId), color: .textPrimary)
                        StatusRow(label: "Type", value: entry.backupType, color: .textSecondary)
                        StatusRow(label: "Created", value: shortDateTime(entry.createdAt), color: .textSecondary)
                    } else {
                        Text("No backup selected — tap a backup in the Backups tab first.")
                            .operatorBody(color: .warning)
                    }
                    if vm.selectedBackup != nil {
                        ActionButton(title: "Load preview", icon: "arrow.counterclockwise.circle", color: .accent) {
                            if vm.apiSecretConfigured, let id = vm.selectedBackup?.backupId {
                                pendingAction = .restorePreview(id: id)
                            }
                        }
                    }
                }

                if vm.backupActionInProgress {
                    OperatorCard(title: "Loading preview…", icon: "clock") {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }

                if let msg = vm.backupActionMessage {
                    StatusBanner(text: msg,
                                 color: vm.backupActionSuccess ? .positive : .warning,
                                 icon: vm.backupActionSuccess ? "checkmark.circle" : "exclamationmark.triangle")
                }

                if let result = vm.backupRestorePreview {
                    if let warning = result.warning {
                        StatusBanner(text: warning, color: .warning, icon: "exclamationmark.triangle.fill")
                    }

                    if let preview = result.preview {
                        OperatorCard(title: "Change Summary", icon: "chart.bar.doc.horizontal") {
                            StatusRow(label: "Would change", value: preview.wouldChange ? "YES" : "NO",
                                      color: preview.wouldChange ? .warning : .positive)
                            StatusRow(label: "Tables with delta", value: "\(preview.tablesWithDelta.count)", color: .textPrimary)
                            StatusRow(label: "Summary", value: preview.changeSummary, color: .textSecondary)
                            StatusRow(label: "Backup type", value: preview.backupType, color: .textSecondary)
                            StatusRow(label: "Backup status", value: preview.backupStatus, color: backupStatusColor(preview.backupStatus))
                        }

                        if !preview.tables.isEmpty {
                            OperatorCard(title: "Table Deltas (\(preview.tables.count) tables)", icon: "tablecells") {
                                ForEach(preview.tables.keys.sorted(), id: \.self) { table in
                                    if let delta = preview.tables[table] {
                                        BackupTableDeltaRow(table: table, delta: delta)
                                    }
                                }
                            }
                        }
                    }
                } else if vm.selectedBackup != nil {
                    OperatorEmptyState(icon: "arrow.counterclockwise.circle", title: "Tap Load preview to compare")
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Download info page

struct BackupDownloadInfoPage: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                BackupSafetyBanner()
                OperatorCard(title: "Download Info", icon: "arrow.down.doc") {
                    Text("File metadata only. The backup file lives on the Railway persistent volume. No file transfer is performed and no secrets are shown.")
                        .operatorBody(color: .textSecondary)
                }
                if let entry = vm.selectedBackup {
                    OperatorCard(title: "File Metadata", icon: "doc.badge.gearshape") {
                        StatusRow(label: "Backup ID", value: entry.backupId, color: .textPrimary)
                        StatusRow(label: "Type", value: entry.backupType, color: .textPrimary)
                        StatusRow(label: "Status", value: entry.status, color: backupStatusColor(entry.status))
                        StatusRow(label: "Created", value: shortDateTime(entry.createdAt), color: .textSecondary)
                        StatusRow(label: "Size", value: entry.formattedSize, color: .textPrimary)
                        StatusRow(label: "Tables", value: "\(entry.tableCount)", color: .textPrimary)
                        StatusRow(label: "Rows", value: "\(entry.rowCount)", color: .textPrimary)
                    }
                    OperatorCard(title: "Integrity", icon: "checkmark.shield") {
                        StatusRow(label: "Checksum (prefix)", value: entry.shortChecksum, color: .textSecondary)
                        StatusRow(label: "File (basename)", value: entry.fileBasename.isEmpty ? "—" : entry.fileBasename, color: .textSecondary)
                    }
                    if !entry.notes.isEmpty {
                        OperatorCard(title: "Notes", icon: "note.text") {
                            Text(entry.notes).operatorBody(color: .textSecondary)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "arrow.down.doc", title: "No backup selected\nTap a backup in Backups")
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Shared sub-components

private struct BackupSafetyBanner: View {
    var body: some View {
        OperatorCard(title: "Backup system — safe diagnostics", icon: "lock.shield") {
            Text("Backup only. Restore preview only. No restore executed. No trades placed. No notifications sent. No secrets displayed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

private struct BackupEntryCard: View {
    let entry: BackupManifestEntry
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(shortId(entry.backupId))
                        .font(.system(size: 13, weight: .bold, design: .monospaced))
                        .foregroundColor(.textPrimary)
                    Spacer()
                    BackupStatusBadge(status: entry.status)
                    if isSelected {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.accent)
                            .font(.system(size: 14))
                    }
                }
                HStack(spacing: 12) {
                    Label(entry.backupType.replacingOccurrences(of: "_", with: " "),
                          systemImage: "externaldrive")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.textSecondary)
                    Label(entry.formattedSize, systemImage: "doc")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.textSecondary)
                    Label("\(entry.rowCount) rows", systemImage: "tablecells")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.textSecondary)
                }
                Text(shortDateTime(entry.createdAt))
                    .font(.system(size: 11))
                    .foregroundColor(.textSecondary)
                if !entry.notes.isEmpty {
                    Text(entry.notes)
                        .font(.system(size: 11))
                        .foregroundColor(.textSecondary)
                        .lineLimit(1)
                }
            }
            .padding(12)
            .background(isSelected ? Color.accent.opacity(0.12) : Color.surface)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .stroke(isSelected ? Color.accent.opacity(0.5) : Color.border, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

private struct BackupStatusBadge: View {
    let status: String

    var body: some View {
        Text(status)
            .font(.system(size: 10, weight: .bold))
            .foregroundColor(.black)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(badgeColor)
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }

    private var badgeColor: Color {
        backupStatusColor(status)
    }
}

private struct BackupCheckRow: View {
    let check: BackupVerifyCheck

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundColor(check.passed ? .positive : .negative)
                .font(.system(size: 13))
            VStack(alignment: .leading, spacing: 2) {
                Text(check.name.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.textPrimary)
                if !check.detail.isEmpty {
                    Text(check.detail)
                        .font(.system(size: 11))
                        .foregroundColor(.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }
}

private struct BackupTableDeltaRow: View {
    let table: String
    let delta: BackupTableDelta

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(table)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundColor(.textPrimary)
                Text("backup: \(delta.backupRows)  live: \(delta.liveRows)")
                    .font(.system(size: 10))
                    .foregroundColor(.textSecondary)
            }
            Spacer()
            if delta.delta != 0 {
                Text(delta.delta > 0 ? "+\(delta.delta)" : "\(delta.delta)")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(delta.delta > 0 ? .warning : .negative)
            } else {
                Image(systemName: "checkmark")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.positive)
            }
        }
        .padding(.vertical, 3)
    }
}

// MARK: - Helpers

func backupStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "VERIFIED":  return .positive
    case "CREATED":   return .accent
    case "FAILED":    return .negative
    case "ARCHIVED":  return .textSecondary
    default:          return .textSecondary
    }
}

private func shortId(_ id: String) -> String {
    id.count <= 20 ? id : String(id.prefix(20)) + "…"
}

private func shortDateTime(_ iso: String) -> String {
    guard !iso.isEmpty else { return "—" }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    var date = formatter.date(from: iso)
    if date == nil {
        formatter.formatOptions = .withInternetDateTime
        date = formatter.date(from: iso)
    }
    guard let d = date else { return String(iso.prefix(19)).replacingOccurrences(of: "T", with: " ") }
    let out = DateFormatter()
    out.dateStyle = .short
    out.timeStyle = .short
    return out.string(from: d)
}
