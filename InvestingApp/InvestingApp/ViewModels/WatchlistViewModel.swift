import Foundation

@MainActor
final class WatchlistViewModel: ObservableObject {
    @Published var alerts: [WatchlistAlert] = []
    @Published var isLoading = false
    @Published var isSaving = false
    @Published var errorMessage: String?

    // Form state for adding a new alert
    @Published var newTicker = ""
    @Published var newAlertPrice = ""
    @Published var newDirection = "above"
    @Published var newNote = ""

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            alerts = try await NetworkManager.shared.fetchWatchlist()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addAlert() async {
        let ticker = newTicker.uppercased().trimmingCharacters(in: .whitespaces)
        guard !ticker.isEmpty,
              let price = Double(newAlertPrice), price > 0 else {
            errorMessage = "Enter a valid ticker and price."
            return
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try await NetworkManager.shared.addWatchlistAlert(
                ticker: ticker,
                alertPrice: price,
                direction: newDirection,
                note: newNote
            )
            HapticManager.notification(.success)
            newTicker = ""
            newAlertPrice = ""
            newNote = ""
            await load()
        } catch {
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
    }

    func deleteAlert(id: Int) async {
        do {
            try await NetworkManager.shared.deleteWatchlistAlert(id: id)
            alerts.removeAll { $0.id == id }
            HapticManager.impact(.medium)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
