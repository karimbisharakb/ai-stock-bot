import Foundation
import SwiftUI

@MainActor
final class OpportunityViewModel: ObservableObject {
    @Published var opportunities: [Opportunity] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var selectedOpportunity: Opportunity?

    private let cacheKey = "cached_opportunities"

    init() {
        loadFromCache()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let ops = try await NetworkManager.shared.fetchOpportunities()
            opportunities = ops
            saveToCache(ops)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadFromCache() {
        if let data = UserDefaults.standard.data(forKey: cacheKey),
           let ops = try? JSONDecoder().decode([Opportunity].self, from: data) {
            opportunities = ops
        }
    }

    private func saveToCache(_ ops: [Opportunity]) {
        if let data = try? JSONEncoder().encode(ops) {
            UserDefaults.standard.set(data, forKey: cacheKey)
        }
    }
}
