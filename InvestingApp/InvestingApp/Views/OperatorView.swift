import SwiftUI

private enum ProposalConfirmAction: Identifiable {
    case approve(id: String)
    case reject(id: String)

    var id: String {
        switch self {
        case .approve(let id): return "approve-\(id)"
        case .reject(let id): return "reject-\(id)"
        }
    }
}

struct OperatorView: View {
    @StateObject private var vm = OperatorViewModel()
    @State private var selectedSection = 0
    @State private var pendingProposalAction: ProposalConfirmAction?

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            SectionPill(title: "Status", index: 0, selected: $selectedSection)
                            SectionPill(title: "Alpha", index: 1, selected: $selectedSection)
                            SectionPill(title: "Report", index: 2, selected: $selectedSection)
                            SectionPill(title: "Outcomes", index: 3, selected: $selectedSection)
                            SectionPill(title: "Learning", index: 4, selected: $selectedSection)
                            SectionPill(title: "Recs", index: 5, selected: $selectedSection)
                            SectionPill(title: "Shadow", index: 6, selected: $selectedSection)
                            SectionPill(title: "Proposals", index: 7, selected: $selectedSection)
                            SectionPill(title: "Actions", index: 8, selected: $selectedSection)
                        }
                        .padding(.horizontal, 16)
                    }
                    .padding(.top, 8)
                    .padding(.bottom, 10)

                    TabView(selection: $selectedSection) {
                        statusPage.tag(0)
                        alphaPage.tag(1)
                        alphaReportPage.tag(2)
                        alphaOutcomesPage.tag(3)
                        alphaLearningPage.tag(4)
                        alphaRecommendationsPage.tag(5)
                        alphaShadowPolicyPage.tag(6)
                        alphaProposalsPage.tag(7)
                        actionsPage.tag(8)
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                }
            }
            .navigationTitle("Operator")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task { await vm.refreshAll() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .foregroundColor(.accent)
                    }
                    .disabled(vm.isLoading)
                }
            }
        }
        .task {
            if vm.backendHealth == nil && vm.alphaCandidates.isEmpty {
                await vm.refreshAll()
            }
        }
        .alert(item: $pendingProposalAction) { action in
            switch action {
            case .approve(let id):
                return Alert(
                    title: Text("Approve for shadow?"),
                    message: Text("Shadow testing only. Live weights are unchanged."),
                    primaryButton: .default(Text("Approve")) {
                        Task { await vm.approveProposal(id: id, note: nil) }
                    },
                    secondaryButton: .cancel()
                )
            case .reject(let id):
                return Alert(
                    title: Text("Reject proposal?"),
                    message: Text("This is permanent. The proposal cannot be recovered."),
                    primaryButton: .destructive(Text("Reject")) {
                        Task { await vm.rejectProposal(id: id, reason: nil) }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
    }

    private var statusPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let error = vm.lastError {
                    StatusBanner(text: error, color: .warning, icon: "wifi.exclamationmark")
                }

                OperatorCard(title: "App Status", icon: "iphone") {
                    StatusRow(label: "Backend health", value: vm.rootHealth?.status.uppercased() ?? "UNKNOWN", color: statusColor(vm.rootHealth?.status))
                    StatusRow(label: "Railway API", value: vm.backendHealth?.status.uppercased() ?? "UNKNOWN", color: statusColor(vm.backendHealth?.status))
                    StatusRow(label: "Database", value: vm.backendHealth?.dbConnected == true ? "CONNECTED" : "UNKNOWN", color: vm.backendHealth?.dbConnected == true ? .positive : .warning)
                    StatusRow(label: "Last successful sync", value: vm.lastSync.map(Self.shortDateTime) ?? "Never", color: vm.lastSync == nil ? .warning : .textPrimary)
                    StatusRow(label: "API_SECRET in app", value: vm.apiSecretConfigured ? "CONFIGURED" : "NOT SET", color: vm.apiSecretConfigured ? .positive : .warning)
                    StatusRow(label: "Build", value: vm.buildInfo, color: .textPrimary)
                }

                OperatorCard(title: "Sideload Reminder", icon: "hammer") {
                    HStack(spacing: 12) {
                        Image(systemName: "calendar.badge.clock")
                            .font(.system(size: 22, weight: .semibold))
                            .foregroundColor(.accent)
                            .frame(width: 42, height: 42)
                            .background(Color.surfaceElevated)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(vm.signingStatusText)
                                .font(.system(size: 16, weight: .semibold))
                                .foregroundColor(.textPrimary)
                            Text("Reminder date: \(Self.shortDate(vm.signingReminderDate))")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textSecondary)
                        }
                        Spacer()
                    }

                    HStack(spacing: 8) {
                        ReminderButton(title: "3d") { vm.setReminder(daysFromNow: 3) }
                        ReminderButton(title: "5d") { vm.setReminder(daysFromNow: 5) }
                        ReminderButton(title: "7d") { vm.setReminder(daysFromNow: 7) }
                    }
                    .padding(.top, 6)
                }

                OperatorCard(title: "Local Cache", icon: "internaldrive") {
                    ForEach(vm.cacheStatus) { entry in
                        StatusRow(
                            label: entry.title,
                            value: entry.hasData ? "\(entry.bytes / 1024) KB" : "EMPTY",
                            color: entry.hasData ? .positive : .textSecondary
                        )
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                if vm.alphaCandidates.isEmpty {
                    OperatorEmptyState(icon: "chart.line.uptrend.xyaxis", title: "No alpha candidates")
                } else {
                    ForEach(vm.alphaCandidates) { candidate in
                        AlphaCandidateCard(candidate: candidate)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaReportPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let report = vm.alphaReport {
                    OperatorCard(title: "Alpha Health", icon: "heart.text.square") {
                        StatusRow(label: "Overall", value: healthLabel(report), color: healthColor(report))
                        StatusRow(label: "Scored", value: "\(report.summary.totalUniqueScored)", color: .textPrimary)
                        StatusRow(label: "Generated", value: report.generatedAt ?? "Unknown", color: .textSecondary)
                    }

                    OperatorCard(title: "Top Issues", icon: "exclamationmark.triangle") {
                        if report.diagnosis.isEmpty {
                            Text("No current alpha issues")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(report.diagnosis.prefix(5)) { issue in
                                IssueRow(issue: issue)
                            }
                        }
                    }

                    OperatorCard(title: "Recommendations", icon: "checklist") {
                        if report.recommendations.isEmpty {
                            Text("No recommendations right now")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(report.recommendations.prefix(5), id: \.self) { item in
                                BulletLine(text: item)
                            }
                        }
                    }

                    OperatorCard(title: "Tier Distribution", icon: "chart.bar") {
                        if report.summary.tierDistribution.isEmpty {
                            Text("No tier data")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(report.summary.tierDistribution.sorted(by: { $0.key < $1.key }), id: \.key) { tier, count in
                                StatusRow(label: tier, value: "\(count)", color: .accent)
                            }
                        }
                    }

                    OperatorCard(title: "Data Quality", icon: "checkmark.shield") {
                        DataQualityRows(dataQuality: report.dataQuality)
                    }
                } else {
                    OperatorEmptyState(icon: "doc.text.magnifyingglass", title: "No alpha report cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaOutcomesPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                OperatorCard(title: "Outcome Counts", icon: "number.square") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Pending", value: "\(vm.pendingOutcomeCount)", color: .warning)
                        AlphaMetric(title: "Complete", value: "\(vm.completedOutcomeCount)", color: .positive)
                        AlphaMetric(title: "Stale", value: "\(vm.staleOutcomeCount)", color: .textSecondary)
                    }
                }

                if vm.alphaOutcomes.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No alpha outcomes")
                } else {
                    ForEach(vm.alphaOutcomes.prefix(20)) { outcome in
                        AlphaOutcomeCard(outcome: outcome)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaLearningPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let learning = vm.alphaLearning {
                    OperatorCard(title: "Learning Status", icon: "brain") {
                        StatusRow(label: "Complete outcomes", value: "\(learning.totalComplete)", color: .textPrimary)
                        StatusRow(
                            label: "False-positive rate",
                            value: learning.falsePositiveRate.map(Self.percent) ?? "Not enough data",
                            color: learning.falsePositiveRate == nil ? .textSecondary : .warning
                        )
                        if let note = learning.note {
                            Text(note)
                                .operatorBody(color: .textSecondary)
                        }
                    }

                    EffectivenessCard(title: "Best Setup Types", icon: "arrow.up.circle", rows: Array(learning.bestSetupTypes.prefix(5)), emptyText: "No complete setup data")
                    EffectivenessCard(title: "Worst Setup Types", icon: "arrow.down.circle", rows: Array(learning.worstSetupTypes.prefix(5)), emptyText: "No complete setup data")
                    EffectivenessCard(title: "Component Effectiveness", icon: "slider.horizontal.3", rows: Array(learning.componentEffectiveness.prefix(6)), emptyText: "No component data")
                    EffectivenessCard(title: "False-Positive Patterns", icon: "xmark.octagon", rows: Array(learning.falsePositivePatterns.prefix(5)), emptyText: "No false-positive pattern yet", showFalsePositive: true)

                    OperatorCard(title: "Suggested Improvements", icon: "wrench.adjustable") {
                        if learning.totalComplete == 0 {
                            Text("Wait for outcomes to complete before tuning.")
                                .operatorBody(color: .textSecondary)
                        } else if learning.falsePositiveRate ?? 0 > 0.35 {
                            Text("Review setups with high false-positive rates before changing thresholds.")
                                .operatorBody(color: .warning)
                        } else {
                            Text("Keep monitoring. No immediate tuning signal.")
                                .operatorBody(color: .textSecondary)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "brain.head.profile", title: "No learning data cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaRecommendationsPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let recommendations = vm.alphaLearningRecommendations {
                    SimulationLabelCard(note: recommendations.note)

                    OperatorCard(title: "Recommendation Status", icon: "lightbulb") {
                        StatusRow(label: "Complete outcomes", value: "\(recommendations.totalCompleteOutcomes)", color: .textPrimary)
                        StatusRow(label: "Component recs", value: "\(recommendations.weightRecommendations.count)", color: .accent)
                        StatusRow(label: "Threshold recs", value: "\(recommendations.thresholdRecommendations.count)", color: .accent)
                        if let warning = recommendations.sampleSizeWarning {
                            Text(warning)
                                .operatorBody(color: .warning)
                        }
                    }

                    OperatorCard(title: "Component Recommendations", icon: "slider.horizontal.3") {
                        if recommendations.weightRecommendations.isEmpty {
                            Text("No component recommendations")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(recommendations.weightRecommendations.prefix(8)) { rec in
                                WeightRecommendationRow(rec: rec)
                            }
                        }
                    }

                    OperatorCard(title: "Threshold Recommendations", icon: "line.3.horizontal.decrease.circle") {
                        if recommendations.thresholdRecommendations.isEmpty {
                            Text("No threshold recommendations")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(recommendations.thresholdRecommendations.prefix(8)) { rec in
                                ThresholdRecommendationRow(rec: rec)
                            }
                        }
                    }

                    if !recommendations.errors.isEmpty {
                        OperatorCard(title: "Warnings", icon: "exclamationmark.triangle") {
                            ForEach(recommendations.errors.prefix(4), id: \.self) { error in
                                BulletLine(text: error)
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "lightbulb", title: "No learning recommendations cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaShadowPolicyPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let policy = vm.alphaShadowPolicy {
                    SimulationLabelCard(note: policy.note)

                    OperatorCard(title: "Replay Summary", icon: "arrow.triangle.2.circlepath") {
                        StatusRow(label: "Live weights", value: "UNCHANGED", color: .positive)
                        StatusRow(label: "Simulation", value: "SHADOW ONLY", color: .warning)
                        StatusRow(label: "Replayed rows", value: "\(policy.replayStats.totalReplayed)", color: .textPrimary)
                        StatusRow(label: "Changed candidates", value: "\(policy.replayStats.changedCandidates.count)", color: .accent)
                        StatusRow(label: "FP reduction", value: policy.replayStats.expectedFalsePositiveReduction.map(Self.percent) ?? "Not enough data", color: .positive)
                        StatusRow(label: "Missed-winner risk", value: policy.replayStats.expectedMissedWinnerRisk.map(Self.percent) ?? "Not enough data", color: .warning)
                    }

                    OperatorCard(title: "Current vs Shadow Weights", icon: "scalemass") {
                        ForEach(policy.shadowWeights.sorted(by: { $0.key < $1.key }), id: \.key) { component, shadow in
                            WeightCompareRow(
                                component: component,
                                current: policy.currentWeights[component],
                                shadow: shadow,
                                delta: policy.weightDeltas[component]
                            )
                        }
                    }

                    OperatorCard(title: "Tier Changes", icon: "arrow.up.arrow.down") {
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Same", value: "\(policy.replayStats.tierUnchanged)", color: .textSecondary)
                            AlphaMetric(title: "Up", value: "\(policy.replayStats.tierUpgraded)", color: .positive)
                            AlphaMetric(title: "Down", value: "\(policy.replayStats.tierDowngraded)", color: .warning)
                        }
                    }

                    OperatorCard(title: "Changed Candidates", icon: "list.bullet.rectangle") {
                        if policy.replayStats.changedCandidates.isEmpty {
                            Text("No tier changes in replay")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(policy.replayStats.changedCandidates.prefix(10)) { change in
                                ShadowCandidateChangeRow(change: change)
                            }
                        }
                    }

                    if !policy.errors.isEmpty {
                        OperatorCard(title: "Warnings", icon: "exclamationmark.triangle") {
                            ForEach(policy.errors.prefix(4), id: \.self) { error in
                                BulletLine(text: error)
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "arrow.triangle.2.circlepath", title: "No shadow policy cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaProposalsPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                SimulationLabelCard(note: "Shadow testing only. Live weights are never changed.")

                if let msg = vm.proposalActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.proposalActionSuccess ? .positive : .warning,
                        icon: vm.proposalActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Proposals", icon: "tray.2") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Active", value: "\(vm.activeProposalCount)", color: .accent)
                        AlphaMetric(title: "Awaiting", value: "\(vm.proposedCount)", color: .warning)
                        AlphaMetric(title: "Total", value: "\(vm.alphaProposals.count)", color: .textPrimary)
                    }

                    Toggle(isOn: $vm.showHistoricalProposals) {
                        Text("Show historical")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(.textSecondary)
                    }
                    .tint(.accent)
                    .onChange(of: vm.showHistoricalProposals) { _ in
                        Task { await vm.refreshProposals() }
                    }

                    ActionButton(
                        title: vm.apiSecretConfigured ? "Generate proposals" : "Generate proposals (no API_SECRET)",
                        icon: "sparkles",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        Task { await vm.generateProposals() }
                    }
                    .disabled(vm.proposalActionInProgress)
                }

                if vm.alphaProposals.isEmpty {
                    OperatorEmptyState(icon: "tray.2", title: "No proposals — run generate first")
                } else {
                    ForEach(vm.alphaProposals) { proposal in
                        ProposalCard(
                            proposal: proposal,
                            shadowResults: vm.proposalShadowResults[proposal.proposalId],
                            actionInProgress: vm.proposalActionInProgress,
                            onLoadShadow: { Task { await vm.loadShadowResults(proposalId: proposal.proposalId) } },
                            onApprove: { pendingProposalAction = .approve(id: proposal.proposalId) },
                            onReject: { pendingProposalAction = .reject(id: proposal.proposalId) }
                        )
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshProposals() }
    }

    private var actionsPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                OperatorCard(title: "Quick Actions", icon: "bolt") {
                    ActionButton(title: "Refresh all data", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshAll() }
                    }
                    ActionButton(title: "Open backend health", icon: "safari", color: .positive) {
                        vm.openBackendHealth()
                    }
                    ActionButton(title: "Copy API debug info", icon: "doc.on.doc", color: .accent) {
                        vm.copyDebugInfo()
                    }
                    ActionButton(title: "Clear local cache", icon: "trash", color: .negative) {
                        vm.clearLocalCache()
                    }
                }

                OperatorCard(title: "Last Sync Error", icon: "exclamationmark.triangle") {
                    Text(vm.lastError ?? "No current sync error")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(vm.lastError == nil ? .textSecondary : .warning)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(16)
        }
    }

    private func statusColor(_ status: String?) -> Color {
        guard let status = status?.lowercased() else { return .warning }
        return status == "ok" ? .positive : .warning
    }

    private func healthLabel(_ report: AlphaReport) -> String {
        if report.summary.totalUniqueScored == 0 { return "NO DATA" }
        if report.diagnosis.contains(where: { $0.severity.uppercased() == "HIGH" }) { return "CHECK" }
        if report.diagnosis.contains(where: { $0.severity.uppercased() == "MEDIUM" }) { return "WATCH" }
        return "OK"
    }

    private func healthColor(_ report: AlphaReport) -> Color {
        switch healthLabel(report) {
        case "OK": return .positive
        case "WATCH": return .warning
        default: return .negative
        }
    }

    private static func percent(_ value: Double) -> String {
        String(format: "%.1f%%", value * 100)
    }

    private static func shortDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        return formatter.string(from: date)
    }

    private static func shortDateTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }
}

private struct OperatorCard<Content: View>: View {
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

private struct SectionPill: View {
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

private struct StatusRow: View {
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

private struct IssueRow: View {
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

private struct DataQualityRows: View {
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

    private func rate(_ value: Double?) -> String {
        guard let value else { return "Unknown" }
        return String(format: "%.0f%%", value * 100)
    }

    private func rateColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        if value >= 0.50 { return .warning }
        return .positive
    }
}

private struct BulletLine: View {
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

private struct AlphaOutcomeCard: View {
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

    private func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%+.1f%%", value * 100)
    }

    private func returnColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        return value >= 0 ? .positive : .negative
    }
}

private struct EffectivenessCard: View {
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

    private func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.1f%%", value * 100)
    }

    private func returnColor(_ value: Double?) -> Color {
        guard let value else { return .textSecondary }
        return value >= 0 ? .positive : .negative
    }
}

private struct LearningMetric: View {
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

private struct AlphaCandidateCard: View {
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

    private func tierColor(_ tier: String?) -> Color {
        switch tier?.uppercased() {
        case "A", "HIGH", "STRONG": return .positive
        case "B", "MEDIUM": return .warning
        case "C", "LOW": return .textSecondary
        default: return .textPrimary
        }
    }
}

private struct AlphaMetric: View {
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

private struct SummaryLine: View {
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

private extension Text {
    func operatorBody(color: Color) -> some View {
        self
            .font(.system(size: 13, weight: .medium))
            .foregroundColor(color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SimulationLabelCard: View {
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

private struct WeightRecommendationRow: View {
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

private struct ThresholdRecommendationRow: View {
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

private struct WeightCompareRow: View {
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

private struct ShadowCandidateChangeRow: View {
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

private struct Badge: View {
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

private func confidenceColor(_ confidence: String) -> Color {
    switch confidence.uppercased() {
    case "HIGH": return .positive
    case "MEDIUM": return .warning
    default: return .textSecondary
    }
}

private func weight(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.3f", value)
}

private func signedWeight(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.3f", value)
}

private func score(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.1f", value)
}

private func signedScore(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.1f", value)
}

private func percent(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%+.1f%%", value * 100)
}

private func deltaColor(_ value: Double?) -> Color {
    guard let value else { return .textSecondary }
    if value > 0 { return .positive }
    if value < 0 { return .warning }
    return .textSecondary
}

private struct ActionButton: View {
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

private struct ReminderButton: View {
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

private struct StatusBanner: View {
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

private struct OperatorEmptyState: View {
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

private struct ProposalCard: View {
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

    private func shortDate(_ iso: String) -> String {
        let trimmed = String(iso.prefix(10))
        return trimmed.isEmpty ? iso : trimmed
    }
}

private struct ProposalShadowResultsView: View {
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
