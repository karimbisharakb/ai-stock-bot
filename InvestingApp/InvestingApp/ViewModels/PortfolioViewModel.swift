import Foundation
import SwiftUI

@MainActor
final class PortfolioViewModel: ObservableObject {
    @Published var portfolio: Portfolio?
    @Published var marketData: MarketData?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var lastUpdated: Date?

    private let cacheKey = "cached_portfolio"
    private let marketCacheKey = "cached_market"

    var isStale: Bool {
        guard let updated = lastUpdated else { return true }
        return Date().timeIntervalSince(updated) > 300
    }

    init() {
        loadFromCache()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        async let portfolioTask = NetworkManager.shared.fetchPortfolio()
        async let marketTask = NetworkManager.shared.fetchMarketData()

        do {
            let (p, m) = try await (portfolioTask, marketTask)
            portfolio = p
            marketData = m
            lastUpdated = Date()
            saveToCache(portfolio: p, market: m)
            HapticManager.impact(.light)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadFromCache() {
        if let data = UserDefaults.standard.data(forKey: cacheKey),
           let p = try? JSONDecoder().decode(Portfolio.self, from: data) {
            portfolio = p
        }
        if let data = UserDefaults.standard.data(forKey: marketCacheKey),
           let m = try? JSONDecoder().decode(MarketData.self, from: data) {
            marketData = m
        }
    }

    private func saveToCache(portfolio: Portfolio, market: MarketData) {
        if let data = try? JSONEncoder().encode(portfolio) {
            UserDefaults.standard.set(data, forKey: cacheKey)
        }
        if let data = try? JSONEncoder().encode(market) {
            UserDefaults.standard.set(data, forKey: marketCacheKey)
        }
    }
}
