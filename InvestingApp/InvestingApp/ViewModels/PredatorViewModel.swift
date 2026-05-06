import Foundation
import SwiftUI

@MainActor
final class PredatorViewModel: ObservableObject {
    @Published var alerts: [PredatorAlert] = []
    @Published var isLoading = false
    @Published var error: String?

    private let cacheKey = "cached_predator_alerts"

    init() {
        loadFromCache()
    }

    func fetch() async {
        isLoading = true
        error = nil
        defer { isLoading = false }
        do {
            let items = try await NetworkManager.shared.fetchPredatorAlerts()
            alerts = items
            saveToCache(items)
        } catch let e {
            error = e.localizedDescription
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
