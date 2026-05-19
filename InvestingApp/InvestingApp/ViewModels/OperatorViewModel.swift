import Foundation
import UIKit

struct CacheEntryStatus: Identifiable {
    let id: String
    let title: String
    let hasData: Bool
    let bytes: Int
}

@MainActor
final class OperatorViewModel: ObservableObject {
    @Published var backendHealth: BackendHealth?
    @Published var rootHealth: RootHealth?
    @Published var alphaCandidates: [AlphaCandidate] = []
    @Published var alphaReport: AlphaReport?
    @Published var alphaOutcomes: [AlphaOutcome] = []
    @Published var alphaLearning: AlphaLearning?
    @Published var alphaLearningRecommendations: AlphaLearningRecommendations?
    @Published var alphaShadowPolicy: AlphaShadowPolicy?
    @Published var alphaProposals: [AlphaProposal] = []
    @Published var showHistoricalProposals = false
    @Published var proposalShadowResults: [String: AlphaProposalShadowResults] = [:]
    @Published var proposalActionInProgress = false
    @Published var proposalActionMessage: String?
    @Published var proposalActionSuccess = false
    @Published var isLoading = false
    @Published var lastSync: Date?
    @Published var lastError: String?
    @Published var cacheStatus: [CacheEntryStatus] = []

    private let alphaCacheKey = "cached_alpha_top"
    private let alphaReportCacheKey = "cached_alpha_report"
    private let alphaOutcomesCacheKey = "cached_alpha_outcomes"
    private let alphaLearningCacheKey = "cached_alpha_learning"
    private let alphaLearningRecommendationsCacheKey = "cached_alpha_learning_recommendations"
    private let alphaShadowPolicyCacheKey = "cached_alpha_shadow_policy"
    private let alphaProposalsCacheKey = "cached_alpha_proposals"
    private let lastSyncKey = "operator_last_successful_sync"
    private let signingReminderKey = "operator_signing_reminder_date"
    private let cacheKeys = [
        "cached_portfolio": "Portfolio",
        "cached_market": "Market",
        "cached_opportunities": "Opportunities",
        "cached_signals": "Feed",
        "cached_alpha_top": "Alpha",
        "cached_alpha_report": "Alpha Report",
        "cached_alpha_outcomes": "Alpha Outcomes",
        "cached_alpha_learning": "Alpha Learning",
        "cached_alpha_learning_recommendations": "Learning Recs",
        "cached_alpha_shadow_policy": "Shadow Policy",
        "cached_alpha_proposals": "Proposals",
        "cached_watchlist": "Watchlist"
    ]

    init() {
        loadLocalState()
    }

    var apiSecretConfigured: Bool {
        !(UserDefaults.standard.string(forKey: "api_secret") ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var signingReminderDate: Date {
        get { UserDefaults.standard.object(forKey: signingReminderKey) as? Date ?? Calendar.current.date(byAdding: .day, value: 5, to: Date()) ?? Date() }
        set { UserDefaults.standard.set(newValue, forKey: signingReminderKey) }
    }

    var signingStatusText: String {
        let days = Calendar.current.dateComponents([.day], from: Date(), to: signingReminderDate).day ?? 0
        if days <= 0 { return "Rebuild in Xcode soon" }
        if days == 1 { return "Rebuild tomorrow" }
        return "Rebuild in \(days) days"
    }

    var buildInfo: String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "local"
        return "\(version) (\(build))"
    }

    var activeProposalCount: Int { alphaProposals.filter { $0.isActive }.count }
    var proposedCount: Int { alphaProposals.filter { $0.status == "PROPOSED" }.count }

    var pendingOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "PENDING" }.count
    }

    var completedOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "COMPLETE" }.count
    }

    var staleOutcomeCount: Int {
        alphaOutcomes.filter { $0.status.uppercased() == "STALE" }.count
    }

    func refreshAll() async {
        isLoading = true
        lastError = nil
        defer { isLoading = false }

        do {
            async let rootTask = NetworkManager.shared.fetchRootHealth()
            async let healthTask = NetworkManager.shared.fetchBackendHealth()
            async let alphaTask = NetworkManager.shared.fetchAlphaTop()
            async let reportTask = NetworkManager.shared.fetchAlphaReport()
            async let outcomesTask = NetworkManager.shared.fetchAlphaOutcomes()
            async let learningTask = NetworkManager.shared.fetchAlphaLearning()
            async let recommendationsTask = NetworkManager.shared.fetchAlphaLearningRecommendations()
            async let shadowPolicyTask = NetworkManager.shared.fetchAlphaShadowPolicy()
            async let proposalsTask = NetworkManager.shared.fetchAlphaProposals(includeHistorical: showHistoricalProposals)

            let (root, health, alpha, report, outcomes, learning, recommendations, shadowPolicy, proposalsResponse) = try await (
                rootTask,
                healthTask,
                alphaTask,
                reportTask,
                outcomesTask,
                learningTask,
                recommendationsTask,
                shadowPolicyTask,
                proposalsTask
            )
            rootHealth = root
            backendHealth = health
            alphaCandidates = alpha.results
            alphaReport = report
            alphaOutcomes = outcomes.results
            alphaLearning = learning
            alphaLearningRecommendations = recommendations
            alphaShadowPolicy = shadowPolicy
            alphaProposals = proposalsResponse.proposals
            lastSync = Date()
            persistAlpha(alpha.results)
            persist(report, key: alphaReportCacheKey)
            persist(outcomes.results, key: alphaOutcomesCacheKey)
            persist(learning, key: alphaLearningCacheKey)
            persist(recommendations, key: alphaLearningRecommendationsCacheKey)
            persist(shadowPolicy, key: alphaShadowPolicyCacheKey)
            persist(proposalsResponse.proposals, key: alphaProposalsCacheKey)
            UserDefaults.standard.set(lastSync, forKey: lastSyncKey)
            updateCacheStatus()
            HapticManager.impact(.light)
        } catch {
            lastError = error.localizedDescription
            loadAlphaCaches()
            updateCacheStatus()
            HapticManager.notification(.error)
        }
    }

    func refreshProposals() async {
        do {
            let response = try await NetworkManager.shared.fetchAlphaProposals(includeHistorical: showHistoricalProposals)
            alphaProposals = response.proposals
            persist(response.proposals, key: alphaProposalsCacheKey)
            updateCacheStatus()
        } catch {
            alphaProposals = load([AlphaProposal].self, key: alphaProposalsCacheKey) ?? alphaProposals
        }
    }

    func generateProposals() async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            let response = try await NetworkManager.shared.generateAlphaProposals(secret: secret)
            proposalActionMessage = response.generated == 0
                ? "No new proposals — recommendations unchanged"
                : "\(response.generated) proposal(s) generated"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func approveProposal(id: String, note: String?) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            _ = try await NetworkManager.shared.approveAlphaProposal(id: id, note: note, secret: secret)
            proposalActionMessage = "Approved for shadow — live weights unchanged"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func rejectProposal(id: String, reason: String?) async {
        guard let secret = UserDefaults.standard.string(forKey: "api_secret"), !secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            proposalActionMessage = "API_SECRET not configured in Settings"
            proposalActionSuccess = false
            return
        }
        proposalActionInProgress = true
        proposalActionMessage = nil
        defer { proposalActionInProgress = false }
        do {
            _ = try await NetworkManager.shared.rejectAlphaProposal(id: id, reason: reason, secret: secret)
            proposalActionMessage = "Proposal rejected"
            proposalActionSuccess = true
            await refreshProposals()
            HapticManager.notification(.success)
        } catch {
            proposalActionMessage = error.localizedDescription
            proposalActionSuccess = false
            HapticManager.notification(.error)
        }
    }

    func loadShadowResults(proposalId: String) async {
        do {
            let results = try await NetworkManager.shared.fetchAlphaProposalShadowResults(id: proposalId)
            proposalShadowResults[proposalId] = results
        } catch {
            // silently fail — view will show "not loaded" state
        }
    }

    func copyDebugInfo() {
        let lines = [
            "base=\(APIEndpoints.base)",
            "root_health=\(rootHealth?.status ?? "unknown")",
            "api_v1_health=\(backendHealth?.status ?? "unknown")",
            "db_connected=\(backendHealth?.dbConnected.description ?? "unknown")",
            "latest_scan=\(backendHealth?.latestScanTime ?? "none")",
            "alpha_candidates=\(alphaCandidates.count)",
            "alpha_outcomes=\(alphaOutcomes.count)",
            "alpha_complete=\(alphaLearning?.totalComplete.description ?? "unknown")",
            "learning_recommendations=\(alphaLearningRecommendations?.weightRecommendations.count.description ?? "unknown")",
            "shadow_changed=\(alphaShadowPolicy?.replayStats.changedCandidates.count.description ?? "unknown")",
            "last_sync=\(lastSync.map { ISO8601DateFormatter().string(from: $0) } ?? "never")",
            "api_secret_configured=\(apiSecretConfigured)",
            "build=\(buildInfo)",
            "last_error=\(lastError ?? "none")"
        ]
        UIPasteboard.general.string = lines.joined(separator: "\n")
        HapticManager.selection()
    }

    func clearLocalCache() {
        for key in cacheKeys.keys {
            UserDefaults.standard.removeObject(forKey: key)
        }
        alphaCandidates = []
        alphaReport = nil
        alphaOutcomes = []
        alphaLearning = nil
        alphaLearningRecommendations = nil
        alphaShadowPolicy = nil
        alphaProposals = []
        proposalShadowResults = [:]
        updateCacheStatus()
        HapticManager.impact(.medium)
    }

    func openBackendHealth() {
        guard let url = URL(string: "\(APIEndpoints.base)/health") else { return }
        UIApplication.shared.open(url)
    }

    func setReminder(daysFromNow: Int) {
        signingReminderDate = Calendar.current.date(byAdding: .day, value: daysFromNow, to: Date()) ?? Date()
        objectWillChange.send()
    }

    private func loadLocalState() {
        lastSync = UserDefaults.standard.object(forKey: lastSyncKey) as? Date
        loadAlphaCaches()
        updateCacheStatus()
    }

    private func loadAlphaCaches() {
        alphaCandidates = load([AlphaCandidate].self, key: alphaCacheKey) ?? []
        alphaReport = load(AlphaReport.self, key: alphaReportCacheKey)
        alphaOutcomes = load([AlphaOutcome].self, key: alphaOutcomesCacheKey) ?? []
        alphaLearning = load(AlphaLearning.self, key: alphaLearningCacheKey)
        alphaLearningRecommendations = load(AlphaLearningRecommendations.self, key: alphaLearningRecommendationsCacheKey)
        alphaShadowPolicy = load(AlphaShadowPolicy.self, key: alphaShadowPolicyCacheKey)
        alphaProposals = load([AlphaProposal].self, key: alphaProposalsCacheKey) ?? []
    }

    private func persistAlpha(_ alpha: [AlphaCandidate]) {
        guard let data = try? JSONEncoder().encode(alpha) else { return }
        UserDefaults.standard.set(data, forKey: alphaCacheKey)
    }

    private func persist<T: Encodable>(_ value: T, key: String) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    private func load<T: Decodable>(_ type: T.Type, key: String) -> T? {
        guard let data = UserDefaults.standard.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    private func updateCacheStatus() {
        cacheStatus = cacheKeys
            .map { key, title in
                let bytes = UserDefaults.standard.data(forKey: key)?.count ?? 0
                return CacheEntryStatus(id: key, title: title, hasData: bytes > 0, bytes: bytes)
            }
            .sorted { $0.title < $1.title }
    }
}
