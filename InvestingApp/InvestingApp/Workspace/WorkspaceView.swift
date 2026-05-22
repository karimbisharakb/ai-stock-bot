import SwiftUI

struct WorkspaceView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @State private var showSwitcher = false
    @State private var showCommandSheet = false
    @State private var tickerDraft = ""
    @State private var pinTitle = ""
    @State private var pinDetail = ""
    @State private var queueNote = ""

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    header
                    contextRibbon
                    tabStrip
                    panelStrip

                    Divider()
                        .overlay(Color.border)

                    workspaceBody(width: geometry.size.width)
                }
            }
        }
        .sheet(isPresented: $showSwitcher) {
            WorkspaceSwitcherView()
                .environmentObject(store)
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showCommandSheet) {
            WorkspaceCommandSheet(
                tickerDraft: $tickerDraft,
                pinTitle: $pinTitle,
                pinDetail: $pinDetail,
                queueNote: $queueNote
            )
            .environmentObject(store)
            .presentationDetents([.medium, .large])
        }
        .onAppear {
            tickerDraft = store.activeContext.ticker ?? ""
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                showSwitcher = true
                HapticManager.impact(.light)
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: store.activeWorkspace.preset == .command ? "rectangle.3.group" : "square.grid.2x2")
                    VStack(alignment: .leading, spacing: 1) {
                        Text(store.activeWorkspace.name)
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundColor(.textPrimary)
                            .lineLimit(1)
                        Text(store.activeWorkspace.preset.subtitle)
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(.textSecondary)
                            .lineLimit(1)
                    }
                }
            }
            .buttonStyle(.plain)

            Spacer()

            Button {
                store.cycleFocusedPanel(forward: false)
                HapticManager.selection()
            } label: {
                Image(systemName: "chevron.left")
                    .workspaceIconButton()
            }
            .accessibilityLabel("Previous panel")

            Button {
                store.cycleFocusedPanel()
                HapticManager.selection()
            } label: {
                Image(systemName: "chevron.right")
                    .workspaceIconButton()
            }
            .accessibilityLabel("Next panel")

            Button {
                showCommandSheet = true
                HapticManager.impact(.medium)
            } label: {
                Image(systemName: "command")
                    .workspaceIconButton(accented: true)
            }
            .accessibilityLabel("Commands")
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(Color.background)
    }

    private var contextRibbon: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "scope")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.accent)
                    TextField("Ticker", text: $tickerDraft)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.textPrimary)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .frame(width: 82)
                        .submitLabel(.done)
                        .onSubmit { store.setTicker(tickerDraft) }
                    Button {
                        store.setTicker(tickerDraft)
                        HapticManager.selection()
                    } label: {
                        Image(systemName: "arrow.right.circle.fill")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(.accent)
                    }
                    .accessibilityLabel("Set ticker")
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))

                ContextChip(icon: "link", title: linkedPanelLabel)
                ContextChip(icon: "tray.full", title: "\(store.activeWorkspace.queue.filter { !$0.isResolved }.count) open")
                ContextChip(icon: "pin.fill", title: "\(store.activeWorkspace.pinned.count) pinned")

                ForEach(store.fragmentationWarnings(), id: \.self) { warning in
                    ContextChip(icon: "exclamationmark.triangle.fill", title: warning, tint: .warning)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
        }
    }

    private var tabStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(store.activeWorkspace.tabs) { tab in
                    Button {
                        store.selectTab(tab.id)
                        HapticManager.selection()
                    } label: {
                        Text(tab.title)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(tab.id == store.activeWorkspace.activeTabID ? .black : .textPrimary)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(tab.id == store.activeWorkspace.activeTabID ? Color.accent : Color.surface)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
        }
    }

    private var panelStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(store.activeTab?.panels ?? []) { panel in
                    Button {
                        store.focusPanel(panel.id)
                        HapticManager.selection()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: panel.kind.systemImage)
                            Text(panel.title)
                                .lineLimit(1)
                            if panel.state.isLinked {
                                Image(systemName: "link")
                                    .font(.system(size: 10, weight: .bold))
                            }
                        }
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(panel.id == store.focusedPanel?.id ? .accent : .textSecondary)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .background(Color.surface.opacity(panel.id == store.focusedPanel?.id ? 1 : 0.65))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(panel.id == store.focusedPanel?.id ? Color.accent.opacity(0.5) : Color.border, lineWidth: 0.5)
                        )
                    }
                    .contextMenu {
                        Button(panel.state.isLinked ? "Unlink Panel" : "Link Panel") {
                            store.togglePanelLink(panel.id)
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
        }
    }

    @ViewBuilder
    private func workspaceBody(width: CGFloat) -> some View {
        if width >= 760, let tab = store.activeTab, let focused = store.focusedPanel {
            HStack(spacing: 1) {
                WorkspacePanelFrame(panel: focused)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                if let secondaryID = tab.secondaryPanelID,
                   let secondary = tab.panels.first(where: { $0.id == secondaryID && $0.id != focused.id }) {
                    WorkspacePanelFrame(panel: secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    IntelligenceRail()
                        .frame(width: min(320, width * 0.32))
                }
            }
        } else if let focused = store.focusedPanel {
            WorkspacePanelFrame(panel: focused)
        } else {
            EmptyWorkspacePanel()
        }
    }

    private var linkedPanelLabel: String {
        let linked = store.activeTab?.panels.filter(\.state.isLinked).count ?? 0
        return "\(linked) linked"
    }
}

private struct ContextChip: View {
    let icon: String
    let title: String
    var tint: Color = .textSecondary

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .lineLimit(1)
        }
        .foregroundColor(tint)
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .background(Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}

private struct WorkspaceSwitcherView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()
                List {
                    Section("Workspaces") {
                        ForEach(store.workspaces) { workspace in
                            Button {
                                store.selectWorkspace(workspace.id)
                                dismiss()
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: workspace.preset == .command ? "rectangle.3.group" : "square.grid.2x2")
                                        .foregroundColor(.accent)
                                        .frame(width: 28)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(workspace.name)
                                            .font(.system(size: 15, weight: .semibold))
                                            .foregroundColor(.textPrimary)
                                        Text(workspace.preset.subtitle)
                                            .font(.system(size: 12))
                                            .foregroundColor(.textSecondary)
                                    }
                                    Spacer()
                                    if workspace.id == store.activeWorkspaceID {
                                        Image(systemName: "check.circle.fill")
                                            .foregroundColor(.positive)
                                    }
                                }
                            }
                            .listRowBackground(Color.surface)
                        }
                        .onDelete { offsets in
                            offsets.map { store.workspaces[$0].id }.forEach(store.deleteWorkspace)
                        }
                    }

                    Section("Presets") {
                        ForEach(WorkspacePreset.allCases) { preset in
                            Button {
                                store.createWorkspace(from: preset)
                                dismiss()
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(preset.title)
                                            .foregroundColor(.textPrimary)
                                            .font(.system(size: 15, weight: .semibold))
                                        Text(preset.subtitle)
                                            .foregroundColor(.textSecondary)
                                            .font(.system(size: 12))
                                    }
                                    Spacer()
                                    Image(systemName: "plus.circle.fill")
                                        .foregroundColor(.accent)
                                }
                            }
                            .listRowBackground(Color.surface)
                        }
                    }

                    Section {
                        Button(role: .destructive) {
                            store.resetActiveWorkspace()
                            dismiss()
                        } label: {
                            Label("Reset Active Layout", systemImage: "arrow.counterclockwise")
                        }
                    }
                    .listRowBackground(Color.surface)
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Workspace")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

private struct WorkspaceCommandSheet: View {
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @Binding var tickerDraft: String
    @Binding var pinTitle: String
    @Binding var pinDetail: String
    @Binding var queueNote: String

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        fieldBlock(title: "Ticker Focus") {
                            HStack(spacing: 10) {
                                TextField("Ticker", text: $tickerDraft)
                                    .workspaceTextField()
                                    .textInputAutocapitalization(.characters)
                                Button {
                                    store.setTicker(tickerDraft)
                                    dismiss()
                                } label: {
                                    Image(systemName: "scope")
                                        .workspaceIconButton(accented: true)
                                }
                            }
                        }

                        fieldBlock(title: "Queue") {
                            HStack(spacing: 10) {
                                TextField("Note", text: $queueNote)
                                    .workspaceTextField()
                                Button {
                                    store.enqueueInvestigation(ticker: tickerDraft, note: queueNote)
                                    queueNote = ""
                                } label: {
                                    Image(systemName: "tray.and.arrow.down.fill")
                                        .workspaceIconButton(accented: true)
                                }
                            }
                        }

                        fieldBlock(title: "Pin") {
                            VStack(spacing: 10) {
                                TextField("Title", text: $pinTitle)
                                    .workspaceTextField()
                                TextField("Detail", text: $pinDetail)
                                    .workspaceTextField()
                                Button {
                                    store.pinCurrentContext(title: pinTitle, detail: pinDetail)
                                    pinTitle = ""
                                    pinDetail = ""
                                } label: {
                                    Label("Pin Context", systemImage: "pin.fill")
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundColor(.black)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 11)
                                        .background(Color.accent)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))
                                }
                            }
                        }

                        fieldBlock(title: "Panels") {
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                                ForEach(store.activeTab?.panels ?? []) { panel in
                                    Button {
                                        store.focusPanel(panel.id)
                                        dismiss()
                                    } label: {
                                        Label(panel.title, systemImage: panel.kind.systemImage)
                                            .font(.system(size: 12, weight: .semibold))
                                            .foregroundColor(.textPrimary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .padding(10)
                                            .background(Color.surface)
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                }
                            }
                        }
                    }
                    .padding(16)
                }
            }
            .navigationTitle("Commands")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func fieldBlock<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(.textSecondary)
                .textCase(.uppercase)
            content()
        }
    }
}

private extension Image {
    func workspaceIconButton(accented: Bool = false) -> some View {
        self
            .font(.system(size: 16, weight: .semibold))
            .foregroundColor(accented ? .black : .textPrimary)
            .frame(width: 36, height: 36)
            .background(accented ? Color.accent : Color.surface)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(accented ? Color.clear : Color.border, lineWidth: 0.5)
            )
    }
}

private extension TextField {
    func workspaceTextField() -> some View {
        self
            .font(.system(size: 15, weight: .medium))
            .foregroundColor(.textPrimary)
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background(Color.surface)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 0.5))
    }
}
