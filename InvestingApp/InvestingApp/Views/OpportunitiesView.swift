import SwiftUI

struct OpportunitiesView: View {
    @StateObject private var vm = OpportunityViewModel()
    @State private var selectedOpportunity: Opportunity?

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                if vm.isLoading && vm.opportunities.isEmpty {
                    skeletonView
                } else if vm.opportunities.isEmpty && vm.errorMessage == nil {
                    emptyView
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            headerPill
                                .padding(.horizontal, 20)
                                .padding(.top, 8)

                            if let error = vm.errorMessage {
                                errorBanner(error)
                                    .padding(.horizontal, 20)
                            }

                            ForEach(vm.opportunities) { opp in
                                OpportunityCard(opportunity: opp)
                                    .padding(.horizontal, 20)
                                    .onTapGesture {
                                        HapticManager.selection()
                                        selectedOpportunity = opp
                                    }
                            }
                            Spacer().frame(height: 100)
                        }
                    }
                    .refreshable { await vm.refresh() }
                }
            }
            .navigationTitle("Opportunities")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .task { await vm.refresh() }
        .sheet(item: $selectedOpportunity) { opp in
            OpportunityDetailView(opportunity: opp)
        }
    }

    var headerPill: some View {
        HStack {
            Image(systemName: "sparkles")
                .foregroundColor(.accent)
                .font(.system(size: 13))
            Text("\(vm.opportunities.count) AI-discovered opportunities")
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.accent.opacity(0.08))
        .cornerRadius(10)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.accent.opacity(0.2), lineWidth: 0.5))
    }

    var skeletonView: some View {
        ScrollView {
            VStack(spacing: 12) {
                ForEach(0..<3, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 16)
                        .fill(Color.surface)
                        .frame(height: 160)
                        .shimmer(isActive: true)
                        .padding(.horizontal, 20)
                }
            }
            .padding(.top, 20)
        }
    }

    var emptyView: some View {
        VStack(spacing: 16) {
            Image(systemName: "star.slash")
                .font(.system(size: 44))
                .foregroundColor(.textSecondary)
            Text("No Opportunities")
                .font(.system(size: 20, weight: .semibold))
                .foregroundColor(.textPrimary)
            Text("AI is scanning markets. Check back soon.")
                .font(.system(size: 14))
                .foregroundColor(.textSecondary)
        }
    }

    func errorBanner(_ msg: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.warning)
                .font(.system(size: 13))
            Text(msg)
                .font(.system(size: 12))
                .foregroundColor(.textSecondary)
                .lineLimit(2)
        }
        .padding(12)
        .background(Color.warning.opacity(0.08))
        .cornerRadius(10)
    }
}

struct OpportunityCard: View {
    let opportunity: Opportunity

    var confidenceColor: Color {
        switch opportunity.confidenceLevel {
        case .high: return .positive
        case .medium: return .warning
        case .low: return .negative
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Top row
            HStack(alignment: .top, spacing: 12) {
                // Ticker
                ZStack {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(confidenceColor.opacity(0.12))
                        .frame(width: 56, height: 56)
                    Text(opportunity.ticker.replacingOccurrences(of: ".TO", with: ""))
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(confidenceColor)
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(opportunity.ticker)
                        .font(.system(size: 17, weight: .bold))
                        .foregroundColor(.textPrimary)
                    Text(opportunity.catalyst)
                        .font(.system(size: 13))
                        .foregroundColor(.textSecondary)
                        .lineLimit(2)
                }

                Spacer()

                ConfidenceRingView(confidence: opportunity.confidence)
            }
            .padding(16)

            Divider().background(Color.border).padding(.horizontal, 16)

            // Bottom metrics
            HStack(spacing: 0) {
                MetricItem(label: "Entry", value: formatPrice(opportunity))
                Divider().background(Color.border).frame(height: 30)
                MetricItem(label: "Position", value: CurrencyFormatter.formatCompact(opportunity.suggestedPositionCAD))
                Divider().background(Color.border).frame(height: 30)
                MetricItem(label: opportunity.currency, value: opportunity.currency == "USD" ? "→ CAD" : "CA$")
            }
            .padding(.vertical, 12)

            // Outcome badges if available
            if let outcome3d = opportunity.outcome3d {
                HStack(spacing: 8) {
                    OutcomeBadge(label: "3d", percent: outcome3d)
                    if let outcome7d = opportunity.outcome7d {
                        OutcomeBadge(label: "7d", percent: outcome7d)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
            }

            // Analyze button
            Button {
                HapticManager.selection()
                NotificationCenter.default.post(
                    name: .analyzeTickerRequested,
                    object: nil,
                    userInfo: ["ticker": opportunity.ticker]
                )
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 11, weight: .semibold))
                    Text("Analyze \(opportunity.ticker.replacingOccurrences(of: ".TO", with: ""))")
                        .font(.system(size: 12, weight: .semibold))
                }
                .foregroundColor(.accent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(Color.accent.opacity(0.1))
                .cornerRadius(10)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 14)
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(confidenceColor.opacity(0.2), lineWidth: 0.5)
        )
    }

    func formatPrice(_ opp: Opportunity) -> String {
        let symbol = opp.currency == "CAD" ? "CA$" : "$"
        return "\(symbol)\(String(format: "%.2f", opp.entryPrice))"
    }
}

struct MetricItem: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 3) {
            Text(label)
                .font(.system(size: 10))
                .foregroundColor(.textSecondary)
            Text(value)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(.textPrimary)
        }
        .frame(maxWidth: .infinity)
    }
}
