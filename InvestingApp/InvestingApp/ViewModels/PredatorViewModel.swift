import Foundation
import SwiftUI

@MainActor
final class PredatorViewModel: ObservableObject {
    @Published var alerts: [PredatorAlert] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let cacheKey = "cached_predator_alerts"

    init() {
        loadFromCache()
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let items = try await NetworkManager.shared.fetchPredatorAlerts()
            alerts = items
            saveToCache(items)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadFromCache() {
        if let data = UserDefaults.standard.data(forKey: cacheKey),
           let items = try? JSONDecoder().decode([PredatorAlert].self, from: data) {
            alerts = items
        }
    }

    private func saveToCache(_ items: [PredatorAlert]) {
        if let data = try? JSONEncoder().encode(items) {
            UserDefaults.standard.set(data, forKey: cacheKey)
        }
    }
}
