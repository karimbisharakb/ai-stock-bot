import Foundation

@MainActor
final class PaycheckPlannerViewModel: ObservableObject {
    @Published var deployment: PaycheckDeployment?
    @Published var isLoading = false
    @Published var isSaving = false
    @Published var errorMessage: String?
    @Published var savedSuccessfully = false

    // Form state
    @Published var paycheckAmount = ""
    @Published var paycheckDay = "15"
    @Published var allocationPercent = "100"

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            deployment = try await NetworkManager.shared.fetchPlannerNextDeployment()
            // Pre-fill form from existing config
            if let d = deployment, d.configured {
                if let amt = d.paycheckAmount, amt > 0 {
                    paycheckAmount = String(format: "%.2f", amt)
                }
                if let day = d.paycheckDay {
                    paycheckDay = "\(day)"
                }
                if let pct = d.allocationPercent {
                    allocationPercent = String(format: "%.0f", pct)
                }
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func save() async {
        guard let amount = Double(paycheckAmount), amount > 0,
              let day = Int(paycheckDay), 1...31 ~= day,
              let pct = Double(allocationPercent), 1...100 ~= pct else {
            errorMessage = "Enter valid paycheck amount, day (1-31), and allocation %."
            return
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            deployment = try await NetworkManager.shared.savePlannerSetup(
                paycheckAmount: amount,
                paycheckDay: day,
                allocationPercent: pct
            )
            savedSuccessfully = true
            HapticManager.notification(.success)
            // Reload to get recommendations
            await load()
        } catch {
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
    }
}
