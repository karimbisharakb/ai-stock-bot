import Foundation
import SwiftUI

enum FeedFilter: String, CaseIterable {
    case all = "All"
    case buy = "Buy"
    case sell = "Sell"
    case confirmed = "Confirmed"
    case rejected = "Rejected"
}

@MainActor
final class FeedViewModel: ObservableObject {
    @Published var signals: [Signal] = []
    @Published var filter: FeedFilter = .all
    @Published var isLoading = false
    @Published var errorMessage: String?

    var filteredSignals: [Signal] {
        switch filter {
        case .all: return signals
        case .buy: return signals.filter { $0.direction.lowercased().contains("buy") }
        case .sell: return signals.filter { $0.direction.lowercased().contains("sell") }
        case .confirmed: return signals.filter { $0.verdict.uppercased() == "CONFIRMED" }
        case .rejected: return signals.filter { $0.verdict.uppercased() == "REJECTED" }
        }
    }

    private let cacheKey = "cached_signals"

    init() {
        loadFromCache()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let s = try await NetworkManager.shared.fetchSignals()
            signals = s
            saveToCache(s)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadFromCache() {
        if let data = UserDefaults.standard.data(forKey: cacheKey),
           let s = try? JSONDecoder().decode([Signal].self, from: data) {
            signals = s
        }
    }

    private func saveToCache(_ s: [Signal]) {
        if let data = try? JSONEncoder().encode(s) {
            UserDefaults.standard.set(data, forKey: cacheKey)
        }
    }
}
