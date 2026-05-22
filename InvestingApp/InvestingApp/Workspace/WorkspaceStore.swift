import Foundation
import SwiftUI

@MainActor
final class WorkspaceStore: ObservableObject {
    private enum Limits {
        static let maxWorkspaces = 8
        static let maxHistory = 80
        static let maxPinned = 40
        static let maxQueue = 60
        static let maxNotebookCharacters = 12_000
    }

    private let persistenceKey = "phase10a.workspace.state.v1"
    private let recoveryKey = "phase10a.workspace.recovery.v1"
    private let schemaVersion = 1

    @Published private(set) var workspaces: [Workspace]
    @Published private(set) var activeWorkspaceID: String

    var activeWorkspace: Workspace {
        workspaces.first(where: { $0.id == activeWorkspaceID }) ?? Self.defaultWorkspaces()[0]
    }

    var activeTab: WorkspaceTab? {
        activeWorkspace.tabs.first(where: { $0.id == activeWorkspace.activeTabID })
    }

    var activeContext: WorkspaceContext {
        activeWorkspace.context
    }

    var focusedPanel: PanelDescriptor? {
        guard let tab = activeTab else { return nil }
        return tab.panels.first(where: { $0.id == tab.focusedPanelID }) ?? tab.panels.first
    }

    init(defaults: UserDefaults = .standard) {
        if let envelope = Self.loadEnvelope(from: defaults, key: persistenceKey, recoveryKey: recoveryKey),
           !envelope.workspaces.isEmpty {
            self.workspaces = Self.sanitize(envelope.workspaces)
            self.activeWorkspaceID = envelope.workspaces.contains(where: { $0.id == envelope.activeWorkspaceID })
                ? envelope.activeWorkspaceID
                : envelope.workspaces[0].id
        } else {
            let seeded = Self.defaultWorkspaces()
            self.workspaces = seeded
            self.activeWorkspaceID = seeded[0].id
            persist(to: defaults)
        }
    }

    func selectWorkspace(_ id: String) {
        guard workspaces.contains(where: { $0.id == id }) else { return }
        activeWorkspaceID = id
        persist()
    }

    func createWorkspace(from preset: WorkspacePreset) {
        guard workspaces.count < Limits.maxWorkspaces else { return }
        let base = Self.workspace(for: preset, suffix: nextSuffix(for: preset.rawValue))
        workspaces.append(base)
        activeWorkspaceID = base.id
        persist()
    }

    func deleteWorkspace(_ id: String) {
        guard workspaces.count > 1 else { return }
        workspaces.removeAll { $0.id == id }
        if activeWorkspaceID == id {
            activeWorkspaceID = workspaces[0].id
        }
        persist()
    }

    func resetActiveWorkspace() {
        updateActiveWorkspace { workspace in
            let replacement = Self.workspace(for: workspace.preset, suffix: workspace.id)
            workspace.tabs = replacement.tabs
            workspace.activeTabID = replacement.activeTabID
            workspace.context = .empty
            workspace.pinned = []
            workspace.queue = []
            workspace.notebook = WorkspaceNotebook(text: "", updatedAt: Date())
            workspace.history = []
        }
    }

    func selectTab(_ tabID: String) {
        updateActiveWorkspace { workspace in
            guard workspace.tabs.contains(where: { $0.id == tabID }) else { return }
            workspace.activeTabID = tabID
            let tab = workspace.tabs.first(where: { $0.id == tabID })
            appendHistory(
                to: &workspace,
                panelID: tab?.focusedPanelID,
                panelKind: tab?.panels.first(where: { $0.id == tab?.focusedPanelID })?.kind,
                label: tab?.title ?? "Tab"
            )
        }
    }

    func focusPanel(_ panelID: String) {
        updateActiveWorkspace { workspace in
            guard let tabIndex = activeTabIndex(in: workspace),
                  workspace.tabs[tabIndex].panels.contains(where: { $0.id == panelID }) else { return }
            workspace.tabs[tabIndex].focusedPanelID = panelID
            let panel = workspace.tabs[tabIndex].panels.first(where: { $0.id == panelID })
            appendHistory(to: &workspace, panelID: panel?.id, panelKind: panel?.kind, label: panel?.title ?? "Panel")
        }
    }

    func cycleFocusedPanel(forward: Bool = true) {
        guard let tab = activeTab, !tab.panels.isEmpty else { return }
        let currentIndex = tab.panels.firstIndex(where: { $0.id == tab.focusedPanelID }) ?? 0
        let offset = forward ? 1 : -1
        let nextIndex = (currentIndex + offset + tab.panels.count) % tab.panels.count
        focusPanel(tab.panels[nextIndex].id)
    }

    func togglePanelLink(_ panelID: String) {
        updateActiveWorkspace { workspace in
            guard let tabIndex = activeTabIndex(in: workspace),
                  let panelIndex = workspace.tabs[tabIndex].panels.firstIndex(where: { $0.id == panelID }) else { return }
            workspace.tabs[tabIndex].panels[panelIndex].state.isLinked.toggle()
            workspace.tabs[tabIndex].panels[panelIndex].state.lastUpdated = Date()
        }
    }

    func setTicker(_ ticker: String?) {
        let cleaned = ticker?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        updateActiveWorkspace { workspace in
            workspace.context.ticker = cleaned?.isEmpty == true ? nil : cleaned
            appendHistory(to: &workspace, panelID: nil, panelKind: nil, label: workspace.context.ticker ?? "Clear ticker")
        }
    }

    func setInvestigation(_ id: String?) {
        let cleaned = id?.trimmingCharacters(in: .whitespacesAndNewlines)
        updateActiveWorkspace { workspace in
            workspace.context.investigationID = cleaned?.isEmpty == true ? nil : cleaned
            appendHistory(to: &workspace, panelID: nil, panelKind: nil, label: workspace.context.investigationID ?? "Clear investigation")
        }
    }

    func setFilter(key: String, value: String?) {
        updateActiveWorkspace { workspace in
            if let value, !value.isEmpty {
                workspace.context.filters[key] = value
            } else {
                workspace.context.filters.removeValue(forKey: key)
            }
        }
    }

    func pinCurrentContext(title: String, detail: String) {
        updateActiveWorkspace { workspace in
            let pin = PinnedContext(
                id: makeID(prefix: "pin", seed: "\(workspace.id)-\(workspace.pinned.count + 1)"),
                title: title.trimmingCharacters(in: .whitespacesAndNewlines),
                detail: detail.trimmingCharacters(in: .whitespacesAndNewlines),
                ticker: workspace.context.ticker,
                sourcePanelID: workspace.tabs.first(where: { $0.id == workspace.activeTabID })?.focusedPanelID,
                createdAt: Date()
            )
            guard !pin.title.isEmpty else { return }
            workspace.pinned.insert(pin, at: 0)
            workspace.pinned = Array(workspace.pinned.prefix(Limits.maxPinned))
        }
    }

    func enqueueInvestigation(ticker: String, note: String) {
        let cleanedTicker = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !cleanedTicker.isEmpty else { return }
        updateActiveWorkspace { workspace in
            let item = InvestigationQueueItem(
                id: makeID(prefix: "queue", seed: "\(workspace.id)-\(cleanedTicker)-\(workspace.queue.count + 1)"),
                ticker: cleanedTicker,
                note: note.trimmingCharacters(in: .whitespacesAndNewlines),
                createdAt: Date(),
                isResolved: false
            )
            workspace.queue.insert(item, at: 0)
            workspace.queue = Array(workspace.queue.prefix(Limits.maxQueue))
        }
    }

    func resolveQueueItem(_ id: String) {
        updateActiveWorkspace { workspace in
            guard let index = workspace.queue.firstIndex(where: { $0.id == id }) else { return }
            workspace.queue[index].isResolved = true
        }
    }

    func updateNotebook(_ text: String) {
        updateActiveWorkspace { workspace in
            workspace.notebook.text = String(text.prefix(Limits.maxNotebookCharacters))
            workspace.notebook.updatedAt = Date()
        }
    }

    func suggestedPanelKinds() -> [WorkspacePanelKind] {
        let visible = Set(activeTab?.panels.map(\.kind) ?? [])
        let context = activeContext
        var suggestions: [WorkspacePanelKind] = []
        if context.ticker != nil {
            suggestions.append(contentsOf: [.analyze, .feed, .riskPosture])
        }
        if !activeWorkspace.pinned.isEmpty {
            suggestions.append(.pinnedEvidence)
        }
        if activeWorkspace.queue.contains(where: { !$0.isResolved }) {
            suggestions.append(.command)
        }
        return suggestions.filter { !visible.contains($0) }.reduce(into: []) { result, item in
            if !result.contains(item) { result.append(item) }
        }
    }

    func fragmentationWarnings() -> [String] {
        var warnings: [String] = []
        let unresolvedTickers = Set(activeWorkspace.queue.filter { !$0.isResolved }.map(\.ticker))
        if let ticker = activeContext.ticker, unresolvedTickers.contains(ticker) {
            warnings.append("\(ticker) still has open queue work")
        }
        if activeWorkspace.pinned.count > 12 {
            warnings.append("Pinned evidence is getting crowded")
        }
        if !activeWorkspace.queue.isEmpty,
           activeWorkspace.queue.filter({ !$0.isResolved }).count == activeWorkspace.queue.count {
            warnings.append("No queued investigations are resolved")
        }
        return warnings
    }

    private func updateActiveWorkspace(_ transform: (inout Workspace) -> Void) {
        guard let index = workspaces.firstIndex(where: { $0.id == activeWorkspaceID }) else { return }
        var copy = workspaces
        transform(&copy[index])
        copy[index].updatedAt = Date()
        copy = Self.sanitize(copy)
        workspaces = copy
        persist()
    }

    private func persist(to defaults: UserDefaults = .standard) {
        let envelope = WorkspacePersistenceEnvelope(
            schemaVersion: schemaVersion,
            activeWorkspaceID: activeWorkspaceID,
            workspaces: Self.sanitize(workspaces)
        )
        guard let data = try? JSONEncoder.workspaceEncoder.encode(envelope) else { return }
        defaults.set(data, forKey: persistenceKey)
    }

    private func nextSuffix(for seed: String) -> String {
        var counter = workspaces.filter { $0.preset.rawValue == seed }.count + 1
        var suffix = "\(seed)-\(counter)"
        while workspaces.contains(where: { $0.id == makeID(prefix: "workspace", seed: suffix) }) {
            counter += 1
            suffix = "\(seed)-\(counter)"
        }
        return suffix
    }

    private static func loadEnvelope(from defaults: UserDefaults, key: String, recoveryKey: String) -> WorkspacePersistenceEnvelope? {
        guard let data = defaults.data(forKey: key) else { return nil }
        do {
            let envelope = try JSONDecoder.workspaceDecoder.decode(WorkspacePersistenceEnvelope.self, from: data)
            guard envelope.schemaVersion == 1 else { return nil }
            return envelope
        } catch {
            defaults.set(data, forKey: recoveryKey)
            defaults.removeObject(forKey: key)
            return nil
        }
    }

    private static func sanitize(_ workspaces: [Workspace]) -> [Workspace] {
        Array(workspaces.prefix(Limits.maxWorkspaces)).map { storedWorkspace in
            var copy = storedWorkspace
            copy.tabs = copy.tabs.filter { !$0.panels.isEmpty }
            if copy.tabs.isEmpty {
                copy.tabs = workspace(for: copy.preset, suffix: copy.id).tabs
            }
            if !copy.tabs.contains(where: { $0.id == copy.activeTabID }) {
                copy.activeTabID = copy.tabs[0].id
            }
            copy.tabs = copy.tabs.map { tab in
                var fixed = tab
                if !fixed.panels.contains(where: { $0.id == fixed.focusedPanelID }) {
                    fixed.focusedPanelID = fixed.panels[0].id
                }
                if let secondary = fixed.secondaryPanelID,
                   !fixed.panels.contains(where: { $0.id == secondary }) {
                    fixed.secondaryPanelID = nil
                }
                return fixed
            }
            copy.pinned = Array(copy.pinned.prefix(Limits.maxPinned))
            copy.queue = Array(copy.queue.prefix(Limits.maxQueue))
            copy.history = Array(copy.history.prefix(Limits.maxHistory))
            copy.notebook.text = String(copy.notebook.text.prefix(Limits.maxNotebookCharacters))
            return copy
        }
    }

    private func activeTabIndex(in workspace: Workspace) -> Int? {
        workspace.tabs.firstIndex(where: { $0.id == workspace.activeTabID })
    }

    private func appendHistory(
        to workspace: inout Workspace,
        panelID: String?,
        panelKind: WorkspacePanelKind?,
        label: String
    ) {
        let event = WorkspaceNavigationEvent(
            id: makeID(prefix: "nav", seed: "\(workspace.id)-\(workspace.history.count + 1)-\(label)"),
            timestamp: Date(),
            panelID: panelID,
            panelKind: panelKind,
            ticker: workspace.context.ticker,
            investigationID: workspace.context.investigationID,
            label: label
        )
        workspace.history.insert(event, at: 0)
        workspace.history = Array(workspace.history.prefix(Limits.maxHistory))
    }
}

private extension WorkspaceStore {
    static func defaultWorkspaces() -> [Workspace] {
        [
            workspace(for: .command, suffix: "command"),
            workspace(for: .research, suffix: "research"),
            workspace(for: .simulation, suffix: "simulation")
        ]
    }

    static func workspace(for preset: WorkspacePreset, suffix: String) -> Workspace {
        let id = makeID(prefix: "workspace", seed: suffix)
        let tabs = tabs(for: preset, workspaceID: id)
        return Workspace(
            id: id,
            name: preset.title,
            preset: preset,
            tabs: tabs,
            activeTabID: tabs[0].id,
            context: .empty,
            pinned: [],
            queue: [],
            notebook: WorkspaceNotebook(text: "", updatedAt: Date()),
            history: [],
            updatedAt: Date()
        )
    }

    static func tabs(for preset: WorkspacePreset, workspaceID: String) -> [WorkspaceTab] {
        switch preset {
        case .command:
            return [
                tab("Command", workspaceID: workspaceID, seed: "command", mode: .split, kinds: [.command, .portfolio, .riskPosture, .feed]),
                tab("Triage", workspaceID: workspaceID, seed: "triage", mode: .stack, kinds: [.opportunities, .predator, .watchlist, .analyze])
            ]
        case .research:
            return [
                tab("Research", workspaceID: workspaceID, seed: "research", mode: .split, kinds: [.analyze, .feed, .pinnedEvidence, .notebook]),
                tab("Compare", workspaceID: workspaceID, seed: "compare", mode: .split, kinds: [.portfolio, .opportunities, .riskPosture, .simulation])
            ]
        case .simulation:
            return [
                tab("Replay", workspaceID: workspaceID, seed: "replay", mode: .split, kinds: [.simulation, .riskPosture, .notebook, .pinnedEvidence]),
                tab("Policy", workspaceID: workspaceID, seed: "policy", mode: .stack, kinds: [.planner, .portfolio, .opportunities])
            ]
        case .triage:
            return [
                tab("Signal Queue", workspaceID: workspaceID, seed: "signal-queue", mode: .focused, kinds: [.opportunities, .predator, .analyze, .feed]),
                tab("Follow Up", workspaceID: workspaceID, seed: "follow-up", mode: .stack, kinds: [.watchlist, .notebook, .pinnedEvidence])
            ]
        }
    }

    static func tab(
        _ title: String,
        workspaceID: String,
        seed: String,
        mode: WorkspaceLayoutMode,
        kinds: [WorkspacePanelKind]
    ) -> WorkspaceTab {
        let panels = kinds.enumerated().map { index, kind in
            PanelDescriptor(
                id: makeID(prefix: "panel", seed: "\(workspaceID)-\(seed)-\(kind.rawValue)-\(index)"),
                kind: kind,
                title: kind.title,
                state: .linked()
            )
        }
        return WorkspaceTab(
            id: makeID(prefix: "tab", seed: "\(workspaceID)-\(seed)"),
            title: title,
            layoutMode: mode,
            panels: panels,
            focusedPanelID: panels[0].id,
            secondaryPanelID: panels.dropFirst().first?.id
        )
    }
}

private func makeID(prefix: String, seed: String) -> String {
    let allowed = CharacterSet.alphanumerics
    let body = seed.lowercased().unicodeScalars.map { scalar -> Character in
        allowed.contains(scalar) ? Character(scalar) : "-"
    }
    let collapsed = String(body)
        .split(separator: "-", omittingEmptySubsequences: true)
        .joined(separator: "-")
    return "\(prefix)-\(collapsed.isEmpty ? "default" : collapsed)"
}

private extension JSONEncoder {
    static var workspaceEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}

private extension JSONDecoder {
    static var workspaceDecoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
