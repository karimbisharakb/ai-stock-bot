import SwiftUI

struct OpportunityDetailView: View {
    let opportunity: Opportunity
    @Environment(\.dismiss) private var dismiss
    @State private var confirming = false
    @State private var showConfirmAlert = false
    @State private var confirmed = false
    @State private var errorMessage: String?

    var confidenceColor: Color {
        switch opportunity.confidenceLevel {
        case .high: return .positive
        case .medium: return .warning
        case .low: return .negative
        }
    }

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        // Header
                        HStack(alignment: .center, spacing: 16) {
                            ZStack {
                                RoundedRectangle(cornerRadius: 16)
                                    .fill(confidenceColor.opacity(0.12))
                                    .frame(width: 72, height: 72)
                                Text(opportunity.ticker.replacingOccurrences(of: ".TO", with: ""))
                                    .font(.system(size: 18, weight: .bold))
                                    .foregroundColor(confidenceColor)
                            }
                            VStack(alignment: .leading, spacing: 6) {
                                Text(opportunity.ticker)
                                    .font(.system(size: 24, weight: .bold))
                                    .foregroundColor(.textPrimary)
                                Text(opportunity.catalyst)
                                    .font(.system(size: 14))
                                    .foregroundColor(.textSecondary)
                            }
                            Spacer()
                            ConfidenceRingView(confidence: opportunity.confidence)
                        }
                        .padding(20)
                        .background(Color.surface)
                        .cornerRadius(16)

                        // Price & Position
                        HStack(spacing: 12) {
                            DetailMetricCard(label: "Entry Price",
                                value: "\(opportunity.currency == "CAD" ? "CA$" : "$")\(String(format: "%.2f", opportunity.entryPrice))",
                                icon: "tag.fill",
                                color: .accent)
                            DetailMetricCard(label: "Suggested Position",
                                value: CurrencyFormatter.formatCAD(opportunity.suggestedPositionCAD),
                                icon: "dollarsign.circle.fill",
                                color: .positive)
                        }

                        // Catalyst detail
                        SectionCard(title: "Catalyst", icon: "bolt.fill", iconColor: .warning) {
                            Text(opportunity.catalystDetail)
                                .font(.system(size: 14))
                                .foregroundColor(.textSecondary)
                                .lineSpacing(5)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        // Technical indicators
                        SectionCard(title: "Technical Analysis", icon: "chart.xyaxis.line", iconColor: .accent) {
                            VStack(spacing: 10) {
                                ForEach(opportunity.indicators) { indicator in
                                    HStack(spacing: 10) {
                                        Image(systemName: indicator.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                                            .foregroundColor(indicator.passed ? .positive : .negative)
                                            .font(.system(size: 16))
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(indicator.name)
                                                .font(.system(size: 13, weight: .medium))
                                                .foregroundColor(.textPrimary)
                                            if let detail = indicator.detail {
                                                Text(detail)
                                                    .font(.system(size: 11))
                                                    .foregroundColor(.textSecondary)
                                            }
                                        }
                                        Spacer()
                                        Text(indicator.value)
                                            .font(.system(size: 13, weight: .semibold))
                                            .foregroundColor(indicator.passed ? .positive : .negative)
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 3)
                                            .background(
                                                (indicator.passed ? Color.positive : Color.negative).opacity(0.12)
                                            )
                                            .cornerRadius(6)
                                    }
                                    if indicator.id != opportunity.indicators.last?.id {
                                        Divider().background(Color.border)
                                    }
                                }
                            }
                        }

                        // Risk factors
                        if !opportunity.riskFactors.isEmpty {
                            SectionCard(title: "Risk Factors", icon: "exclamationmark.triangle.fill", iconColor: .negative) {
                                VStack(alignment: .leading, spacing: 8) {
                                    ForEach(opportunity.riskFactors, id: \.self) { risk in
                                        HStack(alignment: .top, spacing: 8) {
                                            Image(systemName: "minus.circle.fill")
                                                .foregroundColor(.negative.opacity(0.7))
                                                .font(.system(size: 12))
                                                .padding(.top, 2)
                                            Text(risk)
                                                .font(.system(size: 13))
                                                .foregroundColor(.textSecondary)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                    }
                                }
                            }
                        }

                        // Claude reasoning
                        SectionCard(title: "Claude's Analysis", icon: "brain.fill", iconColor: .accent) {
                            Text(opportunity.claudeReasoning)
                                .font(.system(size: 13))
                                .foregroundColor(.textSecondary)
                                .lineSpacing(5)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        if let error = errorMessage {
                            Text(error)
                                .font(.system(size: 12))
                                .foregroundColor(.negative)
                                .padding(.horizontal, 20)
                        }

                        // Action buttons
                        HStack(spacing: 12) {
                            Button {
                                HapticManager.impact(.medium)
                                dismiss()
                            } label: {
                                Text("Pass")
                                    .font(.system(size: 16, weight: .semibold))
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 16)
                                    .background(Color.surface)
                                    .foregroundColor(.textSecondary)
                                    .cornerRadius(14)
                                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.border, lineWidth: 0.5))
                            }

                            Button {
                                HapticManager.impact(.heavy)
                                showConfirmAlert = true
                            } label: {
                                Group {
                                    if confirming {
                                        ProgressView()
                                            .tint(.black)
                                    } else if confirmed {
                                        Label("Confirmed!", systemImage: "checkmark")
                                            .font(.system(size: 16, weight: .semibold))
                                    } else {
                                        Text("Confirm")
                                            .font(.system(size: 16, weight: .semibold))
                                    }
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 16)
                                .background(confirmed ? Color.positive : Color.positive)
                                .foregroundColor(.black)
                                .cornerRadius(14)
                            }
                            .disabled(confirming || confirmed)
                        }
                        .padding(.horizontal, 0)
                        .padding(.bottom, 40)
                    }
                    .padding(20)
                }
            }
            .navigationTitle(opportunity.ticker)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.textSecondary)
                    }
                }
            }
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .alert("Confirm Trade", isPresented: $showConfirmAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Confirm Buy") {
                Task { await confirmTrade() }
            }
        } message: {
            Text("Record a buy of \(opportunity.ticker) at \(opportunity.currency == "CAD" ? "CA$" : "$")\(String(format: "%.2f", opportunity.entryPrice))?")
        }
    }

    func confirmTrade() async {
        confirming = true
        defer { confirming = false }
        do {
            let shares = opportunity.suggestedPositionCAD / opportunity.entryPrice
            _ = try await NetworkManager.shared.confirmTrade(
                ticker: opportunity.ticker,
                shares: shares,
                priceCAD: opportunity.entryPrice,
                type: "BUY"
            )
            withAnimation { confirmed = true }
            HapticManager.notification(.success)
        } catch {
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
    }
}

struct SectionCard<Content: View>: View {
    let title: String
    let icon: String
    let iconColor: Color
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundColor(iconColor)
                    .font(.system(size: 13))
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
            }
            content
        }
        .padding(16)
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))
    }
}

struct DetailMetricCard: View {
    let label: String
    let value: String
    let icon: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.system(size: 18))
            Text(value)
                .font(.system(size: 18, weight: .bold))
                .foregroundColor(.textPrimary)
            Text(label)
                .font(.system(size: 11))
                .foregroundColor(.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color.surface)
        .cornerRadius(14)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.border, lineWidth: 0.5))
    }
}
