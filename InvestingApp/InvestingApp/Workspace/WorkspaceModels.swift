import Foundation

enum WorkspacePreset: String, Codable, CaseIterable, Identifiable {
    case command
    case research
    case simulation
    case triage

    var id: String { rawValue }

    var title: String {
        switch self {
        case .command: return "Command"
        case .research: return "Research"
        case .simulation: return "Simulation"
        case .triage: return "Triage"
        }
    }

    var subtitle: String {
        switch self {
        case .command: return "Posture, risk, alerts"
        case .research: return "Ticker research chain"
        case .simulation: return "Replay and policy lab"
        case .triage: return "Fast signal review"
        }
    }
}

enum WorkspacePanelKind: String, Codable, CaseIterable, Identifiable {
    case command
    case portfolio
    case opportunities
    case feed
    case analyze
    case predator
    case watchlist
    case planner
    case simulation
    case notebook
    case pinnedEvidence
    case riskPosture

    var id: String { rawValue }

    var title: String {
        switch self {
        case .command: return "Command"
        case .portfolio: return "Portfolio"
        case .opportunities: return "Opportunities"
        case .feed: return "Feed"
        case .analyze: return "Analyze"
        case .predator: return "Predator"
        case .watchlist: return "Watchlist"
        case .planner: return "Planner"
        case .simulation: return "Simulation"
        case .notebook: return "Notebook"
        case .pinnedEvidence: return "Evidence"
        case .riskPosture: return "Risk"
        }
    }

    var systemImage: String {
        switch self {
        case .command: return "rectangle.3.group"
        case .portfolio: return "briefcase.fill"
        case .opportunities: return "bolt.fill"
        case .feed: return "waveform"
        case .analyze: return "magnifyingglass"
        case .predator: return "scope"
        case .watchlist: return "bell.badge"
        case .planner: return "calendar.badge.clock"
        case .simulation: return "timeline.selection"
        case .notebook: return "note.text"
        case .pinnedEvidence: return "pin.fill"
        case .riskPosture: return "shield.lefthalf.filled"
        }
    }
}

enum WorkspaceLayoutMode: String, Codable {
    case focused
    case split
    case stack
}

struct WorkspaceContext: Codable, Equatable {
    var ticker: String?
    var investigationID: String?
    var selectedSignalID: String?
    var selectedOpportunityID: String?
    var filters: [String: String]
    var replayState: String?
    var activeNotebookID: String?

    static let empty = WorkspaceContext(
        ticker: nil,
        investigationID: nil,
        selectedSignalID: nil,
        selectedOpportunityID: nil,
        filters: [:],
        replayState: nil,
        activeNotebookID: nil
    )
}

struct PanelState: Codable, Equatable {
    var isLinked: Bool
    var isCollapsed: Bool
    var localFilters: [String: String]
    var lastUpdated: Date

    static func linked() -> PanelState {
        PanelState(isLinked: true, isCollapsed: false, localFilters: [:], lastUpdated: Date())
    }
}

struct PanelDescriptor: Codable, Identifiable, Equatable {
    let id: String
    var kind: WorkspacePanelKind
    var title: String
    var state: PanelState
}

struct WorkspaceTab: Codable, Identifiable, Equatable {
    let id: String
    var title: String
    var layoutMode: WorkspaceLayoutMode
    var panels: [PanelDescriptor]
    var focusedPanelID: String
    var secondaryPanelID: String?
}

struct WorkspaceNavigationEvent: Codable, Identifiable, Equatable {
    let id: String
    let timestamp: Date
    let panelID: String?
    let panelKind: WorkspacePanelKind?
    let ticker: String?
    let investigationID: String?
    let label: String
}

struct PinnedContext: Codable, Identifiable, Equatable {
    let id: String
    var title: String
    var detail: String
    var ticker: String?
    var sourcePanelID: String?
    var createdAt: Date
}

struct InvestigationQueueItem: Codable, Identifiable, Equatable {
    let id: String
    var ticker: String
    var note: String
    var createdAt: Date
    var isResolved: Bool
}

struct WorkspaceNotebook: Codable, Equatable {
    var text: String
    var updatedAt: Date
}

struct Workspace: Codable, Identifiable, Equatable {
    let id: String
    var name: String
    var preset: WorkspacePreset
    var tabs: [WorkspaceTab]
    var activeTabID: String
    var context: WorkspaceContext
    var pinned: [PinnedContext]
    var queue: [InvestigationQueueItem]
    var notebook: WorkspaceNotebook
    var history: [WorkspaceNavigationEvent]
    var updatedAt: Date
}

struct WorkspacePersistenceEnvelope: Codable {
    var schemaVersion: Int
    var activeWorkspaceID: String
    var workspaces: [Workspace]
}
