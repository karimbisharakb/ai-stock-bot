import SwiftUI

struct WorkspacePanelFrame: View {
    @EnvironmentObject private var store: WorkspaceStore
    let panel: PanelDescriptor

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: panel.kind.systemImage)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.accent)
                Text(panel.title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
                    .lineLimit(1)
                Spacer()
                Button {
                    store.togglePanelLink(panel.id)
                    HapticManager.selection()
                } label: {
                    Image(systemName: panel.state.isLinked ? "link" : "link.slash")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(panel.state.isLinked ? .positive : .textSecondary)
                        .frame(width: 30, height: 30)
                        .background(Color.surface)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .accessibilityLabel(panel.state.isLinked ? "Unlink panel" : "Link panel")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(Color.surfaceElevated)

            Divider()
                .overlay(Color.border)

            WorkspacePanelHost(panel: panel)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color.background)
    }
}

struct WorkspacePanelHost: View {
    @EnvironmentObject private var store: WorkspaceStore
    let panel: PanelDescriptor

    var body: some View {
        Group {
            switch panel.kind {
            case .command:
                CommandCanvasPanel()
            case .portfolio:
                HomeView()
            case .opportunities, .predator:
                OpportunitiesView()
            case .feed:
                FeedView()
            case .analyze:
                AnalyzeWorkspaceAdapter()
            case .watchlist:
                WatchlistView()
            case .planner:
                SettingsView()
            case .simulation:
                SimulationWorkspacePanel()
            case .notebook:
                NotebookPanel()
            case .pinnedEvidence:
                PinnedEvidencePanel()
            case .riskPosture:
                RiskPosturePanel()
            }
        }
        .onChange(of: store.activeContext.ticker) { _, ticker in
            guard panel.state.isLinked, panel.kind == .analyze, let ticker else { return }
            NotificationCenter.default.post(name: .analyzeTickerRequested, object: nil, userInfo: ["ticker": ticker])
        }
    }
}

private struct AnalyzeWorkspaceAdapter: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        AnalyzeView()
            .onAppear {
                if let ticker = store.activeContext.ticker {
                    NotificationCenter.default.post(name: .analyzeTickerRequested, object: nil, userInfo: ["ticker": ticker])
                }
            }
    }
}

struct EmptyWorkspacePanel: View {
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "rectangle.dashed")
                .font(.system(size: 34, weight: .light))
                .foregroundColor(.textSecondary)
            Text("No Panel")
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(.textPrimary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.background)
    }
}

struct CommandCanvasPanel: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                PostureRibbon()
                InvestigationPressureCard()
                QueuePanel()
                IntelligenceRail()
            }
            .padding(14)
        }
        .background(Color.background)
    }
}

struct PostureRibbon: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Posture")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundColor(.textSecondary)
                        .textCase(.uppercase)
                    Text(store.activeContext.ticker ?? "Portfolio")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundColor(.textPrimary)
                }
                Spacer()
                Image(systemName: "shield.lefthalf.filled")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(.positive)
            }

            HStack(spacing: 8) {
                MetricPill(title: "Pins", value: "\(store.activeWorkspace.pinned.count)", tint: .accent)
                MetricPill(title: "Open", value: "\(store.activeWorkspace.queue.filter { !$0.isResolved }.count)", tint: .warning)
                MetricPill(title: "History", value: "\(store.activeWorkspace.history.count)", tint: .textSecondary)
            }
        }
        .padding(14)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}

struct InvestigationPressureCard: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Pressure", systemImage: "gauge.with.dots.needle.33percent")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Text(pressureLabel)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(pressureColor)
            }

            ForEach(store.fragmentationWarnings(), id: \.self) { warning in
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(.warning)
                    Text(warning)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(.textSecondary)
                    Spacer()
                }
            }

            if store.fragmentationWarnings().isEmpty {
                Text("No unresolved workspace conflicts")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.textSecondary)
            }
        }
        .padding(14)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }

    private var pressureLabel: String {
        let open = store.activeWorkspace.queue.filter { !$0.isResolved }.count
        if open >= 8 { return "HIGH" }
        if open >= 3 { return "MED" }
        return "LOW"
    }

    private var pressureColor: Color {
        pressureLabel == "HIGH" ? .negative : pressureLabel == "MED" ? .warning : .positive
    }
}

struct QueuePanel: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Investigation Queue", systemImage: "tray.full")
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.textPrimary)

            if store.activeWorkspace.queue.isEmpty {
                Text("Queue is empty")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.textSecondary)
            } else {
                LazyVStack(spacing: 8) {
                    ForEach(store.activeWorkspace.queue) { item in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(item.ticker)
                                    .font(.system(size: 15, weight: .bold))
                                    .foregroundColor(item.isResolved ? .textSecondary : .textPrimary)
                                if !item.note.isEmpty {
                                    Text(item.note)
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundColor(.textSecondary)
                                        .lineLimit(2)
                                }
                            }
                            Spacer()
                            Button {
                                store.setTicker(item.ticker)
                            } label: {
                                Image(systemName: "scope")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(.accent)
                                    .frame(width: 32, height: 32)
                                    .background(Color.surfaceElevated)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                            Button {
                                store.resolveQueueItem(item.id)
                            } label: {
                                Image(systemName: item.isResolved ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 17, weight: .semibold))
                                    .foregroundColor(item.isResolved ? .positive : .textSecondary)
                                    .frame(width: 32, height: 32)
                            }
                        }
                        .padding(10)
                        .background(Color.surfaceElevated)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
        .padding(14)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}

struct IntelligenceRail: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Label("Workspace Intelligence", systemImage: "sparkles")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)

                ForEach(store.suggestedPanelKinds()) { kind in
                    HStack(spacing: 10) {
                        Image(systemName: kind.systemImage)
                            .foregroundColor(.accent)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(kind.title)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.textPrimary)
                            Text(suggestionCopy(for: kind))
                                .font(.system(size: 11, weight: .medium))
                                .foregroundColor(.textSecondary)
                                .lineLimit(2)
                        }
                        Spacer()
                    }
                    .padding(10)
                    .background(Color.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
                }

                if store.suggestedPanelKinds().isEmpty {
                    Text("Current panel set covers the active context")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(.textSecondary)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.surface)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }

                NavigationHistoryPanel()
            }
            .padding(14)
        }
        .background(Color.background)
    }

    private func suggestionCopy(for kind: WorkspacePanelKind) -> String {
        switch kind {
        case .analyze: return "Ticker focus is active"
        case .feed: return "Related market context available"
        case .riskPosture: return "Risk view can anchor triage"
        case .pinnedEvidence: return "Pinned evidence exists"
        case .command: return "Open work remains"
        default: return "Related context available"
        }
    }
}

struct NavigationHistoryPanel: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("History", systemImage: "clock.arrow.circlepath")
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.textPrimary)

            ForEach(store.activeWorkspace.history.prefix(8)) { event in
                VStack(alignment: .leading, spacing: 2) {
                    Text(event.label)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.textPrimary)
                    Text(event.ticker ?? event.investigationID ?? "Workspace")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.textSecondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(9)
                .background(Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

struct PinnedEvidencePanel: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                if store.activeWorkspace.pinned.isEmpty {
                    EmptyStateBlock(icon: "pin.slash", title: "No Pinned Evidence")
                } else {
                    ForEach(store.activeWorkspace.pinned) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(item.title)
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundColor(.textPrimary)
                                Spacer()
                                if let ticker = item.ticker {
                                    Text(ticker)
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundColor(.accent)
                                }
                            }
                            if !item.detail.isEmpty {
                                Text(item.detail)
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundColor(.textSecondary)
                            }
                        }
                        .padding(12)
                        .background(Color.surface)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
                    }
                }
            }
            .padding(14)
        }
        .background(Color.background)
    }
}

struct NotebookPanel: View {
    @EnvironmentObject private var store: WorkspaceStore
    @State private var text = ""
    @State private var saveTask: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 10) {
            TextEditor(text: $text)
                .scrollContentBackground(.hidden)
                .font(.system(size: 14, weight: .medium, design: .monospaced))
                .foregroundColor(.textPrimary)
                .padding(10)
                .background(Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
                .onChange(of: text) { _, newValue in
                    saveTask?.cancel()
                    saveTask = Task {
                        try? await Task.sleep(nanoseconds: 450_000_000)
                        await MainActor.run {
                            store.updateNotebook(newValue)
                        }
                    }
                }

            HStack {
                Text("\(text.count)/12000")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.textSecondary)
                Spacer()
                if let ticker = store.activeContext.ticker {
                    Text(ticker)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.accent)
                }
            }
        }
        .padding(14)
        .background(Color.background)
        .onAppear { text = store.activeWorkspace.notebook.text }
        .onChange(of: store.activeWorkspace.id) { _, _ in
            text = store.activeWorkspace.notebook.text
        }
        .onDisappear {
            saveTask?.cancel()
            store.updateNotebook(text)
        }
    }
}

struct SimulationWorkspacePanel: View {
    @EnvironmentObject private var store: WorkspaceStore
    @State private var replayPosition = 0.35

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Label("Replay", systemImage: "timeline.selection")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.textPrimary)
                        Spacer()
                        Text(store.activeContext.ticker ?? "Portfolio")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(.accent)
                    }

                    Slider(value: $replayPosition, in: 0...1)
                        .tint(.accent)

                    HStack(spacing: 8) {
                        MetricPill(title: "Scenario", value: "Base", tint: .accent)
                        MetricPill(title: "Policy", value: "Hold", tint: .positive)
                        MetricPill(title: "Replay", value: "\(Int(replayPosition * 100))%", tint: .warning)
                    }
                }
                .padding(14)
                .background(Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))

                EmptyStateBlock(icon: "bookmark", title: "No Experiment Bookmarks")
            }
            .padding(14)
        }
        .background(Color.background)
    }
}

struct RiskPosturePanel: View {
    @EnvironmentObject private var store: WorkspaceStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                PostureRibbon()
                VStack(alignment: .leading, spacing: 10) {
                    Label("Active Risk Strip", systemImage: "waveform.path.ecg")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.textPrimary)

                    RiskRow(label: "Ticker Focus", value: store.activeContext.ticker ?? "None", tint: store.activeContext.ticker == nil ? .textSecondary : .accent)
                    RiskRow(label: "Open Queue", value: "\(store.activeWorkspace.queue.filter { !$0.isResolved }.count)", tint: .warning)
                    RiskRow(label: "Pinned Context", value: "\(store.activeWorkspace.pinned.count)", tint: .positive)
                    RiskRow(label: "Linked Panels", value: "\(store.activeTab?.panels.filter(\.state.isLinked).count ?? 0)", tint: .accent)
                }
                .padding(14)
                .background(Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
            }
            .padding(14)
        }
        .background(Color.background)
    }
}

private struct RiskRow: View {
    let label: String
    let value: String
    let tint: Color

    var body: some View {
        HStack {
            Text(label)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textSecondary)
            Spacer()
            Text(value)
                .font(.system(size: 13, weight: .bold))
                .foregroundColor(tint)
        }
        .padding(.vertical, 6)
    }
}

private struct MetricPill: View {
    let title: String
    let value: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            Text(value)
                .font(.system(size: 15, weight: .bold))
                .foregroundColor(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(9)
        .background(Color.surfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct EmptyStateBlock: View {
    let icon: String
    let title: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 28, weight: .light))
                .foregroundColor(.textSecondary)
            Text(title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(28)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}
