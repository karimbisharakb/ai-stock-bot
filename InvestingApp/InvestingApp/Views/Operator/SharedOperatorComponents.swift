import SwiftUI
import Charts

// Shared Operator components, labels, and formatting helpers split from OperatorView.swift.
struct OperatorCard<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.accent)
                Text(title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
            }
            content
        }
        .padding(14)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}

struct SectionPill: View {
    let title: String
    let index: Int
    @Binding var selected: Int

    var body: some View {
        Button {
            selected = index
            HapticManager.selection()
        } label: {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(selected == index ? .black : .textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(selected == index ? Color.accent : Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(selected == index ? Color.clear : Color.border, lineWidth: 0.5)
                )
        }
        .buttonStyle(.plain)
    }
}

struct StatusRow: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textSecondary)
            Spacer(minLength: 12)
            Text(value)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(color)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
        }
    }
}

struct IssueRow: View {
    let issue: AlphaIssue

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(issue.severity.uppercased())
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.black)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(severityColor)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                Text(issue.code.replacingOccurrences(of: "_", with: " "))
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(.textPrimary)
                Spacer()
            }
            Text(issue.message)
                .operatorBody(color: .textSecondary)
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var severityColor: Color {
        switch issue.severity.uppercased() {
        case "HIGH": return .negative
        case "MEDIUM": return .warning
        default: return .textSecondary
        }
    }
}

struct DataQualityRows: View {
    let dataQuality: AlphaDataQuality

    var body: some View {
        VStack(spacing: 8) {
            StatusRow(label: "Rows scored", value: "\(dataQuality.totalScored)", color: .textPrimary)
            StatusRow(label: "Stale tickers", value: "\(dataQuality.staleCount)", color: dataQuality.staleCount == 0 ? .positive : .warning)
            StatusRow(label: "Missing catalyst", value: rate(dataQuality.missingCatalystRate), color: rateColor(dataQuality.missingCatalystRate))
            StatusRow(label: "Missing options", value: rate(dataQuality.missingOptionsRate), color: rateColor(dataQuality.missingOptionsRate))
            StatusRow(label: "Missing risk/reward", value: rate(dataQuality.missingRiskRewardRate), color: rateColor(dataQuality.missingRiskRewardRate))
            if !dataQuality.staleTickers.isEmpty {
                Text("Stale: \(dataQuality.staleTickers.prefix(8).joined(separator: ", "))")
                    .operatorBody(color: .textSecondary)
            }
        }
    }

    func rate(_ value: Double?) -> String {
        guard let value else { return "Unknown" }
        return String(format: "%.0f%%", value * 100)
    }

    func rateColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        if value >= 0.50 { return .warning }
        return .positive
    }
}

struct BulletLine: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(Color.accent)
                .frame(width: 5, height: 5)
                .padding(.top, 6)
            Text(text)
                .operatorBody(color: .textPrimary)
            Spacer(minLength: 0)
        }
    }
}

struct AlphaOutcomeCard: View {
    let outcome: AlphaOutcome

    var body: some View {
        OperatorCard(title: outcome.ticker, icon: "list.bullet.rectangle") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Tier", value: outcome.alphaTier ?? "-", color: .accent)
                AlphaMetric(title: "Setup", value: outcome.setupType ?? "-", color: .textPrimary)
                AlphaMetric(title: "Status", value: outcome.status, color: statusColor)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "1d", value: percent(outcome.return1d), color: returnColor(outcome.return1d))
                AlphaMetric(title: "5d", value: percent(outcome.return5d), color: returnColor(outcome.return5d))
                AlphaMetric(title: "20d", value: percent(outcome.return20d), color: returnColor(outcome.return20d))
            }

            StatusRow(label: "Scanned", value: outcome.scanTime.isEmpty ? "Unknown" : outcome.scanTime, color: .textSecondary)
        }
    }

    private var statusColor: Color {
        switch outcome.status.uppercased() {
        case "COMPLETE": return .positive
        case "PENDING": return .warning
        case "STALE": return .textSecondary
        default: return .textPrimary
        }
    }

    func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%+.1f%%", value * 100)
    }

    func returnColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        return value >= 0 ? .positive : .negative
    }
}

struct EffectivenessCard: View {
    let title: String
    let icon: String
    let rows: [(String, AlphaEffectiveness)]
    let emptyText: String
    var showFalsePositive = false

    var body: some View {
        OperatorCard(title: title, icon: icon) {
            if rows.isEmpty {
                Text(emptyText)
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows, id: \.0) { name, stats in
                    VStack(spacing: 6) {
                        HStack {
                            Text(name.replacingOccurrences(of: "_", with: " "))
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.textPrimary)
                                .lineLimit(1)
                            Spacer()
                            Text("\(stats.count)")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundColor(.accent)
                        }
                        HStack(spacing: 8) {
                            LearningMetric(title: "5d", value: percent(stats.avgReturn5d), color: returnColor(stats.avgReturn5d))
                            LearningMetric(title: "Win", value: percent(stats.winRate), color: .positive)
                            if showFalsePositive {
                                LearningMetric(title: "FP", value: percent(stats.falsePositiveRate), color: .warning)
                            }
                        }
                    }
                    .padding(10)
                    .background(Color.surfaceElevated)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.1f%%", value * 100)
    }

    func returnColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        return value >= 0 ? .positive : .negative
    }
}

struct LearningMetric: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        HStack(spacing: 4) {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(.textSecondary)
            Text(value)
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(color)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct AlphaCandidateCard: View {
    let candidate: AlphaCandidate

    var body: some View {
        OperatorCard(title: candidate.ticker, icon: "target") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Score", value: candidate.alphaScore.map { String(format: "%.1f", $0) } ?? "-", color: .accent)
                AlphaMetric(title: "Tier", value: candidate.alphaTier ?? "-", color: tierColor(candidate.alphaTier))
                AlphaMetric(title: "Setup", value: candidate.setupType ?? "-", color: .textPrimary)
            }

            if !candidate.explanation.isEmpty {
                SummaryLine(title: "Why", text: candidate.explanation)
            }
            SummaryLine(title: "Watch", text: watchText)
            SummaryLine(title: "Plan", text: planText)

            if !candidate.topComponents.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Top components")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(candidate.topComponents, id: \.name) { component in
                        HStack {
                            Text(component.name.replacingOccurrences(of: "_", with: " "))
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textSecondary)
                            Spacer()
                            Text(String(format: "%.1f", component.value))
                                .font(.system(size: 12, weight: .bold))
                                .foregroundColor(.textPrimary)
                        }
                    }
                }
            }
        }
    }

    private var watchText: String {
        if let scanTime = candidate.scanTime {
            return "Latest scan: \(scanTime)"
        }
        return "Wait for next scan before acting."
    }

    private var planText: String {
        let tier = candidate.alphaTier ?? "unranked"
        return "Review manually. Tier: \(tier). No automatic action."
    }

    func tierColor(_ tier: String?) -> Color {
        switch tier?.uppercased() {
        case "A", "HIGH", "STRONG": return .positive
        case "B", "MEDIUM": return .warning
        case "C", "LOW": return .textSecondary
        default: return .textPrimary
        }
    }
}

struct AlphaMetric: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            Text(value)
                .font(.system(size: 14, weight: .bold))
                .foregroundColor(color)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(9)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SummaryLine: View {
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            Text(text)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

extension Text {
    func operatorBody(color: Color) -> some View {
        self
            .font(.system(size: 13, weight: .medium))
            .foregroundColor(color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct SimulationLabelCard: View {
    let note: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "testtube.2")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("Simulation only")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("Live weights unchanged. \(note)")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct LearningSignalLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "book.closed")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Learning signal only")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Use this to judge alpha quality. No trades or live weights change from this screen.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct ReadinessOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "bell.and.waves.left.and.right")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("No alert sent yet")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("Readiness only. WhatsApp alerts and live weights are unchanged.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct NoWhatsAppLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "paperplane.circle")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("Simulation only")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("No WhatsApp sent. These screens only review dry-run messages and QC results.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct DeliveryWarningLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.negative)
            VStack(alignment: .leading, spacing: 3) {
                Text("Manual delivery control")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.negative)
                Text("This may send a real WhatsApp alert if backend flags are enabled.")
                    .operatorBody(color: .textPrimary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.negative.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.negative.opacity(0.35), lineWidth: 0.5))
    }
}

struct DeliverySafetyCard: View {
    let flags: AlphaNotificationDeliveryFlags?

    var body: some View {
        OperatorCard(title: "Delivery Safety", icon: "lock.shield") {
            if let flags {
                StatusRow(label: "Delivery env enabled", value: flags.enabled ? "Enabled" : "Disabled", color: flags.enabled ? .warning : .positive)
                StatusRow(label: "Dry-run-only", value: flags.dryRunOnly ? "Blocks real sends" : "Disabled", color: flags.dryRunOnly ? .positive : .warning)
                StatusRow(label: "Reviewed required", value: flags.requireReviewed ? "Required" : "Not required", color: flags.requireReviewed ? .positive : .warning)
                StatusRow(label: "Minimum QC tier", value: AlphaNotificationQC.label(for: flags.minQCTier), color: qcColor(flags.minQCTier))
            } else {
                Text("Backend controls delivery. App cannot bypass safety flags.")
                    .operatorBody(color: .warning)
            }

            BulletLine(text: "Delivery is disabled unless Railway env vars are enabled.")
            BulletLine(text: "Dry-run-only blocks real sends unless disabled.")
            BulletLine(text: "Reviewed status may be required.")
            BulletLine(text: "QC must pass before delivery can proceed.")
        }
    }
}

struct CanonicalPortfolioLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.seal")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Canonical portfolio state")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Read-only portfolio truth. No broker connection, order placement, or autonomous trading.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct ManualTruthLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "pencil.and.list.clipboard")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Manual truth source")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Manual portfolio corrections only. No trades placed, no broker connection, no delete calls.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct ManualPositionCard: View {
    let position: ManualPortfolioPosition
    let actionInProgress: Bool
    let onEdit: () -> Void
    let onDeactivate: () -> Void

    var body: some View {
        OperatorCard(title: position.ticker, icon: "briefcase") {
            HStack(spacing: 6) {
                Badge(text: position.active ? "ACTIVE" : "INACTIVE", color: position.active ? .positive : .textSecondary)
                Badge(text: position.accountType, color: .accent)
                Badge(text: position.currency, color: .textSecondary)
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Qty", value: quantity(position.quantity), color: .textPrimary)
                AlphaMetric(title: "Avg cost", value: money(position.avgCost), color: .textSecondary)
                AlphaMetric(title: "Realized", value: money(position.realizedPnL), color: pnlColor(position.realizedPnL))
            }

            if !position.note.isEmpty {
                SummaryLine(title: "Note", text: position.note)
            }
            StatusRow(label: "Updated", value: position.updatedAt ?? "Unknown", color: .textSecondary)

            HStack(spacing: 10) {
                ActionButton(title: "Edit", icon: "square.and.pencil", color: .accent) {
                    onEdit()
                }
                ActionButton(title: "Deactivate", icon: "minus.circle", color: .negative) {
                    onDeactivate()
                }
                .disabled(actionInProgress)
            }
        }
    }
}

struct ManualTextField: View {
    let title: String
    @Binding var text: String
    let placeholder: String
    var keyboard: UIKeyboardType = .default

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            TextField(placeholder, text: $text)
                .keyboardType(keyboard)
                .textInputAutocapitalization(.characters)
                .disableAutocorrection(true)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.textPrimary)
                .padding(10)
                .background(Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
        }
    }
}

struct ManualChoiceRow: View {
    let title: String
    let choices: [String]
    @Binding var selection: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            HStack(spacing: 6) {
                ForEach(choices, id: \.self) { choice in
                    Button {
                        selection = choice
                        HapticManager.selection()
                    } label: {
                        Text(choice)
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(selection == choice ? .black : .textPrimary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                            .background(selection == choice ? Color.accent : Color.surfaceElevated)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(selection == choice ? Color.clear : Color.border, lineWidth: 0.5))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

struct JournalOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "book.closed")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Journal only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Position thesis and review records only. No broker connection, order placement, or autonomous trading.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct ManualDisciplineLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "checklist")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Manual discipline only")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Decision checklists help review entries and exits. Approval does NOT place trades.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct GuidanceOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "shield")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("Guidance only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("Risk guardrails and sizing checks are advisory only. No broker connection or order placement.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct ContextOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "globe.americas")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Context only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Market regime is situational context only. No broker connection, order placement, or autonomous action.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct ReplaySafetyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("Simulation only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("Historical replay visualizes simulated decisions only. It never sends notifications or places orders.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct StressSafetyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.shield")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text("Stress test only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.warning)
                Text("Scenario losses are advisory simulations only. No broker connection, order placement, or autonomous action.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.warning.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.35), lineWidth: 0.5))
    }
}

struct AnalyticsOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Analytics only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Strategy scorecards summarize personal behavior. They do not place orders or change live systems.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct PlanningOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "calendar.badge.clock")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Planning only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Long-horizon projections are planning analytics only. Not tax or legal advice.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct BriefingOnlyLabelCard: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "doc.text")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Briefing only — no trades placed")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.accent)
                Text("Daily brief summarizes operating context only. No broker connection, order placement, or autonomous action.")
                    .operatorBody(color: .textSecondary)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.accent.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accent.opacity(0.35), lineWidth: 0.5))
    }
}

struct MarketRegimeDashboardCard: View {
    let regime: MarketRegimeSnapshot

    var body: some View {
        OperatorCard(title: MarketRegimeLabels.label(regime.overallRegime), icon: "chart.line.uptrend.xyaxis") {
            HStack(spacing: 6) {
                Badge(text: MarketRegimeLabels.label(regime.overallRegime), color: regimeColor(regime.overallRegime))
                Badge(text: MarketRegimeLabels.label(regime.volatilityRegime), color: volatilityColor(regime.volatilityRegime))
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Score", value: score(regime.regimeScore), color: regimeScoreColor(regime.regimeScore))
                AlphaMetric(title: "Risk x", value: multiplier(regime.riskMultiplier), color: multiplierColor(regime.riskMultiplier))
                AlphaMetric(title: "Sizing x", value: multiplier(regime.sizingMultiplier), color: multiplierColor(regime.sizingMultiplier))
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Breadth", value: MarketRegimeLabels.label(regime.breadthRegime), color: breadthColor(regime.breadthRegime))
                AlphaMetric(title: "Spec", value: MarketRegimeLabels.label(regime.speculativeRegime), color: speculativeColor(regime.speculativeRegime))
                AlphaMetric(title: "Quality", value: regime.dataQuality, color: dataQualityColor(regime.dataQuality))
            }
            SummaryLine(title: "Explanation", text: regime.explanation.isEmpty ? "No explanation available" : regime.explanation)
            StatusRow(label: "Timestamp", value: regime.capturedAt ?? "Unknown", color: .textSecondary)
        }
    }
}

struct MarketRegimeAdjustmentCard: View {
    let regime: MarketRegimeSnapshot

    var body: some View {
        OperatorCard(title: "Adjustments", icon: "slider.horizontal.3") {
            StatusRow(label: "Alpha threshold", value: signedNumber(regime.alphaThresholdAdjustment), color: deltaColor(regime.alphaThresholdAdjustment))
            StatusRow(label: "Confidence", value: signedNumber(regime.confidenceAdjustment), color: deltaColor(regime.confidenceAdjustment))
            StatusRow(label: "Risk multiplier", value: multiplier(regime.riskMultiplier), color: multiplierColor(regime.riskMultiplier))
            StatusRow(label: "Sizing multiplier", value: multiplier(regime.sizingMultiplier), color: multiplierColor(regime.sizingMultiplier))
            if let points = regime.points {
                StatusRow(label: "Raw points", value: score(points), color: .textSecondary)
            }
        }
    }
}

struct MarketRegimeWarningsCard: View {
    let regime: MarketRegimeSnapshot

    var body: some View {
        OperatorCard(title: "Warnings", icon: "exclamationmark.triangle") {
            if regime.warnings.isEmpty {
                Text("No regime warnings")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(regime.warnings.prefix(8), id: \.self) { warning in
                    BulletLine(text: warning)
                }
            }
            StatusRow(label: "Data quality", value: regime.dataQuality, color: dataQualityColor(regime.dataQuality))
        }
    }
}

struct MarketRegimeHistoryCard: View {
    let regime: MarketRegimeSnapshot

    var body: some View {
        OperatorCard(title: shortDateString(regime.capturedAt), icon: "clock") {
            HStack(spacing: 6) {
                Badge(text: MarketRegimeLabels.label(regime.overallRegime), color: regimeColor(regime.overallRegime))
                Badge(text: MarketRegimeLabels.label(regime.volatilityRegime), color: volatilityColor(regime.volatilityRegime))
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Score", value: score(regime.regimeScore), color: regimeScoreColor(regime.regimeScore))
                AlphaMetric(title: "Breadth", value: MarketRegimeLabels.label(regime.breadthRegime), color: breadthColor(regime.breadthRegime))
                AlphaMetric(title: "Spec", value: MarketRegimeLabels.label(regime.speculativeRegime), color: speculativeColor(regime.speculativeRegime))
            }
            StatusRow(label: "Timestamp", value: regime.capturedAt ?? "Unknown", color: .textSecondary)
        }
    }
}

struct ReplayRunCard: View {
    let run: ReplayRun
    let isSelected: Bool
    let onOpen: () -> Void

    var body: some View {
        OperatorCard(title: run.runId, icon: isSelected ? "clock.badge.checkmark" : "clock.arrow.circlepath") {
            HStack(spacing: 6) {
                Badge(text: run.status, color: replayStatusColor(run.status))
                if !run.tickerFilter.isEmpty {
                    Badge(text: run.tickerFilter.joined(separator: ","), color: .accent)
                }
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Events", value: "\(run.eventCount)", color: .textPrimary)
                AlphaMetric(title: "Alerts", value: "\(run.summary.simulatedAlertCount)", color: .warning)
                AlphaMetric(title: "Missed", value: "\(run.summary.missedWinners)", color: .negative)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Avoided", value: "\(run.summary.avoidedLosers)", color: .positive)
                AlphaMetric(title: "False +", value: "\(run.summary.falsePositives)", color: .warning)
                AlphaMetric(title: "Rows", value: "\(run.maxRows)", color: .textSecondary)
            }
            StatusRow(label: "Period", value: "\(shortDateString(run.startDate)) to \(shortDateString(run.endDate))", color: .textSecondary)
            StatusRow(label: "Created", value: run.createdAt ?? "Unknown", color: .textSecondary)
            if let source = run.sourceFilter {
                StatusRow(label: "Source", value: source.replacingOccurrences(of: "_", with: " ").capitalized, color: .accent)
            }
            if let setup = run.setupTypeFilter {
                StatusRow(label: "Setup", value: setup, color: .accent)
            }
            ActionButton(title: isSelected ? "Reload detail" : "Open detail", icon: "chevron.right.circle", color: .accent) {
                onOpen()
            }
        }
    }
}

struct ReplayRunDetailCard: View {
    let run: ReplayRun

    var body: some View {
        OperatorCard(title: "Replay Detail", icon: "chart.bar.doc.horizontal") {
            if let error = run.summary.error {
                Text(error)
                    .operatorBody(color: .warning)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Events", value: "\(run.summary.eventCount == 0 ? run.eventCount : run.summary.eventCount)", color: .textPrimary)
                AlphaMetric(title: "Would alert", value: "\(run.summary.simulatedAlertCount)", color: .warning)
                AlphaMetric(title: "Prepare", value: "\(run.summary.simulatedPrepareCount)", color: .accent)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Monitor", value: "\(run.summary.simulatedMonitorCount)", color: .textSecondary)
                AlphaMetric(title: "Blocked", value: "\(run.summary.simulatedBlockCount)", color: .warning)
                AlphaMetric(title: "Reject", value: "\(run.summary.simulatedRejectCount)", color: .negative)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Missed", value: "\(run.summary.missedWinners)", color: .negative)
                AlphaMetric(title: "Avoided", value: "\(run.summary.avoidedLosers)", color: .positive)
                AlphaMetric(title: "False +", value: "\(run.summary.falsePositives)", color: .warning)
            }
            StatusRow(label: "Completed", value: run.completedAt ?? "Not complete", color: run.completedAt == nil ? .warning : .textSecondary)
        }
    }
}

struct ReplayBreakdownCard: View {
    let title: String
    let icon: String
    let rows: [String: Int]
    let label: (String) -> String

    var body: some View {
        OperatorCard(title: title, icon: icon) {
            if rows.isEmpty {
                Text("No breakdown data")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows.sorted(by: { $0.value == $1.value ? $0.key < $1.key : $0.value > $1.value }), id: \.key) { key, value in
                    StatusRow(label: label(key), value: "\(value)", color: replayBreakdownColor(key))
                }
            }
        }
    }
}

struct ReplayOpportunityListCard: View {
    let title: String
    let icon: String
    let rows: [ReplayOpportunity]
    let emptyText: String

    var body: some View {
        OperatorCard(title: title, icon: icon) {
            if rows.isEmpty {
                Text(emptyText)
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows) { row in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(row.ticker)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundColor(.textPrimary)
                            Text(shortDateString(row.scanTime))
                                .font(.system(size: 11, weight: .medium))
                                .foregroundColor(.textSecondary)
                        }
                        Spacer()
                        Badge(text: row.alphaTier ?? "UNKNOWN", color: .accent)
                        Text(percentFromWhole(row.return5d))
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(deltaColor(row.return5d))
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }
}

struct ReplayEventCard: View {
    let event: ReplayEvent

    var body: some View {
        OperatorCard(title: event.ticker, icon: replayEventIcon(event)) {
            HStack(spacing: 6) {
                Badge(text: ReplayLabels.decision(event.simulatedDecision), color: replayDecisionColor(event.simulatedDecision))
                Badge(text: ReplayLabels.outcome(event.outcomeClassification), color: replayOutcomeColor(event.outcomeClassification))
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Alpha", value: score(event.alphaScore), color: .accent)
                AlphaMetric(title: "5d", value: percentFromWhole(event.return5d), color: deltaColor(event.return5d))
                AlphaMetric(title: "10d", value: percentFromWhole(event.return10d), color: deltaColor(event.return10d))
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Max gain", value: percentFromWhole(event.maxGain), color: deltaColor(event.maxGain))
                AlphaMetric(title: "Drawdown", value: percentFromWhole(event.maxDrawdown), color: .warning)
                AlphaMetric(title: "Ready", value: score(event.readinessScore), color: .textSecondary)
            }
            StatusRow(label: "Time", value: event.scanTime, color: .textSecondary)
            StatusRow(label: "Alpha tier", value: event.alphaTier ?? "Unknown", color: .accent)
            StatusRow(label: "Readiness", value: event.readinessTier ?? "Unknown", color: .textSecondary)
            StatusRow(label: "Setup", value: event.setupType ?? "Unknown", color: .textSecondary)
            StatusRow(label: "Source", value: event.source?.replacingOccurrences(of: "_", with: " ").capitalized ?? "Unknown", color: .textSecondary)
            if let regime = event.regimeOverall {
                StatusRow(label: "Regime", value: MarketRegimeLabels.label(regime), color: regimeColor(regime))
            }
            if let reason = event.filterReason, !reason.isEmpty {
                SummaryLine(title: "Filter", text: reason)
            }
        }
    }
}

struct StressDashboardCard: View {
    let run: PortfolioStressRun

    var body: some View {
        OperatorCard(title: "Stress Dashboard", icon: "shield.lefthalf.filled") {
            HStack(spacing: 6) {
                Badge(text: PortfolioStressLabels.scenario(run.worstScenario), color: worstRiskColor)
                Badge(text: shortDateString(run.createdAt), color: .textSecondary)
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Worst loss", value: percentFromWhole(run.worstLossPct), color: .warning)
                AlphaMetric(title: "Avg loss", value: percentFromWhole(run.avgLossPct), color: .textPrimary)
                AlphaMetric(title: "Max drawdown", value: percentFromWhole(run.worstLossPct), color: .negative)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Value", value: money(run.portfolioValue), color: .textPrimary)
                AlphaMetric(title: "Cash", value: money(run.cash), color: cashWarningColor)
                AlphaMetric(title: "Scenarios", value: "\(run.scenarioCount)", color: .accent)
            }

            StressWarningRows(run: run)

            let actions = run.scenarioEvents.flatMap(\.recommendedActions)
            if !actions.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Recommended defensive actions")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(Array(Set(actions)).sorted().prefix(6), id: \.self) { action in
                        BulletLine(text: action)
                    }
                }
            }
        }
    }

    private var worstRiskColor: Color {
        let worst = run.scenarioEvents.min { $0.estimatedLossPct < $1.estimatedLossPct }
        return stressRiskColor(worst?.riskLevel ?? "LOW")
    }

    private var cashWarningColor: Color {
        run.cash <= max(abs(run.worstLossPct ?? 0) / 100 * run.portfolioValue * 0.1, 1) ? .warning : .positive
    }
}

struct StressWarningRows: View {
    let run: PortfolioStressRun

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            StatusRow(label: "Cash buffer", value: cashWarning, color: cashWarning == "OK" ? .positive : .warning)
            StatusRow(label: "Concentration", value: concentrationWarning, color: concentrationWarning == "OK" ? .positive : .warning)
            StatusRow(label: "Currency", value: currencyWarning, color: currencyWarning == "OK" ? .positive : .warning)
            StatusRow(label: "Speculative exposure", value: speculativeWarning, color: speculativeWarning == "OK" ? .positive : .warning)
            if !run.warnings.isEmpty {
                ForEach(run.warnings.prefix(4), id: \.self) { warning in
                    BulletLine(text: warning)
                }
            }
        }
    }

    private var cashWarning: String {
        run.cash <= 0 ? "No cash buffer" : "OK"
    }

    private var concentrationWarning: String {
        let worstContribution = run.scenarioEvents.map(\.concentrationContribution).max() ?? 0
        return worstContribution >= 45 ? "One holding drives stress loss" : "OK"
    }

    private var currencyWarning: String {
        run.scenarioEvents.contains { $0.scenarioType == "USD_CAD_MOVE" && abs($0.estimatedLossPct) >= 5 } ? "Currency scenario matters" : "OK"
    }

    private var speculativeWarning: String {
        run.scenarioEvents.contains { ["CRYPTO_RISK_OFF", "VOLATILITY_SPIKE", "ALPHA_FALSE_POSITIVE_CLUSTER"].contains($0.scenarioType) && abs($0.estimatedLossPct) >= 5 } ? "Speculative stress visible" : "OK"
    }
}

struct StressScenarioCard: View {
    let scenario: PortfolioStressScenario
    let cash: Double
    let isSelected: Bool
    let onSelect: () -> Void

    var body: some View {
        OperatorCard(title: PortfolioStressLabels.scenario(scenario.scenarioType), icon: isSelected ? "checkmark.shield" : "exclamationmark.shield") {
            HStack(spacing: 6) {
                Badge(text: PortfolioStressLabels.risk(scenario.riskLevel), color: stressRiskColor(scenario.riskLevel))
                if let label = scenario.scenarioLabel {
                    Badge(text: label, color: .accent)
                }
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Stress value", value: money(scenario.estimatedPortfolioValue + cash), color: .textPrimary)
                AlphaMetric(title: "Loss", value: money(scenario.estimatedLossAmount), color: .warning)
                AlphaMetric(title: "Loss %", value: percentFromWhole(scenario.estimatedLossPct), color: .warning)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Contrib", value: plainPercentFromWhole(scenario.concentrationContribution), color: concentrationColor(scenario.concentrationContribution))
                AlphaMetric(title: "Cash after", value: money(cash + scenario.estimatedLossAmount), color: cash + scenario.estimatedLossAmount < 0 ? .negative : .textPrimary)
                AlphaMetric(title: "Holdings", value: "\(scenario.positionResults.count)", color: .accent)
            }
            if !scenario.worstImpactedHoldings.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Worst impacted holdings")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(scenario.worstImpactedHoldings) { position in
                        StatusRow(label: position.ticker, value: "\(money(position.estimatedLoss)) / \(plainPercentFromWhole(position.shockPct))", color: .warning)
                    }
                }
            }
            ActionButton(title: isSelected ? "Selected" : "View positions", icon: "list.bullet.rectangle", color: .accent) {
                onSelect()
            }
        }
    }
}

struct StressPositionListCard: View {
    let scenario: PortfolioStressScenario

    var body: some View {
        OperatorCard(title: "Position Stress", icon: "list.bullet.rectangle") {
            StatusRow(label: "Scenario", value: PortfolioStressLabels.scenario(scenario.scenarioType), color: stressRiskColor(scenario.riskLevel))
            if scenario.positionResults.isEmpty {
                Text("No position-level stress rows")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(scenario.positionResults.sorted { $0.estimatedLoss < $1.estimatedLoss }) { position in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Text(position.ticker)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundColor(.textPrimary)
                            Spacer()
                            Badge(text: position.sensitivity, color: stressSensitivityColor(position.sensitivity))
                        }
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Shocked price", value: plainPercentFromWhole(position.shockPct), color: .warning)
                            AlphaMetric(title: "Stress value", value: money(position.stressedValue), color: .textPrimary)
                            AlphaMetric(title: "P&L", value: money(position.estimatedLoss), color: pnlColor(position.estimatedLoss))
                        }
                        StatusRow(label: "Loss contribution", value: plainPercentFromWhole(position.lossContributionPct), color: .warning)
                    }
                    .padding(10)
                    .background(Color.surfaceElevated)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }
}

struct StressHistoryCard: View {
    let run: PortfolioStressRun

    var body: some View {
        OperatorCard(title: run.runId, icon: "clock.arrow.circlepath") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Scenarios", value: "\(run.scenarioCount)", color: .accent)
                AlphaMetric(title: "Worst", value: PortfolioStressLabels.scenario(run.worstScenario), color: .warning)
                AlphaMetric(title: "Loss", value: percentFromWhole(run.worstLossPct), color: .warning)
            }
            StatusRow(label: "Created", value: run.createdAt ?? "Unknown", color: .textSecondary)
            StatusRow(label: "Portfolio value", value: money(run.portfolioValue), color: .textPrimary)
            StatusRow(label: "Average stress loss", value: percentFromWhole(run.avgLossPct), color: .textSecondary)
        }
    }
}

struct StrategyDashboardCard: View {
    let summary: StrategySummaryResponse
    let cards: [StrategyScorecard]

    var body: some View {
        OperatorCard(title: "Strategy Dashboard", icon: "chart.bar.doc.horizontal") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Strongest", value: StrategyLabels.name(strongest?.strategy), color: strategyScoreColor(strongest?.riskAdjustedScore))
                AlphaMetric(title: "Weakest", value: StrategyLabels.name(weakest?.strategy), color: .warning)
                AlphaMetric(title: "Confidence", value: StrategyLabels.name(highestConfidence?.strategy), color: confidenceScoreColor(highestConfidence?.confidenceScore))
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Drawdown", value: StrategyLabels.name(highestDrawdown?.strategy), color: .warning)
                AlphaMetric(title: "Discipline", value: StrategyLabels.name(bestDiscipline?.strategy), color: .positive)
                AlphaMetric(title: "False +", value: StrategyLabels.name(worstFalsePositive?.strategy), color: .warning)
            }
            StrategyListRow(title: "Overused", strategies: summary.behaviorMetrics.overused, color: .warning)
            StrategyListRow(title: "Underused", strategies: summary.behaviorMetrics.underused, color: .accent)
            if !summary.priorityRecommendations.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Priority recommendations")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(summary.priorityRecommendations.prefix(4)) { rec in
                        BulletLine(text: "\(StrategyLabels.name(rec.strategy)): \(StrategyLabels.recommendation(rec.recommendation))")
                    }
                }
            }
            StatusRow(label: "Computed", value: summary.computedAt ?? "Unknown", color: .textSecondary)
        }
    }

    private var strongest: StrategyScorecard? {
        cards.compactMap { $0.riskAdjustedScore == nil ? nil : $0 }.max { ($0.riskAdjustedScore ?? -1) < ($1.riskAdjustedScore ?? -1) }
    }

    private var weakest: StrategyScorecard? {
        cards.compactMap { $0.riskAdjustedScore == nil ? nil : $0 }.min { ($0.riskAdjustedScore ?? 999) < ($1.riskAdjustedScore ?? 999) }
    }

    private var highestConfidence: StrategyScorecard? {
        cards.max { ($0.confidenceScore ?? -1) < ($1.confidenceScore ?? -1) }
    }

    private var highestDrawdown: StrategyScorecard? {
        cards.compactMap { $0.avgMaxDrawdown == nil ? nil : $0 }.min { ($0.avgMaxDrawdown ?? 999) < ($1.avgMaxDrawdown ?? 999) }
    }

    private var bestDiscipline: StrategyScorecard? {
        cards.max { ($0.checklistDisciplineScore ?? -1) < ($1.checklistDisciplineScore ?? -1) }
    }

    private var worstFalsePositive: StrategyScorecard? {
        cards.max { ($0.falsePositiveRate ?? -1) < ($1.falsePositiveRate ?? -1) }
    }
}

struct StrategyListRow: View {
    let title: String
    let strategies: [String]
    let color: Color

    var body: some View {
        StatusRow(
            label: title,
            value: strategies.isEmpty ? "None" : strategies.map { StrategyLabels.name($0) }.joined(separator: ", "),
            color: strategies.isEmpty ? .textSecondary : color
        )
    }
}

struct StrategyScorecardCard: View {
    let card: StrategyScorecard
    let isSelected: Bool
    let onOpen: () -> Void

    var body: some View {
        OperatorCard(title: StrategyLabels.name(card.strategy), icon: isSelected ? "chart.bar.fill" : "chart.bar") {
            HStack(spacing: 6) {
                Badge(text: confidenceLabel(card.confidenceScore), color: confidenceScoreColor(card.confidenceScore))
                ForEach(card.recommendations.prefix(2), id: \.self) { rec in
                    Badge(text: StrategyLabels.recommendation(rec), color: recommendationColor(rec))
                }
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Risk adj", value: score(card.riskAdjustedScore), color: strategyScoreColor(card.riskAdjustedScore))
                AlphaMetric(title: "Confidence", value: score(card.confidenceScore), color: confidenceScoreColor(card.confidenceScore))
                AlphaMetric(title: "Win", value: plainPercentFromWhole(card.winRate), color: winRateColor(card.winRate))
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Avg return", value: percentFromWhole(card.avgReturn), color: deltaColor(card.avgReturn))
                AlphaMetric(title: "Drawdown", value: percentFromWhole(card.avgMaxDrawdown), color: drawdownColor(card.avgMaxDrawdown))
                AlphaMetric(title: "False +", value: plainPercentFromWhole(card.falsePositiveRate), color: falsePositiveColor(card.falsePositiveRate))
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Thesis", value: score(card.thesisCompleteness), color: disciplineColor(card.thesisCompleteness))
                AlphaMetric(title: "Checklist", value: score(card.checklistDisciplineScore), color: disciplineColor(card.checklistDisciplineScore))
                AlphaMetric(title: "Stress", value: plainPercentFromWhole(card.stressSensitivity), color: stressSensitivityColor(card.stressSensitivity))
            }
            StatusRow(label: "Active positions", value: "\(card.activePositions)", color: card.activePositions > 0 ? .accent : .textSecondary)
            StatusRow(label: "Candidates", value: "\(card.totalCandidates)", color: card.dataAvailable ? .textPrimary : .textSecondary)
            ActionButton(title: isSelected ? "Reload detail" : "Open detail", icon: "chevron.right.circle", color: .accent) {
                onOpen()
            }
        }
    }
}

struct StrategyDetailCard: View {
    let card: StrategyScorecard

    var body: some View {
        OperatorCard(title: "\(StrategyLabels.name(card.strategy)) Detail", icon: "doc.text.magnifyingglass") {
            StrategyDetailSection(title: "Recommendation explanations", rows: recommendationRows)
            StrategyDetailSection(title: "Regime sensitivity", rows: regimeRows)
            StrategyDetailSection(title: "Validation quality", rows: validationRows)
            StrategyDetailSection(title: "Historical behavior", rows: behaviorRows)
            StrategyDetailSection(title: "Strengths", rows: card.strengths.isEmpty ? ["No clear strength yet"] : card.strengths)
            StrategyDetailSection(title: "Weaknesses", rows: card.weaknesses.isEmpty ? ["No clear weakness yet"] : card.weaknesses)
            StrategyDetailSection(title: "Suggested operator improvements", rows: improvementRows)
            StatusRow(label: "Computed", value: card.computedAt ?? "Unknown", color: .textSecondary)
        }
    }

    private var recommendationRows: [String] {
        card.recommendations.map { StrategyLabels.recommendation($0) }
    }

    private var regimeRows: [String] {
        ["Stress sensitivity: \(plainPercentFromWhole(card.stressSensitivity))", card.stressSensitivity ?? 0 >= 15 ? "Tighten sizing during risk-off regimes" : "Regime sensitivity appears contained"]
    }

    private var validationRows: [String] {
        ["Validation quality: \(score(card.validationQuality))", "False-positive rate: \(plainPercentFromWhole(card.falsePositiveRate))"]
    }

    private var behaviorRows: [String] {
        [
            "Win rate: \(plainPercentFromWhole(card.winRate))",
            "Average return: \(percentFromWhole(card.avgReturn))",
            "Average drawdown: \(percentFromWhole(card.avgMaxDrawdown))",
            "Candidates: \(card.totalCandidates), decisions: \(card.totalDecisions)"
        ]
    }

    private var improvementRows: [String] {
        if card.recommendations.contains("monitor_only") {
            return ["Keep observing before sizing up", "Do not infer too much from sparse data"]
        }
        return card.recommendations.map { rec in
            switch rec {
            case "require_stricter_checklist": return "Require full checklist completion before entry"
            case "improve_thesis_quality": return "Write stronger thesis, risks, invalidation, and exit plan"
            case "use_smaller_sizing": return "Use smaller initial sizing where drawdowns are large"
            case "avoid_during_risk_off": return "Pause or monitor only in risk-off regimes"
            case "reduce_exposure": return "Reduce exposure until behavior improves"
            case "increase_focus", "promote_to_core": return "Focus attention, but keep manual discipline"
            default: return StrategyLabels.recommendation(rec)
            }
        }
    }
}

struct StrategyDetailSection: View {
    let title: String
    let rows: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            ForEach(rows, id: \.self) { row in
                BulletLine(text: row)
            }
        }
    }
}

struct PlannerDashboardCard: View {
    let snapshot: PlannerSnapshot

    var body: some View {
        OperatorCard(title: "Planner Dashboard", icon: "chart.pie") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Value", value: money(snapshot.portfolioValue), color: .textPrimary)
                AlphaMetric(title: "Cash", value: money(snapshot.cash), color: .accent)
                AlphaMetric(title: "TFSA room", value: tfsaRoomText, color: .textSecondary)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Urgency", value: PlannerLabels.urgency(snapshot.rebalanceUrgency), color: plannerUrgencyColor(snapshot.rebalanceUrgency))
                AlphaMetric(title: "Risk", value: score(snapshot.riskScore), color: strategyScoreColor(100 - snapshot.riskScore))
                AlphaMetric(title: "Regime", value: MarketRegimeLabels.label(snapshot.regime), color: regimeColor(snapshot.regime))
            }
            StatusRow(label: "Current allocation", value: dominantBucket(snapshot.currentAllocation), color: .textPrimary)
            StatusRow(label: "Target allocation", value: dominantBucket(snapshot.targetAllocation), color: .accent)
            StatusRow(label: "Allocation drift", value: maxDriftText, color: plannerUrgencyColor(snapshot.rebalanceUrgency))
            SummaryLine(title: "Cash reserve guidance", text: snapshot.cashDeploymentGuidance.isEmpty ? "No cash reserve guidance" : snapshot.cashDeploymentGuidance)
            SummaryLine(title: "Risk-reduction guidance", text: snapshot.riskReductionGuidance.isEmpty ? "No risk guidance" : snapshot.riskReductionGuidance)
            SummaryLine(title: "Contribution guidance", text: snapshot.contributionGuidance.isEmpty ? "No contribution guidance" : snapshot.contributionGuidance)
            if !snapshot.strategyAlignmentNotes.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Strategy alignment notes")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(snapshot.strategyAlignmentNotes.prefix(4), id: \.self) { note in
                        BulletLine(text: note)
                    }
                }
            }
            StatusRow(label: "Computed", value: snapshot.createdAt ?? "Unknown", color: .textSecondary)
        }
    }

    private var tfsaRoomText: String {
        let match = snapshot.contributionGuidance
            .split(separator: " ")
            .first { $0.contains(",") || $0.allSatisfy(\.isNumber) }
        return match.map(String.init) ?? "See guidance"
    }

    private var maxDriftText: String {
        guard let max = snapshot.drift.values.max(by: { abs($0) < abs($1) }) else { return "-" }
        return signedNumber(max)
    }

    func dominantBucket(_ allocation: [String: Double]) -> String {
        guard let item = allocation.max(by: { $0.value < $1.value }) else { return "Unknown" }
        return "\(PlannerLabels.bucket(item.key)) \(plainPercentFromWhole(item.value))"
    }
}

struct PlannerAllocationCard: View {
    let row: PlannerAllocationRow

    var body: some View {
        OperatorCard(title: PlannerLabels.bucket(row.bucket), icon: rowIcon) {
            HStack(spacing: 6) {
                Badge(text: row.status, color: allocationStatusColor(row.driftPct))
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Current", value: plainPercentFromWhole(row.currentPct), color: .textPrimary)
                AlphaMetric(title: "Target", value: plainPercentFromWhole(row.targetPct), color: .accent)
                AlphaMetric(title: "Drift", value: signedNumber(row.driftPct), color: allocationStatusColor(row.driftPct))
            }
            SummaryLine(title: "Explanation", text: row.explanation)
        }
    }

    private var rowIcon: String {
        abs(row.driftPct) >= 8 ? "exclamationmark.triangle" : "circle.grid.2x2"
    }
}

struct PlannerProjectionChartCard: View {
    let projections: PlannerProjections

    var body: some View {
        OperatorCard(title: "Projection Path", icon: "chart.xyaxis.line") {
            Chart(chartPoints) { point in
                LineMark(
                    x: .value("Years", point.years),
                    y: .value("Value", point.value)
                )
                .foregroundStyle(by: .value("Scenario", point.label))
                PointMark(
                    x: .value("Years", point.years),
                    y: .value("Value", point.value)
                )
                .foregroundStyle(by: .value("Scenario", point.label))
            }
            .chartXAxis {
                AxisMarks(values: [1, 3, 5, 10]) { value in
                    AxisGridLine().foregroundStyle(Color.border)
                    AxisValueLabel {
                        if let years = value.as(Int.self) {
                            Text("\(years)y")
                                .foregroundColor(.textSecondary)
                        }
                    }
                }
            }
            .chartYAxis {
                AxisMarks { value in
                    AxisGridLine().foregroundStyle(Color.border)
                    AxisValueLabel {
                        if let amount = value.as(Double.self) {
                            Text(shortMoney(amount))
                                .foregroundColor(.textSecondary)
                        }
                    }
                }
            }
            .frame(height: 190)
            .padding(.top, 6)
            StatusRow(label: "Starting value", value: money(projections.startingValue), color: .textSecondary)
            StatusRow(label: "Monthly contribution", value: money(projections.monthlyContribution), color: .accent)
        }
    }

    private var chartPoints: [PlannerProjectionPoint] {
        projections.allScenarios.flatMap { scenario in
            scenario.rows.map {
                PlannerProjectionPoint(label: scenario.name.capitalized, years: $0.years, value: $0.projectedValue)
            }
        }
    }
}

struct PlannerProjectionCards: View {
    let projections: PlannerProjections

    var body: some View {
        ForEach(projections.allScenarios, id: \.name) { scenario in
            OperatorCard(title: scenario.name.capitalized, icon: projectionIcon(scenario.name)) {
                if scenario.rows.isEmpty {
                    Text("No projection rows")
                        .operatorBody(color: .textSecondary)
                } else {
                    ForEach(scenario.rows) { row in
                        HStack(spacing: 8) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(row.years) year\(row.years == 1 ? "" : "s")")
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundColor(.textPrimary)
                                Text("Contribution \(money(row.totalContributed))")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundColor(.textSecondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 3) {
                                Text(money(row.projectedValue))
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundColor(projectionColor(scenario.name))
                                Text("Compounding \(money(row.compoundingImpact))")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundColor(deltaColor(row.compoundingImpact))
                            }
                        }
                        .padding(.vertical, 5)
                        StatusRow(label: "Contribution impact", value: money(row.contributionImpact), color: .accent)
                    }
                }
            }
        }
    }

    func projectionIcon(_ scenario: String) -> String {
        scenario == "downside" ? "exclamationmark.triangle" : "chart.line.uptrend.xyaxis"
    }
}

struct PlannerProjectionPoint: Identifiable {
    var id: String { "\(label)-\(years)" }
    let label: String
    let years: Int
    let value: Double
}

struct ReviewOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Review Only", icon: "eye") {
            Text("Review only / no trades placed")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct EducationalOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Educational Only", icon: "graduationcap") {
            Text("Educational only. Not financial advice. No trades placed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct ResearchActionBanner: View {
    let message: String?
    let success: Bool

    var body: some View {
        if let message, !message.isEmpty {
            StatusBanner(text: message, color: success ? .positive : .warning, icon: success ? "checkmark.circle" : "exclamationmark.triangle")
        }
    }
}

struct ResearchPeriodPicker: View {
    @Binding var selection: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Period")
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(researchPeriods, id: \.self) { choice in
                        Button {
                            selection = choice
                            HapticManager.selection()
                        } label: {
                            Text(choice)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(selection == choice ? .black : .textPrimary)
                                .frame(width: 48)
                                .padding(.vertical, 8)
                                .background(selection == choice ? Color.accent : Color.surfaceElevated)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(selection == choice ? Color.clear : Color.border, lineWidth: 0.5))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

struct MarketPulseCard: View {
    let item: MarketTickerSnapshot

    var body: some View {
        OperatorCard(title: marketLabel(item), icon: "chart.line.uptrend.xyaxis") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Price", value: money(item.price), color: .textPrimary)
                AlphaMetric(title: "Return", value: signedPercent(item.changePct), color: deltaColor(item.changePct))
                AlphaMetric(title: "Ticker", value: item.ticker, color: .accent)
            }
            ResearchSparkline(points: item.sparkline, color: deltaColor(item.changePct))
        }
    }
}

struct ResearchSparkline: View {
    let points: [ResearchSparkPoint]
    let color: Color

    var body: some View {
        if points.count >= 2 {
            Chart(points) { point in
                LineMark(
                    x: .value("Time", point.t),
                    y: .value("Value", point.v)
                )
                .foregroundStyle(color)
                .lineStyle(StrokeStyle(lineWidth: 2))
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .frame(height: 54)
            .padding(8)
            .background(Color.surfaceElevated)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        } else {
            Text("No chart data")
                .operatorBody(color: .textSecondary)
                .padding(10)
                .background(Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct SectorPerformanceSummary: View {
    let response: SectorPerformanceResponse

    var body: some View {
        OperatorCard(title: "Strongest / Weakest", icon: "arrow.up.arrow.down") {
            StatusRow(label: "Strongest", value: sectorLabel(response.sectors.max { ($0.changePct ?? -999) < ($1.changePct ?? -999) }), color: .positive)
            StatusRow(label: "Weakest", value: sectorLabel(response.sectors.min { ($0.changePct ?? 999) < ($1.changePct ?? 999) }), color: .negative)
            StatusRow(label: "Period", value: response.period, color: .textSecondary)
        }
    }
}

struct SectorPerformanceCard: View {
    let row: SectorPerformanceRow
    let maxAbs: Double

    var body: some View {
        OperatorCard(title: row.sectorName, icon: "chart.bar") {
            StatusRow(label: row.ticker, value: signedPercent(row.changePct), color: deltaColor(row.changePct))
            GeometryReader { proxy in
                let width = maxAbs > 0 ? proxy.size.width * min(abs(row.changePct ?? 0) / maxAbs, 1) : 0
                HStack(spacing: 0) {
                    Rectangle()
                        .fill(deltaColor(row.changePct))
                        .frame(width: max(width, 3), height: 8)
                    Spacer(minLength: 0)
                }
            }
            .frame(height: 8)
            .background(Color.surfaceElevated)
            .clipShape(RoundedRectangle(cornerRadius: 5))
        }
    }
}

struct StockResearchCard: View {
    let stock: StockResearchResponse

    var body: some View {
        OperatorCard(title: stock.ticker, icon: "doc.text.magnifyingglass") {
            Text(stock.name ?? "No company name")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.textPrimary)
            StatusRow(label: "Sector", value: [stock.sector, stock.industry].compactMap { $0 }.joined(separator: " / ").nilIfEmpty ?? "-", color: .textSecondary)
            HStack(spacing: 8) {
                AlphaMetric(title: "Price", value: money(stock.quote.price), color: .textPrimary)
                AlphaMetric(title: "Return", value: signedPercent(stock.quote.changePct), color: deltaColor(stock.quote.changePct))
                AlphaMetric(title: "Tech", value: score(stock.technicals.technicalScore), color: .accent)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Quality", value: score(stock.fundamentals.fundamentalScore), color: .accent)
                AlphaMetric(title: "RSI", value: stock.technicals.rsiBucket ?? "-", color: rsiColor(stock.technicals.rsiBucket))
                AlphaMetric(title: "52W", value: percentFromWhole(stock.technicals.week52Position), color: .textPrimary)
            }
            BriefSection(title: "At a glance") {
                StatusRow(label: "Trend vs 200DMA", value: trendVs200(stock), color: trendColor(stock))
                StatusRow(label: "ROE tier", value: stock.fundamentals.roeTier ?? "-", color: .textPrimary)
                StatusRow(label: "D/E tier", value: stock.fundamentals.deTier ?? "-", color: .textPrimary)
                StatusRow(label: "Volatility tier", value: stock.fundamentals.betaTier ?? "-", color: .textPrimary)
                StatusRow(label: "Valuation tier", value: stock.fundamentals.valuationTier ?? "-", color: .textPrimary)
            }
            BriefSection(title: "Key stats") {
                StatusRow(label: "Market cap", value: stock.quote.marketCap.map(shortMoney) ?? "-", color: .textPrimary)
                StatusRow(label: "Volume", value: compactNumber(stock.quote.volume), color: .textPrimary)
                StatusRow(label: "P/E", value: score(stock.fundamentals.peRatio), color: .textPrimary)
                StatusRow(label: "ROE", value: percentFromWhole(stock.fundamentals.roePct), color: .textPrimary)
            }
            ResearchSparkline(points: stock.sparkline, color: deltaColor(stock.quote.changePct))
        }
    }
}

struct ETFResearchCard: View {
    let etf: ETFResearchResponse

    var body: some View {
        OperatorCard(title: etf.ticker, icon: "rectangle.stack") {
            Text(etf.name ?? "No ETF name")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.textPrimary)
            StatusRow(label: "Category", value: etf.category ?? "-", color: .textSecondary)
            HStack(spacing: 8) {
                AlphaMetric(title: "Price", value: money(etf.quote.price), color: .textPrimary)
                AlphaMetric(title: "Return", value: signedPercent(etf.quote.changePct), color: deltaColor(etf.quote.changePct))
                AlphaMetric(title: "Risk", value: score(etf.risk.riskScore), color: riskScoreColor(etf.risk.riskScore))
            }
            BriefSection(title: "Returns") {
                ForEach(["1D", "5D", "1M", "3M", "6M", "1Y"], id: \.self) { key in
                    StatusRow(label: key, value: signedPercent(etf.returns[key]), color: deltaColor(etf.returns[key]))
                }
            }
            BriefSection(title: "Risk / cost") {
                StatusRow(label: "Annualized volatility", value: percent(etf.risk.annualizedVolatility), color: .textPrimary)
                StatusRow(label: "Expense ratio", value: percent(etf.expenseRatio), color: .textPrimary)
                StatusRow(label: "AUM", value: etf.aum.map(shortMoney) ?? "-", color: .textPrimary)
            }
            BriefSection(title: "Top holdings") {
                if etf.topHoldings.isEmpty {
                    Text("No holdings data").operatorBody(color: .textSecondary)
                } else {
                    ForEach(etf.topHoldings.prefix(8)) { holding in
                        StatusRow(label: holding.ticker ?? holding.name ?? "-", value: percent(holding.weightPct), color: .textPrimary)
                    }
                }
            }
            BriefSection(title: "Peers / alternatives") {
                StatusRow(label: "Peers", value: etf.peers.isEmpty ? "-" : etf.peers.joined(separator: ", "), color: .textSecondary)
                StatusRow(label: "Cheaper alternatives", value: etf.cheaperAlternatives.isEmpty ? "-" : etf.cheaperAlternatives.joined(separator: ", "), color: .textSecondary)
            }
            ResearchSparkline(points: etf.sparkline, color: deltaColor(etf.quote.changePct))
        }
    }
}

struct MacroResearchCard: View {
    let macro: MacroResearchResponse

    var body: some View {
        OperatorCard(title: "Macro Indicators", icon: "globe.americas") {
            StatusRow(label: "FRED data", value: macro.available ? "Available" : "Unavailable", color: macro.available ? .positive : .warning)
            if let reason = macro.reason, !reason.isEmpty {
                Text(reason)
                    .operatorBody(color: .warning)
            }
            let rows = preferredMacroRows(macro.indicators)
            if rows.isEmpty {
                Text("No macro indicators available")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows, id: \.0) { key, item in
                    StatusRow(label: macroLabel(key, fallback: item.label), value: macroValue(item), color: .textPrimary)
                }
            }
        }
    }
}

struct NewsSectionCard: View {
    let title: String
    let response: ResearchNewsResponse?

    var body: some View {
        OperatorCard(title: title, icon: "newspaper") {
            if let items = response?.items, !items.isEmpty {
                ForEach(items.prefix(10)) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.title ?? "Untitled")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text([item.publisher, item.publishedAt].compactMap { $0 }.joined(separator: " • "))
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(.textSecondary)
                        if let summary = item.summary, !summary.isEmpty {
                            Text(summary)
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textSecondary)
                                .lineLimit(3)
                        }
                    }
                    .padding(10)
                    .background(Color.surfaceElevated)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            } else {
                Text("No headlines cached")
                    .operatorBody(color: .textSecondary)
            }
        }
    }
}

struct ResearchAIAnalysisCard: View {
    let title: String
    let analysis: ResearchAIResponse

    var body: some View {
        OperatorCard(title: title, icon: "brain") {
            Badge(text: "Educational analysis only", color: analysis.complianceOk ? .positive : .warning)
            Text(analysis.commentary.isEmpty ? "No commentary returned" : analysis.commentary)
                .operatorBody(color: .textPrimary)
            if let disclaimer = analysis.disclaimer {
                Text(disclaimer)
                    .operatorBody(color: .textSecondary)
            }
        }
    }
}

struct ResearchOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Research Only", icon: "book") {
            Text("Research only. No trades placed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct ResearchWorkflowOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Research Workflow Only", icon: "checklist") {
            Text("Research workflow only. No trades placed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct ResearchWorkflowItemCard: View {
    let item: ResearchWorkflowItem
    let selected: Bool
    @Binding var snoozeValue: String
    @Binding var snoozeUnit: String
    @Binding var noteText: String
    let onSelect: () -> Void
    let onStart: () -> Void
    let onDone: () -> Void
    let onSnooze: () -> Void
    let onArchive: () -> Void
    let onNote: () -> Void

    var body: some View {
        OperatorCard(title: item.ticker ?? item.itemId, icon: workflowIcon(item)) {
            HStack(spacing: 6) {
                Badge(text: ResearchWorkflowLabels.status(item.status), color: workflowStatusColor(item.status))
                Badge(text: ResearchWorkflowLabels.priority(item.priority), color: watchPriorityColor(item.priority))
                Badge(text: ResearchWorkflowLabels.source(item.source), color: .accent)
            }
            StatusRow(label: "Item id", value: item.itemId, color: .textSecondary)
            StatusRow(label: "Reason", value: item.reason.isEmpty ? "-" : item.reason, color: .textPrimary)
            StatusRow(label: "Due", value: shortDateString(item.dueAt), color: workflowDueColor(item))
            StatusRow(label: "Due state", value: workflowDueState(item), color: workflowDueColor(item))
            StatusRow(label: "Linked entity", value: "\(item.linkedEntityType) \(item.linkedEntityId ?? "-")", color: .textSecondary)
            HStack(spacing: 8) {
                AlphaMetric(title: "Urgency", value: score(item.urgencyScore), color: .warning)
                AlphaMetric(title: "Opportunity", value: score(item.opportunityScore), color: .positive)
                AlphaMetric(title: "Risk", value: score(item.riskScore), color: .warning)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Stale", value: score(item.staleScore), color: .textSecondary)
                AlphaMetric(title: "Total", value: score(item.priorityScore), color: workflowScoreColor(item.priorityScore))
            }
            HStack(spacing: 8) {
                SmallActionButton(title: "Start", icon: "play.circle", color: .accent, action: onStart)
                SmallActionButton(title: "Done", icon: "checkmark.circle", color: .positive, action: onDone)
                SmallActionButton(title: "Archive", icon: "archivebox", color: .negative, action: onArchive)
            }
            SmallActionButton(title: selected ? "Selected" : "Select for note/snooze", icon: "cursorarrow.click", color: selected ? .positive : .accent, action: onSelect)
            if selected {
                HStack(spacing: 8) {
                    ManualTextField(title: "Snooze", text: $snoozeValue, placeholder: "24", keyboard: .numberPad)
                    ManualChoiceRow(title: "Unit", choices: ["hours", "days"], selection: $snoozeUnit)
                }
                ActionButton(title: "Snooze item", icon: "clock.badge", color: .warning, action: onSnooze)
                ManualTextField(title: "Append note", text: $noteText, placeholder: "Research note")
                ActionButton(title: "Add append-only note", icon: "note.text.badge.plus", color: .accent, action: onNote)
            }
        }
    }
}

struct WorkflowSummarySection: View {
    let title: String
    let items: [ResearchWorkflowItem]

    var body: some View {
        OperatorCard(title: title, icon: "list.bullet.rectangle") {
            if items.isEmpty {
                Text("No items")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(items) { item in
                    SummaryLine(
                        title: "\(item.ticker ?? item.itemId) • \(ResearchWorkflowLabels.source(item.source))",
                        text: "\(ResearchWorkflowLabels.priority(item.priority)) / \(ResearchWorkflowLabels.status(item.status)) / score \(score(item.priorityScore))"
                    )
                }
            }
        }
    }
}

struct WeeklyCompactCard: View {
    let text: String
    let lastRefresh: String?
    let onCopy: () -> Void

    var body: some View {
        OperatorCard(title: "Compact Weekly Review", icon: "message") {
            Text(text.isEmpty ? "No compact weekly review text" : text)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .textSelection(.enabled)
            StatusRow(label: "Last refreshed", value: lastRefresh ?? "Unknown", color: .textSecondary)
            ActionButton(title: "Copy compact weekly review", icon: "doc.on.doc", color: .accent, action: onCopy)
        }
    }
}

struct WeeklyDetailedCard: View {
    let review: WeeklyReviewDetailedResponse

    var body: some View {
        OperatorCard(title: "Detailed Weekly Review", icon: "list.bullet.rectangle") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Grade", value: WeeklyReviewLabels.grade(review.grade), color: weeklyGradeColor(review.grade))
                AlphaMetric(title: "Generated", value: shortDateString(review.generatedAt), color: .textSecondary)
            }
            BriefSection(title: "Grade explanation") {
                Text(gradeExplanation(review))
                    .operatorBody(color: .textSecondary)
            }
            BriefSection(title: "Portfolio weekly change") {
                if review.portfolioWeeklyChange.available {
                    StatusRow(label: "Change", value: "\(money(review.portfolioWeeklyChange.changeCad)) / \(percentFromWhole(review.portfolioWeeklyChange.changePct))", color: pnlColor(review.portfolioWeeklyChange.changeCad))
                    StatusRow(label: "End value", value: money(review.portfolioWeeklyChange.endValue), color: .textPrimary)
                } else {
                    Text("No portfolio weekly change available").operatorBody(color: .textSecondary)
                }
            }
            BriefSection(title: "Accountability metrics") {
                StatusRow(label: "Reviews completed", value: percent(review.accountabilityMetrics.reviewCompletionRate), color: .textPrimary)
                StatusRow(label: "Overdue reviews", value: "\(review.accountabilityMetrics.overdueReviewCount)", color: review.accountabilityMetrics.overdueReviewCount > 0 ? .warning : .positive)
                StatusRow(label: "Checklist discipline", value: percent(review.accountabilityMetrics.checklistDisciplineScore), color: .textPrimary)
                StatusRow(label: "Ignored important items", value: "\(review.accountabilityMetrics.ignoredHighPriorityWorkflow)", color: review.accountabilityMetrics.ignoredHighPriorityWorkflow > 0 ? .warning : .positive)
                StatusRow(label: "Unreviewed alert drafts", value: "\(review.accountabilityMetrics.unreviewedDryRuns)", color: review.accountabilityMetrics.unreviewedDryRuns > 0 ? .warning : .positive)
                StatusRow(label: "Stale theses", value: "\(review.accountabilityMetrics.staleTheses)", color: review.accountabilityMetrics.staleTheses > 0 ? .warning : .positive)
                StatusRow(label: "False positives", value: "\(review.accountabilityMetrics.alphaFalsePositiveCount)", color: review.accountabilityMetrics.alphaFalsePositiveCount > 0 ? .warning : .positive)
                StatusRow(label: "Missed winners", value: "\(review.accountabilityMetrics.missedWinnerCount)", color: review.accountabilityMetrics.missedWinnerCount > 0 ? .warning : .positive)
                StatusRow(label: "Open risk warnings", value: "\(review.accountabilityMetrics.riskWarningsUnresolved)", color: review.accountabilityMetrics.riskWarningsUnresolved > 0 ? .warning : .positive)
            }
            BriefSection(title: "Alpha and validation") {
                StatusRow(label: "Alpha candidates generated", value: "\(review.alphaGenerated.count)", color: .textPrimary)
                StatusRow(label: "Validation outcomes", value: "\(review.validationOutcomes.completedCount)", color: .textPrimary)
                StatusRow(label: "False positives", value: "\(review.validationOutcomes.falsePositiveCount)", color: review.validationOutcomes.falsePositiveCount > 0 ? .warning : .positive)
                StatusRow(label: "Positive outcomes", value: "\(review.validationOutcomes.positiveCount)", color: .positive)
            }
            BriefSection(title: "Notifications and QC") {
                StatusRow(label: "Dry-runs created", value: "\(review.notificationActivity.createdThisWeek)", color: .textPrimary)
                StatusRow(label: "Dry-runs reviewed", value: "\(review.notificationActivity.reviewedThisWeek)", color: .positive)
                StatusRow(label: "Dry-runs dismissed", value: "\(review.notificationActivity.dismissedThisWeek)", color: .textSecondary)
                StatusRow(label: "QC suppressions", value: "\(review.qcSuppressions.suppressedThisWeek)", color: .warning)
            }
            BriefSection(title: "Decisions and research") {
                StatusRow(label: "Decision checklists created", value: "\(review.checklistDiscipline.createdThisWeek)", color: .textPrimary)
                StatusRow(label: "Approved", value: "\(review.checklistDiscipline.approvedThisWeek)", color: .positive)
                StatusRow(label: "Rejected", value: "\(review.checklistDiscipline.rejectedThisWeek)", color: .warning)
                StatusRow(label: "Workflow completed", value: "\(review.workflowSummary.completedThisWeek)", color: .positive)
                StatusRow(label: "Workflow overdue", value: "\(review.workflowSummary.overdueCount)", color: review.workflowSummary.overdueCount > 0 ? .warning : .positive)
            }
            BriefSection(title: "Thesis and watchlist") {
                StatusRow(label: "Thesis reviews completed", value: "\(review.thesisSummary.reviewsCompletedThisWeek)", color: .positive)
                StatusRow(label: "Thesis overdue", value: "\(review.thesisSummary.overdueCount)", color: review.thesisSummary.overdueCount > 0 ? .warning : .positive)
                StatusRow(label: "Watchlist updated", value: "\(review.watchlistChanges.updatedThisWeek)", color: .textPrimary)
                StatusRow(label: "Watchlist archived", value: "\(review.watchlistChanges.archivedThisWeek)", color: .textSecondary)
            }
            BriefSection(title: "Stress / planner / regime") {
                StatusRow(label: "Strategy scorecards", value: "\(review.scorecardChanges.computedThisWeek)", color: .textPrimary)
                StatusRow(label: "Top strategy", value: review.scorecardChanges.topStrategy ?? "-", color: .accent)
                StatusRow(label: "Stress runs", value: "\(review.stressTestChanges.runsThisWeek)", color: .textPrimary)
                StatusRow(label: "Worst stress loss", value: percentFromWhole(review.stressTestChanges.worstLossPct), color: .warning)
                StatusRow(label: "Planner urgency", value: PlannerLabels.urgency(review.plannerDriftChanges.lastUrgency), color: plannerUrgencyColor(review.plannerDriftChanges.lastUrgency))
                StatusRow(label: "Regime", value: "\(MarketRegimeLabels.label(review.regimeChanges.openingRegime)) → \(MarketRegimeLabels.label(review.regimeChanges.closingRegime))", color: regimeColor(review.regimeChanges.closingRegime))
            }
            WeeklyObservationSection(title: "Mistakes", rows: review.keyMistakes)
            WeeklyObservationSection(title: "Best decisions", rows: review.bestDecisions)
            WeeklyObservationSection(title: "Missed opportunities", rows: review.missedOpportunities)
            BriefBulletSection(title: "Focus for next week", rows: review.focusNextWeek, emptyText: "No next-week focus items")
        }
    }
}

struct WeeklyObservationSection: View {
    let title: String
    let rows: [WeeklyObservation]

    var body: some View {
        BriefSection(title: title) {
            if rows.isEmpty {
                Text("No items").operatorBody(color: .textSecondary)
            } else {
                ForEach(rows) { row in
                    SummaryLine(title: row.ticker ?? row.type.replacingOccurrences(of: "_", with: " "), text: row.description)
                }
            }
        }
    }
}

struct WeeklyDebugCard: View {
    let debug: WeeklyReviewDebugResponse

    var body: some View {
        OperatorCard(title: "Weekly Debug", icon: "ladybug") {
            BriefDisclosure(title: "Data sources") {
                StatusRow(label: "Portfolio", value: debug.dataSources.portfolioAvailable ? "Available" : "Missing", color: debug.dataSources.portfolioAvailable ? .positive : .warning)
                StatusRow(label: "Alpha generated", value: "\(debug.dataSources.alphaGeneratedCount)", color: .textPrimary)
                StatusRow(label: "Outcomes completed", value: "\(debug.dataSources.outcomesCompletedCount)", color: .textPrimary)
                StatusRow(label: "Dry-runs created", value: "\(debug.dataSources.dryrunsCreatedCount)", color: .textPrimary)
                StatusRow(label: "QC evaluated", value: "\(debug.dataSources.qcEvaluatedCount)", color: .textPrimary)
                StatusRow(label: "Workflow completed", value: "\(debug.dataSources.workflowCompletedCount)", color: .textPrimary)
                StatusRow(label: "Thesis reviews", value: "\(debug.dataSources.thesisReviewsCount)", color: .textPrimary)
                StatusRow(label: "Regime snapshots", value: "\(debug.dataSources.regimeSnapshotsCount)", color: .textPrimary)
                StatusRow(label: "Risk warnings", value: "\(debug.dataSources.riskWarningsUnresolved)", color: .warning)
            }
            BriefDisclosure(title: "Section counts") {
                StatusRow(label: "Mistakes", value: "\(debug.detailed.keyMistakes.count)", color: .textPrimary)
                StatusRow(label: "Best decisions", value: "\(debug.detailed.bestDecisions.count)", color: .textPrimary)
                StatusRow(label: "Missed opportunities", value: "\(debug.detailed.missedOpportunities.count)", color: .textPrimary)
                StatusRow(label: "Focus items", value: "\(debug.detailed.focusNextWeek.count)", color: .textPrimary)
            }
        }
    }
}

struct WeeklyHistoryCard: View {
    let entry: WeeklyReviewHistoryEntry

    var body: some View {
        OperatorCard(title: entry.weekStart, icon: "calendar") {
            HStack(spacing: 6) {
                Badge(text: WeeklyReviewLabels.grade(entry.grade), color: weeklyGradeColor(entry.grade))
                Badge(text: entry.mode ?? "compact", color: .textSecondary)
            }
            StatusRow(label: "Week start", value: entry.weekStart, color: .textPrimary)
            StatusRow(label: "Sent status", value: entry.sentAt == nil ? "Not sent" : "Sent", color: entry.sentAt == nil ? .textSecondary : .positive)
            StatusRow(label: "Created/sent", value: shortDateString(entry.sentAt), color: .textSecondary)
        }
    }
}

struct ResearchWatchlistForm: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        ManualTextField(title: "Ticker", text: $vm.watchlistTicker, placeholder: "AAPL")
        ManualTextField(title: "Name optional", text: $vm.watchlistName, placeholder: "Apple Inc.")
        WatchlistChoicePicker(title: "Asset type", choices: ["STOCK", "ETF", "CRYPTO", "INDEX", "OTHER"], selection: $vm.watchlistAssetType, label: ResearchWatchlistLabels.assetType)
        WatchlistChoicePicker(title: "Category", choices: ["CORE", "ALPHA", "SPECULATIVE", "MACRO", "HEDGE", "LEARNING"], selection: $vm.watchlistCategory, label: ResearchWatchlistLabels.category)
        WatchlistChoicePicker(title: "Status", choices: ["WATCHING", "REVIEW_SOON", "ACTIVE_RESEARCH", "PAUSED", "ARCHIVED"], selection: $vm.watchlistStatus, label: ResearchWatchlistLabels.status)
        ManualChoiceRow(title: "Priority", choices: ["LOW", "MEDIUM", "HIGH"], selection: $vm.watchlistPriority)
        ManualTextField(title: "Reason", text: $vm.watchlistReason, placeholder: "Why this is on the list")
        ManualTextField(title: "Next review date optional", text: $vm.watchlistNextReviewAt, placeholder: "2026-06-01")
        ManualTextField(title: "Linked Alpha candidate optional", text: $vm.watchlistLinkedAlphaCandidateId, placeholder: "numeric id", keyboard: .numberPad)
        ManualTextField(title: "Linked thesis optional", text: $vm.watchlistLinkedThesisId, placeholder: "numeric id", keyboard: .numberPad)
    }
}

struct CatalystForm: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        ManualTextField(title: "Ticker optional", text: $vm.catalystTicker, placeholder: "NVDA")
        ManualTextField(title: "Title", text: $vm.catalystTitle, placeholder: "Earnings date")
        ManualTextField(title: "Description", text: $vm.catalystDescription, placeholder: "What needs to be tracked")
        WatchlistChoicePicker(title: "Type", choices: ["EARNINGS", "FDA_REGULATORY", "MACRO", "PRODUCT", "CONTRACT", "INVESTOR_DAY", "THESIS_REVIEW", "WATCHLIST_REVIEW", "ALPHA_CONFIRMATION", "PORTFOLIO_RISK", "OTHER"], selection: $vm.catalystType, label: CatalystLabels.type)
        ManualTextField(title: "Date", text: $vm.catalystDate, placeholder: "2026-06-18")
        WatchlistChoicePicker(title: "Confidence", choices: ["LOW", "MEDIUM", "HIGH"], selection: $vm.catalystConfidence, label: CatalystLabels.level)
        WatchlistChoicePicker(title: "Importance", choices: ["LOW", "MEDIUM", "HIGH"], selection: $vm.catalystImportance, label: CatalystLabels.level)
        ManualTextField(title: "Linked entity type optional", text: $vm.catalystLinkedEntityType, placeholder: "THESIS")
        ManualTextField(title: "Linked entity id optional", text: $vm.catalystLinkedEntityId, placeholder: "ticker or id")
    }
}

struct EventTrackingOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Event Tracking Only", icon: "calendar") {
            Text("Catalysts track dates and review pressure only. No trades are placed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct CatalystGroupSection: View {
    let title: String
    let icon: String
    let catalysts: [Catalyst]
    let onEdit: (Catalyst) -> Void
    let onOpen: (Catalyst) -> Void
    let onComplete: (Catalyst) -> Void
    let onArchive: (Catalyst) -> Void

    var body: some View {
        if !catalysts.isEmpty {
            OperatorCard(title: title, icon: icon) {
                ForEach(catalysts) { catalyst in
                    CatalystCard(
                        catalyst: catalyst,
                        onEdit: { onEdit(catalyst) },
                        onOpen: { onOpen(catalyst) },
                        onComplete: { onComplete(catalyst) },
                        onArchive: { onArchive(catalyst) }
                    )
                }
            }
        }
    }
}

struct CatalystCard: View {
    let catalyst: Catalyst
    let onEdit: () -> Void
    let onOpen: () -> Void
    let onComplete: () -> Void
    let onArchive: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(catalyst.ticker ?? CatalystLabels.source(catalyst.source))
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(.textPrimary)
                    Text(catalyst.title)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.textSecondary)
                }
                Spacer()
                Badge(text: catalystDateState(catalyst), color: catalystDateColor(catalyst))
            }
            HStack(spacing: 6) {
                Badge(text: CatalystLabels.type(catalyst.catalystType), color: .accent)
                Badge(text: CatalystLabels.status(catalyst.status), color: catalystStatusColor(catalyst.status))
                Badge(text: CatalystLabels.level(catalyst.importance), color: catalystImportanceColor(catalyst.importance))
            }
            if !catalyst.description.isEmpty {
                Text(catalyst.description)
                    .operatorBody(color: .textSecondary)
            }
            StatusRow(label: "Date", value: shortDateString(catalyst.date), color: catalystDateColor(catalyst))
            StatusRow(label: "Confidence", value: CatalystLabels.level(catalyst.confidence), color: .textSecondary)
            StatusRow(label: "Source", value: CatalystLabels.source(catalyst.source), color: .textSecondary)
            if let linked = catalyst.linkedEntityType {
                StatusRow(label: "Linked", value: [linked, catalyst.linkedEntityId].compactMap { $0 }.joined(separator: " / "), color: .textSecondary)
            }
            HStack(spacing: 8) {
                SmallActionButton(title: "Edit", icon: "square.and.pencil", color: .accent, action: onEdit)
                SmallActionButton(title: "Detail", icon: "doc.text.magnifyingglass", color: .positive, action: onOpen)
                SmallActionButton(title: "Done", icon: "checkmark.circle", color: .positive, action: onComplete)
                SmallActionButton(title: "Archive", icon: "archivebox", color: .negative, action: onArchive)
            }
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct CatalystSummaryCard: View {
    let summary: CatalystSummaryResponse

    var body: some View {
        OperatorCard(title: "Calendar Pressure", icon: "chart.bar") {
            StatusRow(label: "Catalysts this week", value: "\(summary.thisWeekCount)", color: .accent)
            StatusRow(label: "Catalysts next week", value: "\(summary.nextWeekCount)", color: .textPrimary)
            StatusRow(label: "High importance count", value: "\(summary.highImportanceCount)", color: .warning)
            StatusRow(label: "Overdue reviews", value: "\(summary.overdueCount)", color: summary.overdueCount > 0 ? .warning : .textSecondary)
            BriefSection(title: "Portfolio positions with upcoming catalysts") {
                if summary.portfolioCatalysts.isEmpty {
                    Text("No portfolio-linked catalysts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.portfolioCatalysts) { item in
                        BulletLine(text: "\(item.ticker ?? "-") - \(item.title) - \(shortDateString(item.date))")
                    }
                }
            }
            BriefSection(title: "Alpha candidates with upcoming catalysts") {
                if summary.alphaCatalysts.isEmpty {
                    Text("No Alpha-linked catalysts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.alphaCatalysts) { item in
                        BulletLine(text: "\(item.ticker) - \(item.readinessTier.replacingOccurrences(of: "_", with: " ").capitalized)")
                    }
                }
            }
            BriefSection(title: "Overdue / missed catalysts") {
                if summary.overdueCatalysts.isEmpty {
                    Text("No overdue catalysts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.overdueCatalysts) { item in
                        BulletLine(text: "\(item.ticker ?? "-") - \(item.title) - \(shortDateString(item.date))")
                    }
                }
            }
            BriefSection(title: "Missing catalyst dates in active theses") {
                if summary.missingThesisDates.isEmpty {
                    Text("No missing thesis catalyst dates").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.missingThesisDates, id: \.self) { ticker in
                        BulletLine(text: ticker)
                    }
                }
            }
        }
    }
}

struct CatalystDetailCard: View {
    let catalyst: Catalyst

    var body: some View {
        OperatorCard(title: catalyst.title, icon: "calendar.badge.clock") {
            HStack(spacing: 6) {
                Badge(text: CatalystLabels.type(catalyst.catalystType), color: .accent)
                Badge(text: CatalystLabels.status(catalyst.status), color: catalystStatusColor(catalyst.status))
                Badge(text: CatalystLabels.level(catalyst.importance), color: catalystImportanceColor(catalyst.importance))
            }
            StatusRow(label: "Catalyst id", value: catalyst.catalystId, color: .textSecondary)
            StatusRow(label: "Ticker", value: catalyst.ticker ?? "-", color: .textPrimary)
            StatusRow(label: "Date", value: shortDateString(catalyst.date), color: catalystDateColor(catalyst))
            StatusRow(label: "Date state", value: catalystDateState(catalyst), color: catalystDateColor(catalyst))
            StatusRow(label: "Confidence", value: CatalystLabels.level(catalyst.confidence), color: .textSecondary)
            StatusRow(label: "Source", value: CatalystLabels.source(catalyst.source), color: .textSecondary)
            StatusRow(label: "Linked entity", value: [catalyst.linkedEntityType, catalyst.linkedEntityId].compactMap { $0 }.joined(separator: " / ").nilIfEmpty ?? "-", color: .textSecondary)
            StatusRow(label: "Created", value: shortDateString(catalyst.createdAt), color: .textSecondary)
            StatusRow(label: "Updated", value: shortDateString(catalyst.updatedAt), color: .textSecondary)
            if !catalyst.description.isEmpty {
                BriefSection(title: "Description") {
                    Text(catalyst.description).operatorBody(color: .textPrimary)
                }
            }
        }
    }
}

struct InAppInboxOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "In-App Inbox Only", icon: "bell") {
            Text("Local notifications only. No Apple Push, no WhatsApp send, and no trades are placed. The app cannot bypass backend safety flags.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct NotificationPreferencesOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Preferences Only", icon: "slider.horizontal.3") {
            Text("Controls in-app inbox and digest filtering only. No push sent, no WhatsApp sent, and no trades are placed.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct DiagnosticsOnlyLabelCard: View {
    var body: some View {
        OperatorCard(title: "Diagnostics Only", icon: "stethoscope") {
            Text("Read-only system diagnostics. No trades are placed and no notifications are sent.")
                .operatorBody(color: .textSecondary)
        }
    }
}

struct SystemHealthOverviewCard: View {
    let report: SystemReleaseCheckResponse
    let title: String

    var body: some View {
        OperatorCard(title: title, icon: "checklist.checked") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Status", value: systemStatusLabel(report.overallStatus), color: systemStatusColor(report.overallStatus))
                AlphaMetric(title: "Passed", value: "\(report.checksPassed)", color: .positive)
                AlphaMetric(title: "Warned", value: "\(report.checksWarned)", color: report.checksWarned == 0 ? .textSecondary : .warning)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Failed", value: "\(report.checksFailed)", color: report.checksFailed == 0 ? .textSecondary : .negative)
                AlphaMetric(title: "Total", value: "\(report.checksTotal)", color: .textPrimary)
                AlphaMetric(title: "Mode", value: report.mode.capitalized, color: .accent)
            }
            StatusRow(label: "Generated", value: shortDateString(report.generatedAt), color: .textSecondary)
            if !report.warnings.isEmpty {
                BriefSection(title: "Warnings") {
                    ForEach(report.warnings.prefix(4)) { warning in
                        SystemMessageRow(message: warning, color: .warning)
                    }
                }
            }
            if !report.failures.isEmpty {
                BriefSection(title: "Failures") {
                    ForEach(report.failures.prefix(4)) { failure in
                        SystemMessageRow(message: failure, color: .negative)
                    }
                }
            }
            if !report.recommendedFixes.isEmpty {
                BriefSection(title: "Recommended fixes") {
                    ForEach(report.recommendedFixes.prefix(5), id: \.self) { fix in
                        BulletLine(text: fix)
                    }
                }
            }
        }
    }
}

struct SystemHealthSectionCard: View {
    let name: String
    let results: [SystemCheckResult]

    var body: some View {
        let passed = results.filter { $0.status.uppercased() == "PASS" }.count
        let warned = results.filter { $0.status.uppercased() == "WARN" }.count
        let failed = results.filter { $0.status.uppercased() == "FAIL" }.count
        let status = failed > 0 ? "DEGRADED" : warned > 0 ? "WATCH" : "HEALTHY"

        OperatorCard(title: systemSectionLabel(name), icon: "rectangle.3.group") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Status", value: systemStatusLabel(status), color: systemStatusColor(status))
                AlphaMetric(title: "Passed", value: "\(passed)", color: .positive)
                AlphaMetric(title: "Warned", value: "\(warned)", color: warned == 0 ? .textSecondary : .warning)
            }
            AlphaMetric(title: "Failed", value: "\(failed)", color: failed == 0 ? .textSecondary : .negative)
            ForEach(results.prefix(5)) { result in
                SystemCheckRow(result: result)
            }
            let fixes = results.compactMap(\.fix).filter { !$0.isEmpty }
            if !fixes.isEmpty {
                BriefSection(title: "Fixes") {
                    ForEach(fixes.prefix(3), id: \.self) { fix in
                        BulletLine(text: fix)
                    }
                }
            }
        }
    }
}

struct SystemEnvironmentCard: View {
    let environment: [String: FlexibleJSONValue]

    var body: some View {
        OperatorCard(title: "Environment Summary", icon: "server.rack") {
            if environment.isEmpty {
                Text("No environment summary returned.")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(environment.keys.sorted(), id: \.self) { key in
                    StatusRow(label: key, value: safeEnvironmentValue(environment[key]?.stringValue), color: environmentColor(environment[key]?.stringValue))
                }
            }
        }
    }
}

struct SystemRoutesCard: View {
    let response: SystemRoutesResponse

    var body: some View {
        OperatorCard(title: "Route Registry", icon: "point.3.connected.trianglepath.dotted") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Registered", value: "\(response.count)", color: .textPrimary)
                AlphaMetric(title: "Required", value: "\(response.requiredCount)", color: .accent)
                AlphaMetric(title: "Missing", value: "\(response.missingCriticalRoutes.count)", color: response.missingCriticalRoutes.isEmpty ? .positive : .negative)
            }
            if let error = response.error, !error.isEmpty {
                Text(error).operatorBody(color: .negative)
            }
        }
    }
}

struct SystemRouteRow: View {
    let route: SystemRouteInfo

    var body: some View {
        OperatorCard(title: route.path, icon: route.isMissing ? "exclamationmark.triangle" : "link") {
            HStack(spacing: 6) {
                Badge(text: route.method, color: .accent)
                Badge(text: route.groupLabel, color: .textSecondary)
                Badge(text: route.isMissing ? "Missing" : "Registered", color: route.isMissing ? .negative : .positive)
                if route.critical {
                    Badge(text: "Critical", color: .warning)
                }
            }
        }
    }
}

struct SystemFlagsCard: View {
    let response: SystemFlagsResponse

    private let keys = [
        "LEGACY_NOTIFICATIONS_ENABLED",
        "UNIFIED_NOTIFICATIONS_ENABLED",
        "ALPHA_NOTIFICATIONS_ENABLED",
        "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY",
        "EOD_BRIEF_ENABLED",
        "WEEKLY_REVIEW_ENABLED",
        "NOTIFICATION_CENTER_ENABLED"
    ]

    var body: some View {
        OperatorCard(title: "Safety Flags", icon: "flag") {
            StatusRow(label: "Generated", value: shortDateString(response.generatedAt), color: .textSecondary)
            ForEach(keys, id: \.self) { key in
                let value = response.boolFlag(key)
                StatusRow(label: key, value: flagValue(value), color: flagColor(key: key, value: value))
            }
            BriefSection(title: "Warnings") {
                ForEach(flagWarnings(response), id: \.self) { warning in
                    BulletLine(text: warning)
                }
                if flagWarnings(response).isEmpty {
                    Text("No safety flag warnings detected.")
                        .operatorBody(color: .positive)
                }
            }
        }
    }
}

struct SystemMessageRow: View {
    let message: SystemCheckMessage
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(message.name.replacingOccurrences(of: "_", with: " "))
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(color)
            Text(message.detail)
                .operatorBody(color: .textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct SystemCheckRow: View {
    let result: SystemCheckResult

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Badge(text: result.status.uppercased(), color: checkStatusColor(result.status))
                Text(result.name.replacingOccurrences(of: "_", with: " "))
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(.textPrimary)
                Spacer()
            }
            if !result.detail.isEmpty {
                Text(result.detail).operatorBody(color: .textSecondary)
            }
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

func systemStatusLabel(_ value: String) -> String {
    switch value.uppercased() {
    case "HEALTHY": return "Healthy"
    case "WATCH": return "Watch"
    case "DEGRADED": return "Degraded"
    case "CRITICAL": return "Critical"
    default: return value.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

func systemStatusColor(_ value: String) -> Color {
    switch value.uppercased() {
    case "HEALTHY": return .positive
    case "WATCH": return .warning
    case "DEGRADED", "CRITICAL": return .negative
    default: return .textSecondary
    }
}

func checkStatusColor(_ value: String) -> Color {
    switch value.uppercased() {
    case "PASS": return .positive
    case "WARN": return .warning
    case "FAIL": return .negative
    default: return .textSecondary
    }
}

func systemSectionLabel(_ value: String) -> String {
    switch value {
    case "core": return "Core"
    case "routes": return "Routes"
    case "notification_safety": return "Notification Safety"
    case "data_health": return "Data Health"
    case "brief_safety": return "Brief Safety"
    case "alpha_safety": return "Alpha Safety"
    default: return value.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

func safeEnvironmentValue(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "unknown" }
    if value == "***" { return "configured" }
    return value
}

func environmentColor(_ value: String?) -> Color {
    guard let value else { return .textSecondary }
    if value == "(unset)" { return .warning }
    if value == "***" { return .positive }
    return .textPrimary
}

func flagValue(_ value: Bool?) -> String {
    guard let value else { return "Unknown" }
    return value ? "On" : "Off"
}

func flagColor(key: String, value: Bool?) -> Color {
    guard let value else { return .textSecondary }
    switch key {
    case "LEGACY_NOTIFICATIONS_ENABLED", "ALPHA_NOTIFICATIONS_ENABLED", "EOD_BRIEF_ENABLED", "WEEKLY_REVIEW_ENABLED":
        return value ? .warning : .positive
    case "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY":
        return value ? .positive : .negative
    case "UNIFIED_NOTIFICATIONS_ENABLED", "NOTIFICATION_CENTER_ENABLED":
        return value ? .positive : .warning
    default:
        return value ? .accent : .textSecondary
    }
}

func flagWarnings(_ response: SystemFlagsResponse) -> [String] {
    var warnings: [String] = []
    if response.boolFlag("LEGACY_NOTIFICATIONS_ENABLED") == true {
        warnings.append("Old alert path is on")
    }
    if response.boolFlag("ALPHA_NOTIFICATIONS_ENABLED") == true {
        warnings.append("Real Alpha alerts may send")
    }
    if response.boolFlag("ALPHA_NOTIFICATIONS_DRY_RUN_ONLY") == false {
        warnings.append("Dry-run safety may be off")
    }
    if response.boolFlag("EOD_BRIEF_ENABLED") == true {
        warnings.append("Scheduled message enabled")
    }
    if response.boolFlag("WEEKLY_REVIEW_ENABLED") == true {
        warnings.append("Scheduled message enabled")
    }
    return warnings
}

struct NotificationPreferenceForm: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        BriefSection(title: "Enabled categories") {
            ForEach(vm.notificationPreferenceCategories, id: \.self) { category in
                Toggle(isOn: Binding(
                    get: { vm.preferenceEnabledCategories.contains(category) },
                    set: { enabled in
                        if enabled { vm.preferenceEnabledCategories.insert(category) }
                        else { vm.preferenceEnabledCategories.remove(category) }
                    }
                )) {
                    Text(NotificationLabels.category(category))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.textPrimary)
                }
                .tint(.accent)
            }
        }
        WatchlistChoicePicker(title: "Minimum severity", choices: ["INFO", "WATCH", "WARNING", "CRITICAL"], selection: $vm.preferenceMinimumSeverity, label: NotificationLabels.severity)
        Toggle("Quiet hours enabled", isOn: $vm.preferenceQuietHoursEnabled)
            .tint(.accent)
            .font(.system(size: 12, weight: .semibold))
            .foregroundColor(.textPrimary)
        ManualTextField(title: "Quiet hours start", text: $vm.preferenceQuietHoursStart, placeholder: "22:00")
        ManualTextField(title: "Quiet hours end", text: $vm.preferenceQuietHoursEnd, placeholder: "07:00")
        ManualTextField(title: "Timezone", text: $vm.preferenceTimezone, placeholder: "America/Toronto")
        WatchlistChoicePicker(title: "Digest mode", choices: ["OFF", "DAILY", "MORNING_AND_EOD", "WEEKLY"], selection: $vm.preferenceDigestMode, label: NotificationLabels.digestMode)
        ManualTextField(title: "Max notifications per digest", text: $vm.preferenceMaxNotificationsPerDigest, placeholder: "20", keyboard: .numberPad)
        Toggle("Include read items", isOn: $vm.preferenceIncludeReadItems)
            .tint(.accent)
            .font(.system(size: 12, weight: .semibold))
            .foregroundColor(.textPrimary)
        ManualTextField(title: "Auto archive after days", text: $vm.preferenceAutoArchiveAfterDays, placeholder: "7", keyboard: .numberPad)
        if let prefs = vm.notificationPreferences {
            StatusRow(label: "Updated", value: shortDateString(prefs.updatedAt), color: .textSecondary)
        }
    }
}

struct NotificationCategoryPreferenceCard: View {
    @Binding var override: NotificationCategoryPreference
    let onSave: () -> Void

    var body: some View {
        OperatorCard(title: NotificationLabels.category(override.category), icon: notificationCategoryIcon(override.category)) {
            Toggle("Enabled", isOn: $override.enabled)
                .tint(.accent)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.textPrimary)
            WatchlistChoicePicker(
                title: "Minimum severity",
                choices: ["INFO", "WATCH", "WARNING", "CRITICAL"],
                selection: Binding(
                    get: { override.minimumSeverity ?? "INFO" },
                    set: { override.minimumSeverity = $0 }
                ),
                label: NotificationLabels.severity
            )
            Toggle("Digest only", isOn: $override.digestOnly)
                .tint(.accent)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.textPrimary)
            Toggle("Quiet hours override", isOn: Binding(
                get: { override.quietHoursOverride ?? false },
                set: { override.quietHoursOverride = $0 }
            ))
            .tint(.accent)
            .font(.system(size: 12, weight: .semibold))
            .foregroundColor(.textPrimary)
            StatusRow(label: "Updated", value: shortDateString(override.updatedAt), color: .textSecondary)
            SmallActionButton(title: "Save override", icon: "checkmark.circle", color: .accent, action: onSave)
        }
    }
}

struct NotificationDigestCard: View {
    let digest: NotificationDigestResponse

    var body: some View {
        OperatorCard(title: digest.title, icon: "doc.text") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Mode", value: digestModeSelectorLabel(digest.mode), color: .textPrimary)
                AlphaMetric(title: "Included", value: "\(digest.includedCount)", color: .accent)
                AlphaMetric(title: "Omitted", value: "\(digest.omittedCount)", color: .textSecondary)
            }
            StatusRow(label: "Generated", value: shortDateString(digest.generatedAt), color: .textSecondary)
            BriefSection(title: "By category") {
                if digest.byCategory.isEmpty {
                    Text("No category counts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(digest.byCategory.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                        StatusRow(label: NotificationLabels.category(key), value: "\(value)", color: notificationCategoryColor(key))
                    }
                }
            }
            BriefSection(title: "By severity") {
                if digest.bySeverity.isEmpty {
                    Text("No severity counts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(digest.bySeverity.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                        StatusRow(label: NotificationLabels.severity(key), value: "\(value)", color: notificationSeverityColor(key))
                    }
                }
            }
            DigestNotificationSection(title: "Top critical / warning", items: digest.topCriticalWarning)
            DigestNotificationSection(title: "Top Alpha", items: digest.topAlpha)
            DigestNotificationSection(title: "Top risk", items: digest.topRisk)
            DigestNotificationSection(title: "Research / catalyst / checklist", items: digest.topResearchCatalystChecklist)
        }
    }
}

struct DigestNotificationSection: View {
    let title: String
    let items: [InAppNotification]

    var body: some View {
        BriefSection(title: title) {
            if items.isEmpty {
                Text("No items").operatorBody(color: .textSecondary)
            } else {
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.title)
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(.textPrimary)
                        Text("\(NotificationLabels.category(item.category)) / \(NotificationLabels.severity(item.severity)) / \(shortDateString(item.createdAt))")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
}

struct LocalNotificationSettingsCard: View {
    @ObservedObject var vm: OperatorViewModel

    var body: some View {
        OperatorCard(title: "Local Notification Settings", icon: "iphone.radiowaves.left.and.right") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Permission", value: localPermissionText(vm.localNotificationPermissionStatus), color: localPermissionColor(vm.localNotificationPermissionStatus))
                AlphaMetric(title: "Local", value: vm.localNotificationsEnabled ? "On" : "Off", color: vm.localNotificationsEnabled ? .positive : .textSecondary)
                AlphaMetric(title: "Unread", value: "\(vm.notificationSummary?.unreadCount ?? 0)", color: .accent)
            }
            Text("Local notifications are scheduled only after this app refreshes the in-app inbox. No APNs, no device token, no backend send.")
                .operatorBody(color: .textSecondary)
            HStack(spacing: 8) {
                SmallActionButton(title: "Enable", icon: "bell.badge", color: .positive) {
                    Task { await vm.requestLocalNotificationPermission() }
                }
                SmallActionButton(title: "Disable", icon: "bell.slash", color: .textSecondary) {
                    vm.disableLocalNotifications()
                }
                SmallActionButton(title: "Test", icon: "paperplane", color: .accent) {
                    Task { await vm.sendTestLocalNotification() }
                }
            }
            LocalNotificationToggle(title: "Notify for critical only", isOn: $vm.notifyCriticalOnly)
            LocalNotificationToggle(title: "Notify for critical + warning", isOn: $vm.notifyCriticalWarning)
            LocalNotificationToggle(title: "Notify for unread Alpha notifications", isOn: $vm.notifyUnreadAlpha)
            LocalNotificationToggle(title: "Notify for daily brief available", isOn: $vm.notifyDailyBriefAvailable)
            LocalNotificationToggle(title: "Notify for research workflow due", isOn: $vm.notifyResearchWorkflowDue)
            LocalNotificationToggle(title: "Notify for catalyst due soon", isOn: $vm.notifyCatalystDueSoon)
            SmallActionButton(title: "Save settings", icon: "checkmark.circle", color: .accent) {
                vm.saveLocalNotificationSettings()
            }
        }
    }
}

struct LocalNotificationToggle: View {
    let title: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.textPrimary)
        }
        .tint(.accent)
        .padding(.vertical, 2)
    }
}

struct NotificationFilterPicker: View {
    @Binding var selection: String
    private let filters = [
        ("all", "All"),
        ("unread", "Unread"),
        ("critical_warning", "Critical / warning"),
        ("alpha", "Alpha"),
        ("portfolio", "Portfolio"),
        ("risk", "Risk"),
        ("research", "Research"),
        ("catalyst", "Catalyst"),
        ("checklist", "Checklist"),
        ("system", "System")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Filter")
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(filters, id: \.0) { filter in
                        Button {
                            selection = filter.0
                            HapticManager.selection()
                        } label: {
                            Text(filter.1)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(selection == filter.0 ? .black : .textPrimary)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(selection == filter.0 ? Color.accent : Color.surfaceElevated)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(selection == filter.0 ? Color.clear : Color.border, lineWidth: 0.5))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

struct NotificationInboxCard: View {
    let notification: InAppNotification
    let onOpen: () -> Void
    let onRead: () -> Void
    let onUnread: () -> Void
    let onDismiss: () -> Void
    let onArchive: () -> Void

    var body: some View {
        OperatorCard(title: notification.title.isEmpty ? "Notification" : notification.title, icon: notificationIcon(notification)) {
            HStack(spacing: 6) {
                Badge(text: NotificationLabels.category(notification.category), color: notificationCategoryColor(notification.category))
                Badge(text: NotificationLabels.severity(notification.severity), color: notificationSeverityColor(notification.severity))
                Badge(text: NotificationLabels.status(notification.status), color: notificationStatusColor(notification.status))
                if notification.status == "UNREAD" {
                    Badge(text: "Unread", color: .accent)
                }
            }
            Text(notification.shortBody.isEmpty ? "No body provided" : notification.shortBody)
                .operatorBody(color: notification.status == "UNREAD" ? .textPrimary : .textSecondary)
            StatusRow(label: "Ticker", value: notification.ticker ?? "-", color: .textSecondary)
            StatusRow(label: "Linked", value: linkedNotificationText(notification), color: .textSecondary)
            StatusRow(label: "Created", value: shortDateString(notification.createdAt), color: .textSecondary)
            StatusRow(label: "Expires", value: shortDateString(notification.expiresAt), color: .textSecondary)
            StatusRow(label: "Source", value: notification.source.replacingOccurrences(of: "_", with: " ").capitalized, color: .textSecondary)
            HStack(spacing: 8) {
                SmallActionButton(title: "Detail", icon: "doc.text.magnifyingglass", color: .positive, action: onOpen)
                SmallActionButton(title: notification.status == "UNREAD" ? "Read" : "Unread", icon: notification.status == "UNREAD" ? "checkmark.circle" : "circle", color: .accent, action: notification.status == "UNREAD" ? onRead : onUnread)
                SmallActionButton(title: "Dismiss", icon: "xmark.circle", color: .warning, action: onDismiss)
                SmallActionButton(title: "Archive", icon: "archivebox", color: .negative, action: onArchive)
            }
        }
    }
}

struct NotificationSummaryCard: View {
    let summary: NotificationSummaryResponse
    let onOpen: (InAppNotification) -> Void

    var body: some View {
        OperatorCard(title: "Inbox State", icon: "chart.bar.doc.horizontal") {
            StatusRow(label: "Unread count", value: "\(summary.unreadCount)", color: .accent)
            StatusRow(label: "Critical count", value: "\(summary.criticalCount)", color: summary.criticalCount > 0 ? .negative : .textSecondary)
            StatusRow(label: "Warning count", value: "\(summary.warningCount)", color: summary.warningCount > 0 ? .warning : .textSecondary)
            StatusRow(label: "Stale notifications", value: "\(summary.staleNotificationCount)", color: summary.staleNotificationCount > 0 ? .warning : .textSecondary)
            StatusRow(label: "Visible unread", value: "\(summary.visibleUnreadCount)", color: .accent)
            StatusRow(label: "Filtered by preferences", value: "\(summary.filteredCount)", color: summary.filteredCount > 0 ? .warning : .textSecondary)
            StatusRow(label: "Suppressed by preferences", value: "\(summary.suppressedByPreferencesCount)", color: summary.suppressedByPreferencesCount > 0 ? .warning : .textSecondary)
            StatusRow(label: "Quiet hours", value: summary.quietHoursActive ? "Active" : "Inactive", color: summary.quietHoursActive ? .warning : .textSecondary)
            StatusRow(label: "Generated", value: shortDateString(summary.generatedAt), color: .textSecondary)
            if summary.suppressedByPreferencesCount > 0 {
                Text("Some items are hidden by your notification preferences.")
                    .operatorBody(color: .warning)
            }
            BriefSection(title: "By category") {
                if summary.byCategory.isEmpty {
                    Text("No unread category counts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.byCategory.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                        StatusRow(label: NotificationLabels.category(key), value: "\(value)", color: notificationCategoryColor(key))
                    }
                }
            }
            BriefSection(title: "By severity") {
                if summary.bySeverity.isEmpty {
                    Text("No unread severity counts").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.bySeverity.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                        StatusRow(label: NotificationLabels.severity(key), value: "\(value)", color: notificationSeverityColor(key))
                    }
                }
            }
            BriefSection(title: "Top notifications") {
                if summary.topNotifications.isEmpty {
                    Text("No top notifications").operatorBody(color: .textSecondary)
                } else {
                    ForEach(summary.topNotifications) { item in
                        Button {
                            onOpen(item)
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(item.title)
                                    .font(.system(size: 12, weight: .bold))
                                    .foregroundColor(.textPrimary)
                                Text("\(NotificationLabels.category(item.category)) / \(NotificationLabels.severity(item.severity)) / \(shortDateString(item.createdAt))")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundColor(.textSecondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

struct NotificationDetailCard: View {
    let notification: InAppNotification

    var body: some View {
        OperatorCard(title: notification.title.isEmpty ? "Notification Detail" : notification.title, icon: notificationIcon(notification)) {
            HStack(spacing: 6) {
                Badge(text: NotificationLabels.category(notification.category), color: notificationCategoryColor(notification.category))
                Badge(text: NotificationLabels.severity(notification.severity), color: notificationSeverityColor(notification.severity))
                Badge(text: NotificationLabels.status(notification.status), color: notificationStatusColor(notification.status))
            }
            StatusRow(label: "Notification id", value: notification.notificationId, color: .textSecondary)
            StatusRow(label: "Ticker", value: notification.ticker ?? "-", color: .textPrimary)
            StatusRow(label: "Linked entity", value: linkedNotificationText(notification), color: .textSecondary)
            StatusRow(label: "Source", value: notification.source.replacingOccurrences(of: "_", with: " ").capitalized, color: .textSecondary)
            StatusRow(label: "Created", value: shortDateString(notification.createdAt), color: .textSecondary)
            StatusRow(label: "Updated", value: shortDateString(notification.updatedAt), color: .textSecondary)
            StatusRow(label: "Expires", value: shortDateString(notification.expiresAt), color: .textSecondary)
            StatusRow(label: "Action path", value: notification.actionURL ?? "-", color: .accent)
            BriefSection(title: "Full body") {
                Text(notification.body.isEmpty ? "No body provided" : notification.body)
                    .operatorBody(color: .textPrimary)
            }
        }
    }
}

struct WatchlistChoicePicker: View {
    let title: String
    let choices: [String]
    @Binding var selection: String
    let label: (String) -> String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(choices, id: \.self) { choice in
                        Button {
                            selection = choice
                            HapticManager.selection()
                        } label: {
                            Text(label(choice))
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(selection == choice ? .black : .textPrimary)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(selection == choice ? Color.accent : Color.surfaceElevated)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(selection == choice ? Color.clear : Color.border, lineWidth: 0.5))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

struct ResearchWatchlistItemCard: View {
    let item: ResearchWatchlistItem
    let onEdit: () -> Void
    let onOpen: () -> Void
    let onArchive: () -> Void

    var body: some View {
        OperatorCard(title: item.ticker, icon: watchlistIcon(item)) {
            HStack(spacing: 6) {
                Badge(text: ResearchWatchlistLabels.assetType(item.assetType), color: .accent)
                Badge(text: ResearchWatchlistLabels.category(item.category), color: .textSecondary)
                Badge(text: ResearchWatchlistLabels.status(item.status), color: watchStatusColor(item.status))
                Badge(text: ResearchWatchlistLabels.priority(item.priority), color: watchPriorityColor(item.priority))
            }
            StatusRow(label: "Name", value: item.name ?? "-", color: .textPrimary)
            StatusRow(label: "Reason", value: item.reason.isEmpty ? "-" : item.reason, color: .textSecondary)
            StatusRow(label: "Linked Alpha", value: item.linkedAlphaCandidateId.map(String.init) ?? "-", color: .textSecondary)
            StatusRow(label: "Linked thesis", value: item.linkedThesisId.map(String.init) ?? "-", color: .textSecondary)
            StatusRow(label: "Review", value: watchReviewState(item), color: watchReviewColor(item))
            StatusRow(label: "Next review", value: shortDateString(item.nextReviewAt), color: .textSecondary)
            StatusRow(label: "Updated", value: shortDateString(item.updatedAt), color: .textSecondary)
            HStack(spacing: 8) {
                SmallActionButton(title: "Edit", icon: "square.and.pencil", color: .accent, action: onEdit)
                SmallActionButton(title: "Detail", icon: "doc.text.magnifyingglass", color: .positive, action: onOpen)
                SmallActionButton(title: "Archive", icon: "archivebox", color: .negative, action: onArchive)
            }
        }
    }
}

struct ResearchWatchlistSuggestionCard: View {
    let suggestion: ResearchWatchlistSuggestion
    let onUse: () -> Void

    var body: some View {
        OperatorCard(title: suggestion.ticker ?? ResearchWatchlistLabels.source(suggestion.source), icon: "lightbulb") {
            HStack(spacing: 6) {
                Badge(text: ResearchWatchlistLabels.source(suggestion.source), color: .accent)
                Badge(text: ResearchWatchlistLabels.priority(suggestion.priority), color: watchPriorityColor(suggestion.priority))
                Badge(text: ResearchWatchlistLabels.category(suggestion.category), color: .textSecondary)
            }
            StatusRow(label: "Suggested category", value: ResearchWatchlistLabels.category(suggestion.category), color: .textPrimary)
            StatusRow(label: "Source", value: ResearchWatchlistLabels.source(suggestion.source), color: .textSecondary)
            Text(suggestion.reason.isEmpty ? "No explanation provided" : suggestion.reason)
                .operatorBody(color: .textPrimary)
            SmallActionButton(title: "Use in form", icon: "arrow.turn.down.right", color: .accent, action: onUse)
        }
    }
}

struct SuggestionSourceCounts: View {
    let response: ResearchWatchlistSuggestionsResponse

    var body: some View {
        OperatorCard(title: "Sources", icon: "square.grid.2x2") {
            StatusRow(label: "Alpha candidates", value: "\(response.alphaCandidates.count)", color: .textPrimary)
            StatusRow(label: "Alert readiness", value: "\(response.alertGate.count)", color: .textPrimary)
            StatusRow(label: "Replay missed winners", value: "\(response.missedWinners.count)", color: .textPrimary)
            StatusRow(label: "Validation trends", value: "\(response.validationTrends.count)", color: .textPrimary)
            StatusRow(label: "Thesis warnings", value: "\(response.thesisWarnings.count)", color: .textPrimary)
            StatusRow(label: "Scorecard gaps", value: "\(response.scorecardGaps.count)", color: .textPrimary)
        }
    }
}

struct ResearchWatchlistDetailCard: View {
    let detail: ResearchWatchlistDetail

    var body: some View {
        OperatorCard(title: "\(detail.item.ticker) Detail", icon: "doc.text.magnifyingglass") {
            ResearchWatchlistItemSummary(item: detail.item)
            BriefSection(title: "Linked context") {
                StatusRow(label: "Alpha candidate", value: detail.item.linkedAlphaCandidateId.map(String.init) ?? "-", color: .textSecondary)
                StatusRow(label: "Thesis", value: detail.item.linkedThesisId.map(String.init) ?? "-", color: .textSecondary)
            }
            BriefSection(title: "Review status") {
                StatusRow(label: "State", value: watchReviewState(detail.item), color: watchReviewColor(detail.item))
                StatusRow(label: "Next review", value: shortDateString(detail.item.nextReviewAt), color: .textSecondary)
                StatusRow(label: "Updated", value: shortDateString(detail.item.updatedAt), color: .textSecondary)
            }
            BriefSection(title: "Notes") {
                if detail.notes.isEmpty {
                    Text("No notes yet").operatorBody(color: .textSecondary)
                } else {
                    ForEach(detail.notes) { note in
                        ResearchWatchlistNoteRow(note: note)
                    }
                }
            }
        }
    }
}

struct ResearchWatchlistItemSummary: View {
    let item: ResearchWatchlistItem

    var body: some View {
        HStack(spacing: 6) {
            Badge(text: ResearchWatchlistLabels.assetType(item.assetType), color: .accent)
            Badge(text: ResearchWatchlistLabels.category(item.category), color: .textSecondary)
            Badge(text: ResearchWatchlistLabels.status(item.status), color: watchStatusColor(item.status))
            Badge(text: ResearchWatchlistLabels.priority(item.priority), color: watchPriorityColor(item.priority))
        }
        StatusRow(label: "Name", value: item.name ?? "-", color: .textPrimary)
        StatusRow(label: "Reason", value: item.reason.isEmpty ? "-" : item.reason, color: .textSecondary)
        StatusRow(label: "Created", value: shortDateString(item.createdAt), color: .textSecondary)
    }
}

struct ResearchWatchlistNoteRow: View {
    let note: ResearchWatchlistNote

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Badge(text: ResearchWatchlistLabels.noteType(note.noteType), color: .accent)
                Text(shortDateString(note.createdAt))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.textSecondary)
            }
            Text(note.text)
                .operatorBody(color: .textPrimary)
            if !note.tags.isEmpty {
                Text(note.tags.joined(separator: ", "))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.textSecondary)
            }
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SmallActionButton: View {
    let title: String
    let icon: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 11, weight: .bold))
                Text(title)
                    .font(.system(size: 11, weight: .bold))
            }
            .foregroundColor(color)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background(Color.surfaceElevated)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

struct BriefCompactCard: View {
    let brief: String
    let lastRefresh: String?
    let onCopy: () -> Void

    var body: some View {
        OperatorCard(title: "Compact WhatsApp Brief", icon: "message") {
            Text(brief.isEmpty ? "No compact brief text" : brief)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .textSelection(.enabled)
            StatusRow(label: "Last refreshed", value: lastRefresh ?? "Unknown", color: .textSecondary)
            ActionButton(title: "Copy compact brief", icon: "doc.on.doc", color: .accent) {
                onCopy()
            }
        }
    }
}

struct BriefDetailedCard: View {
    let brief: DailyBriefDetailedResponse

    var body: some View {
        OperatorCard(title: "Detailed Brief", icon: "list.bullet.rectangle") {
            StatusRow(label: "Generated", value: brief.generatedAt ?? "Unknown", color: .textSecondary)
            BriefSection(title: "Portfolio summary") {
                StatusRow(label: "Positions", value: "\(brief.portfolioTruth.positionCount)", color: .textPrimary)
                StatusRow(label: "Total value", value: money(brief.portfolioTruth.totalValue), color: .textPrimary)
                StatusRow(label: "Cash", value: money(brief.portfolioTruth.cash), color: .accent)
                StatusRow(label: "Unrealized P&L", value: "\(money(brief.portfolioTruth.unrealizedPnl)) / \(plainPercentFromWhole(brief.portfolioTruth.unrealizedPnlPct))", color: pnlColor(brief.portfolioTruth.unrealizedPnl))
            }
            BriefBulletSection(title: "Overnight changes", rows: brief.overnightChanges, emptyText: "No overnight warning changes")
            BriefSection(title: "Alpha readiness highlights") {
                if brief.alphaHighlights.isEmpty {
                    Text("No alpha readiness highlights").operatorBody(color: .textSecondary)
                } else {
                    ForEach(brief.alphaHighlights) { item in
                        StatusRow(label: item.ticker, value: "\(AlphaAlertReadiness.label(for: item.readinessTier)) / \(score(item.alphaScore))", color: readinessColor(item.readinessTier))
                    }
                }
            }
            BriefSection(title: "Dry-run notification highlights") {
                if brief.dryrunHighlights.isEmpty {
                    Text("No dry-run highlights").operatorBody(color: .textSecondary)
                } else {
                    ForEach(brief.dryrunHighlights) { item in
                        SummaryLine(title: "\(item.ticker) \(item.status)", text: item.messagePreview)
                    }
                }
            }
            BriefSection(title: "QC suppression summary") {
                StatusRow(label: "Evaluated", value: "\(brief.qcSuppressionSummary.totalEvaluated)", color: .textPrimary)
                StatusRow(label: "Suppressed", value: "\(brief.qcSuppressionSummary.suppressedCount)", color: .warning)
                StatusRow(label: "Allowed", value: "\(brief.qcSuppressionSummary.allowedCount)", color: .positive)
                StatusRow(label: "Average QC", value: score(brief.qcSuppressionSummary.avgQcScore), color: .accent)
            }
            BriefSection(title: "Market regime") {
                StatusRow(label: "Overall", value: MarketRegimeLabels.label(brief.marketRegime.overallRegime), color: regimeColor(brief.marketRegime.overallRegime))
                StatusRow(label: "Score", value: score(brief.marketRegime.regimeScore), color: regimeScoreColor(brief.marketRegime.regimeScore))
                BriefBulletSection(title: "Warnings", rows: brief.marketRegime.warnings, emptyText: "No regime warnings")
            }
            BriefBulletSection(title: "Portfolio risk warnings", rows: brief.riskWarnings, emptyText: "No portfolio risk warnings")
            BriefSection(title: "Stress test summary") {
                if brief.stressWorstCase.available {
                    StatusRow(label: "Worst scenario", value: PortfolioStressLabels.scenario(brief.stressWorstCase.worstScenario), color: .warning)
                    StatusRow(label: "Worst loss", value: percentFromWhole(brief.stressWorstCase.worstLossPct), color: .warning)
                    StatusRow(label: "Average loss", value: percentFromWhole(brief.stressWorstCase.avgLossPct), color: .textSecondary)
                } else {
                    Text("No stress test available").operatorBody(color: .textSecondary)
                }
            }
            BriefSection(title: "Decision checklists due") {
                if brief.checklistsDue.isEmpty {
                    Text("No checklist items due").operatorBody(color: .textSecondary)
                } else {
                    ForEach(brief.checklistsDue) { item in
                        StatusRow(label: "\(item.ticker) \(item.decisionType)", value: item.checklistStatus, color: .warning)
                    }
                }
            }
            BriefSection(title: "Thesis reviews due") {
                StatusRow(label: "Overdue", value: "\(brief.thesisReviewsDue.overdueCount)", color: brief.thesisReviewsDue.overdueCount > 0 ? .warning : .positive)
                BriefBulletSection(title: "Missing thesis", rows: brief.thesisReviewsDue.missingThesis, emptyText: "No missing thesis warnings")
                BriefBulletSection(title: "Missing exit plan", rows: brief.thesisReviewsDue.missingExitPlan, emptyText: "No missing exit-plan warnings")
            }
            BriefBulletSection(title: "Strategy warnings", rows: brief.scorecardWarnings, emptyText: "No strategy warnings")
            BriefSection(title: "Planner drift") {
                if brief.plannerSummary.available {
                    StatusRow(label: "Urgency", value: PlannerLabels.urgency(brief.plannerSummary.rebalanceUrgency), color: plannerUrgencyColor(brief.plannerSummary.rebalanceUrgency))
                    ForEach(brief.plannerSummary.priorityAreas.prefix(3)) { area in
                        StatusRow(label: PlannerLabels.bucket(area.bucket), value: "\(area.action) \(signedNumber(area.driftPct))", color: allocationStatusColor(area.driftPct))
                    }
                } else {
                    Text("No planner snapshot available").operatorBody(color: .textSecondary)
                }
            }
            BriefSection(title: "Cash/TFSA notes") {
                StatusRow(label: "Cash", value: money(brief.cashTfsaNotes.cash), color: .accent)
                StatusRow(label: "TFSA room", value: money(brief.cashTfsaNotes.tfsaRoom), color: .textPrimary)
            }
            BriefBulletSection(title: "Key actions", rows: brief.keyActions, emptyText: "No key actions")
        }
    }
}

struct BriefDebugCard: View {
    let debug: DailyBriefDebugResponse

    var body: some View {
        OperatorCard(title: "Debug Structure", icon: "ladybug") {
            BriefDisclosure(title: "Data sources") {
                StatusRow(label: "Portfolio", value: debug.dataSources.portfolioAvailable ? "Available" : "Missing", color: debug.dataSources.portfolioAvailable ? .positive : .warning)
                StatusRow(label: "Alpha candidates", value: "\(debug.dataSources.alphaCandidatesCount)", color: .textPrimary)
                StatusRow(label: "Dry runs", value: "\(debug.dataSources.dryRunsCount)", color: .textPrimary)
                StatusRow(label: "Regime", value: debug.dataSources.regimeAvailable ? "Available" : "Missing", color: debug.dataSources.regimeAvailable ? .positive : .warning)
                StatusRow(label: "Stress", value: debug.dataSources.stressRunAvailable ? "Available" : "Missing", color: debug.dataSources.stressRunAvailable ? .positive : .warning)
                StatusRow(label: "Planner", value: debug.dataSources.plannerSnapshotAvailable ? "Available" : "Missing", color: debug.dataSources.plannerSnapshotAvailable ? .positive : .warning)
                StatusRow(label: "Pending checklists", value: "\(debug.dataSources.pendingChecklistsCount)", color: .textPrimary)
                StatusRow(label: "Scorecard computed", value: debug.dataSources.scorecardComputedAt ?? "Unknown", color: .textSecondary)
            }
            BriefDisclosure(title: "Raw section counts") {
                StatusRow(label: "Overnight", value: "\(debug.detailed.overnightChanges.count)", color: .textPrimary)
                StatusRow(label: "Alpha", value: "\(debug.detailed.alphaHighlights.count)", color: .textPrimary)
                StatusRow(label: "Dry runs", value: "\(debug.detailed.dryrunHighlights.count)", color: .textPrimary)
                StatusRow(label: "Risk warnings", value: "\(debug.detailed.riskWarnings.count)", color: .textPrimary)
                StatusRow(label: "Key actions", value: "\(debug.detailed.keyActions.count)", color: .textPrimary)
            }
        }
    }
}

struct BriefSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            content
        }
    }
}

struct BriefBulletSection: View {
    let title: String
    let rows: [String]
    let emptyText: String

    var body: some View {
        BriefSection(title: title) {
            if rows.isEmpty {
                Text(emptyText).operatorBody(color: .textSecondary)
            } else {
                ForEach(rows, id: \.self) { row in
                    BulletLine(text: row)
                }
            }
        }
    }
}

struct BriefDisclosure<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 8) {
                content
            }
            .padding(.top, 8)
        } label: {
            Text(title)
                .font(.system(size: 13, weight: .bold))
                .foregroundColor(.textPrimary)
        }
        .tint(.accent)
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct RiskWarningRows: View {
    let title: String
    let warnings: [String]

    var body: some View {
        if warnings.isEmpty {
            StatusRow(label: title, value: "OK", color: .positive)
        } else {
            VStack(alignment: .leading, spacing: 7) {
                Text(title)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.textSecondary)
                    .textCase(.uppercase)
                ForEach(warnings.prefix(4), id: \.self) { warning in
                    BulletLine(text: warning)
                }
            }
        }
    }
}

struct TickerRiskCard: View {
    let row: TickerRiskRow
    let policy: RiskPolicy
    let onSizeCheck: () -> Void

    var body: some View {
        OperatorCard(title: row.ticker, icon: row.riskFlags.isEmpty ? "shield" : "exclamationmark.shield") {
            HStack(spacing: 6) {
                Badge(text: row.volatilityTier, color: row.isSpeculative ? .warning : .positive)
                Badge(text: row.isSpeculative ? "SPECULATIVE" : "CORE", color: row.isSpeculative ? .warning : .accent)
                Badge(text: row.isCad ? "CAD" : "USD", color: .textSecondary)
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Concentration", value: plainPercentFromWhole(row.concentrationPct), color: concentrationColor(row.concentrationPct))
                AlphaMetric(title: "Max size", value: plainPercentFromWhole(policy.maxSinglePositionPct), color: .textSecondary)
                AlphaMetric(title: "Max loss", value: money(row.maxExpectedLoss(policy: policy)), color: .warning)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Sizing", value: row.suggestedSizingTier(policy: policy).replacingOccurrences(of: "_", with: " "), color: sizingTierColor(row.suggestedSizingTier(policy: policy)))
                AlphaMetric(title: "Theme", value: row.theme, color: .accent)
                AlphaMetric(title: "Value", value: money(row.marketValue), color: .textPrimary)
            }
            if row.riskFlags.isEmpty {
                SummaryLine(title: "Warnings", text: "No ticker-level warning flags")
            } else {
                ForEach(row.riskFlags.prefix(4), id: \.self) { flag in
                    Badge(text: flag.replacingOccurrences(of: "_", with: " "), color: .warning)
                }
            }
            ActionButton(title: "Run size check", icon: "ruler", color: .accent) {
                onSizeCheck()
            }
        }
    }
}

struct SizeCheckCard: View {
    let sizeCheck: DecisionSizeCheckResponse

    var body: some View {
        OperatorCard(title: "\(sizeCheck.ticker) \(sizeCheck.decisionType)", icon: "ruler") {
            HStack(spacing: 6) {
                Badge(text: sizeCheck.sizingGuidance.sizingTier.replacingOccurrences(of: "_", with: " "), color: sizingTierColor(sizeCheck.sizingGuidance.sizingTier))
                Badge(text: "GUIDANCE ONLY", color: .warning)
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Max size", value: money(sizeCheck.sizingGuidance.maxPositionSizeCad), color: .textPrimary)
                AlphaMetric(title: "Suggested", value: money(sizeCheck.sizingGuidance.suggestedSizeCad), color: .accent)
                AlphaMetric(title: "Risk %", value: plainPercentFromWhole(sizeCheck.sizingGuidance.maxPortfolioRiskPct), color: .warning)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Current", value: plainPercentFromWhole(sizeCheck.sizingGuidance.currentConcentrationPct), color: concentrationColor(sizeCheck.sizingGuidance.currentConcentrationPct))
                AlphaMetric(title: "Budget", value: money(sizeCheck.sizingGuidance.remainingBudgetCad), color: .textSecondary)
                AlphaMetric(title: "Max loss", value: money(sizeCheck.sizingGuidance.maxLossAmountCad), color: .warning)
            }

            StatusRow(label: "Stop distance", value: plainPercentFromWhole(sizeCheck.sizingGuidance.stopDistancePct), color: .textSecondary)
            SummaryLine(title: "Risk/reward notes", text: sizeCheck.sizingGuidance.riskRewardNote)
            if sizeCheck.sizingGuidance.haircutApplied {
                SummaryLine(title: "Haircut", text: sizeCheck.sizingGuidance.haircutReason ?? "Size haircut applied")
            }
            RiskWarningRows(title: "Blockers", warnings: sizeCheck.blockers)
            RiskWarningRows(title: "Warnings", warnings: sizeCheck.warnings)

            if sizeCheck.checklistItemSuggestions.isEmpty {
                SummaryLine(title: "Checklist suggestions", text: "No checklist suggestions")
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Checklist suggestions")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(sizeCheck.checklistItemSuggestions.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                        StatusRow(label: key.replacingOccurrences(of: "_", with: " "), value: suggestionLabel(value), color: suggestionColor(value))
                    }
                }
            }
            StatusRow(label: "Checked", value: sizeCheck.checkedAt ?? "Unknown", color: .textSecondary)
        }
    }
}

struct DecisionChecklistCard: View {
    let checklist: DecisionChecklist
    let isSelected: Bool
    let onOpen: () -> Void

    var body: some View {
        OperatorCard(title: "\(checklist.ticker) \(checklist.decisionType)", icon: isSelected ? "checklist.checked" : "checklist") {
            HStack(spacing: 6) {
                Badge(text: DecisionChecklistLabels.status(checklist.checklistStatus, readiness: checklist.readiness), color: decisionStatusColor(checklist.checklistStatus, readiness: checklist.readiness))
                Badge(text: checklist.readiness.replacingOccurrences(of: "_", with: " "), color: readinessDecisionColor(checklist.readiness))
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Complete", value: plainPercentFromWhole(checklist.checklistCompletion), color: .accent)
                AlphaMetric(title: "Blocking", value: "\(checklist.blockingItems)", color: checklist.blockingItems == 0 ? .positive : .warning)
                AlphaMetric(title: "Created", value: shortDateString(checklist.createdAt), color: .textSecondary)
            }
            StatusRow(label: "Reviewed", value: checklist.reviewedAt ?? "Not reviewed", color: checklist.reviewedAt == nil ? .textSecondary : .positive)
            ActionButton(title: "Open checklist", icon: "chevron.right.circle", color: .accent) {
                onOpen()
            }
        }
    }
}

struct DecisionChecklistDetailCard: View {
    let checklist: DecisionChecklist
    @Binding var notes: [String: String]
    let actionInProgress: Bool
    let onSetItem: (DecisionChecklistItem, Bool?) -> Void
    let sizeCheck: DecisionSizeCheckResponse?
    let onRunSizeCheck: () -> Void
    let onApprove: () -> Void
    let onReject: () -> Void

    var body: some View {
        OperatorCard(title: "Checklist Detail", icon: "list.bullet.clipboard") {
            HStack(spacing: 6) {
                Badge(text: checklist.checklistStatus, color: decisionStatusColor(checklist.checklistStatus, readiness: checklist.readiness))
                Badge(text: checklist.readiness.replacingOccurrences(of: "_", with: " "), color: readinessDecisionColor(checklist.readiness))
                Spacer()
            }

            StatusRow(label: "ID", value: checklist.checklistId, color: .textSecondary)
            StatusRow(label: "Linked thesis", value: checklist.linkedThesisId.map(String.init) ?? "None", color: .textSecondary)
            StatusRow(label: "Linked alpha", value: checklist.linkedAlphaCandidateId ?? "None", color: .textSecondary)
            StatusRow(label: "Completion", value: plainPercentFromWhole(checklist.checklistCompletion), color: .accent)
            StatusRow(label: "Blocking items", value: "\(checklist.blockingItems)", color: checklist.blockingItems == 0 ? .positive : .warning)
            ActionButton(title: "Run size check", icon: "ruler", color: .accent) {
                onRunSizeCheck()
            }
            .disabled(actionInProgress)

            if let sizeCheck, sizeCheck.ticker == checklist.ticker && sizeCheck.decisionType == checklist.decisionType {
                SizeCheckInlineCard(sizeCheck: sizeCheck)
            }

            if !blockingItems.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Blocking items")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(blockingItems.prefix(5)) { item in
                        BulletLine(text: item.label)
                    }
                }
            }

            Divider().background(Color.border)

            ForEach(checklist.items) { item in
                DecisionChecklistItemRow(
                    item: item,
                    note: Binding(
                        get: { notes[item.itemKey] ?? item.note },
                        set: { notes[item.itemKey] = $0 }
                    ),
                    actionInProgress: actionInProgress,
                    onSet: { passed in onSetItem(item, passed) }
                )
            }

            if checklist.auditTrail.isEmpty {
                SummaryLine(title: "Audit trail", text: "Not included by this endpoint")
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Audit trail")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(checklist.auditTrail.prefix(5)) { entry in
                        StatusRow(label: entry.action, value: entry.performedAt ?? "Unknown", color: .textSecondary)
                    }
                }
            }

            Divider().background(Color.border)
            SummaryLine(title: "Safety", text: "Approval does NOT place trades.")
            HStack(spacing: 10) {
                ActionButton(title: "Approve", icon: "checkmark.circle", color: .positive) {
                    onApprove()
                }
                .disabled(actionInProgress || checklist.checklistStatus == "APPROVED")
                ActionButton(title: "Reject", icon: "xmark.circle", color: .negative) {
                    onReject()
                }
                .disabled(actionInProgress || checklist.checklistStatus == "REJECTED")
            }
        }
    }

    private var blockingItems: [DecisionChecklistItem] {
        checklist.items.filter { $0.required && $0.passed == false }
    }
}

struct DecisionChecklistItemRow: View {
    let item: DecisionChecklistItem
    @Binding var note: String
    let actionInProgress: Bool
    let onSet: (Bool?) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Badge(text: stateText, color: stateColor)
                if item.required {
                    Badge(text: "REQUIRED", color: .warning)
                }
                Spacer()
            }
            Text(item.label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Note", text: $note)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textPrimary)
                .padding(9)
                .background(Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
            HStack(spacing: 6) {
                ChecklistStateButton(title: "Pass", color: .positive, selected: item.passed == true) { onSet(true) }
                ChecklistStateButton(title: "Fail", color: .negative, selected: item.passed == false) { onSet(false) }
                ChecklistStateButton(title: "Unset", color: .textSecondary, selected: item.passed == nil) { onSet(nil) }
            }
            .disabled(actionInProgress)
        }
        .padding(10)
        .background(Color.surface.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }

    private var stateText: String {
        if item.passed == true { return "PASSED" }
        if item.passed == false { return "FAILED" }
        return "UNSET"
    }

    private var stateColor: Color {
        if item.passed == true { return .positive }
        if item.passed == false { return .negative }
        return .textSecondary
    }
}

struct SizeCheckInlineCard: View {
    let sizeCheck: DecisionSizeCheckResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Badge(text: sizeCheck.sizingGuidance.sizingTier.replacingOccurrences(of: "_", with: " "), color: sizingTierColor(sizeCheck.sizingGuidance.sizingTier))
                Badge(text: "NO TRADES", color: .warning)
                Spacer()
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Suggested", value: money(sizeCheck.sizingGuidance.suggestedSizeCad), color: .accent)
                AlphaMetric(title: "Max size", value: money(sizeCheck.sizingGuidance.maxPositionSizeCad), color: .textPrimary)
                AlphaMetric(title: "Max loss", value: money(sizeCheck.sizingGuidance.maxLossAmountCad), color: .warning)
            }
            if !sizeCheck.blockers.isEmpty {
                ForEach(sizeCheck.blockers.prefix(3), id: \.self) { blocker in
                    BulletLine(text: blocker)
                }
            }
            SummaryLine(title: "Sizing note", text: sizeCheck.sizingGuidance.riskRewardNote)
        }
        .padding(10)
        .background(Color.warning.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.warning.opacity(0.25), lineWidth: 0.5))
    }
}

struct ChecklistStateButton: View {
    let title: String
    let color: Color
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(selected ? .black : color)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(selected ? color : Color.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(selected ? Color.clear : color.opacity(0.35), lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

enum DecisionChecklistLabels {
    static func status(_ status: String, readiness: String) -> String {
        if status == "APPROVED" { return "APPROVED" }
        if status == "REJECTED" { return "REJECTED" }
        if readiness == "READY_FOR_MANUAL_DECISION" { return "READY_FOR_MANUAL_DECISION" }
        if readiness == "NEEDS_REVIEW" { return "NEEDS_REVIEW" }
        return "NOT_READY"
    }
}

struct ThesisSummaryCard: View {
    let thesis: PositionThesis
    let isSelected: Bool
    let staleTickers: [String]
    let overdueTickers: [String]
    let onOpen: () -> Void

    var body: some View {
        OperatorCard(title: thesis.ticker, icon: isSelected ? "doc.text.fill" : "doc.text") {
            HStack(spacing: 6) {
                Badge(text: thesis.status, color: thesisStatusColor(thesis.status))
                Badge(text: thesis.convictionLevel, color: convictionColor(thesis.convictionLevel))
                Badge(text: thesis.timeHorizon, color: .textSecondary)
                if staleTickers.contains(thesis.ticker) {
                    Badge(text: "STALE", color: .warning)
                }
                if overdueTickers.contains(thesis.ticker) {
                    Badge(text: "OVERDUE", color: .negative)
                }
                Spacer()
            }
            SummaryLine(title: "Summary", text: thesis.thesisTitle.isEmpty ? "No title" : thesis.thesisTitle)
            StatusRow(label: "Next review", value: thesis.nextReviewAt ?? "Not set", color: overdueTickers.contains(thesis.ticker) ? .negative : .textSecondary)
            ActionButton(title: "Open thesis", icon: "chevron.right.circle", color: .accent) {
                onOpen()
            }
        }
    }
}

struct ThesisDetailCard: View {
    let thesis: PositionThesis

    var body: some View {
        OperatorCard(title: "Thesis Detail", icon: "doc.plaintext") {
            StatusRow(label: "Ticker", value: thesis.ticker, color: .textPrimary)
            StatusRow(label: "Title", value: thesis.thesisTitle.isEmpty ? "-" : thesis.thesisTitle, color: .textPrimary)
            StatusRow(label: "Setup", value: thesis.setupType.isEmpty ? "-" : thesis.setupType, color: .accent)
            StatusRow(label: "Conviction", value: thesis.convictionLevel, color: convictionColor(thesis.convictionLevel))
            StatusRow(label: "Horizon", value: thesis.timeHorizon, color: .textSecondary)
            SummaryLine(title: "Thesis", text: thesis.thesisText.isEmpty ? "-" : thesis.thesisText)
            SummaryLine(title: "Entry reason", text: thesis.entryReason.isEmpty ? "-" : thesis.entryReason)
            SummaryLine(title: "Catalysts", text: thesis.expectedCatalysts.isEmpty ? "-" : thesis.expectedCatalysts)
            SummaryLine(title: "Risks", text: thesis.riskFactors.isEmpty ? "-" : thesis.riskFactors)
            StatusRow(label: "Invalidation", value: money(thesis.invalidationLevel), color: .warning)
            StatusRow(label: "Target", value: money(thesis.targetLevel), color: .positive)
            SummaryLine(title: "Exit plan", text: thesis.exitPlan.isEmpty ? "-" : thesis.exitPlan)
            StatusRow(label: "Review frequency", value: "\(thesis.reviewFrequencyDays) days", color: .textSecondary)
            StatusRow(label: "Next review", value: thesis.nextReviewAt ?? "Not set", color: .textSecondary)
            StatusRow(label: "Updated", value: thesis.updatedAt ?? "Unknown", color: .textSecondary)
        }
    }
}

struct JournalEntryCard: View {
    let entry: PositionJournalEntry

    var body: some View {
        OperatorCard(title: entry.entryType.replacingOccurrences(of: "_", with: " "), icon: "book.closed") {
            SummaryLine(title: entry.ticker, text: entry.text)
            if !entry.tags.isEmpty {
                HStack(spacing: 6) {
                    ForEach(entry.tags.prefix(4), id: \.self) { tag in
                        Badge(text: tag, color: .textSecondary)
                    }
                    Spacer()
                }
            }
            if let change = entry.confidenceChange, !change.isEmpty {
                StatusRow(label: "Confidence change", value: change, color: .accent)
            }
            StatusRow(label: "Created", value: entry.createdAt ?? "Unknown", color: .textSecondary)
        }
    }
}

struct ReviewWarningRows: View {
    let title: String
    let tickers: [String]
    let color: Color

    var body: some View {
        if tickers.isEmpty {
            StatusRow(label: title, value: "None", color: .positive)
        } else {
            StatusRow(label: title, value: tickers.joined(separator: ", "), color: color)
        }
    }
}

struct ReviewRowsCard: View {
    let title: String
    let icon: String
    let rows: [ThesisReviewRow]
    let color: Color
    let onOpen: (String) -> Void

    var body: some View {
        OperatorCard(title: title, icon: icon) {
            if rows.isEmpty {
                Text("None")
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows) { row in
                    Button {
                        onOpen(row.ticker)
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(spacing: 6) {
                                Text(row.ticker)
                                    .font(.system(size: 14, weight: .bold))
                                    .foregroundColor(.textPrimary)
                                Badge(text: row.convictionLevel, color: convictionColor(row.convictionLevel))
                                Badge(text: row.status, color: thesisStatusColor(row.status))
                                Spacer()
                            }
                            Text(row.thesisTitle.isEmpty ? "No title" : row.thesisTitle)
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textSecondary)
                                .lineLimit(2)
                            StatusRow(label: "Next review", value: row.nextReviewAt ?? "Not set", color: color)
                        }
                        .padding(10)
                        .background(Color.surfaceElevated)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

func thesisStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "ACTIVE": return .positive
    case "WATCH": return .accent
    case "CLOSED": return .textSecondary
    case "ARCHIVED": return .warning
    default: return .textSecondary
    }
}

func convictionColor(_ conviction: String) -> Color {
    switch conviction.uppercased() {
    case "HIGH": return .positive
    case "MEDIUM": return .accent
    case "LOW": return .warning
    default: return .textSecondary
    }
}

func decisionStatusColor(_ status: String, readiness: String) -> Color {
    switch status.uppercased() {
    case "APPROVED": return .positive
    case "REJECTED": return .negative
    case "READY": return .positive
    case "ARCHIVED": return .textSecondary
    default: return readinessDecisionColor(readiness)
    }
}

func readinessDecisionColor(_ readiness: String) -> Color {
    switch readiness.uppercased() {
    case "READY_FOR_MANUAL_DECISION": return .positive
    case "NEEDS_REVIEW": return .warning
    case "NOT_READY": return .textSecondary
    default: return .textSecondary
    }
}

func riskScoreColor(_ score: Double?) -> Color {
    guard let score else { return .textSecondary }
    if score >= 70 { return .negative }
    if score >= 40 { return .warning }
    return .positive
}

func sizingTierColor(_ tier: String) -> Color {
    switch tier.uppercased() {
    case "NORMAL": return .positive
    case "HIGH_CONVICTION_ONLY", "SMALL_ONLY": return .warning
    case "TOO_RISKY", "NOT_READY": return .negative
    default: return .textSecondary
    }
}

func suggestionLabel(_ value: Bool?) -> String {
    guard let value else { return "Review manually" }
    return value ? "Suggested pass" : "Suggested fail"
}

func suggestionColor(_ value: Bool?) -> Color {
    guard let value else { return .textSecondary }
    return value ? .positive : .warning
}

func regimeColor(_ regime: String?) -> Color {
    switch regime?.uppercased() {
    case "RISK_ON": return .positive
    case "NEUTRAL": return .accent
    case "RISK_OFF": return .warning
    case "PANIC": return .negative
    default: return .textSecondary
    }
}

func volatilityColor(_ regime: String) -> Color {
    switch regime.uppercased() {
    case "CALM": return .positive
    case "ELEVATED": return .accent
    case "HIGH": return .warning
    case "EXTREME": return .negative
    default: return .textSecondary
    }
}

func breadthColor(_ regime: String) -> Color {
    switch regime.uppercased() {
    case "BROAD_STRENGTH": return .positive
    case "NARROW_STRENGTH", "MIXED": return .warning
    case "WEAK": return .negative
    default: return .textSecondary
    }
}

func speculativeColor(_ regime: String) -> Color {
    switch regime.uppercased() {
    case "SPECULATION_ACTIVE": return .positive
    case "SELECTIVE": return .accent
    case "DEFENSIVE": return .warning
    case "SPECULATION_DEAD": return .negative
    default: return .textSecondary
    }
}

func regimeScoreColor(_ score: Double?) -> Color {
    guard let score else { return .textSecondary }
    if score >= 70 { return .positive }
    if score >= 45 { return .accent }
    if score >= 25 { return .warning }
    return .negative
}

func dataQualityColor(_ quality: String) -> Color {
    switch quality.uppercased() {
    case "GOOD", "OK", "HIGH": return .positive
    case "PARTIAL", "MEDIUM": return .warning
    case "POOR", "LOW": return .negative
    default: return .textSecondary
    }
}

func multiplier(_ value: Double) -> String {
    String(format: "%.2fx", value)
}

func multiplierColor(_ value: Double) -> Color {
    if value > 1.05 { return .positive }
    if value < 0.95 { return .warning }
    return .textSecondary
}

func signedNumber(_ value: Double) -> String {
    String(format: "%+.1f", value)
}

struct CanonicalPositionCard: View {
    let position: CanonicalPosition

    var body: some View {
        OperatorCard(title: position.ticker, icon: "briefcase") {
            HStack(spacing: 6) {
                if position.isStale {
                    Badge(text: "STALE", color: .warning)
                } else {
                    Badge(text: "VERIFIED", color: .positive)
                }
                Badge(text: percentFromWhole(position.concentrationPercent), color: concentrationColor(position.concentrationPercent))
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Qty", value: quantity(position.quantity), color: .textPrimary)
                AlphaMetric(title: "Avg cost", value: money(position.avgCost), color: .textSecondary)
                AlphaMetric(title: "Price", value: money(position.marketPrice), color: position.isStale ? .warning : .textPrimary)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Value", value: money(position.marketValue), color: .textPrimary)
                AlphaMetric(title: "Unrealized", value: money(position.unrealizedPnL), color: pnlColor(position.unrealizedPnL))
                AlphaMetric(title: "Realized", value: money(position.realizedPnL), color: pnlColor(position.realizedPnL))
            }

            StatusRow(label: "Last verified", value: position.priceFetchedAt ?? position.reconciledAt ?? "Unknown", color: position.isStale ? .warning : .textSecondary)
            if let source = position.source {
                StatusRow(label: "Source", value: source, color: .textSecondary)
            }
        }
    }
}

struct ReconciliationRunCard: View {
    let run: PortfolioReconciliationRun

    var body: some View {
        OperatorCard(title: run.runId.prefix(8).description, icon: "arrow.triangle.2.circlepath") {
            HStack(spacing: 6) {
                Badge(text: run.status, color: run.status.uppercased() == "OK" ? .positive : .warning)
                if let trigger = run.trigger {
                    Badge(text: trigger.uppercased(), color: .accent)
                }
                Spacer()
            }

            StatusRow(label: "Positions", value: "\(run.positionCount)", color: .textPrimary)
            StatusRow(label: "Issues", value: "\(run.issues.count)", color: run.issues.isEmpty ? .positive : .warning)
            StatusRow(label: "Duration", value: run.durationMs.map { String(format: "%.0f ms", $0) } ?? "-", color: .textSecondary)
            StatusRow(label: "Reconciled", value: run.reconciledAt ?? "Unknown", color: .textSecondary)

            if !run.issues.isEmpty {
                ForEach(run.issues.prefix(5), id: \.self) { issue in
                    BulletLine(text: issue.replacingOccurrences(of: "_", with: " "))
                }
            }
        }
    }
}

struct PortfolioSnapshotCard: View {
    let snapshot: PortfolioSnapshot

    var body: some View {
        OperatorCard(title: shortId, icon: "camera.metering.matrix") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Value", value: money(snapshot.totalPortfolioValue), color: .textPrimary)
                AlphaMetric(title: "Cash", value: money(snapshot.cash), color: .accent)
                AlphaMetric(title: "Equity", value: money(snapshot.totalMarketValue), color: .textPrimary)
            }
            HStack(spacing: 8) {
                AlphaMetric(title: "Realized", value: money(snapshot.totalRealizedPnL), color: pnlColor(snapshot.totalRealizedPnL))
                AlphaMetric(title: "Unrealized", value: money(snapshot.totalUnrealizedPnL), color: pnlColor(snapshot.totalUnrealizedPnL))
                AlphaMetric(title: "Stale", value: "\(snapshot.staleCount)", color: snapshot.staleCount == 0 ? .positive : .warning)
            }
            StatusRow(label: "Positions", value: "\(snapshot.positionCount)", color: .textPrimary)
            StatusRow(label: "Timestamp", value: snapshot.takenAt ?? "Unknown", color: .textSecondary)
            if let trigger = snapshot.trigger {
                StatusRow(label: "Trigger", value: trigger, color: .textSecondary)
            }
        }
    }

    private var shortId: String {
        snapshot.snapshotId.prefix(8).description
    }
}

struct ValidationLeaderboardCard: View {
    let title: String
    let icon: String
    let rows: [(setup: String, rate: Double, count: Int)]
    let valueColor: Color
    let emptyText: String

    var body: some View {
        OperatorCard(title: title, icon: icon) {
            if rows.isEmpty {
                Text(emptyText)
                    .operatorBody(color: .textSecondary)
            } else {
                ForEach(rows.prefix(5), id: \.setup) { row in
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.setup)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.textPrimary)
                                .lineLimit(1)
                            Text("\(row.count) record\(row.count == 1 ? "" : "s")")
                                .font(.system(size: 11, weight: .medium))
                                .foregroundColor(.textSecondary)
                        }
                        Spacer()
                        Text(plainPercent(row.rate))
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(valueColor)
                    }
                    .padding(10)
                    .background(Color.surfaceElevated)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }
}

struct ValidationRecordCard: View {
    let record: AlphaValidationRecord

    var body: some View {
        OperatorCard(title: record.ticker, icon: "checkmark.seal") {
            HStack(spacing: 8) {
                AlphaMetric(title: "Behavior", value: record.behaviorLabel, color: behaviorColor(record.behaviorClass))
                AlphaMetric(title: "Score", value: score(record.validationScore), color: .accent)
                AlphaMetric(title: "Conf", value: record.confidence, color: confidenceColor(record.confidence))
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Setup", value: record.setupType ?? "-", color: .textPrimary)
                AlphaMetric(title: "Tier", value: record.alphaTier ?? "-", color: .textSecondary)
            }

            if let success = record.keySuccessReason, !success.isEmpty {
                SummaryLine(title: "Success", text: success)
            }
            if let failure = record.keyFailureReason, !failure.isEmpty {
                SummaryLine(title: "Failure", text: failure)
            }

            if !record.scanTime.isEmpty || record.computedAt != nil {
                StatusRow(label: "Scan", value: record.scanTime.isEmpty ? "Unknown" : record.scanTime, color: .textSecondary)
                if let computedAt = record.computedAt {
                    StatusRow(label: "Validated", value: computedAt, color: .textSecondary)
                }
            }
        }
    }
}

struct AlertCandidateCard: View {
    let candidate: AlphaAlertCandidate

    var body: some View {
        OperatorCard(title: candidate.ticker, icon: candidate.alertReady ? "bell" : "eye") {
            HStack(spacing: 6) {
                Badge(text: AlphaAlertReadiness.label(for: candidate.readinessTier), color: readinessColor(candidate.readinessTier))
                Badge(text: AlphaAlertReadiness.alertLabel(candidate.alertReady), color: candidate.alertReady ? .positive : .warning)
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Readiness", value: score(candidate.readinessScore), color: readinessColor(candidate.readinessTier))
                AlphaMetric(title: "Alpha", value: score(candidate.alphaScore), color: .accent)
                AlphaMetric(title: "Tier", value: candidate.alphaTier ?? "-", color: .textPrimary)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Setup", value: candidate.setupType ?? "-", color: .textPrimary)
                AlphaMetric(title: "Wait", value: candidate.suggestedWaitWindow ?? "-", color: .textSecondary)
            }

            if !candidate.reason.isEmpty {
                SummaryLine(title: "Reason", text: candidate.reason)
            }

            SummaryLine(title: "What must happen next", text: nextStepText)

            if candidate.blockingFactors.isEmpty {
                SummaryLine(title: "Blockers", text: "No blockers reported")
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Blocking factors")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(candidate.blockingFactors.prefix(5), id: \.self) { factor in
                        BulletLine(text: factor)
                    }
                }
            }

            if candidate.confirmationNeeded.isEmpty {
                SummaryLine(title: "Confirmation needed", text: "No extra confirmation listed")
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Confirmation needed")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(candidate.confirmationNeeded.prefix(5), id: \.self) { item in
                        BulletLine(text: item)
                    }
                }
            }
        }
    }

    private var nextStepText: String {
        if candidate.alertReady {
            return "Review manually before sending or acting. This screen does not send alerts."
        }
        if !candidate.blockingFactors.isEmpty {
            return "Wait for blockers to clear, then re-check readiness."
        }
        if !candidate.confirmationNeeded.isEmpty {
            return "Wait for the listed confirmation before treating this as alert-worthy."
        }
        return "Monitor the next scan for stronger evidence."
    }
}

struct DryRunNotificationCard: View {
    let item: AlphaDryRunNotification
    let actionInProgress: Bool
    let onReview: () -> Void
    let onDismiss: () -> Void
    let deliveryInProgress: Bool
    let onSend: () -> Void

    var body: some View {
        OperatorCard(title: item.ticker, icon: "message.badge") {
            HStack(spacing: 6) {
                Badge(text: item.status, color: statusColor)
                Badge(text: AlphaAlertReadiness.label(for: item.readinessTier), color: readinessColor(item.readinessTier))
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Alpha", value: score(item.alphaScore), color: .accent)
                AlphaMetric(title: "Setup", value: item.setupType ?? "-", color: .textPrimary)
                AlphaMetric(title: "Created", value: shortDate(item.createdAt), color: .textSecondary)
            }

            SummaryLine(title: "Proposed message", text: item.messageText.isEmpty ? "No message text" : item.messageText)

            if let reason = item.reason, !reason.isEmpty {
                SummaryLine(title: "Reason", text: reason)
            }

            if !item.confirmationNeeded.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Confirmation needed")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(item.confirmationNeeded.prefix(4), id: \.self) { line in
                        BulletLine(text: line)
                    }
                }
            }

            if item.status == "DRY_RUN" {
                Divider().background(Color.border)
                HStack(spacing: 10) {
                    Button {
                        onReview()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                            Text("Review")
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color.positive)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(actionInProgress)

                    Button {
                        onDismiss()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "xmark")
                                .font(.system(size: 12, weight: .bold))
                            Text("Dismiss")
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color.negative)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(actionInProgress)
                }
            }

            if item.status == "REVIEWED" {
                Divider().background(Color.border)
                SummaryLine(title: "Delivery warning", text: "This may send a real WhatsApp alert if backend flags are enabled.")
                Button {
                    onSend()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "paperplane")
                            .font(.system(size: 13, weight: .bold))
                        Text("Attempt manual send")
                            .font(.system(size: 13, weight: .semibold))
                        Spacer()
                    }
                    .foregroundColor(.black)
                    .padding(11)
                    .background(Color.negative)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(deliveryInProgress)
            }
        }
    }

    private var statusColor: Color {
        switch item.status.uppercased() {
        case "DRY_RUN": return .warning
        case "REVIEWED": return .positive
        case "DISMISSED": return .negative
        case "EXPIRED": return .textSecondary
        default: return .textPrimary
        }
    }

    func shortDate(_ iso: String) -> String {
        let trimmed = String(iso.prefix(10))
        return trimmed.isEmpty ? "Unknown" : trimmed
    }
}

struct DeliveryLogCard: View {
    let entry: AlphaNotificationDeliveryEntry

    var body: some View {
        OperatorCard(title: title, icon: "paperplane") {
            HStack(spacing: 6) {
                Badge(text: AlphaNotificationDelivery.label(for: entry.status), color: deliveryColor(entry.status))
                if let tier = entry.readinessTier, !tier.isEmpty {
                    Badge(text: AlphaAlertReadiness.label(for: tier), color: readinessColor(tier))
                }
                Spacer()
            }

            StatusRow(label: "Dry-run ID", value: entry.dryRunId.isEmpty ? "-" : entry.dryRunId, color: .textSecondary)
            StatusRow(label: "Time", value: entry.sentAt ?? entry.createdAt ?? "Unknown", color: .textSecondary)

            if let reason = entry.reason, !reason.isEmpty {
                SummaryLine(title: "Reason", text: reason.replacingOccurrences(of: "_", with: " "))
            }

            if let provider = entry.providerResponse, !provider.isEmpty {
                SummaryLine(title: "Provider", text: provider)
            } else {
                SummaryLine(title: "Provider", text: "No provider response")
            }
        }
    }

    private var title: String {
        entry.ticker.isEmpty ? "Delivery attempt" : entry.ticker
    }
}

struct NotificationQCCard: View {
    let record: AlphaNotificationQCRecord

    var body: some View {
        OperatorCard(title: record.ticker, icon: record.allowNotification ? "checkmark.shield" : "shield.slash") {
            HStack(spacing: 6) {
                Badge(text: AlphaNotificationQC.label(for: record.qcTier), color: qcColor(record.qcTier))
                Badge(text: AlphaNotificationQC.allowLabel(record.allowNotification), color: record.allowNotification ? .positive : .warning)
                Spacer()
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "QC", value: score(record.qcScore), color: qcColor(record.qcTier))
                AlphaMetric(title: "Novelty", value: score(record.noveltyScore), color: .accent)
                AlphaMetric(title: "Stability", value: score(record.stabilityScore), color: .textPrimary)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Info gain", value: score(record.informationGainScore), color: .accent)
                AlphaMetric(title: "Cooldown", value: cooldown(record.cooldownRemaining), color: cooldownColor)
                AlphaMetric(title: "Setup", value: record.setupType ?? "-", color: .textPrimary)
            }

            StatusRow(label: "Readiness", value: AlphaAlertReadiness.label(for: record.readinessTier), color: readinessColor(record.readinessTier))
            StatusRow(label: "Alpha score", value: score(record.alphaScore), color: .accent)
            if let reason = record.suppressionReason, !reason.isEmpty {
                SummaryLine(title: "Suppression reason", text: reason.replacingOccurrences(of: "_", with: " ").capitalized)
            }
            if !record.qualityFlags.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Quality flags")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    ForEach(record.qualityFlags.prefix(6), id: \.self) { flag in
                        BulletLine(text: flag.replacingOccurrences(of: "_", with: " ").capitalized)
                    }
                }
            }
            StatusRow(label: "Evaluated", value: record.evaluatedAt.isEmpty ? "Unknown" : record.evaluatedAt, color: .textSecondary)
        }
    }

    private var cooldownColor: Color {
        guard let value = record.cooldownRemaining else { return .textSecondary }
        return value > 0 ? .warning : .positive
    }

    func cooldown(_ value: Double?) -> String {
        guard let value else { return "-" }
        if value <= 0 { return "Clear" }
        return String(format: "%.1fh", value)
    }
}

struct WeightRecommendationRow: View {
    let rec: AlphaWeightRecommendation

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(rec.component.replacingOccurrences(of: "_", with: " "))
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.textPrimary)
                    .lineLimit(1)
                Spacer()
                Badge(text: rec.action, color: actionColor)
                Badge(text: rec.confidence, color: confidenceColor(rec.confidence))
            }

            HStack(spacing: 8) {
                LearningMetric(title: "Current", value: weight(rec.currentWeight), color: .textPrimary)
                LearningMetric(title: "Delta", value: signedWeight(rec.shrunkDelta), color: deltaColor(rec.shrunkDelta))
                LearningMetric(title: "Samples", value: "\(rec.sampleSize)", color: .accent)
            }

            SummaryLine(title: "Why", text: rec.reason)
            SummaryLine(title: "Risk", text: rec.risk)
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var actionColor: Color {
        switch rec.action.uppercased() {
        case "INCREASE": return .positive
        case "DECREASE": return .warning
        default: return .textSecondary
        }
    }
}

struct ThresholdRecommendationRow: View {
    let rec: AlphaThresholdRecommendation

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(rec.tier.replacingOccurrences(of: "_", with: " "))
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.textPrimary)
                    .lineLimit(1)
                Spacer()
                Badge(text: rec.action, color: actionColor)
                Badge(text: rec.confidence, color: confidenceColor(rec.confidence))
            }

            HStack(spacing: 8) {
                LearningMetric(title: "Current", value: score(rec.currentThreshold), color: .textPrimary)
                LearningMetric(title: "Delta", value: signedScore(rec.suggestedDelta), color: deltaColor(rec.suggestedDelta))
            }

            SummaryLine(title: "Why", text: rec.reason)
            SummaryLine(title: "Risk", text: rec.risk)
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var actionColor: Color {
        switch rec.action.uppercased() {
        case "TIGHTEN": return .warning
        case "LOOSEN": return .positive
        default: return .textSecondary
        }
    }
}

struct WeightCompareRow: View {
    let component: String
    let current: Double?
    let shadow: Double?
    let delta: Double?

    var body: some View {
        VStack(spacing: 6) {
            HStack {
                Text(component.replacingOccurrences(of: "_", with: " "))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.textPrimary)
                    .lineLimit(1)
                Spacer()
                Text(signedWeight(delta))
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(deltaColor(delta))
            }
            HStack(spacing: 8) {
                LearningMetric(title: "Current", value: weight(current), color: .textPrimary)
                LearningMetric(title: "Shadow", value: weight(shadow), color: .accent)
            }
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ShadowCandidateChangeRow: View {
    let change: AlphaShadowCandidateChange

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(change.ticker)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Badge(text: "\(change.oldTier) -> \(change.shadowTier)", color: changeColor)
            }

            HStack(spacing: 8) {
                AlphaMetric(title: "Old", value: score(change.oldScore), color: .textPrimary)
                AlphaMetric(title: "Shadow", value: score(change.shadowScore), color: .accent)
                AlphaMetric(title: "5d", value: percent(change.return5d), color: returnColor)
            }
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var changeColor: Color {
        if change.isFalsePositive == true { return .positive }
        if change.isWinner == true { return .warning }
        return .accent
    }

    private var returnColor: Color {
        guard let value = change.return5d else { return .textSecondary }
        return value >= 0 ? .positive : .negative
    }
}

struct Badge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .foregroundColor(.black)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

func confidenceColor(_ confidence: String) -> Color {
    switch confidence.uppercased() {
    case "HIGH": return .positive
    case "MEDIUM": return .warning
    default: return .textSecondary
    }
}

func behaviorColor(_ behavior: String) -> Color {
    let normalized = behavior.uppercased()
    if AlphaValidationBehavior.positive.contains(normalized) { return .positive }
    if AlphaValidationBehavior.trapLike.contains(normalized) { return .warning }
    return .textSecondary
}

func readinessColor(_ tier: String) -> Color {
    switch tier.uppercased() {
    case "RARE_ALERT", "ALERT_READY": return .positive
    case "PRE_ALERT": return .warning
    case "MONITOR": return .accent
    default: return .textSecondary
    }
}

func qcColor(_ tier: String) -> Color {
    switch tier.uppercased() {
    case "PRIORITY": return .positive
    case "ALLOW": return .accent
    case "SUPPRESS": return .warning
    case "BLOCK": return .negative
    default: return .textSecondary
    }
}

func deliveryColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "SENT": return .positive
    case "DRY_RUN_ONLY", "BLOCKED", "NOT_REVIEWED", "QC_BLOCKED", "DUPLICATE": return .warning
    case "ERROR": return .negative
    default: return .textSecondary
    }
}

func replayStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "COMPLETE": return .positive
    case "PENDING": return .warning
    case "FAILED": return .negative
    default: return .textSecondary
    }
}

func replayDecisionColor(_ value: String?) -> Color {
    switch (value ?? "").uppercased() {
    case "WOULD_ALERT": return .warning
    case "WOULD_PREPARE": return .accent
    case "WOULD_MONITOR": return .textSecondary
    case "WOULD_BLOCK", "WOULD_REJECT": return .negative
    case "WOULD_IGNORE": return .textSecondary
    default: return .textSecondary
    }
}

func replayOutcomeColor(_ value: String?) -> Color {
    switch (value ?? "").lowercased() {
    case "early_but_valid", "avoided_loser", "correct_ignore": return .positive
    case "missed_winner", "false_positive", "too_late": return .warning
    case "inconclusive": return .textSecondary
    default: return .textSecondary
    }
}

func replayBreakdownColor(_ key: String) -> Color {
    let normalized = key.lowercased()
    if ["early_but_valid", "avoided_loser", "correct_ignore"].contains(normalized) { return .positive }
    if ["missed_winner", "false_positive", "too_late"].contains(normalized) { return .warning }
    return replayDecisionColor(key)
}

func replayEventIcon(_ event: ReplayEvent) -> String {
    switch event.outcomeClassification?.lowercased() {
    case "missed_winner", "false_positive", "too_late":
        return "exclamationmark.triangle"
    case "early_but_valid", "avoided_loser", "correct_ignore":
        return "checkmark.seal"
    default:
        return "list.bullet.rectangle"
    }
}

func stressRiskColor(_ risk: String) -> Color {
    switch risk.uppercased() {
    case "LOW": return .positive
    case "MODERATE": return .accent
    case "HIGH": return .warning
    case "SEVERE": return .negative
    default: return .textSecondary
    }
}

func stressSensitivityColor(_ sensitivity: String) -> Color {
    switch sensitivity.lowercased() {
    case "low": return .positive
    case "moderate": return .accent
    case "high": return .warning
    case "severe": return .negative
    default: return .textSecondary
    }
}

func strategyScoreColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 70 { return .positive }
    if value >= 40 { return .accent }
    return .warning
}

func confidenceScoreColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 70 { return .positive }
    if value >= 35 { return .accent }
    return .textSecondary
}

func confidenceLabel(_ value: Double?) -> String {
    guard let value else { return "Low confidence" }
    if value >= 70 { return "High confidence" }
    if value >= 35 { return "Moderate confidence" }
    return "Low confidence"
}

func winRateColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 60 { return .positive }
    if value >= 30 { return .accent }
    return .warning
}

func drawdownColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value <= -15 { return .negative }
    if value <= -8 { return .warning }
    return .positive
}

func falsePositiveColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 40 { return .negative }
    if value >= 25 { return .warning }
    return .positive
}

func disciplineColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 70 { return .positive }
    if value >= 40 { return .accent }
    return .warning
}

func stressSensitivityColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 20 { return .negative }
    if value >= 12 { return .warning }
    return .positive
}

func recommendationColor(_ rec: String) -> Color {
    switch rec {
    case "promote_to_core", "increase_focus": return .positive
    case "reduce_exposure", "require_stricter_checklist", "use_smaller_sizing", "avoid_during_risk_off": return .warning
    case "improve_thesis_quality": return .accent
    default: return .textSecondary
    }
}

func plannerUrgencyColor(_ urgency: String) -> Color {
    switch urgency.uppercased() {
    case "NONE": return .positive
    case "LOW": return .accent
    case "MEDIUM": return .warning
    case "HIGH": return .negative
    default: return .textSecondary
    }
}

func allocationStatusColor(_ drift: Double) -> Color {
    let absDrift = abs(drift)
    if absDrift < 3 { return .positive }
    if absDrift < 8 { return .accent }
    if absDrift < 15 { return .warning }
    return .negative
}

func projectionColor(_ scenario: String) -> Color {
    switch scenario.lowercased() {
    case "aggressive": return .positive
    case "base": return .accent
    case "conservative": return .textPrimary
    case "downside": return .warning
    default: return .textSecondary
    }
}

func money(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "$%.2f", value)
}

func shortMoney(_ value: Double) -> String {
    let absValue = abs(value)
    if absValue >= 1_000_000 {
        return String(format: "$%.1fM", value / 1_000_000)
    }
    if absValue >= 1_000 {
        return String(format: "$%.0fK", value / 1_000)
    }
    return String(format: "$%.0f", value)
}

func quantity(_ value: Double) -> String {
    String(format: "%.4g", value)
}

func percentFromWhole(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.1f%%", value)
}

func pnlColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value > 0 { return .positive }
    if value < 0 { return .negative }
    return .textSecondary
}

func concentrationColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value >= 35 { return .negative }
    if value >= 25 { return .warning }
    return .positive
}

func groupedPortfolioIssues(_ issues: [String]) -> (missing: [String], stale: [String], conflicts: [String], impossible: [String]) {
    let missing = issues.filter { issue in
        issue.contains("MISSING") || issue.contains("EXTRA")
    }
    let stale = issues.filter { $0.contains("STALE_PRICE") }
    let conflicts = issues.filter { issue in
        issue.contains("DUPLICATE") || issue.contains("CONFLICT")
    }
    let impossible = issues.filter { issue in
        issue.contains("NEGATIVE") || issue.contains("IMPOSSIBLE")
    }
    return (missing, stale, conflicts, impossible)
}

func weight(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.3f", value)
}

func signedWeight(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.3f", value)
}

func score(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.1f", value)
}

func signedScore(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.1f", value)
}

func percent(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.1f%%", value * 100)
}

func plainPercent(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.1f%%", value * 100)
}

func plainPercentFromWhole(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.1f%%", value)
}

func shortDateString(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "Unknown" }
    return String(value.prefix(10))
}

func marketLabel(_ item: MarketTickerSnapshot) -> String {
    item.label ?? item.sectorName ?? item.ticker
}

func signedPercent(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.2f%%", value)
}

func maxSectorMove(_ rows: [SectorPerformanceRow]) -> Double {
    max(rows.map { abs($0.changePct ?? 0) }.max() ?? 0, 1)
}

func sectorLabel(_ row: SectorPerformanceRow?) -> String {
    guard let row else { return "-" }
    return "\(row.sectorName) \(signedPercent(row.changePct))"
}

func rsiColor(_ bucket: String?) -> Color {
    switch bucket?.uppercased() {
    case "OVERBOUGHT", "OVERSOLD": return .warning
    case "NEUTRAL": return .positive
    default: return .textSecondary
    }
}

func trendVs200(_ stock: StockResearchResponse) -> String {
    guard let price = stock.quote.price, let sma200 = stock.technicals.sma200 else { return "Unknown" }
    if price > sma200 { return "Above 200DMA" }
    if price < sma200 { return "Below 200DMA" }
    return "At 200DMA"
}

func trendColor(_ stock: StockResearchResponse) -> Color {
    guard let price = stock.quote.price, let sma200 = stock.technicals.sma200 else { return .textSecondary }
    if price > sma200 { return .positive }
    if price < sma200 { return .warning }
    return .textSecondary
}

func compactNumber(_ value: Double?) -> String {
    guard let value else { return "-" }
    let absValue = abs(value)
    if absValue >= 1_000_000_000 { return String(format: "%.1fB", value / 1_000_000_000) }
    if absValue >= 1_000_000 { return String(format: "%.1fM", value / 1_000_000) }
    if absValue >= 1_000 { return String(format: "%.0fK", value / 1_000) }
    return String(format: "%.0f", value)
}

func preferredMacroRows(_ indicators: [String: MacroIndicator]) -> [(String, MacroIndicator)] {
    let preferred = ["GDP", "UNRATE", "CPIAUCSL", "CORESTICKM159SFRBATL", "FEDFUNDS", "T10Y2Y", "RSAFS", "INDPRO", "T10YIE", "BAMLH0A0HYM2"]
    var rows: [(String, MacroIndicator)] = preferred.compactMap { key in
        indicators[key].map { (key, $0) }
    }
    let included = Set(rows.map(\.0))
    rows.append(contentsOf: indicators.filter { !included.contains($0.key) }.sorted { $0.key < $1.key })
    return rows
}

func macroLabel(_ key: String, fallback: String) -> String {
    switch key {
    case "GDP": return "GDP"
    case "UNRATE": return "Unemployment"
    case "CPIAUCSL": return "CPI"
    case "CORESTICKM159SFRBATL": return "Core CPI"
    case "FEDFUNDS": return "Fed funds"
    case "T10Y2Y": return "10Y-2Y spread"
    case "RSAFS": return "Retail sales"
    case "INDPRO": return "Industrial production"
    case "T10YIE": return "Yield curve / breakeven"
    default: return fallback.isEmpty ? key : fallback
    }
}

func macroValue(_ item: MacroIndicator) -> String {
    guard let value = item.value else { return "-" }
    let dateSuffix = item.date.map { " \($0.prefix(10))" } ?? ""
    return String(format: "%.2f%@", value, dateSuffix)
}

enum ResearchWatchlistLabels {
    static func assetType(_ value: String) -> String {
        switch value.uppercased() {
        case "STOCK": return "Stock"
        case "ETF": return "ETF"
        case "CRYPTO": return "Crypto"
        case "INDEX": return "Index"
        case "OTHER": return "Other"
        default: return value
        }
    }

    static func category(_ value: String) -> String {
        switch value.uppercased() {
        case "CORE": return "Core"
        case "ALPHA": return "Alpha"
        case "SPECULATIVE": return "Speculative"
        case "MACRO": return "Macro"
        case "HEDGE": return "Hedge"
        case "LEARNING": return "Learning"
        default: return value
        }
    }

    static func status(_ value: String) -> String {
        switch value.uppercased() {
        case "WATCHING": return "Watching"
        case "REVIEW_SOON": return "Review soon"
        case "ACTIVE_RESEARCH": return "Active research"
        case "PAUSED": return "Paused"
        case "ARCHIVED": return "Archived"
        default: return value
        }
    }

    static func priority(_ value: String) -> String {
        switch value.uppercased() {
        case "LOW": return "Low"
        case "MEDIUM": return "Medium"
        case "HIGH": return "High"
        default: return value
        }
    }

    static func noteType(_ value: String) -> String {
        switch value.uppercased() {
        case "RESEARCH": return "Research"
        case "NEWS": return "News"
        case "CATALYST": return "Catalyst"
        case "RISK": return "Risk"
        case "VALUATION": return "Valuation"
        case "TECHNICAL": return "Technical"
        case "MACRO": return "Macro"
        case "OTHER": return "Other"
        default: return value
        }
    }

    static func source(_ value: String) -> String {
        switch value {
        case "alpha_candidates": return "Alpha candidates"
        case "alert_gate": return "Alert readiness"
        case "replay_missed_winners": return "Replay missed winners"
        case "validation_trends": return "Validation trends"
        case "thesis_warnings": return "Thesis warnings"
        case "scorecard_gaps": return "Scorecard gaps"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

}

enum CatalystLabels {
    static func type(_ value: String) -> String {
        switch value.uppercased() {
        case "EARNINGS": return "Earnings"
        case "FDA_REGULATORY": return "FDA / regulatory"
        case "MACRO": return "Macro"
        case "PRODUCT": return "Product"
        case "CONTRACT": return "Contract"
        case "INVESTOR_DAY": return "Investor day"
        case "THESIS_REVIEW": return "Thesis review"
        case "WATCHLIST_REVIEW": return "Watchlist review"
        case "ALPHA_CONFIRMATION": return "Alpha confirmation"
        case "PORTFOLIO_RISK": return "Portfolio risk"
        case "OTHER": return "Other"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func status(_ value: String) -> String {
        switch value.uppercased() {
        case "UPCOMING": return "Upcoming"
        case "COMPLETED": return "Completed"
        case "MISSED": return "Missed"
        case "ARCHIVED": return "Archived"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func level(_ value: String) -> String {
        switch value.uppercased() {
        case "LOW": return "Low"
        case "MEDIUM": return "Medium"
        case "HIGH": return "High"
        default: return value
        }
    }

    static func source(_ value: String) -> String {
        switch value.lowercased() {
        case "alpha": return "Alpha"
        case "thesis": return "Thesis"
        case "watchlist": return "Watchlist"
        case "macro": return "Macro"
        case "manual": return "Manual"
        case "research": return "Research"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

enum NotificationLabels {
    static func category(_ value: String) -> String {
        switch value.uppercased() {
        case "BRIEF": return "Brief"
        case "ALPHA", "ALPHA_SIGNAL": return "Alpha"
        case "PORTFOLIO": return "Portfolio"
        case "RISK": return "Risk"
        case "REGIME", "MARKET": return "Regime"
        case "RESEARCH": return "Research"
        case "CATALYST": return "Catalyst"
        case "CHECKLIST", "COMPLIANCE": return "Checklist"
        case "WEEKLY_REVIEW", "PERFORMANCE": return "Weekly review"
        case "SYSTEM": return "System"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func severity(_ value: String) -> String {
        switch value.uppercased() {
        case "INFO", "DEBUG": return "Info"
        case "WATCH": return "Watch"
        case "WARNING": return "Warning"
        case "CRITICAL": return "Critical"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func status(_ value: String) -> String {
        switch value.uppercased() {
        case "UNREAD": return "Unread"
        case "READ": return "Read"
        case "ARCHIVED": return "Archived"
        case "DISMISSED": return "Dismissed"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func digestMode(_ value: String) -> String {
        switch value.uppercased() {
        case "OFF": return "Off"
        case "DAILY": return "Daily"
        case "MORNING_AND_EOD": return "Morning and EOD"
        case "WEEKLY": return "Weekly"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

enum ResearchWorkflowLabels {
    static func status(_ value: String) -> String {
        switch value.uppercased() {
        case "OPEN": return "Open"
        case "IN_PROGRESS": return "In progress"
        case "DONE": return "Done"
        case "SNOOZED": return "Snoozed"
        case "ARCHIVED": return "Archived"
        default: return value
        }
    }

    static func priority(_ value: String) -> String {
        ResearchWatchlistLabels.priority(value)
    }

    static func source(_ value: String) -> String {
        switch value {
        case "alpha_candidate", "alpha_gate": return "Alpha candidate"
        case "alert_readiness": return "Alert readiness"
        case "replay_missed_winner", "replay_missed": return "Missed winner"
        case "validation_sustained_trend", "validation_trends": return "Held its move"
        case "thesis_review", "thesis_due": return "Thesis review"
        case "strategy_warning", "scorecard_warning": return "Strategy warning"
        case "regime_change": return "Regime change"
        case "watchlist_due", "watchlist_stale": return "Watchlist review"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

enum WeeklyReviewLabels {
    static func grade(_ value: String) -> String {
        switch value.uppercased() {
        case "A": return "Excellent discipline"
        case "B": return "Good week"
        case "C": return "Mixed week"
        case "D": return "Needs attention"
        case "F": return "Poor discipline"
        default: return value
        }
    }
}

func weeklyGradeColor(_ grade: String?) -> Color {
    switch grade?.uppercased() {
    case "A": return .positive
    case "B": return .accent
    case "C": return .warning
    case "D", "F": return .negative
    default: return .textSecondary
    }
}

func gradeExplanation(_ review: WeeklyReviewDetailedResponse) -> String {
    let metrics = review.accountabilityMetrics
    var parts: [String] = [WeeklyReviewLabels.grade(review.grade)]
    if metrics.overdueReviewCount > 0 {
        parts.append("\(metrics.overdueReviewCount) overdue review(s)")
    }
    if metrics.unreviewedDryRuns > 0 {
        parts.append("\(metrics.unreviewedDryRuns) unreviewed alert draft(s)")
    }
    if metrics.alphaFalsePositiveCount > 0 {
        parts.append("\(metrics.alphaFalsePositiveCount) false positive(s)")
    }
    if metrics.missedWinnerCount > 0 {
        parts.append("\(metrics.missedWinnerCount) missed winner(s)")
    }
    return parts.joined(separator: " · ")
}

func workflowStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "OPEN": return .accent
    case "IN_PROGRESS": return .warning
    case "DONE": return .positive
    case "SNOOZED", "ARCHIVED": return .textSecondary
    default: return .textSecondary
    }
}

func workflowScoreColor(_ value: Double) -> Color {
    if value >= 75 { return .warning }
    if value >= 50 { return .accent }
    return .textSecondary
}

func workflowIcon(_ item: ResearchWorkflowItem) -> String {
    switch item.status.uppercased() {
    case "IN_PROGRESS": return "play.circle"
    case "DONE": return "checkmark.circle"
    case "SNOOZED": return "clock"
    case "ARCHIVED": return "archivebox"
    default: return item.ticker == nil ? "checklist" : "binoculars"
    }
}

func workflowDueState(_ item: ResearchWorkflowItem) -> String {
    guard let due = parsedISODate(item.dueAt) else {
        if let snoozed = parsedISODate(item.snoozedUntil), snoozed > Date() {
            return "Snoozed"
        }
        return "No due date"
    }
    if due < Date() { return "Overdue" }
    if Calendar.current.isDateInToday(due) { return "Due today" }
    return "Scheduled"
}

func workflowDueColor(_ item: ResearchWorkflowItem) -> Color {
    switch workflowDueState(item) {
    case "Overdue": return .warning
    case "Due today": return .accent
    case "Snoozed", "No due date": return .textSecondary
    default: return .positive
    }
}

func watchPriorityColor(_ priority: String) -> Color {
    switch priority.uppercased() {
    case "HIGH": return .warning
    case "MEDIUM": return .accent
    case "LOW": return .textSecondary
    default: return .textSecondary
    }
}

func watchStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "ACTIVE_RESEARCH": return .positive
    case "REVIEW_SOON": return .warning
    case "WATCHING": return .accent
    case "PAUSED", "ARCHIVED": return .textSecondary
    default: return .textSecondary
    }
}

func catalystStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "UPCOMING": return .accent
    case "COMPLETED": return .positive
    case "MISSED": return .warning
    case "ARCHIVED": return .textSecondary
    default: return .textSecondary
    }
}

func catalystImportanceColor(_ importance: String) -> Color {
    switch importance.uppercased() {
    case "HIGH": return .warning
    case "MEDIUM": return .accent
    case "LOW": return .textSecondary
    default: return .textSecondary
    }
}

func catalystDateState(_ catalyst: Catalyst) -> String {
    if catalyst.status == "ARCHIVED" { return "Archived" }
    if catalyst.status == "COMPLETED" { return "Completed" }
    if catalyst.status == "MISSED" { return "Missed" }
    guard let date = parsedISODate(catalyst.date) else { return "No date" }
    if date < Calendar.current.startOfDay(for: Date()) { return "Overdue" }
    if Calendar.current.isDateInToday(date) { return "Today" }
    if let week = Calendar.current.date(byAdding: .day, value: 7, to: Date()), date <= week {
        return "This week"
    }
    return "Upcoming"
}

func catalystDateColor(_ catalyst: Catalyst) -> Color {
    switch catalystDateState(catalyst) {
    case "Overdue", "Missed": return .warning
    case "Today", "This week": return .accent
    case "Completed": return .positive
    case "Archived", "No date": return .textSecondary
    default: return .textPrimary
    }
}

func notificationSeverityColor(_ severity: String) -> Color {
    switch severity.uppercased() {
    case "CRITICAL": return .negative
    case "WARNING": return .warning
    case "WATCH": return .accent
    case "INFO", "DEBUG": return .textSecondary
    default: return .textSecondary
    }
}

func notificationStatusColor(_ status: String) -> Color {
    switch status.uppercased() {
    case "UNREAD": return .accent
    case "READ": return .textSecondary
    case "DISMISSED", "ARCHIVED": return .textSecondary
    default: return .textSecondary
    }
}

func notificationCategoryColor(_ category: String) -> Color {
    switch category.uppercased() {
    case "RISK", "PORTFOLIO": return .warning
    case "ALPHA", "ALPHA_SIGNAL", "CATALYST": return .accent
    case "CHECKLIST", "COMPLIANCE": return .positive
    default: return .textSecondary
    }
}

func notificationCategoryIcon(_ category: String) -> String {
    switch category.uppercased() {
    case "PORTFOLIO": return "briefcase"
    case "RISK": return "exclamationmark.triangle"
    case "ALPHA", "ALPHA_SIGNAL": return "sparkles"
    case "RESEARCH": return "binoculars"
    case "CATALYST": return "calendar"
    case "CHECKLIST", "COMPLIANCE": return "checklist"
    case "BRIEF", "WEEKLY_REVIEW": return "doc.text"
    default: return "bell"
    }
}

func digestModeSelectorLabel(_ value: String) -> String {
    switch value.lowercased() {
    case "daily": return "Daily"
    case "eod": return "EOD"
    case "weekly": return "Weekly"
    default: return NotificationLabels.digestMode(value)
    }
}

func localPermissionText(_ status: String) -> String {
    switch status {
    case "not_requested": return "Not requested"
    case "allowed": return "Allowed"
    case "denied": return "Denied"
    case "provisional": return "Provisional"
    default: return "Unknown"
    }
}

func localPermissionColor(_ status: String) -> Color {
    switch status {
    case "allowed", "provisional": return .positive
    case "denied": return .warning
    default: return .textSecondary
    }
}

func notificationIcon(_ notification: InAppNotification) -> String {
    switch notification.category.uppercased() {
    case "PORTFOLIO": return "briefcase"
    case "RISK": return "exclamationmark.triangle"
    case "ALPHA", "ALPHA_SIGNAL": return "sparkles"
    case "RESEARCH": return "binoculars"
    case "CATALYST": return "calendar"
    case "CHECKLIST", "COMPLIANCE": return "checklist"
    default: return notification.status == "UNREAD" ? "bell.badge" : "bell"
    }
}

func linkedNotificationText(_ notification: InAppNotification) -> String {
    [notification.entityType, notification.entityId].compactMap { $0 }.joined(separator: " / ").nilIfEmpty ?? "-"
}

func watchlistIcon(_ item: ResearchWatchlistItem) -> String {
    switch item.assetType.uppercased() {
    case "ETF", "INDEX": return "rectangle.stack"
    case "CRYPTO": return "bitcoinsign.circle"
    default: return item.status == "ARCHIVED" ? "archivebox" : "binoculars"
    }
}

func watchReviewState(_ item: ResearchWatchlistItem) -> String {
    guard let reviewDate = parsedISODate(item.nextReviewAt) else {
        return item.status == "ARCHIVED" ? "Archived" : "No date"
    }
    if reviewDate < Date() { return "Overdue" }
    if Calendar.current.isDateInToday(reviewDate) { return "Review today" }
    return "Current"
}

func watchReviewColor(_ item: ResearchWatchlistItem) -> Color {
    switch watchReviewState(item) {
    case "Overdue": return .warning
    case "Review today": return .accent
    case "Archived", "No date": return .textSecondary
    default: return .positive
    }
}

func parsedISODate(_ value: String?) -> Date? {
    guard let value, !value.isEmpty else { return nil }
    if let date = ISO8601DateFormatter().date(from: value) { return date }
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.date(from: String(value.prefix(10)))
}

func deltaColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value > 0 { return .positive }
    if value < 0 { return .warning }
    return .textSecondary
}

extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

struct ActionButton: View {
    let title: String
    let icon: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(color)
                    .frame(width: 28)
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.textSecondary)
            }
            .padding(12)
            .background(Color.surfaceElevated)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

struct ReminderButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .bold))
                .foregroundColor(.black)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(Color.accent)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

struct StatusBanner: View {
    let text: String
    let color: Color
    let icon: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(color)
            Text(text)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .padding(12)
        .background(color.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(color.opacity(0.35), lineWidth: 0.5))
    }
}

struct OperatorEmptyState: View {
    let icon: String
    let title: String

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 32, weight: .light))
                .foregroundColor(.textSecondary)
            Text(title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(32)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}

struct ProposalCard: View {
    let proposal: AlphaProposal
    let shadowResults: AlphaProposalShadowResults?
    let actionInProgress: Bool
    let onLoadShadow: () -> Void
    let onApprove: () -> Void
    let onReject: () -> Void

    @State private var showShadow = false

    var body: some View {
        OperatorCard(title: proposalTitle, icon: "doc.badge.gearshape") {
            HStack(spacing: 6) {
                Badge(text: proposal.kind, color: kindColor)
                Badge(text: proposal.status.replacingOccurrences(of: "_", with: " "), color: statusColor)
                Badge(text: proposal.confidence, color: confidenceColor(proposal.confidence))
                Spacer()
            }

            StatusRow(label: "Samples", value: "\(proposal.sampleSize)", color: .textPrimary)
            StatusRow(label: "Created", value: shortDate(proposal.createdAt), color: .textSecondary)
            StatusRow(label: "Expires", value: shortDate(proposal.expiresAt), color: .textSecondary)

            if let evidence = proposal.evidenceSummary {
                SummaryLine(title: "Evidence", text: evidence)
            }
            if let benefit = proposal.expectedBenefit {
                SummaryLine(title: "Benefit", text: benefit)
            }
            if let downside = proposal.expectedDownside {
                SummaryLine(title: "Downside", text: downside)
            }
            if let risk = proposal.riskWarning {
                SummaryLine(title: "Risk", text: risk)
            }
            if let reviewer = proposal.reviewedBy, let reviewedAt = proposal.reviewedAt {
                StatusRow(label: "Reviewed by", value: "\(reviewer) · \(shortDate(reviewedAt))", color: .textSecondary)
            }
            if let note = proposal.reviewNote {
                SummaryLine(title: "Note", text: note)
            }

            Divider().background(Color.border)

            Button {
                showShadow.toggle()
                if showShadow && shadowResults == nil {
                    onLoadShadow()
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: showShadow ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.accent)
                    Text(showShadow ? "Hide shadow results" : "Shadow results")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.accent)
                    Spacer()
                }
            }
            .buttonStyle(.plain)

            if showShadow {
                if let results = shadowResults {
                    ProposalShadowResultsView(results: results)
                } else {
                    Text("Loading shadow results…")
                        .operatorBody(color: .textSecondary)
                }
            }

            if proposal.isActionable {
                Divider().background(Color.border)
                HStack(spacing: 10) {
                    Button {
                        onApprove()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                            Text("Approve shadow")
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color.positive)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(actionInProgress)

                    Button {
                        onReject()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "xmark")
                                .font(.system(size: 12, weight: .bold))
                            Text("Reject")
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color.negative)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(actionInProgress)
                }
            }
        }
    }

    private var proposalTitle: String {
        "\(proposal.kind) proposal · \(proposal.proposalId.prefix(8))"
    }

    private var kindColor: Color {
        proposal.kind == "WEIGHT" ? .accent : .warning
    }

    private var statusColor: Color {
        switch proposal.status {
        case "PROPOSED": return .warning
        case "APPROVED_FOR_SHADOW": return .positive
        case "REJECTED": return .negative
        case "ROLLBACK_READY": return .accent
        default: return .textSecondary
        }
    }

    func shortDate(_ iso: String) -> String {
        let trimmed = String(iso.prefix(10))
        return trimmed.isEmpty ? iso : trimmed
    }
}

struct ProposalShadowResultsView: View {
    let results: AlphaProposalShadowResults

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 8) {
                AlphaMetric(title: "Replayed", value: "\(results.replayStats.totalReplayed)", color: .textPrimary)
                AlphaMetric(title: "Up", value: "\(results.replayStats.tierUpgraded)", color: .positive)
                AlphaMetric(title: "Down", value: "\(results.replayStats.tierDowngraded)", color: .warning)
            }
            StatusRow(
                label: "FP reduction",
                value: results.replayStats.expectedFalsePositiveReduction.map { Self.pct($0) } ?? "Not enough data",
                color: .positive
            )
            StatusRow(
                label: "Missed-winner risk",
                value: results.replayStats.expectedMissedWinnerRisk.map { Self.pct($0) } ?? "Not enough data",
                color: .warning
            )
        }
        .padding(10)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private static func pct(_ v: Double) -> String { String(format: "%.1f%%", v * 100) }
}
