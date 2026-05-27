import Foundation
import SwiftUI

// MARK: - Chat VM

@MainActor
final class ResearchChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var personas: [PersonaInfo] = []
    @Published var selectedPersona: String = "VALUE"
    @Published var inputText: String = ""
    @Published var isSending = false
    @Published var isLoadingHistory = false
    @Published var errorMessage: String?

    let sessionId: String

    init(sessionId: String = "default") {
        self.sessionId = sessionId
    }

    func loadPersonas() async {
        guard personas.isEmpty else { return }
        do {
            let resp: PersonasResponse = try await NetworkManager.shared.fetchPersonas()
            personas = resp.personas
        } catch {
            // Use defaults silently
            personas = PersonaInfo.defaults
        }
    }

    func loadHistory() async {
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            let resp: ChatHistoryResponse = try await NetworkManager.shared.fetchChatHistory(
                sessionId: sessionId, persona: selectedPersona, limit: 40
            )
            messages = resp.messages
        } catch {
            // Start fresh
        }
    }

    func send() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }
        inputText = ""
        isSending = true
        errorMessage = nil

        let userMsg = ChatMessage(
            dbId: nil, role: "user", content: text,
            persona: selectedPersona, personaName: nil, personaEmoji: nil,
            ticker: nil, timestamp: ISO8601DateFormatter().string(from: Date())
        )
        messages.append(userMsg)

        do {
            let resp: ChatResponse = try await NetworkManager.shared.researchChat(
                message: text, persona: selectedPersona, sessionId: sessionId
            )
            let assistantMsg = ChatMessage(
                dbId: nil, role: "assistant", content: resp.response,
                persona: resp.persona, personaName: resp.personaName,
                personaEmoji: resp.personaEmoji, ticker: nil,
                timestamp: ISO8601DateFormatter().string(from: Date())
            )
            messages.append(assistantMsg)
        } catch {
            errorMessage = error.localizedDescription
            messages.removeLast()
            inputText = text
        }
        isSending = false
    }

    func switchPersona(_ key: String) {
        selectedPersona = key
        messages = []
        Task { await loadHistory() }
    }
}

// MARK: - Compare VM

@MainActor
final class ResearchCompareViewModel: ObservableObject {
    @Published var result: CompareResult?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var tickerInput: String = ""
    @Published var perspective: String = "ALL"

    func compare() async {
        let tickers = tickerInput
            .uppercased()
            .components(separatedBy: CharacterSet(charactersIn: ", "))
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        guard tickers.count >= 2 else {
            errorMessage = "Enter at least 2 tickers (comma-separated)"
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            result = try await NetworkManager.shared.compareTickers(tickers: tickers, perspective: perspective)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Social Trending VM

@MainActor
final class SocialTrendingViewModel: ObservableObject {
    @Published var trending: [SocialTrend] = []
    @Published var isLoading = false
    @Published var fromCache = false
    @Published var scannedAt: String?
    @Published var errorMessage: String?

    func load() async {
        guard trending.isEmpty || !fromCache else { return }
        await refresh()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let resp: SocialTrendingResponse = try await NetworkManager.shared.fetchTrendingSocial()
            trending = resp.trending
            fromCache = resp.fromCache
            scannedAt = resp.scannedAt
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - News Impact VM

@MainActor
final class NewsImpactViewModel: ObservableObject {
    @Published var items: [NewsImpactItem] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    func load() async {
        guard items.isEmpty else { return }
        await refresh()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let resp: NewsImpactListResponse = try await NetworkManager.shared.fetchNewsImpact(limit: 30)
            items = resp.items
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Saved Research VM

@MainActor
final class SavedResearchViewModel: ObservableObject {
    @Published var items: [SavedResearchItem] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    func load() async {
        guard items.isEmpty else { return }
        await refresh()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let resp: SavedResearchListResponse = try await NetworkManager.shared.fetchSavedResearch()
            items = resp.items
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func delete(id: Int) async {
        do {
            try await NetworkManager.shared.deleteSavedResearch(id: id)
            items.removeAll { $0.dbId == id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Top-level Research VM (coordinates all sub-VMs)

@MainActor
final class ResearchViewModel: ObservableObject {
    @Published var selectedTab: ResearchTab = .chat
    @Published var marketBrief: MarketBriefData?
    @Published var sectors: [SectorData] = []
    @Published var isBriefLoading = false
    @Published var briefError: String?

    let chatVM = ResearchChatViewModel()
    let compareVM = ResearchCompareViewModel()
    let trendingVM = SocialTrendingViewModel()
    let newsVM = NewsImpactViewModel()
    let savedVM = SavedResearchViewModel()

    func loadMarketBrief() async {
        guard marketBrief == nil else { return }
        isBriefLoading = true
        briefError = nil
        defer { isBriefLoading = false }
        do {
            marketBrief = try await NetworkManager.shared.fetchMarketBrief()
        } catch {
            briefError = error.localizedDescription
        }
    }

    func loadSectors() async {
        guard sectors.isEmpty else { return }
        do {
            let resp: SectorHeatmapResponse = try await NetworkManager.shared.fetchSectors()
            sectors = resp.sectors
        } catch {
            // non-fatal, sectors just won't show
        }
    }
}

enum ResearchTab: String, CaseIterable {
    case chat = "Chat"
    case compare = "Compare"
    case trending = "Trending"
    case news = "News"
    case library = "Library"

    var systemImage: String {
        switch self {
        case .chat: return "bubble.left.and.bubble.right.fill"
        case .compare: return "arrow.left.arrow.right"
        case .trending: return "flame.fill"
        case .news: return "newspaper.fill"
        case .library: return "bookmark.fill"
        }
    }
}

// MARK: - PersonaInfo defaults

extension PersonaInfo {
    static let defaults: [PersonaInfo] = [
        PersonaInfo(key: "VALUE", name: "Value Investor", emoji: "🏰", color: "positive",
                    intro: "I analyze stocks through a fundamental, long-term lens."),
        PersonaInfo(key: "MOMENTUM", name: "Momentum Trader", emoji: "⚡", color: "accent",
                    intro: "I follow price trends, breakouts, and technical setups."),
        PersonaInfo(key: "RISK", name: "Risk Analyst", emoji: "🛡️", color: "warning",
                    intro: "I focus on downside scenarios, volatility, and drawdown."),
        PersonaInfo(key: "MACRO", name: "Macro Strategist", emoji: "🌍", color: "purple",
                    intro: "I examine macro trends, rates, sectors, and global flows."),
    ]
}

// MARK: - ChatMessage memberwise init (for local creation)

extension ChatMessage {
    init(dbId: Int?, role: String, content: String, persona: String?,
         personaName: String?, personaEmoji: String?, ticker: String?, timestamp: String?) {
        self.dbId = dbId
        self.role = role
        self.content = content
        self.persona = persona
        self.personaName = personaName
        self.personaEmoji = personaEmoji
        self.ticker = ticker
        self.timestamp = timestamp
    }
}
