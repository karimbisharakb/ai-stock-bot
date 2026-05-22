import SwiftUI
import UIKit
import Charts

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

private enum DryRunConfirmAction: Identifiable {
    case review(id: String)
    case dismiss(id: String)
    case send(id: String)

    var id: String {
        switch self {
        case .review(let id): return "review-\(id)"
        case .dismiss(let id): return "dismiss-\(id)"
        case .send(let id): return "send-\(id)"
        }
    }
}

private enum PortfolioConfirmAction: Identifiable {
    case reconcile
    case updateManualAccount
    case deactivateManualPosition(ticker: String)
    case manualReconcile
    case saveThesis
    case appendJournal
    case createDecisionChecklist
    case approveDecisionChecklist(id: String)
    case rejectDecisionChecklist(id: String)
    case refreshMarketRegime
    case createReplayRun
    case runPortfolioStress
    case refreshPlanner

    var id: String {
        switch self {
        case .reconcile: return "reconcile"
        case .updateManualAccount: return "update-manual-account"
        case .deactivateManualPosition(let ticker): return "deactivate-\(ticker)"
        case .manualReconcile: return "manual-reconcile"
        case .saveThesis: return "save-thesis"
        case .appendJournal: return "append-journal"
        case .createDecisionChecklist: return "create-decision-checklist"
        case .approveDecisionChecklist(let id): return "approve-decision-\(id)"
        case .rejectDecisionChecklist(let id): return "reject-decision-\(id)"
        case .refreshMarketRegime: return "refresh-market-regime"
        case .createReplayRun: return "create-replay-run"
        case .runPortfolioStress: return "run-portfolio-stress"
        case .refreshPlanner: return "refresh-planner"
        }
    }
}

private enum ResearchWatchlistConfirmAction: Identifiable {
    case save
    case note
    case archive(ticker: String)

    var id: String {
        switch self {
        case .save: return "research-watchlist-save"
        case .note: return "research-watchlist-note"
        case .archive(let ticker): return "research-watchlist-archive-\(ticker)"
        }
    }
}

private enum ResearchWorkflowConfirmAction: Identifiable {
    case done(itemId: String)
    case archive(itemId: String)

    var id: String {
        switch self {
        case .done(let itemId): return "workflow-done-\(itemId)"
        case .archive(let itemId): return "workflow-archive-\(itemId)"
        }
    }
}

private enum CatalystConfirmAction: Identifiable {
    case save
    case complete(id: String)
    case archive(id: String)

    var id: String {
        switch self {
        case .save: return "catalyst-save"
        case .complete(let id): return "catalyst-complete-\(id)"
        case .archive(let id): return "catalyst-archive-\(id)"
        }
    }
}

private enum NotificationConfirmAction: Identifiable {
    case dismiss(id: String)
    case archive(id: String)
    case archiveRead
    case savePreferences
    case saveCategory(category: String)

    var id: String {
        switch self {
        case .dismiss(let id): return "notification-dismiss-\(id)"
        case .archive(let id): return "notification-archive-\(id)"
        case .archiveRead: return "notification-archive-read"
        case .savePreferences: return "notification-save-preferences"
        case .saveCategory(let category): return "notification-save-category-\(category)"
        }
    }
}

let researchPeriods = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "Max"]

struct OperatorView: View {
    @StateObject private var vm = OperatorViewModel()
    @State private var selectedSection = 0
    @State private var pendingProposalAction: ProposalConfirmAction?
    @State private var pendingDryRunAction: DryRunConfirmAction?
    @State private var pendingPortfolioAction: PortfolioConfirmAction?
    @State private var pendingResearchWatchlistAction: ResearchWatchlistConfirmAction?
    @State private var pendingResearchWorkflowAction: ResearchWorkflowConfirmAction?
    @State private var pendingCatalystAction: CatalystConfirmAction?
    @State private var pendingNotificationAction: NotificationConfirmAction?
    @State private var pendingBackupAction: BackupConfirmAction?

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
                            SectionPill(title: "Validate", index: 8, selected: $selectedSection)
                            SectionPill(title: "Records", index: 9, selected: $selectedSection)
                            SectionPill(title: "Alerts", index: 10, selected: $selectedSection)
                            SectionPill(title: "Gate", index: 11, selected: $selectedSection)
                            SectionPill(title: "Dry Run", index: 12, selected: $selectedSection)
                            SectionPill(title: "QC", index: 13, selected: $selectedSection)
                            SectionPill(title: "QC Sum", index: 14, selected: $selectedSection)
                            SectionPill(title: "Delivery", index: 15, selected: $selectedSection)
                            SectionPill(title: "Truth", index: 16, selected: $selectedSection)
                            SectionPill(title: "Recon", index: 17, selected: $selectedSection)
                            SectionPill(title: "Snaps", index: 18, selected: $selectedSection)
                            SectionPill(title: "Manual", index: 19, selected: $selectedSection)
                            SectionPill(title: "Thesis", index: 20, selected: $selectedSection)
                            SectionPill(title: "Reviews", index: 21, selected: $selectedSection)
                            SectionPill(title: "Checklist", index: 22, selected: $selectedSection)
                            SectionPill(title: "Decisions", index: 23, selected: $selectedSection)
                            SectionPill(title: "Risk", index: 24, selected: $selectedSection)
                            SectionPill(title: "Size", index: 25, selected: $selectedSection)
                            SectionPill(title: "Regime", index: 26, selected: $selectedSection)
                            SectionPill(title: "Reg Hist", index: 27, selected: $selectedSection)
                            SectionPill(title: "Replay", index: 28, selected: $selectedSection)
                            SectionPill(title: "Events", index: 29, selected: $selectedSection)
                            SectionPill(title: "Stress", index: 30, selected: $selectedSection)
                            SectionPill(title: "Stress Hist", index: 31, selected: $selectedSection)
                            SectionPill(title: "Strategy", index: 32, selected: $selectedSection)
                            SectionPill(title: "Planner", index: 33, selected: $selectedSection)
                            SectionPill(title: "Brief", index: 34, selected: $selectedSection)
                            SectionPill(title: "EOD", index: 35, selected: $selectedSection)
                            SectionPill(title: "Pulse", index: 36, selected: $selectedSection)
                            SectionPill(title: "Sectors", index: 37, selected: $selectedSection)
                            SectionPill(title: "Stock", index: 38, selected: $selectedSection)
                            SectionPill(title: "ETF", index: 39, selected: $selectedSection)
                            SectionPill(title: "Macro", index: 40, selected: $selectedSection)
                            SectionPill(title: "News", index: 41, selected: $selectedSection)
                            SectionPill(title: "Watch", index: 42, selected: $selectedSection)
                            SectionPill(title: "Watch Suggs", index: 43, selected: $selectedSection)
                            SectionPill(title: "Watch Detail", index: 44, selected: $selectedSection)
                            SectionPill(title: "Workflow", index: 45, selected: $selectedSection)
                            SectionPill(title: "WF Summary", index: 46, selected: $selectedSection)
                            SectionPill(title: "Weekly", index: 47, selected: $selectedSection)
                            SectionPill(title: "Weekly Hist", index: 48, selected: $selectedSection)
                            SectionPill(title: "Catalysts", index: 49, selected: $selectedSection)
                            SectionPill(title: "Cat Summary", index: 50, selected: $selectedSection)
                            SectionPill(title: "Cat Detail", index: 51, selected: $selectedSection)
                            SectionPill(title: "Inbox", index: 52, selected: $selectedSection)
                            SectionPill(title: "Inbox Summary", index: 53, selected: $selectedSection)
                            SectionPill(title: "Inbox Detail", index: 54, selected: $selectedSection)
                            SectionPill(title: "Prefs", index: 55, selected: $selectedSection)
                            SectionPill(title: "Pref Cats", index: 56, selected: $selectedSection)
                            SectionPill(title: "Digest", index: 57, selected: $selectedSection)
                            SectionPill(title: "System", index: 58, selected: $selectedSection)
                            SectionPill(title: "Routes", index: 59, selected: $selectedSection)
                            SectionPill(title: "Flags", index: 60, selected: $selectedSection)
                            SectionPill(title: "Actions", index: 61, selected: $selectedSection)
                            SectionPill(title: "Backups", index: 62, selected: $selectedSection)
                            SectionPill(title: "Bk Detail", index: 63, selected: $selectedSection)
                            SectionPill(title: "Bk Create", index: 64, selected: $selectedSection)
                            SectionPill(title: "Bk Verify", index: 65, selected: $selectedSection)
                            SectionPill(title: "Bk Preview", index: 66, selected: $selectedSection)
                            SectionPill(title: "Bk Info", index: 67, selected: $selectedSection)
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
                        alphaValidationSummaryPage.tag(8)
                        alphaValidationRecordsPage.tag(9)
                        alphaAlertReadinessPage.tag(10)
                        alphaAlertGatePage.tag(11)
                        alphaDryRunNotificationsPage.tag(12)
                        alphaNotificationQCPage.tag(13)
                        alphaNotificationQCSummaryPage.tag(14)
                        alphaDeliveryPage.tag(15)
                        portfolioTruthPage.tag(16)
                        portfolioReconciliationPage.tag(17)
                        portfolioSnapshotsPage.tag(18)
                        manualPortfolioPage.tag(19)
                        portfolioThesisPage.tag(20)
                        portfolioReviewsPage.tag(21)
                        decisionChecklistPage.tag(22)
                        decisionSummaryPage.tag(23)
                        portfolioRiskPage.tag(24)
                        sizeCheckPage.tag(25)
                        marketRegimePage.tag(26)
                        marketRegimeHistoryPage.tag(27)
                        replayRunsPage.tag(28)
                        replayEventsPage.tag(29)
                        portfolioStressPage.tag(30)
                        portfolioStressHistoryPage.tag(31)
                        strategyPage.tag(32)
                        plannerPage.tag(33)
                        briefPage.tag(34)
                        eodBriefPage.tag(35)
                        marketPulsePage.tag(36)
                        sectorPerformancePage.tag(37)
                        stockResearchPage.tag(38)
                        etfResearchPage.tag(39)
                        macroResearchPage.tag(40)
                        newsResearchPage.tag(41)
                        researchWatchlistPage.tag(42)
                        researchWatchlistSuggestionsPage.tag(43)
                        researchWatchlistDetailPage.tag(44)
                        researchWorkflowQueuePage.tag(45)
                        researchWorkflowSummaryPage.tag(46)
                        weeklyReviewPage.tag(47)
                        weeklyReviewHistoryPage.tag(48)
                        catalystCalendarPage.tag(49)
                        catalystSummaryPage.tag(50)
                        catalystDetailPage.tag(51)
                        notificationCenterPage.tag(52)
                        notificationSummaryPage.tag(53)
                        notificationDetailPage.tag(54)
                        notificationPreferencesPage.tag(55)
                        notificationCategoryPreferencesPage.tag(56)
                        notificationDigestPage.tag(57)
                        systemHealthPage.tag(58)
                        systemRoutesPage.tag(59)
                        systemFlagsPage.tag(60)
                        actionsPage.tag(61)
                        backupListPage.tag(62)
                        backupDetailPage.tag(63)
                        backupCreatePage.tag(64)
                        backupVerifyPage.tag(65)
                        backupRestorePreviewPage.tag(66)
                        backupDownloadInfoPage.tag(67)
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
        .alert(item: $pendingDryRunAction) { action in
            switch action {
            case .review(let id):
                return Alert(
                    title: Text("Mark reviewed?"),
                    message: Text("Dry-run only. No WhatsApp notification is sent."),
                    primaryButton: .default(Text("Review")) {
                        Task { await vm.reviewDryRun(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .dismiss(let id):
                return Alert(
                    title: Text("Dismiss dry-run?"),
                    message: Text("Dry-run only. No WhatsApp notification is sent."),
                    primaryButton: .destructive(Text("Dismiss")) {
                        Task { await vm.dismissDryRun(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .send(let id):
                return Alert(
                    title: Text("Attempt delivery?"),
                    message: Text("This may send a real WhatsApp alert if backend flags are enabled."),
                    primaryButton: .destructive(Text("Attempt send")) {
                        Task { await vm.sendReviewedDryRun(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingPortfolioAction) { action in
            switch action {
            case .reconcile:
                return Alert(
                    title: Text("Run portfolio reconcile?"),
                    message: Text("This refreshes canonical portfolio truth. It does not place orders or change holdings."),
                    primaryButton: .default(Text("Run reconcile")) {
                        Task { await vm.reconcilePortfolio() }
                    },
                    secondaryButton: .cancel()
                )
            case .updateManualAccount:
                return Alert(
                    title: Text("Update account settings?"),
                    message: Text("This changes the manual truth source. No trades are placed."),
                    primaryButton: .default(Text("Update")) {
                        Task { await vm.updateManualAccount() }
                    },
                    secondaryButton: .cancel()
                )
            case .deactivateManualPosition(let ticker):
                return Alert(
                    title: Text("Deactivate \(ticker)?"),
                    message: Text("This keeps the audit trail and never deletes the position."),
                    primaryButton: .destructive(Text("Deactivate")) {
                        Task { await vm.deactivateManualPosition(ticker: ticker) }
                    },
                    secondaryButton: .cancel()
                )
            case .manualReconcile:
                return Alert(
                    title: Text("Run manual reconcile?"),
                    message: Text("This rebuilds canonical portfolio state from manual positions. No trades are placed."),
                    primaryButton: .default(Text("Run reconcile")) {
                        Task { await vm.reconcileManualPortfolio() }
                    },
                    secondaryButton: .cancel()
                )
            case .saveThesis:
                return Alert(
                    title: Text("Save thesis?"),
                    message: Text("This updates thesis and review notes only. No trades are placed."),
                    primaryButton: .default(Text("Save")) {
                        Task { await vm.saveThesis() }
                    },
                    secondaryButton: .cancel()
                )
            case .appendJournal:
                return Alert(
                    title: Text("Add journal entry?"),
                    message: Text("Journal entries are append-only. No trades are placed."),
                    primaryButton: .default(Text("Add entry")) {
                        Task { await vm.appendJournalEntry() }
                    },
                    secondaryButton: .cancel()
                )
            case .createDecisionChecklist:
                return Alert(
                    title: Text("Create checklist?"),
                    message: Text("Manual discipline only. This does not place trades."),
                    primaryButton: .default(Text("Create")) {
                        Task { await vm.createDecisionChecklist() }
                    },
                    secondaryButton: .cancel()
                )
            case .approveDecisionChecklist(let id):
                return Alert(
                    title: Text("Approve checklist?"),
                    message: Text("Approval does NOT place trades."),
                    primaryButton: .default(Text("Approve")) {
                        Task { await vm.approveDecisionChecklist(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .rejectDecisionChecklist(let id):
                return Alert(
                    title: Text("Reject checklist?"),
                    message: Text("This records a rejected manual decision. No trades are placed."),
                    primaryButton: .destructive(Text("Reject")) {
                        Task { await vm.rejectDecisionChecklist(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .refreshMarketRegime:
                return Alert(
                    title: Text("Refresh market regime?"),
                    message: Text("This updates market context only. No trades are placed."),
                    primaryButton: .default(Text("Refresh")) {
                        Task { await vm.runMarketRegimeRefresh() }
                    },
                    secondaryButton: .cancel()
                )
            case .createReplayRun:
                return Alert(
                    title: Text("Run historical replay?"),
                    message: Text("Simulation only. No trades or notifications are sent."),
                    primaryButton: .default(Text("Run replay")) {
                        Task { await vm.createReplayRun() }
                    },
                    secondaryButton: .cancel()
                )
            case .runPortfolioStress:
                return Alert(
                    title: Text("Run portfolio stress test?"),
                    message: Text("Stress test only. No trades are placed."),
                    primaryButton: .default(Text("Run stress test")) {
                        Task { await vm.runPortfolioStress() }
                    },
                    secondaryButton: .cancel()
                )
            case .refreshPlanner:
                return Alert(
                    title: Text("Refresh long-horizon planner?"),
                    message: Text("Planning only. No trades are placed. This is not tax or legal advice."),
                    primaryButton: .default(Text("Refresh planner")) {
                        Task { await vm.refreshPlannerSnapshot() }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingResearchWatchlistAction) { action in
            switch action {
            case .save:
                return Alert(
                    title: Text("Save research watchlist item?"),
                    message: Text("Research only. This does not place trades."),
                    primaryButton: .default(Text("Save")) {
                        Task { await vm.saveResearchWatchlistItem() }
                    },
                    secondaryButton: .cancel()
                )
            case .note:
                return Alert(
                    title: Text("Add append-only note?"),
                    message: Text("The note is added to research memory only. No trades are placed."),
                    primaryButton: .default(Text("Add note")) {
                        Task { await vm.appendResearchWatchlistNote() }
                    },
                    secondaryButton: .cancel()
                )
            case .archive(let ticker):
                return Alert(
                    title: Text("Archive \(ticker)?"),
                    message: Text("This never deletes the item. It only changes the research status to archived."),
                    primaryButton: .destructive(Text("Archive")) {
                        Task { await vm.archiveResearchWatchlistItem(ticker: ticker) }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingResearchWorkflowAction) { action in
            switch action {
            case .done(let itemId):
                return Alert(
                    title: Text("Mark workflow done?"),
                    message: Text("Research workflow only. No trades are placed."),
                    primaryButton: .default(Text("Done")) {
                        Task { await vm.completeResearchWorkflowItem(itemId) }
                    },
                    secondaryButton: .cancel()
                )
            case .archive(let itemId):
                return Alert(
                    title: Text("Archive workflow item?"),
                    message: Text("Archive only. This never deletes research history and never places trades."),
                    primaryButton: .destructive(Text("Archive")) {
                        Task { await vm.archiveResearchWorkflowItem(itemId) }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingCatalystAction) { action in
            switch action {
            case .save:
                return Alert(
                    title: Text("Save catalyst?"),
                    message: Text("Event tracking only. No trades are placed."),
                    primaryButton: .default(Text("Save")) {
                        Task { await vm.saveCatalyst() }
                    },
                    secondaryButton: .cancel()
                )
            case .complete(let id):
                return Alert(
                    title: Text("Mark catalyst complete?"),
                    message: Text("This updates event tracking only. No trades are placed."),
                    primaryButton: .default(Text("Complete")) {
                        Task { await vm.completeCatalyst(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .archive(let id):
                return Alert(
                    title: Text("Archive catalyst?"),
                    message: Text("Archive only. This never deletes the event and never places trades."),
                    primaryButton: .destructive(Text("Archive")) {
                        Task { await vm.archiveCatalyst(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingNotificationAction) { action in
            switch action {
            case .dismiss(let id):
                return Alert(
                    title: Text("Dismiss notification?"),
                    message: Text("In-app inbox only. No push notification is sent and no trades are placed."),
                    primaryButton: .destructive(Text("Dismiss")) {
                        Task { await vm.dismissNotification(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .archive(let id):
                return Alert(
                    title: Text("Archive notification?"),
                    message: Text("Archive only. This never deletes the notification and never places trades."),
                    primaryButton: .destructive(Text("Archive")) {
                        Task { await vm.archiveNotification(id: id) }
                    },
                    secondaryButton: .cancel()
                )
            case .archiveRead:
                return Alert(
                    title: Text("Archive all read notifications?"),
                    message: Text("Archive only. No notifications are deleted, no push is sent, and no trades are placed."),
                    primaryButton: .destructive(Text("Archive read")) {
                        Task { await vm.archiveReadNotifications() }
                    },
                    secondaryButton: .cancel()
                )
            case .savePreferences:
                return Alert(
                    title: Text("Save notification preferences?"),
                    message: Text("Preferences only. No push, no WhatsApp, and no trades are placed."),
                    primaryButton: .default(Text("Save")) {
                        Task { await vm.saveNotificationPreferences() }
                    },
                    secondaryButton: .cancel()
                )
            case .saveCategory(let category):
                return Alert(
                    title: Text("Save \(NotificationLabels.category(category)) override?"),
                    message: Text("This changes in-app inbox and digest filtering only. No messages are sent."),
                    primaryButton: .default(Text("Save")) {
                        if let override = vm.notificationCategoryPreferences.first(where: { $0.category == category }) {
                            Task { await vm.updateNotificationCategoryPreference(override) }
                        }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
        .alert(item: $pendingBackupAction) { action in
            switch action {
            case .createBackup:
                return Alert(
                    title: Text("Create backup?"),
                    message: Text("Backup only. No restore. No trades placed. No notifications sent."),
                    primaryButton: .default(Text("Create")) {
                        Task { await vm.createBackup() }
                    },
                    secondaryButton: .cancel()
                )
            case .verifyBackup(let id):
                return Alert(
                    title: Text("Verify backup?"),
                    message: Text("Read-only checksum and structure check. No data is written or restored."),
                    primaryButton: .default(Text("Verify")) {
                        Task { await vm.verifySelectedBackup() }
                    },
                    secondaryButton: .cancel()
                )
            case .restorePreview(let id):
                return Alert(
                    title: Text("Load restore preview?"),
                    message: Text("Preview only — no restore performed. Compares backup vs live row counts. No data is written."),
                    primaryButton: .default(Text("Preview")) {
                        Task { await vm.fetchRestorePreviewForSelectedBackup() }
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
                    .onChange(of: vm.showHistoricalProposals) {
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

    private var alphaValidationSummaryPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                LearningSignalLabelCard()

                if let summary = vm.alphaValidationSummary {
                    OperatorCard(title: "Validation Summary", icon: "checkmark.seal") {
                        StatusRow(label: "Validated", value: "\(summary.totalValidated)", color: .textPrimary)
                        StatusRow(label: "Avg score", value: score(summary.avgValidationScore), color: .accent)
                        StatusRow(label: "Trap rate", value: plainPercent(summary.overallTrapRate), color: .warning)
                        StatusRow(label: "Sustainability", value: plainPercent(summary.overallSustainabilityRate), color: .positive)
                        if let note = summary.note {
                            Text(note)
                                .operatorBody(color: .textSecondary)
                        }
                    }

                    OperatorCard(title: "Validation Distribution", icon: "chart.pie") {
                        if summary.behaviorDistribution.isEmpty {
                            Text("No validation distribution yet")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.behaviorDistribution.sorted(by: { $0.key < $1.key }), id: \.key) { behavior, count in
                                StatusRow(label: AlphaValidationBehavior.label(for: behavior), value: "\(count)", color: behaviorColor(behavior))
                            }
                        }
                    }

                    OperatorCard(title: "Best Validated Setups", icon: "arrow.up.circle") {
                        if summary.bestValidatedSetups.isEmpty {
                            Text("No validated setup leaderboard")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.bestValidatedSetups.prefix(5)) { item in
                                StatusRow(label: item.setupType, value: score(item.avgValidationScore), color: .positive)
                            }
                        }
                    }

                    OperatorCard(title: "Trap-Prone Setups", icon: "exclamationmark.triangle") {
                        if summary.worstTrapProneSetups.isEmpty {
                            Text("No trap-prone setup data")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.worstTrapProneSetups.prefix(5)) { item in
                                StatusRow(label: item.setupType, value: plainPercent(item.trapRate), color: .warning)
                            }
                        }
                    }

                    ValidationLeaderboardCard(
                        title: "Sustainability Leaderboard",
                        icon: "leaf",
                        rows: vm.sustainabilityLeaderboard,
                        valueColor: .positive,
                        emptyText: "No sustained setup data"
                    )

                    ValidationLeaderboardCard(
                        title: "Fake Breakout Leaderboard",
                        icon: "xmark.octagon",
                        rows: vm.fakeBreakoutLeaderboard,
                        valueColor: .warning,
                        emptyText: "No failed breakout data"
                    )
                } else {
                    OperatorEmptyState(icon: "checkmark.seal", title: "No validation summary cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaValidationRecordsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                LearningSignalLabelCard()

                OperatorCard(title: "Recent Validations", icon: "list.bullet.clipboard") {
                    StatusRow(label: "Cached records", value: "\(vm.validationRecordCount)", color: .textPrimary)
                }

                if vm.alphaValidations.isEmpty {
                    OperatorEmptyState(icon: "list.bullet.clipboard", title: "No validation records")
                } else {
                    ForEach(vm.alphaValidations.prefix(30)) { record in
                        ValidationRecordCard(record: record)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaAlertReadinessPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReadinessOnlyLabelCard()

                OperatorCard(title: "Alert Readiness", icon: "bell.badge") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Candidates", value: "\(vm.alphaAlertCandidates.count)", color: .textPrimary)
                        AlphaMetric(title: "Alert-worthy", value: "\(vm.alertReadyCount)", color: .positive)
                        AlphaMetric(title: "Need proof", value: "\(max(vm.alphaAlertCandidates.count - vm.alertReadyCount, 0))", color: .warning)
                    }
                }

                if vm.alphaAlertCandidates.isEmpty {
                    OperatorEmptyState(icon: "bell.slash", title: "No alert readiness candidates")
                } else {
                    ForEach(vm.alphaAlertCandidates.prefix(30)) { candidate in
                        AlertCandidateCard(candidate: candidate)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaAlertGatePage: some View {
        ScrollView {
            VStack(spacing: 12) {
                ReadinessOnlyLabelCard()

                if let summary = vm.alphaAlertGateSummary {
                    OperatorCard(title: "Gate Summary", icon: "lock.shield") {
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Evaluated", value: "\(summary.totalEvaluated)", color: .textPrimary)
                            AlphaMetric(title: "Close", value: "\(summary.nearAlertCount)", color: .warning)
                            AlphaMetric(title: "Ready", value: "\(summary.alertReadyCount)", color: .positive)
                        }
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Validation reject", value: "\(summary.rejectedDueToValidation)", color: .warning)
                            AlphaMetric(title: "Trap reject", value: "\(summary.rejectedDueToTrapRisk)", color: .warning)
                        }
                        if let note = summary.note {
                            Text(note)
                                .operatorBody(color: .textSecondary)
                        }
                    }

                    OperatorCard(title: "Readiness Tiers", icon: "chart.pie") {
                        if summary.readinessDistribution.isEmpty {
                            Text("No readiness distribution yet")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.readinessDistribution.sorted(by: { $0.key < $1.key }), id: \.key) { tier, count in
                                StatusRow(label: AlphaAlertReadiness.label(for: tier), value: "\(count)", color: readinessColor(tier))
                            }
                        }
                    }

                    OperatorCard(title: "Top Blockers", icon: "exclamationmark.octagon") {
                        if summary.topBlockers.isEmpty {
                            Text("No blockers reported")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.topBlockers.prefix(5)) { item in
                                StatusRow(label: item.factor, value: "\(item.count)", color: .warning)
                            }
                        }
                    }

                    OperatorCard(title: "Confirmation Needs", icon: "checklist") {
                        if summary.topConfirmationsNeeded.isEmpty {
                            Text("No confirmation needs reported")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.topConfirmationsNeeded.prefix(5)) { item in
                                StatusRow(label: item.confirmation, value: "\(item.count)", color: .accent)
                            }
                        }
                    }

                    OperatorCard(title: "Top Ready Setups", icon: "bell") {
                        if summary.topAlertReady.isEmpty {
                            Text("No alert-ready setups right now")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.topAlertReady.prefix(5)) { item in
                                HStack(alignment: .firstTextBaseline) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.ticker)
                                            .font(.system(size: 13, weight: .bold))
                                            .foregroundColor(.textPrimary)
                                        Text(item.setupType ?? "Unknown setup")
                                            .font(.system(size: 11, weight: .medium))
                                            .foregroundColor(.textSecondary)
                                    }
                                    Spacer()
                                    Text(score(item.readinessScore))
                                        .font(.system(size: 13, weight: .bold))
                                        .foregroundColor(.positive)
                                }
                                .padding(10)
                                .background(Color.surfaceElevated)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "lock.shield", title: "No alert gate summary cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshAll() }
    }

    private var alphaDryRunNotificationsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                NoWhatsAppLabelCard()

                if let msg = vm.notificationActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.notificationActionSuccess ? .positive : .warning,
                        icon: vm.notificationActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Dry-run Notifications", icon: "message.badge") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Active", value: "\(vm.activeDryRunCount)", color: .warning)
                        AlphaMetric(title: "Total", value: "\(vm.alphaDryRunNotifications.count)", color: .textPrimary)
                    }

                    ActionButton(
                        title: vm.apiSecretConfigured ? "Generate dry-runs" : "Generate dry-runs (no API_SECRET)",
                        icon: "plus.message",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        Task { await vm.generateDryRunNotifications() }
                    }
                    .disabled(vm.notificationActionInProgress)
                }

                if vm.alphaDryRunNotifications.isEmpty {
                    OperatorEmptyState(icon: "message.badge", title: "No dry-run notifications")
                } else {
                    ForEach(vm.alphaDryRunNotifications.prefix(30)) { item in
                        DryRunNotificationCard(
                            item: item,
                            actionInProgress: vm.notificationActionInProgress,
                            onReview: { pendingDryRunAction = .review(id: item.dryRunId) },
                            onDismiss: { pendingDryRunAction = .dismiss(id: item.dryRunId) },
                            deliveryInProgress: vm.deliveryActionInProgress,
                            onSend: { pendingDryRunAction = .send(id: item.dryRunId) }
                        )
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshNotificationDryRunsAndQC() }
    }

    private var alphaNotificationQCPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                NoWhatsAppLabelCard()

                OperatorCard(title: "QC Results", icon: "checkmark.shield") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Records", value: "\(vm.alphaNotificationQCRecords.count)", color: .textPrimary)
                        AlphaMetric(title: "Would pass", value: "\(vm.qcAllowedCount)", color: .positive)
                        AlphaMetric(title: "Would not", value: "\(max(vm.alphaNotificationQCRecords.count - vm.qcAllowedCount, 0))", color: .warning)
                    }
                }

                if vm.alphaNotificationQCRecords.isEmpty {
                    OperatorEmptyState(icon: "checkmark.shield", title: "No QC records")
                } else {
                    ForEach(vm.alphaNotificationQCRecords.prefix(30)) { record in
                        NotificationQCCard(record: record)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshNotificationDryRunsAndQC() }
    }

    private var alphaDeliveryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                DeliveryWarningLabelCard()

                if let msg = vm.deliveryActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.deliveryActionSuccess ? .positive : .warning,
                        icon: vm.deliveryActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                DeliverySafetyCard(flags: vm.alphaDeliveryFlags)

                OperatorCard(title: "Delivery Log", icon: "paperplane") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Attempts", value: "\(vm.alphaDeliveryLog.count)", color: .textPrimary)
                        AlphaMetric(title: "Reviewed", value: "\(vm.reviewedDryRunCount)", color: .accent)
                    }
                }

                if vm.alphaDeliveryLog.isEmpty {
                    OperatorEmptyState(icon: "paperplane", title: "No delivery attempts")
                } else {
                    ForEach(vm.alphaDeliveryLog.prefix(30)) { entry in
                        DeliveryLogCard(entry: entry)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshDeliveryLog() }
    }

    private var alphaNotificationQCSummaryPage: some View {
        ScrollView {
            VStack(spacing: 12) {
                NoWhatsAppLabelCard()

                if let summary = vm.alphaNotificationQCSummary {
                    OperatorCard(title: "QC Summary", icon: "chart.bar.doc.horizontal") {
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Evaluated", value: "\(summary.totalEvaluated)", color: .textPrimary)
                            AlphaMetric(title: "Suppressed", value: "\(summary.suppressedCount)", color: .warning)
                            AlphaMetric(title: "Allowed", value: "\(summary.allowedCount)", color: .positive)
                        }
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Avg QC", value: score(summary.avgQCScore), color: .accent)
                            AlphaMetric(title: "Priority", value: "\(summary.priorityCandidates)", color: .positive)
                            AlphaMetric(title: "Cooldown", value: "\(summary.cooldownActiveCount)", color: .warning)
                        }
                        StatusRow(label: "Duplicates", value: "\(summary.duplicateSuppressions)", color: .warning)
                        StatusRow(label: "Unstable", value: "\(summary.unstableSuppressions)", color: .warning)
                        StatusRow(label: "Low quality", value: "\(summary.lowQualitySuppressions)", color: .warning)
                        if let note = summary.note {
                            Text(note)
                                .operatorBody(color: .textSecondary)
                        }
                    }

                    OperatorCard(title: "QC Tiers", icon: "chart.pie") {
                        if summary.qcTierDistribution.isEmpty {
                            Text("No QC tier distribution yet")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(summary.qcTierDistribution.sorted(by: { $0.key < $1.key }), id: \.key) { tier, count in
                                StatusRow(label: AlphaNotificationQC.label(for: tier), value: "\(count)", color: qcColor(tier))
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "chart.bar.doc.horizontal", title: "No QC summary cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshNotificationDryRunsAndQC() }
    }

    private var portfolioTruthPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                CanonicalPortfolioLabelCard()

                if let msg = vm.portfolioActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.portfolioActionSuccess ? .positive : .warning,
                        icon: vm.portfolioActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                if let portfolio = vm.canonicalPortfolio {
                    let aggregates = portfolio.aggregates
                    OperatorCard(title: "Portfolio Truth", icon: "checkmark.seal") {
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Value", value: money(aggregates.totalPortfolioValue), color: .textPrimary)
                            AlphaMetric(title: "Cash", value: money(aggregates.cash), color: .accent)
                            AlphaMetric(title: "Equity", value: money(aggregates.totalMarketValue), color: .textPrimary)
                        }
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Realized", value: money(aggregates.totalRealizedPnL), color: pnlColor(aggregates.totalRealizedPnL))
                            AlphaMetric(title: "Unrealized", value: money(aggregates.totalUnrealizedPnL), color: pnlColor(aggregates.totalUnrealizedPnL))
                            AlphaMetric(title: "Stale", value: "\(aggregates.staleCount)", color: aggregates.staleCount == 0 ? .positive : .warning)
                        }
                        StatusRow(label: "Largest position", value: vm.largestCanonicalPosition?.ticker ?? "-", color: .textPrimary)
                        StatusRow(label: "Concentration", value: percentFromWhole(vm.largestCanonicalPosition?.concentrationPercent), color: concentrationColor(vm.largestCanonicalPosition?.concentrationPercent))
                        StatusRow(label: "Snapshot timestamp", value: aggregates.reconciledAt ?? "Unknown", color: .textSecondary)
                    }

                    OperatorCard(title: "Manual Reconcile", icon: "arrow.triangle.2.circlepath") {
                        Text("Refreshes canonical portfolio truth. No orders are placed and holdings are not changed.")
                            .operatorBody(color: .textSecondary)
                        ActionButton(
                            title: vm.apiSecretConfigured ? "Run reconcile" : "Run reconcile (no API_SECRET)",
                            icon: "arrow.triangle.2.circlepath",
                            color: vm.apiSecretConfigured ? .accent : .warning
                        ) {
                            pendingPortfolioAction = .reconcile
                        }
                        .disabled(vm.portfolioActionInProgress)
                    }

                    if portfolio.positions.isEmpty {
                        OperatorEmptyState(icon: "briefcase", title: "No canonical positions")
                    } else {
                        ForEach(portfolio.positions) { position in
                            CanonicalPositionCard(position: position)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "briefcase", title: "No canonical portfolio cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshCanonicalPortfolio() }
    }

    private var portfolioReconciliationPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                CanonicalPortfolioLabelCard()

                OperatorCard(title: "Drift Warnings", icon: "exclamationmark.triangle") {
                    if let latest = vm.portfolioReconciliationRuns.first {
                        let grouped = groupedPortfolioIssues(latest.issues)
                        StatusRow(label: "Latest status", value: latest.status, color: latest.status == "OK" ? .positive : .warning)
                        StatusRow(label: "Missing positions", value: "\(grouped.missing.count)", color: grouped.missing.isEmpty ? .positive : .warning)
                        StatusRow(label: "Stale prices", value: "\(grouped.stale.count)", color: grouped.stale.isEmpty ? .positive : .warning)
                        StatusRow(label: "Duplicate/conflict", value: "\(grouped.conflicts.count)", color: grouped.conflicts.isEmpty ? .positive : .warning)
                        StatusRow(label: "Impossible states", value: "\(grouped.impossible.count)", color: grouped.impossible.isEmpty ? .positive : .negative)
                        StatusRow(label: "Concentration risks", value: "\(vm.canonicalPositions.filter { ($0.concentrationPercent ?? 0) >= 25 }.count)", color: vm.canonicalPositions.contains { ($0.concentrationPercent ?? 0) >= 25 } ? .warning : .positive)

                        if latest.issues.isEmpty {
                            Text("No reconciliation issues reported")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(latest.issues.prefix(8), id: \.self) { issue in
                                BulletLine(text: issue.replacingOccurrences(of: "_", with: " "))
                            }
                        }
                    } else {
                        Text("No reconciliation runs cached")
                            .operatorBody(color: .textSecondary)
                    }
                }

                if vm.portfolioReconciliationRuns.isEmpty {
                    OperatorEmptyState(icon: "list.bullet.clipboard", title: "No reconciliation history")
                } else {
                    ForEach(vm.portfolioReconciliationRuns.prefix(20)) { run in
                        ReconciliationRunCard(run: run)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshCanonicalPortfolio() }
    }

    private var portfolioSnapshotsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                CanonicalPortfolioLabelCard()

                OperatorCard(title: "Snapshots", icon: "camera.metering.matrix") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Cached", value: "\(vm.portfolioSnapshots.count)", color: .textPrimary)
                        AlphaMetric(title: "Positions", value: "\(vm.portfolioSnapshots.first?.positionCount ?? 0)", color: .accent)
                        AlphaMetric(title: "Stale", value: "\(vm.portfolioSnapshots.first?.staleCount ?? 0)", color: (vm.portfolioSnapshots.first?.staleCount ?? 0) == 0 ? .positive : .warning)
                    }
                }

                if vm.portfolioSnapshots.isEmpty {
                    OperatorEmptyState(icon: "camera.metering.matrix", title: "No immutable snapshots")
                } else {
                    ForEach(vm.portfolioSnapshots.prefix(20)) { snapshot in
                        PortfolioSnapshotCard(snapshot: snapshot)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshCanonicalPortfolio() }
    }

    private var manualPortfolioPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ManualTruthLabelCard()

                if let msg = vm.manualActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.manualActionSuccess ? .positive : .warning,
                        icon: vm.manualActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Manual Portfolio", icon: "pencil.and.list.clipboard") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Positions", value: "\(vm.manualPositions.count)", color: .textPrimary)
                        AlphaMetric(title: "Cash", value: money(vm.manualPortfolio?.accountSettings.availableCash), color: .accent)
                        AlphaMetric(title: "Room", value: money(vm.manualPortfolio?.accountSettings.contributionRoom), color: .textSecondary)
                    }
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Manual reconcile" : "Manual reconcile (no API_SECRET)",
                        icon: "arrow.triangle.2.circlepath",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .manualReconcile
                    }
                    .disabled(vm.manualActionInProgress)
                }

                OperatorCard(title: "Position Editor", icon: "square.and.pencil") {
                    ManualTextField(title: "Ticker", text: $vm.manualTicker, placeholder: "AAPL")
                    HStack(spacing: 8) {
                        ManualTextField(title: "Quantity", text: $vm.manualQuantity, placeholder: "10", keyboard: .decimalPad)
                        ManualTextField(title: "Avg cost", text: $vm.manualAvgCost, placeholder: "150.00", keyboard: .decimalPad)
                    }
                    ManualTextField(title: "Realized P&L", text: $vm.manualRealizedPnL, placeholder: "0.00", keyboard: .numbersAndPunctuation)
                    ManualChoiceRow(title: "Account", choices: ["TFSA", "CASH", "RRSP", "OTHER"], selection: $vm.manualAccountType)
                    ManualChoiceRow(title: "Currency", choices: ["CAD", "USD"], selection: $vm.manualCurrency)
                    ManualTextField(title: "Note", text: $vm.manualNote, placeholder: "Optional note")
                    HStack(spacing: 10) {
                        ActionButton(title: "Clear", icon: "xmark", color: .textSecondary) {
                            vm.clearManualPositionForm()
                        }
                        ActionButton(
                            title: vm.apiSecretConfigured ? "Save position" : "Save position (no API_SECRET)",
                            icon: "checkmark",
                            color: vm.apiSecretConfigured ? .positive : .warning
                        ) {
                            Task { await vm.upsertManualPosition() }
                        }
                        .disabled(vm.manualActionInProgress)
                    }
                }

                OperatorCard(title: "Account Settings", icon: "gearshape") {
                    ManualTextField(title: "Account name", text: $vm.accountName, placeholder: "My TFSA")
                    ManualChoiceRow(title: "Account type", choices: ["TFSA", "CASH", "RRSP", "OTHER"], selection: $vm.accountType)
                    ManualChoiceRow(title: "Base currency", choices: ["CAD", "USD"], selection: $vm.accountBaseCurrency)
                    ManualTextField(title: "Available cash", text: $vm.accountAvailableCash, placeholder: "0.00", keyboard: .decimalPad)
                    ManualTextField(title: "Contribution room", text: $vm.accountContributionRoom, placeholder: "Optional", keyboard: .decimalPad)
                    ManualTextField(title: "Notes", text: $vm.accountNotes, placeholder: "Optional notes")
                    StatusRow(label: "Updated", value: vm.manualPortfolio?.accountSettings.updatedAt ?? "Unknown", color: .textSecondary)
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Update account" : "Update account (no API_SECRET)",
                        icon: "checkmark.circle",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .updateManualAccount
                    }
                    .disabled(vm.manualActionInProgress)
                }

                if vm.manualPositions.isEmpty {
                    OperatorEmptyState(icon: "pencil.and.list.clipboard", title: "No manual positions")
                } else {
                    ForEach(vm.manualPositions) { position in
                        ManualPositionCard(
                            position: position,
                            actionInProgress: vm.manualActionInProgress,
                            onEdit: { vm.populateManualPositionForm(position) },
                            onDeactivate: { pendingPortfolioAction = .deactivateManualPosition(ticker: position.ticker) }
                        )
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshManualPortfolio() }
    }

    private var portfolioThesisPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                JournalOnlyLabelCard()

                if let msg = vm.thesisActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.thesisActionSuccess ? .positive : .warning,
                        icon: vm.thesisActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Thesis List", icon: "doc.text.magnifyingglass") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Theses", value: "\(vm.portfolioTheses.count)", color: .textPrimary)
                        AlphaMetric(title: "Due", value: "\(vm.reviewDueCount)", color: vm.reviewDueCount == 0 ? .positive : .warning)
                        AlphaMetric(title: "Warnings", value: "\(vm.thesisWarningCount)", color: vm.thesisWarningCount == 0 ? .positive : .warning)
                    }
                    ActionButton(title: "New thesis", icon: "plus", color: .accent) {
                        vm.clearThesisForm()
                    }
                }

                if vm.portfolioTheses.isEmpty {
                    OperatorEmptyState(icon: "doc.text", title: "No position theses")
                } else {
                    ForEach(vm.portfolioTheses) { thesis in
                        ThesisSummaryCard(
                            thesis: thesis,
                            isSelected: vm.selectedThesisDetail?.thesis.ticker == thesis.ticker,
                            staleTickers: vm.portfolioReviews?.warnings.staleThesis ?? [],
                            overdueTickers: vm.portfolioReviews?.reviews.overdue.map(\.ticker) ?? []
                        ) {
                            Task { await vm.loadThesisDetail(ticker: thesis.ticker) }
                        }
                    }
                }

                if let detail = vm.selectedThesisDetail {
                    ThesisDetailCard(thesis: detail.thesis)
                }

                OperatorCard(title: "Thesis Editor", icon: "square.and.pencil") {
                    ManualTextField(title: "Ticker", text: $vm.thesisTicker, placeholder: "AAPL")
                    ManualTextField(title: "Title", text: $vm.thesisTitle, placeholder: "Why this position exists")
                    ManualTextField(title: "Setup type", text: $vm.thesisSetupType, placeholder: "Breakout, compounder, income")
                    ManualTextField(title: "Thesis", text: $vm.thesisText, placeholder: "Core thesis")
                    ManualChoiceRow(title: "Conviction", choices: ["LOW", "MEDIUM", "HIGH"], selection: $vm.thesisConviction)
                    ManualChoiceRow(title: "Horizon", choices: ["SHORT", "MEDIUM", "LONG"], selection: $vm.thesisTimeHorizon)
                    ManualChoiceRow(title: "Status", choices: ["ACTIVE", "WATCH", "CLOSED", "ARCHIVED"], selection: $vm.thesisStatus)
                    ManualTextField(title: "Entry reason", text: $vm.thesisEntryReason, placeholder: "Initial reason")
                    ManualTextField(title: "Expected catalysts", text: $vm.thesisExpectedCatalysts, placeholder: "Catalysts to watch")
                    ManualTextField(title: "Risk factors", text: $vm.thesisRiskFactors, placeholder: "Main risks")
                    HStack(spacing: 8) {
                        ManualTextField(title: "Invalidation", text: $vm.thesisInvalidationLevel, placeholder: "Optional", keyboard: .decimalPad)
                        ManualTextField(title: "Target", text: $vm.thesisTargetLevel, placeholder: "Optional", keyboard: .decimalPad)
                    }
                    ManualTextField(title: "Exit plan", text: $vm.thesisExitPlan, placeholder: "What would make me exit")
                    HStack(spacing: 8) {
                        ManualTextField(title: "Review days", text: $vm.thesisReviewFrequencyDays, placeholder: "30", keyboard: .numberPad)
                        ManualTextField(title: "Next review", text: $vm.thesisNextReviewAt, placeholder: "Optional ISO date")
                    }
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Save thesis" : "Save thesis (no API_SECRET)",
                        icon: "checkmark.circle",
                        color: vm.apiSecretConfigured ? .positive : .warning
                    ) {
                        pendingPortfolioAction = .saveThesis
                    }
                    .disabled(vm.thesisActionInProgress)
                }

                OperatorCard(title: "Journal", icon: "book.closed") {
                    ManualChoiceRow(
                        title: "Entry type",
                        choices: ["NOTE", "REVIEW", "THESIS_UPDATE", "RISK_UPDATE", "CATALYST_UPDATE", "EXIT_PLAN_UPDATE"],
                        selection: $vm.journalEntryType
                    )
                    ManualTextField(title: "Text", text: $vm.journalText, placeholder: "Append a note or review")
                    ManualTextField(title: "Tags", text: $vm.journalTags, placeholder: "Optional comma-separated tags")
                    ManualTextField(title: "Confidence change", text: $vm.journalConfidenceChange, placeholder: "Optional")
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Add journal entry" : "Add journal entry (no API_SECRET)",
                        icon: "plus.circle",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .appendJournal
                    }
                    .disabled(vm.thesisActionInProgress)
                }

                if vm.thesisJournalEntries.isEmpty {
                    OperatorEmptyState(icon: "book.closed", title: "No journal entries loaded")
                } else {
                    ForEach(vm.thesisJournalEntries) { entry in
                        JournalEntryCard(entry: entry)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshThesisSystem() }
    }

    private var portfolioReviewsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                JournalOnlyLabelCard()

                OperatorCard(title: "Reviews", icon: "calendar.badge.clock") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Due", value: "\(vm.reviewDueCount)", color: vm.reviewDueCount == 0 ? .positive : .warning)
                        AlphaMetric(title: "Overdue", value: "\(vm.reviewOverdueCount)", color: vm.reviewOverdueCount == 0 ? .positive : .negative)
                        AlphaMetric(title: "Upcoming", value: "\(vm.portfolioReviews?.reviews.upcomingCount ?? 0)", color: .accent)
                    }
                }

                if let warnings = vm.portfolioReviews?.warnings {
                    OperatorCard(title: "Warnings", icon: "exclamationmark.triangle") {
                        ReviewWarningRows(title: "Missing thesis", tickers: warnings.missingThesis, color: .warning)
                        ReviewWarningRows(title: "Missing exit plan", tickers: warnings.missingExitPlan, color: .warning)
                        ReviewWarningRows(title: "Stale thesis", tickers: warnings.staleThesis, color: .negative)
                    }
                }

                ReviewRowsCard(title: "Overdue Reviews", icon: "exclamationmark.octagon", rows: vm.portfolioReviews?.reviews.overdue ?? [], color: .negative) { ticker in
                    Task { await vm.loadThesisDetail(ticker: ticker) }
                    selectedSection = 20
                }
                ReviewRowsCard(title: "Due Reviews", icon: "calendar.badge.exclamationmark", rows: vm.portfolioReviews?.reviews.due ?? [], color: .warning) { ticker in
                    Task { await vm.loadThesisDetail(ticker: ticker) }
                    selectedSection = 20
                }
                ReviewRowsCard(title: "Upcoming Reviews", icon: "calendar", rows: vm.portfolioReviews?.reviews.upcoming ?? [], color: .accent) { ticker in
                    Task { await vm.loadThesisDetail(ticker: ticker) }
                    selectedSection = 20
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshThesisSystem() }
    }

    private var decisionChecklistPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ManualDisciplineLabelCard()

                if let msg = vm.decisionActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.decisionActionSuccess ? .positive : .warning,
                        icon: vm.decisionActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Create Checklist", icon: "checklist") {
                    ManualTextField(title: "Ticker", text: $vm.decisionTicker, placeholder: "AAPL")
                    ManualChoiceRow(title: "Decision type", choices: ["ENTER", "ADD", "REDUCE", "EXIT", "HOLD"], selection: $vm.decisionType)
                    ManualTextField(title: "Alpha candidate id", text: $vm.decisionAlphaCandidateId, placeholder: "Optional")
                    ManualTextField(title: "Thesis id", text: $vm.decisionThesisId, placeholder: "Optional", keyboard: .numberPad)
                    HStack(spacing: 10) {
                        ActionButton(title: "Clear", icon: "xmark", color: .textSecondary) {
                            vm.clearDecisionChecklistForm()
                        }
                        ActionButton(
                            title: vm.apiSecretConfigured ? "Create checklist" : "Create checklist (no API_SECRET)",
                            icon: "plus.circle",
                            color: vm.apiSecretConfigured ? .accent : .warning
                        ) {
                            pendingPortfolioAction = .createDecisionChecklist
                        }
                        .disabled(vm.decisionActionInProgress)
                    }
                }

                OperatorCard(title: "Checklist List", icon: "list.bullet.clipboard") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Total", value: "\(vm.decisionChecklists.count)", color: .textPrimary)
                        AlphaMetric(title: "Ready", value: "\(vm.readyDecisionCount)", color: .positive)
                        AlphaMetric(title: "Avg", value: plainPercentFromWhole(vm.averageChecklistCompletion), color: .accent)
                    }
                }

                if vm.decisionChecklists.isEmpty {
                    OperatorEmptyState(icon: "checklist", title: "No decision checklists")
                } else {
                    ForEach(vm.decisionChecklists) { checklist in
                        DecisionChecklistCard(
                            checklist: checklist,
                            isSelected: vm.selectedDecisionChecklist?.checklistId == checklist.checklistId
                        ) {
                            Task { await vm.loadDecisionChecklistDetail(id: checklist.checklistId) }
                        }
                    }
                }

                if let checklist = vm.selectedDecisionChecklist {
                    DecisionChecklistDetailCard(
                        checklist: checklist,
                        notes: $vm.checklistItemNotes,
                        actionInProgress: vm.decisionActionInProgress,
                        onSetItem: { item, passed in
                            Task { await vm.updateDecisionChecklistItem(item, passed: passed) }
                        },
                        sizeCheck: vm.sizeCheck,
                        onRunSizeCheck: {
                            vm.sizeCheckTicker = checklist.ticker
                            vm.sizeCheckDecisionType = checklist.decisionType
                            Task { await vm.runSizeCheck(ticker: checklist.ticker, decisionType: checklist.decisionType) }
                        },
                        onApprove: { pendingPortfolioAction = .approveDecisionChecklist(id: checklist.checklistId) },
                        onReject: { pendingPortfolioAction = .rejectDecisionChecklist(id: checklist.checklistId) }
                    )
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshDecisionSystem() }
    }

    private var decisionSummaryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ManualDisciplineLabelCard()

                OperatorCard(title: "Decision Summary", icon: "chart.bar.doc.horizontal") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Pending", value: "\(vm.decisionSummary?.pendingCount ?? 0)", color: .warning)
                        AlphaMetric(title: "Approved", value: "\(vm.decisionSummary?.approvedCount ?? 0)", color: .positive)
                        AlphaMetric(title: "Rejected", value: "\(vm.decisionSummary?.rejectedCount ?? 0)", color: .negative)
                    }
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Ready", value: "\(vm.readyDecisionCount)", color: .positive)
                        AlphaMetric(title: "Avg done", value: plainPercentFromWhole(vm.averageChecklistCompletion), color: .accent)
                        AlphaMetric(title: "Archived", value: "\(vm.decisionSummary?.archivedCount ?? 0)", color: .textSecondary)
                    }
                }

                OperatorCard(title: "Decision Types", icon: "square.grid.2x2") {
                    if let summary = vm.decisionSummary, !summary.byDecisionType.isEmpty {
                        ForEach(summary.byDecisionType.sorted(by: { $0.key < $1.key }), id: \.key) { type, count in
                            StatusRow(label: type, value: "\(count)", color: .accent)
                        }
                    } else {
                        Text("No decision type data")
                            .operatorBody(color: .textSecondary)
                    }
                }

                OperatorCard(title: "Most Common Blocking Items", icon: "exclamationmark.triangle") {
                    if vm.commonBlockingItems.isEmpty {
                        Text("No failed required items in cached checklist details")
                            .operatorBody(color: .textSecondary)
                    } else {
                        ForEach(vm.commonBlockingItems.prefix(6), id: \.label) { item in
                            StatusRow(label: item.label, value: "\(item.count)", color: .warning)
                        }
                    }
                }

                OperatorCard(title: "Pending Checklists", icon: "clock") {
                    if vm.decisionSummary?.pendingChecklists.isEmpty ?? true {
                        Text("No pending decision checklists")
                            .operatorBody(color: .textSecondary)
                    } else {
                        ForEach(vm.decisionSummary?.pendingChecklists.prefix(8) ?? []) { checklist in
                            Button {
                                Task { await vm.loadDecisionChecklistDetail(id: checklist.checklistId) }
                                selectedSection = 22
                            } label: {
                                HStack(spacing: 8) {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("\(checklist.ticker) \(checklist.decisionType)")
                                            .font(.system(size: 13, weight: .bold))
                                            .foregroundColor(.textPrimary)
                                        Text(checklist.createdAt ?? "Unknown")
                                            .font(.system(size: 11, weight: .medium))
                                            .foregroundColor(.textSecondary)
                                    }
                                    Spacer()
                                    Badge(text: DecisionChecklistLabels.status(checklist.checklistStatus, readiness: checklist.readiness), color: decisionStatusColor(checklist.checklistStatus, readiness: checklist.readiness))
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
            .padding(16)
        }
        .refreshable { await vm.refreshDecisionSystem() }
    }

    private var portfolioRiskPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                GuidanceOnlyLabelCard()

                if let risk = vm.portfolioRisk {
                    OperatorCard(title: "Risk Dashboard", icon: "shield.lefthalf.filled") {
                        HStack(spacing: 8) {
                            AlphaMetric(title: "Risk", value: score(risk.portfolioRiskScore), color: riskScoreColor(risk.portfolioRiskScore))
                            AlphaMetric(title: "Spec", value: plainPercentFromWhole(risk.speculativePct), color: risk.speculativePct > risk.policy.maxSpeculativePct ? .warning : .textPrimary)
                            AlphaMetric(title: "Cash", value: plainPercentFromWhole(risk.cashPct), color: risk.cashPct < risk.policy.minCashReservePct ? .warning : .positive)
                        }
                        HStack(spacing: 8) {
                            AlphaMetric(title: "CAD", value: plainPercentFromWhole(risk.cadPct), color: .accent)
                            AlphaMetric(title: "USD", value: plainPercentFromWhole(risk.usdPct), color: .accent)
                            AlphaMetric(title: "Largest", value: plainPercentFromWhole(vm.largestRiskPosition?.concentrationPct), color: concentrationColor(vm.largestRiskPosition?.concentrationPct))
                        }
                        StatusRow(label: "Checked", value: risk.checkedAt ?? "Unknown", color: .textSecondary)
                    }

                    OperatorCard(title: "Warnings", icon: "exclamationmark.triangle") {
                        RiskWarningRows(title: "Concentration", warnings: risk.concentrationWarnings)
                        RiskWarningRows(title: "Sizing", warnings: risk.sizingWarnings)
                        RiskWarningRows(title: "Theme", warnings: risk.themeWarnings)
                        StatusRow(label: "Cash reserve", value: risk.cashWarning ?? "OK", color: risk.cashWarning == nil ? .positive : .warning)
                        StatusRow(label: "Drawdown", value: risk.drawdownWarning ?? "OK", color: risk.drawdownWarning == nil ? .positive : .warning)
                    }

                    OperatorCard(title: "Recommended Actions", icon: "checklist") {
                        if risk.recommendedActions.isEmpty {
                            Text("No risk actions recommended")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(risk.recommendedActions.prefix(8), id: \.self) { action in
                                BulletLine(text: action)
                            }
                        }
                    }

                    OperatorCard(title: "Theme Exposure", icon: "square.grid.2x2") {
                        if risk.themeExposure.isEmpty {
                            Text("No theme exposure data")
                                .operatorBody(color: .textSecondary)
                        } else {
                            ForEach(risk.themeExposure.sorted(by: { $0.value > $1.value }), id: \.key) { theme, pct in
                                StatusRow(label: theme, value: plainPercentFromWhole(pct), color: .accent)
                            }
                        }
                    }

                    if risk.tickerRiskTable.isEmpty {
                        OperatorEmptyState(icon: "tablecells", title: "No ticker risk rows")
                    } else {
                        ForEach(risk.tickerRiskTable) { row in
                            TickerRiskCard(row: row, policy: risk.policy) {
                                vm.sizeCheckTicker = row.ticker
                                vm.sizeCheckDecisionType = "ENTER"
                                selectedSection = 25
                                Task { await vm.runSizeCheck(ticker: row.ticker, decisionType: "ENTER") }
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "shield", title: "No portfolio risk cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshPortfolioRisk() }
    }

    private var sizeCheckPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                GuidanceOnlyLabelCard()

                if let msg = vm.decisionActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.decisionActionSuccess ? .positive : .warning,
                        icon: vm.decisionActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Size Check", icon: "ruler") {
                    ManualTextField(title: "Ticker", text: $vm.sizeCheckTicker, placeholder: "AAPL")
                    ManualChoiceRow(title: "Decision type", choices: ["ENTER", "ADD", "REDUCE", "EXIT", "HOLD"], selection: $vm.sizeCheckDecisionType)
                    ActionButton(title: "Run size check", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.runSizeCheck() }
                    }
                    .disabled(vm.decisionActionInProgress)
                }

                if let sizeCheck = vm.sizeCheck {
                    SizeCheckCard(sizeCheck: sizeCheck)
                } else {
                    OperatorEmptyState(icon: "ruler", title: "No size check loaded")
                }
            }
            .padding(16)
        }
    }

    private var marketRegimePage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ContextOnlyLabelCard()

                if let msg = vm.regimeActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.regimeActionSuccess ? .positive : .warning,
                        icon: vm.regimeActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Market Regime", icon: "globe.americas") {
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Refresh regime" : "Refresh regime (no API_SECRET)",
                        icon: "arrow.clockwise",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .refreshMarketRegime
                    }
                    .disabled(vm.regimeActionInProgress)
                }

                if let regime = vm.marketRegime {
                    MarketRegimeDashboardCard(regime: regime)
                    MarketRegimeAdjustmentCard(regime: regime)
                    MarketRegimeWarningsCard(regime: regime)
                } else {
                    OperatorEmptyState(icon: "globe.americas", title: "No regime snapshot")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshMarketRegimeData() }
    }

    private var marketRegimeHistoryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ContextOnlyLabelCard()

                OperatorCard(title: "Regime History", icon: "clock.arrow.circlepath") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Snapshots", value: "\(vm.marketRegimeHistory.count)", color: .textPrimary)
                        AlphaMetric(title: "Latest", value: vm.marketRegime.map { MarketRegimeLabels.label($0.overallRegime) } ?? "-", color: regimeColor(vm.marketRegime?.overallRegime))
                        AlphaMetric(title: "Score", value: score(vm.marketRegime?.regimeScore), color: regimeScoreColor(vm.marketRegime?.regimeScore))
                    }
                }

                if vm.marketRegimeHistory.isEmpty {
                    OperatorEmptyState(icon: "clock", title: "No regime history")
                } else {
                    ForEach(vm.marketRegimeHistory) { snapshot in
                        MarketRegimeHistoryCard(regime: snapshot)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshMarketRegimeData() }
    }

    private var replayRunsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReplaySafetyLabelCard()

                if let msg = vm.replayActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.replayActionSuccess ? .positive : .warning,
                        icon: vm.replayActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Create Replay", icon: "calendar.badge.clock") {
                    HStack(spacing: 8) {
                        ManualTextField(title: "Start", text: $vm.replayStartDate, placeholder: "2026-01-01")
                        ManualTextField(title: "End", text: $vm.replayEndDate, placeholder: "2026-05-01")
                    }
                    HStack(spacing: 8) {
                        ManualTextField(title: "Ticker", text: $vm.replayTicker, placeholder: "Optional")
                        ManualTextField(title: "Max rows", text: $vm.replayMaxRows, placeholder: "500", keyboard: .numberPad)
                    }
                    ManualChoiceRow(title: "Source", choices: ["Any", "predator_shadow", "alpha_universe"], selection: $vm.replaySource)
                    ManualTextField(title: "Setup type", text: $vm.replaySetupType, placeholder: "Optional")
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Run historical replay" : "Run replay (no API_SECRET)",
                        icon: "arrow.triangle.2.circlepath",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .createReplayRun
                    }
                    .disabled(vm.replayActionInProgress)
                }

                OperatorCard(title: "Replay Runs", icon: "clock.arrow.circlepath") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Runs", value: "\(vm.replayRuns.count)", color: .textPrimary)
                        AlphaMetric(title: "Selected", value: vm.selectedReplayRun?.runId.prefix(8).description ?? "-", color: .accent)
                        AlphaMetric(title: "Events", value: "\(vm.selectedReplayRun?.eventCount ?? 0)", color: .textPrimary)
                    }
                    ActionButton(title: "Refresh replays", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshReplayRuns() }
                    }
                }

                if vm.replayRuns.isEmpty {
                    OperatorEmptyState(icon: "clock.arrow.circlepath", title: "No replay runs")
                } else {
                    ForEach(vm.replayRuns) { run in
                        ReplayRunCard(run: run, isSelected: vm.selectedReplayRun?.runId == run.runId) {
                            Task { await vm.loadReplayRunDetail(id: run.runId) }
                        }
                    }
                }

                if let run = vm.selectedReplayRun {
                    ReplayRunDetailCard(run: run)
                    ReplayBreakdownCard(title: "Decision Breakdown", icon: "switch.2", rows: run.summary.decisionBreakdown, label: ReplayLabels.decision)
                    ReplayBreakdownCard(title: "Outcome Breakdown", icon: "target", rows: run.summary.outcomeBreakdown, label: ReplayLabels.outcome)
                    ReplayBreakdownCard(title: "Regime Breakdown", icon: "globe.americas", rows: run.summary.regimeBreakdown, label: { MarketRegimeLabels.label($0) })
                    ReplayBreakdownCard(title: "Setup Breakdown", icon: "square.grid.2x2", rows: run.summary.setupBreakdown, label: { $0.replacingOccurrences(of: "_", with: " ").capitalized })
                    ReplayBreakdownCard(title: "Source Breakdown", icon: "tray.full", rows: run.summary.sourceBreakdown, label: { $0.replacingOccurrences(of: "_", with: " ").capitalized })
                    ReplayOpportunityListCard(title: "Best Simulated Opportunities", icon: "arrow.up.circle", rows: run.summary.bestSimulatedOpportunities, emptyText: "No simulated opportunities")
                    ReplayOpportunityListCard(title: "Worst Simulated Alerts", icon: "arrow.down.circle", rows: run.summary.worstSimulatedAlerts, emptyText: "No simulated alerts")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshReplayRuns() }
    }

    private var replayEventsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReplaySafetyLabelCard()

                OperatorCard(title: "Replay Events", icon: "list.bullet.rectangle") {
                    StatusRow(label: "Selected replay", value: vm.selectedReplayRun?.runId ?? "None", color: vm.selectedReplayRun == nil ? .warning : .textPrimary)
                    StatusRow(label: "Loaded events", value: "\(vm.replayEvents.count)", color: .textPrimary)
                    ActionButton(title: "Load events", icon: "arrow.down.circle", color: .accent) {
                        Task { await vm.refreshReplayEvents() }
                    }
                }

                if vm.replayEvents.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No replay events loaded")
                } else {
                    ForEach(vm.replayEvents) { event in
                        ReplayEventCard(event: event)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshReplayEvents() }
    }

    private var portfolioStressPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                StressSafetyLabelCard()

                if let msg = vm.stressActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.stressActionSuccess ? .positive : .warning,
                        icon: vm.stressActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Portfolio Stress", icon: "exclamationmark.shield") {
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Run stress test" : "Run stress test (no API_SECRET)",
                        icon: "arrow.triangle.2.circlepath",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .runPortfolioStress
                    }
                    .disabled(vm.stressActionInProgress)
                }

                if let run = vm.portfolioStressRun {
                    StressDashboardCard(run: run)

                    if run.scenarioEvents.isEmpty {
                        OperatorEmptyState(icon: "tray", title: "No stress scenarios")
                    } else {
                        ForEach(run.scenarioEvents) { scenario in
                            StressScenarioCard(
                                scenario: scenario,
                                cash: run.cash,
                                isSelected: vm.selectedStressScenario?.scenarioType == scenario.scenarioType
                            ) {
                                vm.selectStressScenario(scenario)
                            }
                        }
                    }

                    if let scenario = vm.selectedStressScenario {
                        StressPositionListCard(scenario: scenario)
                    }
                } else {
                    OperatorEmptyState(icon: "exclamationmark.shield", title: "No stress test cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshPortfolioStress() }
    }

    private var portfolioStressHistoryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                StressSafetyLabelCard()

                OperatorCard(title: "Stress History", icon: "clock.arrow.circlepath") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Runs", value: "\(vm.portfolioStressHistory.count)", color: .textPrimary)
                        AlphaMetric(title: "Latest", value: vm.portfolioStressRun.map { PortfolioStressLabels.scenario($0.worstScenario) } ?? "-", color: stressRiskColor(vm.portfolioStressRun?.scenarioEvents.first?.riskLevel ?? "LOW"))
                        AlphaMetric(title: "Worst", value: percentFromWhole(vm.portfolioStressRun?.worstLossPct), color: .warning)
                    }
                    ActionButton(title: "Refresh stress history", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshPortfolioStress() }
                    }
                }

                if vm.portfolioStressHistory.isEmpty {
                    OperatorEmptyState(icon: "clock", title: "No stress history")
                } else {
                    ForEach(vm.portfolioStressHistory) { run in
                        StressHistoryCard(run: run)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshPortfolioStress() }
    }

    private var strategyPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                AnalyticsOnlyLabelCard()

                if let summary = vm.strategySummary {
                    StrategyDashboardCard(summary: summary, cards: vm.strategyScorecards)
                } else {
                    OperatorEmptyState(icon: "chart.bar.doc.horizontal", title: "No strategy summary cached")
                }

                OperatorCard(title: "Strategy Scorecards", icon: "rectangle.grid.1x2") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Cards", value: "\(vm.strategyScorecards.count)", color: .textPrimary)
                        AlphaMetric(title: "With data", value: "\(vm.strategySummary?.strategiesWithData ?? 0)", color: .accent)
                        AlphaMetric(title: "Selected", value: vm.selectedStrategyScorecard.map { StrategyLabels.name($0.strategy) } ?? "-", color: .textSecondary)
                    }
                    ActionButton(title: "Refresh strategies", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshStrategies() }
                    }
                }

                if vm.strategyScorecards.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No strategy scorecards")
                } else {
                    ForEach(vm.strategyScorecards) { card in
                        StrategyScorecardCard(card: card, isSelected: vm.selectedStrategyScorecard?.strategy == card.strategy) {
                            Task { await vm.loadStrategyDetail(strategy: card.strategy) }
                        }
                    }
                }

                if let selected = vm.selectedStrategyScorecard {
                    StrategyDetailCard(card: selected)
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshStrategies() }
    }

    private var plannerPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                PlanningOnlyLabelCard()

                if let msg = vm.plannerActionMessage {
                    StatusBanner(
                        text: msg,
                        color: vm.plannerActionSuccess ? .positive : .warning,
                        icon: vm.plannerActionSuccess ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }

                OperatorCard(title: "Long-Horizon Planner", icon: "calendar.badge.clock") {
                    ManualTextField(title: "Monthly contribution", text: $vm.plannerMonthlyContribution, placeholder: "500", keyboard: .decimalPad)
                    ActionButton(
                        title: vm.apiSecretConfigured ? "Refresh planner" : "Refresh planner (no API_SECRET)",
                        icon: "arrow.clockwise",
                        color: vm.apiSecretConfigured ? .accent : .warning
                    ) {
                        pendingPortfolioAction = .refreshPlanner
                    }
                    .disabled(vm.plannerActionInProgress)
                }

                if let snapshot = vm.plannerSnapshot {
                    PlannerDashboardCard(snapshot: snapshot)

                    ForEach(snapshot.allocationRows()) { row in
                        PlannerAllocationCard(row: row)
                    }
                } else {
                    OperatorEmptyState(icon: "calendar.badge.clock", title: "No planner snapshot cached")
                }

                if let projections = vm.plannerProjections ?? vm.plannerSnapshot?.projections {
                    PlannerProjectionChartCard(projections: projections)
                    PlannerProjectionCards(projections: projections)
                } else {
                    OperatorEmptyState(icon: "chart.xyaxis.line", title: "No projections cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshPlannerData() }
    }

    private var briefPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                BriefingOnlyLabelCard()

                OperatorCard(title: "Daily Brief", icon: "doc.text") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Compact", value: vm.compactBrief?.brief.isEmpty == false ? "Ready" : "Empty", color: vm.compactBrief?.brief.isEmpty == false ? .positive : .textSecondary)
                        AlphaMetric(title: "Detailed", value: vm.detailedBrief?.generatedAt.map { shortDateString($0) } ?? "-", color: .accent)
                        AlphaMetric(title: "Refreshed", value: vm.briefLastRefresh.map(Self.shortDateTime) ?? "-", color: .textSecondary)
                    }
                    ActionButton(title: "Refresh brief", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshDailyBrief() }
                    }
                }

                if let compact = vm.compactBrief {
                    BriefCompactCard(brief: compact.brief, lastRefresh: vm.briefLastRefresh.map(Self.shortDateTime), onCopy: vm.copyCompactBrief)
                } else {
                    OperatorEmptyState(icon: "message", title: "No compact brief cached")
                }

                if let detailed = vm.detailedBrief {
                    BriefDetailedCard(brief: detailed)
                } else {
                    OperatorEmptyState(icon: "list.bullet.rectangle", title: "No detailed brief cached")
                }

                if let debug = vm.debugBrief {
                    BriefDebugCard(debug: debug)
                } else {
                    OperatorEmptyState(icon: "ladybug", title: "No debug brief cached")
                }
            }
            .padding(16)
        }
        .refreshable { await vm.refreshDailyBrief() }
    }

    private var eodBriefPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReviewOnlyLabelCard()

                OperatorCard(title: "EOD Brief", icon: "moon.stars") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Compact", value: vm.eodCompactBrief?.brief.isEmpty == false ? "Ready" : "Empty", color: vm.eodCompactBrief?.brief.isEmpty == false ? .positive : .textSecondary)
                        AlphaMetric(title: "Detailed", value: vm.eodDetailedBrief?.generatedAt.map { shortDateString($0) } ?? "-", color: .accent)
                        AlphaMetric(title: "Refreshed", value: vm.eodBriefLastRefresh.map(Self.shortDateTime) ?? "-", color: .textSecondary)
                    }
                    ActionButton(title: "Refresh EOD brief", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshEODBrief() }
                    }
                }

                if let compact = vm.eodCompactBrief {
                    BriefCompactCard(brief: compact.brief, lastRefresh: vm.eodBriefLastRefresh.map(Self.shortDateTime), onCopy: vm.copyEODCompactBrief)
                } else {
                    OperatorEmptyState(icon: "message", title: "No EOD compact brief cached")
                }

                if let detailed = vm.eodDetailedBrief {
                    BriefDetailedCard(brief: detailed)
                } else {
                    OperatorEmptyState(icon: "list.bullet.rectangle", title: "No EOD detailed brief cached")
                }

                if let debug = vm.eodDebugBrief {
                    BriefDebugCard(debug: debug)
                } else {
                    OperatorEmptyState(icon: "ladybug", title: "No EOD debug brief cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.eodCompactBrief == nil { await vm.refreshEODBrief() }
        }
        .refreshable { await vm.refreshEODBrief() }
    }

    private var marketPulsePage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Market Pulse", icon: "waveform.path.ecg") {
                    ResearchPeriodPicker(selection: $vm.marketResearchPeriod)
                    ActionButton(title: "Refresh market pulse", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshMarketPulse() }
                    }
                }
                if let pulse = vm.marketPulse {
                    ForEach(pulse.market) { item in
                        MarketPulseCard(item: item)
                    }
                } else {
                    OperatorEmptyState(icon: "chart.line.uptrend.xyaxis", title: "No market pulse cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.marketPulse == nil { await vm.refreshMarketPulse() }
        }
        .refreshable { await vm.refreshMarketPulse() }
    }

    private var sectorPerformancePage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Sector Performance", icon: "square.grid.3x3") {
                    ResearchPeriodPicker(selection: $vm.sectorResearchPeriod)
                    ActionButton(title: "Refresh sectors", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshSectorPerformance() }
                    }
                }
                if let sectors = vm.sectorPerformance {
                    SectorPerformanceSummary(response: sectors)
                    ForEach(sectors.sectors) { row in
                        SectorPerformanceCard(row: row, maxAbs: maxSectorMove(sectors.sectors))
                    }
                } else {
                    OperatorEmptyState(icon: "chart.bar.xaxis", title: "No sector performance cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.sectorPerformance == nil { await vm.refreshSectorPerformance() }
        }
        .refreshable { await vm.refreshSectorPerformance() }
    }

    private var stockResearchPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Stock Research", icon: "magnifyingglass.circle") {
                    ManualTextField(title: "Ticker", text: $vm.stockResearchTicker, placeholder: "AAPL")
                    ResearchPeriodPicker(selection: $vm.stockResearchPeriod)
                    ActionButton(title: "Refresh stock research", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshStockResearch() }
                    }
                    ActionButton(title: "Educational AI analysis", icon: "brain", color: .warning) {
                        Task { await vm.generateStockAIAnalysis() }
                    }
                    .disabled(vm.researchActionInProgress)
                }
                if let stock = vm.stockResearch {
                    StockResearchCard(stock: stock)
                } else {
                    OperatorEmptyState(icon: "doc.text.magnifyingglass", title: "No stock research cached")
                }
                if let analysis = vm.stockAIAnalysis {
                    ResearchAIAnalysisCard(title: "Stock AI Analysis", analysis: analysis)
                }
            }
            .padding(16)
        }
        .task {
            if vm.stockResearch == nil { await vm.refreshStockResearch() }
        }
        .refreshable { await vm.refreshStockResearch() }
    }

    private var etfResearchPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "ETF Research", icon: "rectangle.stack") {
                    ManualTextField(title: "Ticker", text: $vm.etfResearchTicker, placeholder: "QQQ")
                    ResearchPeriodPicker(selection: $vm.etfResearchPeriod)
                    ActionButton(title: "Refresh ETF research", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshETFResearch() }
                    }
                    ActionButton(title: "Educational AI analysis", icon: "brain", color: .warning) {
                        Task { await vm.generateETFAnalysis() }
                    }
                    .disabled(vm.researchActionInProgress)
                }
                if let etf = vm.etfResearch {
                    ETFResearchCard(etf: etf)
                } else {
                    OperatorEmptyState(icon: "rectangle.stack.badge.person.crop", title: "No ETF research cached")
                }
                if let analysis = vm.etfAIAnalysis {
                    ResearchAIAnalysisCard(title: "ETF AI Analysis", analysis: analysis)
                }
            }
            .padding(16)
        }
        .task {
            if vm.etfResearch == nil { await vm.refreshETFResearch() }
        }
        .refreshable { await vm.refreshETFResearch() }
    }

    private var macroResearchPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Macro", icon: "globe.americas") {
                    ActionButton(title: "Refresh macro", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshMacroResearch() }
                    }
                    ActionButton(title: "Educational AI analysis", icon: "brain", color: .warning) {
                        Task { await vm.generateMacroAIAnalysis() }
                    }
                    .disabled(vm.researchActionInProgress)
                }
                if let macro = vm.macroResearch {
                    MacroResearchCard(macro: macro)
                } else {
                    OperatorEmptyState(icon: "chart.line.uptrend.xyaxis.circle", title: "No macro data cached")
                }
                if let analysis = vm.macroAIAnalysis {
                    ResearchAIAnalysisCard(title: "Macro AI Analysis", analysis: analysis)
                }
            }
            .padding(16)
        }
        .task {
            if vm.macroResearch == nil { await vm.refreshMacroResearch() }
        }
        .refreshable { await vm.refreshMacroResearch() }
    }

    private var newsResearchPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EducationalOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "News", icon: "newspaper") {
                    ActionButton(title: "Refresh market headlines", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshMarketNews() }
                    }
                    ManualTextField(title: "Ticker headlines", text: $vm.newsTicker, placeholder: "NVDA")
                    ActionButton(title: "Refresh ticker headlines", icon: "magnifyingglass", color: .accent) {
                        Task { await vm.refreshTickerNews() }
                    }
                }
                NewsSectionCard(title: "Market Headlines", response: vm.marketNews)
                NewsSectionCard(title: "\(vm.newsTicker.uppercased()) Headlines", response: vm.tickerNews)
            }
            .padding(16)
        }
        .task {
            if vm.marketNews == nil { await vm.refreshMarketNews() }
        }
        .refreshable { await vm.refreshMarketNews() }
    }

    private var researchWatchlistPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ResearchOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)

                OperatorCard(title: "Add / Edit Item", icon: "square.and.pencil") {
                    ResearchWatchlistForm(vm: vm)
                    ActionButton(title: "Save watchlist item", icon: "checkmark.circle", color: .accent) {
                        pendingResearchWatchlistAction = .save
                    }
                    .disabled(vm.researchActionInProgress)
                    ActionButton(title: "Clear form", icon: "xmark.circle", color: .textSecondary) {
                        vm.clearResearchWatchlistForm()
                    }
                }

                OperatorCard(title: "Research Watchlist", icon: "binoculars") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Items", value: "\(vm.researchWatchlist.count)", color: .textPrimary)
                        AlphaMetric(title: "High", value: "\(vm.researchWatchlist.filter { $0.priority == "HIGH" }.count)", color: .warning)
                        AlphaMetric(title: "Due", value: "\(vm.researchWatchlist.filter { watchReviewState($0) != "Current" }.count)", color: .accent)
                    }
                    ActionButton(title: "Refresh watchlist", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshResearchWatchlist() }
                    }
                }

                if vm.researchWatchlist.isEmpty {
                    OperatorEmptyState(icon: "binoculars", title: "No research watchlist items")
                } else {
                    ForEach(vm.researchWatchlist) { item in
                        ResearchWatchlistItemCard(item: item) {
                            vm.populateResearchWatchlistForm(item)
                        } onOpen: {
                            selectedSection = 44
                            Task { await vm.loadResearchWatchlistDetail(ticker: item.ticker) }
                        } onArchive: {
                            pendingResearchWatchlistAction = .archive(ticker: item.ticker)
                        }
                    }
                }
            }
            .padding(16)
        }
        .task {
            if vm.researchWatchlist.isEmpty { await vm.refreshResearchWatchlist() }
        }
        .refreshable { await vm.refreshResearchWatchlist() }
    }

    private var researchWatchlistSuggestionsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ResearchOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)

                OperatorCard(title: "Suggestions", icon: "lightbulb") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Total", value: "\(vm.researchWatchlistSuggestions?.total ?? 0)", color: .textPrimary)
                        AlphaMetric(title: "Generated", value: shortDateString(vm.researchWatchlistSuggestions?.generatedAt), color: .textSecondary)
                    }
                    ActionButton(title: "Refresh suggestions", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshResearchWatchlistSuggestions() }
                    }
                }

                if let suggestions = vm.researchWatchlistSuggestions {
                    SuggestionSourceCounts(response: suggestions)
                    if suggestions.combined.isEmpty {
                        OperatorEmptyState(icon: "tray", title: "No suggestions")
                    } else {
                        ForEach(suggestions.combined) { suggestion in
                            ResearchWatchlistSuggestionCard(suggestion: suggestion) {
                                vm.populateResearchWatchlistForm(suggestion)
                                selectedSection = 42
                            }
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "lightbulb", title: "No watchlist suggestions cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.researchWatchlistSuggestions == nil { await vm.refreshResearchWatchlistSuggestions() }
        }
        .refreshable { await vm.refreshResearchWatchlistSuggestions() }
    }

    private var researchWatchlistDetailPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ResearchOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)

                OperatorCard(title: "Load Detail", icon: "magnifyingglass") {
                    ManualTextField(title: "Ticker", text: $vm.watchlistTicker, placeholder: "AAPL")
                    ActionButton(title: "Load watchlist detail", icon: "arrow.down.circle", color: .accent) {
                        Task { await vm.loadResearchWatchlistDetail(ticker: vm.watchlistTicker) }
                    }
                }

                if let detail = vm.selectedResearchWatchlistDetail {
                    ResearchWatchlistDetailCard(detail: detail)
                    OperatorCard(title: "Append Note", icon: "note.text") {
                        ManualChoiceRow(title: "Note type", choices: ["RESEARCH", "NEWS", "CATALYST", "RISK", "VALUATION", "TECHNICAL", "MACRO", "OTHER"], selection: $vm.watchlistNoteType)
                        ManualTextField(title: "Text", text: $vm.watchlistNoteText, placeholder: "What changed?")
                        ManualTextField(title: "Tags", text: $vm.watchlistNoteTags, placeholder: "optional, comma separated")
                        ActionButton(title: "Add append-only note", icon: "plus.circle", color: .accent) {
                            pendingResearchWatchlistAction = .note
                        }
                        .disabled(vm.researchActionInProgress)
                    }

                    OperatorCard(title: "Archive", icon: "archivebox") {
                        Text("Archive only. This never deletes the item and never places trades.")
                            .operatorBody(color: .textSecondary)
                        ActionButton(title: "Archive \(detail.item.ticker)", icon: "archivebox", color: .negative) {
                            pendingResearchWatchlistAction = .archive(ticker: detail.item.ticker)
                        }
                        .disabled(vm.researchActionInProgress)
                    }
                } else {
                    OperatorEmptyState(icon: "doc.text.magnifyingglass", title: "No watchlist detail loaded")
                }
            }
            .padding(16)
        }
        .refreshable {
            if let ticker = vm.selectedResearchWatchlistDetail?.item.ticker ?? (vm.watchlistTicker.isEmpty ? nil : vm.watchlistTicker) {
                await vm.loadResearchWatchlistDetail(ticker: ticker)
            }
        }
    }

    private var researchWorkflowQueuePage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ResearchWorkflowOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Research Workflow Queue", icon: "list.bullet.clipboard") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Items", value: "\(vm.researchWorkflowQueue.count)", color: .textPrimary)
                        AlphaMetric(title: "Overdue", value: "\(vm.researchWorkflowQueue.filter { workflowDueState($0) == "Overdue" }.count)", color: .warning)
                        AlphaMetric(title: "High", value: "\(vm.researchWorkflowQueue.filter { $0.priority == "HIGH" }.count)", color: .accent)
                    }
                    ActionButton(title: "Refresh queue", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshResearchWorkflowQueue() }
                    }
                }
                if vm.researchWorkflowQueue.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No workflow items")
                } else {
                    ForEach(vm.researchWorkflowQueue) { item in
                        ResearchWorkflowItemCard(
                            item: item,
                            selected: vm.workflowSelectedItemId == item.itemId,
                            snoozeValue: $vm.workflowSnoozeValue,
                            snoozeUnit: $vm.workflowSnoozeUnit,
                            noteText: $vm.workflowNoteText,
                            onSelect: { vm.workflowSelectedItemId = item.itemId },
                            onStart: { Task { await vm.startResearchWorkflowItem(item.itemId) } },
                            onDone: { pendingResearchWorkflowAction = .done(itemId: item.itemId) },
                            onSnooze: { Task { await vm.snoozeResearchWorkflowItem(item.itemId) } },
                            onArchive: { pendingResearchWorkflowAction = .archive(itemId: item.itemId) },
                            onNote: { Task { await vm.appendResearchWorkflowNote(item.itemId) } }
                        )
                    }
                }
            }
            .padding(16)
        }
        .task {
            if vm.researchWorkflowQueue.isEmpty { await vm.refreshResearchWorkflowQueue() }
        }
        .refreshable { await vm.refreshResearchWorkflowQueue() }
    }

    private var researchWorkflowSummaryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ResearchWorkflowOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Workflow Summary", icon: "chart.bar.doc.horizontal") {
                    StatusRow(label: "Generated", value: shortDateString(vm.researchWorkflowSummary?.generatedAt), color: .textSecondary)
                    ActionButton(title: "Refresh summary", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshResearchWorkflowSummary() }
                    }
                }
                if let summary = vm.researchWorkflowSummary {
                    WorkflowSummarySection(title: "Top review items", items: summary.topItems)
                    WorkflowSummarySection(title: "Overdue items", items: summary.overdue)
                    WorkflowSummarySection(title: "Snoozed returning today", items: summary.snoozedReturning)
                    WorkflowSummarySection(title: "High-priority new items", items: summary.newHighPriority)
                    WorkflowSummarySection(title: "Completed today", items: summary.completedToday)
                    WorkflowSummarySection(title: "Bottlenecks", items: summary.bottlenecks)
                } else {
                    OperatorEmptyState(icon: "chart.bar.doc.horizontal", title: "No workflow summary cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.researchWorkflowSummary == nil { await vm.refreshResearchWorkflowSummary() }
        }
        .refreshable { await vm.refreshResearchWorkflowSummary() }
    }

    private var weeklyReviewPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReviewOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Weekly Review", icon: "calendar.badge.checkmark") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Grade", value: vm.weeklyReviewDetailed.map { WeeklyReviewLabels.grade($0.grade) } ?? "-", color: weeklyGradeColor(vm.weeklyReviewDetailed?.grade))
                        AlphaMetric(title: "Week", value: vm.weeklyReviewDetailed?.weekLabel ?? "-", color: .textSecondary)
                        AlphaMetric(title: "Refreshed", value: vm.weeklyReviewLastRefresh.map(Self.shortDateTime) ?? "-", color: .textSecondary)
                    }
                    ActionButton(title: "Refresh weekly review", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshWeeklyReview() }
                    }
                }
                if let compact = vm.weeklyReviewCompact {
                    WeeklyCompactCard(text: compact.text, lastRefresh: vm.weeklyReviewLastRefresh.map(Self.shortDateTime), onCopy: vm.copyWeeklyReviewCompact)
                } else {
                    OperatorEmptyState(icon: "message", title: "No compact weekly review cached")
                }
                if let detailed = vm.weeklyReviewDetailed {
                    WeeklyDetailedCard(review: detailed)
                } else {
                    OperatorEmptyState(icon: "list.bullet.rectangle", title: "No detailed weekly review cached")
                }
                if let debug = vm.weeklyReviewDebug {
                    WeeklyDebugCard(debug: debug)
                } else {
                    OperatorEmptyState(icon: "ladybug", title: "No weekly debug cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.weeklyReviewDetailed == nil { await vm.refreshWeeklyReview() }
        }
        .refreshable { await vm.refreshWeeklyReview() }
    }

    private var weeklyReviewHistoryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ReviewOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Weekly History", icon: "clock.arrow.circlepath") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Reports", value: "\(vm.weeklyReviewHistory.count)", color: .textPrimary)
                    }
                    ActionButton(title: "Refresh history", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshWeeklyReviewHistory() }
                    }
                }
                if vm.weeklyReviewHistory.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No weekly review history")
                } else {
                    ForEach(vm.weeklyReviewHistory) { entry in
                        WeeklyHistoryCard(entry: entry)
                    }
                }
            }
            .padding(16)
        }
        .task {
            if vm.weeklyReviewHistory.isEmpty { await vm.refreshWeeklyReviewHistory() }
        }
        .refreshable { await vm.refreshWeeklyReviewHistory() }
    }

    private var catalystCalendarPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EventTrackingOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Catalyst Calendar", icon: "calendar") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Events", value: "\(vm.catalysts.count)", color: .textPrimary)
                        AlphaMetric(title: "High", value: "\(vm.catalysts.filter { $0.importance == "HIGH" }.count)", color: .warning)
                        AlphaMetric(title: "Missed", value: "\(vm.catalysts.filter { catalystDateState($0) == "Overdue" || $0.status == "MISSED" }.count)", color: .negative)
                    }
                    ManualTextField(title: "Ticker filter optional", text: $vm.catalystTickerFilter, placeholder: "NVDA")
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Refresh", icon: "arrow.clockwise", color: .accent) {
                            Task { await vm.refreshCatalysts() }
                        }
                        SmallActionButton(title: "Clear filter", icon: "xmark.circle", color: .textSecondary) {
                            vm.catalystTickerFilter = ""
                            Task { await vm.refreshCatalysts() }
                        }
                    }
                }
                OperatorCard(title: "Manual Catalyst", icon: "plus.circle") {
                    CatalystForm(vm: vm)
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Save", icon: "checkmark.circle", color: .positive) {
                            pendingCatalystAction = .save
                        }
                        SmallActionButton(title: "Clear", icon: "xmark.circle", color: .textSecondary) {
                            vm.clearCatalystForm()
                        }
                    }
                }
                CatalystGroupSection(
                    title: "High importance",
                    icon: "exclamationmark.triangle",
                    catalysts: vm.catalysts.filter { $0.importance == "HIGH" },
                    onEdit: { vm.populateCatalystForm($0) },
                    onOpen: { catalyst in Task { await vm.loadCatalystDetail(id: catalyst.catalystId) } },
                    onComplete: { pendingCatalystAction = .complete(id: $0.catalystId) },
                    onArchive: { pendingCatalystAction = .archive(id: $0.catalystId) }
                )
                CatalystGroupSection(
                    title: "Upcoming",
                    icon: "calendar.badge.clock",
                    catalysts: vm.catalysts.filter { catalystDateState($0) != "Overdue" && $0.status == "UPCOMING" },
                    onEdit: { vm.populateCatalystForm($0) },
                    onOpen: { catalyst in Task { await vm.loadCatalystDetail(id: catalyst.catalystId) } },
                    onComplete: { pendingCatalystAction = .complete(id: $0.catalystId) },
                    onArchive: { pendingCatalystAction = .archive(id: $0.catalystId) }
                )
                CatalystGroupSection(
                    title: "Overdue / missed",
                    icon: "clock.badge.exclamationmark",
                    catalysts: vm.catalysts.filter { catalystDateState($0) == "Overdue" || $0.status == "MISSED" },
                    onEdit: { vm.populateCatalystForm($0) },
                    onOpen: { catalyst in Task { await vm.loadCatalystDetail(id: catalyst.catalystId) } },
                    onComplete: { pendingCatalystAction = .complete(id: $0.catalystId) },
                    onArchive: { pendingCatalystAction = .archive(id: $0.catalystId) }
                )
                CatalystGroupSection(
                    title: "Portfolio linked",
                    icon: "briefcase",
                    catalysts: vm.catalysts.filter { ["THESIS", "WATCHLIST", "WORKFLOW_ITEM"].contains($0.linkedEntityType ?? "") },
                    onEdit: { vm.populateCatalystForm($0) },
                    onOpen: { catalyst in Task { await vm.loadCatalystDetail(id: catalyst.catalystId) } },
                    onComplete: { pendingCatalystAction = .complete(id: $0.catalystId) },
                    onArchive: { pendingCatalystAction = .archive(id: $0.catalystId) }
                )
            }
            .padding(16)
        }
        .task {
            if vm.catalysts.isEmpty { await vm.refreshCatalysts() }
        }
        .refreshable { await vm.refreshCatalysts() }
    }

    private var catalystSummaryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EventTrackingOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Catalyst Summary", icon: "calendar.badge.exclamationmark") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "This week", value: "\(vm.catalystSummary?.thisWeekCount ?? 0)", color: .accent)
                        AlphaMetric(title: "Next week", value: "\(vm.catalystSummary?.nextWeekCount ?? 0)", color: .textPrimary)
                        AlphaMetric(title: "High", value: "\(vm.catalystSummary?.highImportanceCount ?? 0)", color: .warning)
                    }
                    ActionButton(title: "Refresh summary", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshCatalystSummary() }
                    }
                }
                if let summary = vm.catalystSummary {
                    CatalystSummaryCard(summary: summary)
                } else {
                    OperatorEmptyState(icon: "calendar.badge.exclamationmark", title: "No catalyst summary cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.catalystSummary == nil { await vm.refreshCatalystSummary() }
        }
        .refreshable { await vm.refreshCatalystSummary() }
    }

    private var catalystDetailPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                EventTrackingOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Catalyst Detail", icon: "doc.text.magnifyingglass") {
                    ManualTextField(title: "Catalyst id", text: $vm.catalystLookupId, placeholder: "catalyst id")
                    ActionButton(title: "Load catalyst", icon: "magnifyingglass", color: .accent) {
                        Task { await vm.loadCatalystDetail(id: vm.catalystLookupId) }
                    }
                }
                if let catalyst = vm.selectedCatalyst {
                    CatalystDetailCard(catalyst: catalyst)
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Complete", icon: "checkmark.circle", color: .positive) {
                            pendingCatalystAction = .complete(id: catalyst.catalystId)
                        }
                        SmallActionButton(title: "Archive", icon: "archivebox", color: .negative) {
                            pendingCatalystAction = .archive(id: catalyst.catalystId)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "calendar", title: "No catalyst selected")
                }
            }
            .padding(16)
        }
        .refreshable {
            if let id = vm.selectedCatalyst?.catalystId {
                await vm.loadCatalystDetail(id: id)
            }
        }
    }

    private var notificationCenterPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                InAppInboxOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Notification Center", icon: "tray.full") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Inbox", value: "\(vm.notifications.count)", color: .textPrimary)
                        AlphaMetric(title: "Visible", value: "\(vm.notificationSummary?.visibleUnreadCount ?? vm.notifications.filter { $0.status == "UNREAD" }.count)", color: .accent)
                        AlphaMetric(title: "Critical", value: "\(vm.notificationSummary?.criticalCount ?? vm.notifications.filter { $0.severity == "CRITICAL" }.count)", color: .negative)
                    }
                    if let summary = vm.notificationSummary {
                        StatusRow(label: "Filtered by preferences", value: "\(summary.filteredCount)", color: summary.filteredCount > 0 ? .warning : .textSecondary)
                        StatusRow(label: "Suppressed by preferences", value: "\(summary.suppressedByPreferencesCount)", color: summary.suppressedByPreferencesCount > 0 ? .warning : .textSecondary)
                        StatusRow(label: "Quiet hours", value: summary.quietHoursActive ? "Active" : "Inactive", color: summary.quietHoursActive ? .warning : .textSecondary)
                        if summary.suppressedByPreferencesCount > 0 {
                            Text("Some items are hidden by your notification preferences.")
                                .operatorBody(color: .warning)
                        }
                    }
                    NotificationFilterPicker(selection: $vm.notificationFilter)
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Refresh", icon: "arrow.clockwise", color: .accent) {
                            Task { await vm.refreshNotifications() }
                        }
                        SmallActionButton(title: "Generate", icon: "wand.and.stars", color: .positive) {
                            Task { await vm.generateInAppNotifications() }
                        }
                        SmallActionButton(title: "All read", icon: "checkmark.circle", color: .textSecondary) {
                            Task { await vm.markAllNotificationsRead() }
                        }
                    }
                }
                LocalNotificationSettingsCard(vm: vm)
                if vm.filteredNotifications.isEmpty {
                    OperatorEmptyState(icon: "tray", title: "No notifications for this filter")
                } else {
                    ForEach(vm.filteredNotifications) { item in
                        NotificationInboxCard(
                            notification: item,
                            onOpen: { Task { await vm.loadNotificationDetail(id: item.notificationId) } },
                            onRead: { Task { await vm.markNotificationRead(id: item.notificationId) } },
                            onUnread: { Task { await vm.markNotificationUnread(id: item.notificationId) } },
                            onDismiss: { pendingNotificationAction = .dismiss(id: item.notificationId) },
                            onArchive: { pendingNotificationAction = .archive(id: item.notificationId) }
                        )
                    }
                }
            }
            .padding(16)
        }
        .task {
            await vm.refreshLocalNotificationPermissionStatus()
            if vm.notifications.isEmpty { await vm.refreshNotifications() }
            if vm.notificationSummary == nil { await vm.refreshNotificationSummary() }
        }
        .refreshable {
            await vm.refreshNotifications()
            await vm.refreshNotificationSummary()
        }
    }

    private var notificationSummaryPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                InAppInboxOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Notification Summary", icon: "bell.badge") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Unread", value: "\(vm.notificationSummary?.unreadCount ?? 0)", color: .accent)
                        AlphaMetric(title: "Critical", value: "\(vm.notificationSummary?.criticalCount ?? 0)", color: .negative)
                        AlphaMetric(title: "Warning", value: "\(vm.notificationSummary?.warningCount ?? 0)", color: .warning)
                    }
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Refresh", icon: "arrow.clockwise", color: .accent) {
                            Task { await vm.refreshNotificationSummary() }
                        }
                        SmallActionButton(title: "Archive read", icon: "archivebox", color: .negative) {
                            pendingNotificationAction = .archiveRead
                        }
                    }
                }
                if let summary = vm.notificationSummary {
                    NotificationSummaryCard(summary: summary) { item in
                        Task { await vm.loadNotificationDetail(id: item.notificationId) }
                    }
                } else {
                    OperatorEmptyState(icon: "bell.badge", title: "No notification summary cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.notificationSummary == nil { await vm.refreshNotificationSummary() }
        }
        .refreshable { await vm.refreshNotificationSummary() }
    }

    private var notificationDetailPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                InAppInboxOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Notification Detail", icon: "doc.text.magnifyingglass") {
                    ManualTextField(title: "Notification id", text: $vm.notificationLookupId, placeholder: "notification id")
                    ActionButton(title: "Load notification", icon: "magnifyingglass", color: .accent) {
                        Task { await vm.loadNotificationDetail(id: vm.notificationLookupId) }
                    }
                }
                if let item = vm.selectedNotification {
                    NotificationDetailCard(notification: item)
                    HStack(spacing: 8) {
                        SmallActionButton(title: item.status == "UNREAD" ? "Read" : "Unread", icon: item.status == "UNREAD" ? "checkmark.circle" : "circle", color: .accent) {
                            if item.status == "UNREAD" {
                                Task { await vm.markNotificationRead(id: item.notificationId) }
                            } else {
                                Task { await vm.markNotificationUnread(id: item.notificationId) }
                            }
                        }
                        SmallActionButton(title: "Dismiss", icon: "xmark.circle", color: .warning) {
                            pendingNotificationAction = .dismiss(id: item.notificationId)
                        }
                        SmallActionButton(title: "Archive", icon: "archivebox", color: .negative) {
                            pendingNotificationAction = .archive(id: item.notificationId)
                        }
                    }
                } else {
                    OperatorEmptyState(icon: "bell", title: "No notification selected")
                }
            }
            .padding(16)
        }
        .refreshable {
            if let id = vm.selectedNotification?.notificationId {
                await vm.loadNotificationDetail(id: id)
            }
        }
    }

    private var notificationPreferencesPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                NotificationPreferencesOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Notification Preferences", icon: "slider.horizontal.3") {
                    NotificationPreferenceForm(vm: vm)
                    SmallActionButton(title: "Save preferences", icon: "checkmark.circle", color: .accent) {
                        pendingNotificationAction = .savePreferences
                    }
                }
            }
            .padding(16)
        }
        .task {
            if vm.notificationPreferences == nil { await vm.refreshNotificationPreferences() }
        }
        .refreshable { await vm.refreshNotificationPreferences() }
    }

    private var notificationCategoryPreferencesPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                NotificationPreferencesOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Category Overrides", icon: "square.grid.2x2") {
                    StatusRow(label: "Overrides", value: "\(vm.notificationCategoryPreferences.count)", color: .textPrimary)
                    ActionButton(title: "Refresh category overrides", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshNotificationCategoryPreferences() }
                    }
                }
                ForEach($vm.notificationCategoryPreferences) { $override in
                    NotificationCategoryPreferenceCard(override: $override) {
                        pendingNotificationAction = .saveCategory(category: override.category)
                    }
                }
            }
            .padding(16)
        }
        .task {
            if vm.notificationCategoryPreferences.isEmpty { await vm.refreshNotificationCategoryPreferences() }
        }
        .refreshable { await vm.refreshNotificationCategoryPreferences() }
    }

    private var notificationDigestPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                NotificationPreferencesOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Digest Preview", icon: "doc.text") {
                    WatchlistChoicePicker(title: "Mode", choices: ["daily", "eod", "weekly"], selection: $vm.notificationDigestMode, label: digestModeSelectorLabel)
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Included", value: "\(vm.notificationDigest?.includedCount ?? 0)", color: .accent)
                        AlphaMetric(title: "Omitted", value: "\(vm.notificationDigest?.omittedCount ?? 0)", color: .textSecondary)
                    }
                    ActionButton(title: "Refresh digest", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshNotificationDigest() }
                    }
                }
                if let digest = vm.notificationDigest {
                    NotificationDigestCard(digest: digest)
                } else {
                    OperatorEmptyState(icon: "doc.text", title: "No digest cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.notificationDigest == nil { await vm.refreshNotificationDigest() }
        }
        .refreshable { await vm.refreshNotificationDigest() }
    }

    private var systemHealthPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                DiagnosticsOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "System Health", icon: "stethoscope") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Compact", value: systemStatusLabel(vm.systemReleaseCompact?.overallStatus ?? "Unknown"), color: systemStatusColor(vm.systemReleaseCompact?.overallStatus ?? ""))
                        AlphaMetric(title: "Full", value: systemStatusLabel(vm.systemReleaseFull?.overallStatus ?? "Unknown"), color: systemStatusColor(vm.systemReleaseFull?.overallStatus ?? ""))
                    }
                    HStack(spacing: 8) {
                        SmallActionButton(title: "Compact", icon: "arrow.clockwise", color: .accent) {
                            Task { await vm.refreshSystemReleaseCompact() }
                        }
                        SmallActionButton(title: "Full", icon: "checklist", color: .positive) {
                            Task { await vm.refreshSystemReleaseFull() }
                        }
                    }
                    ActionButton(title: "Refresh diagnostics", icon: "arrow.triangle.2.circlepath", color: .accent) {
                        Task { await vm.refreshSystemDiagnostics() }
                    }
                }
                if let compact = vm.systemReleaseCompact {
                    SystemHealthOverviewCard(report: compact, title: "Compact Release Check")
                }
                if let full = vm.systemReleaseFull {
                    SystemHealthOverviewCard(report: full, title: "Full Release Check")
                    ForEach(full.orderedSections, id: \.name) { section in
                        SystemHealthSectionCard(name: section.name, results: section.results)
                    }
                    SystemEnvironmentCard(environment: full.environment)
                } else if let compact = vm.systemReleaseCompact {
                    ForEach(compact.orderedSections, id: \.name) { section in
                        SystemHealthSectionCard(name: section.name, results: section.results)
                    }
                    SystemEnvironmentCard(environment: compact.environment)
                } else {
                    OperatorEmptyState(icon: "stethoscope", title: "No system health cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.systemReleaseCompact == nil { await vm.refreshSystemReleaseCompact() }
        }
        .refreshable { await vm.refreshSystemReleaseCompact() }
    }

    private var systemRoutesPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                DiagnosticsOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Routes", icon: "point.3.connected.trianglepath.dotted") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Routes", value: "\(vm.systemRoutes?.count ?? 0)", color: .textPrimary)
                        AlphaMetric(title: "Missing", value: "\(vm.systemRoutes?.missingCriticalRoutes.count ?? 0)", color: (vm.systemRoutes?.missingCriticalRoutes.isEmpty ?? true) ? .positive : .negative)
                    }
                    ActionButton(title: "Refresh routes", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshSystemRoutes() }
                    }
                }
                if let routes = vm.systemRoutes {
                    SystemRoutesCard(response: routes)
                    if !routes.missingCriticalRoutes.isEmpty {
                        BriefSection(title: "Missing critical routes") {
                            ForEach(routes.missingCriticalRoutes) { route in
                                SystemRouteRow(route: route)
                            }
                        }
                    }
                    ForEach(routes.routes) { route in
                        SystemRouteRow(route: route)
                    }
                } else {
                    OperatorEmptyState(icon: "link", title: "No route registry cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.systemRoutes == nil { await vm.refreshSystemRoutes() }
        }
        .refreshable { await vm.refreshSystemRoutes() }
    }

    private var systemFlagsPage: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                DiagnosticsOnlyLabelCard()
                ResearchActionBanner(message: vm.researchActionMessage, success: vm.researchActionSuccess)
                OperatorCard(title: "Flags", icon: "flag") {
                    HStack(spacing: 8) {
                        AlphaMetric(title: "Alpha sends", value: flagValue(vm.systemFlags?.boolFlag("ALPHA_NOTIFICATIONS_ENABLED")), color: flagColor(key: "ALPHA_NOTIFICATIONS_ENABLED", value: vm.systemFlags?.boolFlag("ALPHA_NOTIFICATIONS_ENABLED")))
                        AlphaMetric(title: "Dry run", value: flagValue(vm.systemFlags?.boolFlag("ALPHA_NOTIFICATIONS_DRY_RUN_ONLY")), color: flagColor(key: "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY", value: vm.systemFlags?.boolFlag("ALPHA_NOTIFICATIONS_DRY_RUN_ONLY")))
                    }
                    ActionButton(title: "Refresh flags", icon: "arrow.clockwise", color: .accent) {
                        Task { await vm.refreshSystemFlags() }
                    }
                }
                if let flags = vm.systemFlags {
                    SystemFlagsCard(response: flags)
                } else {
                    OperatorEmptyState(icon: "flag", title: "No flags cached")
                }
            }
            .padding(16)
        }
        .task {
            if vm.systemFlags == nil { await vm.refreshSystemFlags() }
        }
        .refreshable { await vm.refreshSystemFlags() }
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

    // MARK: - Backup pages (Phase I30)

    private var backupListPage: some View {
        BackupListPage(vm: vm)
    }

    private var backupDetailPage: some View {
        BackupDetailPage(vm: vm)
    }

    private var backupCreatePage: some View {
        BackupCreatePage(vm: vm, pendingAction: $pendingBackupAction)
    }

    private var backupVerifyPage: some View {
        BackupVerifyPage(vm: vm, pendingAction: $pendingBackupAction)
    }

    private var backupRestorePreviewPage: some View {
        BackupRestorePreviewPage(vm: vm, pendingAction: $pendingBackupAction)
    }

    private var backupDownloadInfoPage: some View {
        BackupDownloadInfoPage(vm: vm)
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
