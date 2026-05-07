import Foundation

@MainActor
final class PortfolioHealthViewModel: ObservableObject {
    @Published var health: PortfolioHealth?
    @Published var isLoading = false
    @Published var errorMessage: String?

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            health = try await NetworkManager.shared.fetchPortfolioHealth()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
