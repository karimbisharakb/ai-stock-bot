import Foundation
import SwiftUI

enum AnalysisStep: String, CaseIterable {
    case fetchingData = "Fetching market data..."
    case fundamentals = "Analyzing fundamentals..."
    case runningAI = "Running AI analysis..."
    case complete = "Analysis complete"
}

@MainActor
final class AnalyzeViewModel: ObservableObject {
    @Published var query = ""
    @Published var result: AnalysisResult?
    @Published var isLoading = false
    @Published var currentStep: AnalysisStep = .fetchingData
    @Published var stepIndex: Int = 0
    @Published var errorMessage: String?

    private var stepTimer: Timer?

    func analyze() async {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        let ticker = query.uppercased().trimmingCharacters(in: .whitespaces)

        isLoading = true
        result = nil
        errorMessage = nil
        stepIndex = 0
        currentStep = .fetchingData
        startStepAnimation()

        do {
            let r = try await NetworkManager.shared.analyzeStock(ticker: ticker)
            stopStepAnimation()
            currentStep = .complete
            result = r
            HapticManager.notification(.success)
        } catch {
            stopStepAnimation()
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
        isLoading = false
    }

    private func startStepAnimation() {
        let steps = AnalysisStep.allCases.dropLast()
        var idx = 0
        stepTimer = Timer.scheduledTimer(withTimeInterval: 1.8, repeats: true) { [weak self] _ in
            guard let self else { return }
            idx = (idx + 1) % steps.count
            Task { @MainActor in
                withAnimation {
                    self.currentStep = Array(steps)[idx]
                    self.stepIndex = idx
                }
            }
        }
    }

    private func stopStepAnimation() {
        stepTimer?.invalidate()
        stepTimer = nil
    }
}
