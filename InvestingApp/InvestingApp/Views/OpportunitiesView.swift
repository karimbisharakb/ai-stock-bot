import SwiftUI

struct OpportunitiesView: View {
    @StateObject private var predatorVM = PredatorViewModel()
    @StateObject private var scannerVM = OpportunityViewModel()
    @State private var expandedPredatorID: Int?

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                if predatorVM.isLoading && predatorVM.alerts.isEmpty
                    && scannerVM.isLoading && scannerVM.opportunities.isEmpty {
                    skeletonView
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            // ── Pre-Explosion Alerts ──────────────────────
                            sectionHeader(
                                icon: "bolt.fill",
                                title: "Pre-Explosion Alerts",
                                color: .negative
                            )
                            .padding(.horizontal, 20)
                            .padding(.top, 8)

                            if predatorVM.alerts.isEmpty && !predatorVM.isLoading {
                                emptyCard(
                                    icon: "chart.xyaxis.line",
                                    text: "Scanning \(30) tickers — alerts fire at score ≥ 8/10."
                                )
                                .padding(.horizontal, 20)
                            } else {
                                ForEach(predatorVM.alerts) { alert in
                                    PredatorCard(alert: alert, isExpanded: expandedPredatorID == alert.id)
                                        .padding(.horizontal, 20)
                                        .onTapGesture {
                                            HapticManager.selection()
                                            withAnimation(.spring(response: 0.3)) {
                                                expandedPredatorID = (expandedPredatorID == alert.id) ? nil : alert.id
                                            }
                                        }
                                }
                            }

                            // ── Scanner Picks ─────────────────────────────
                            sectionHeader(
                                icon: "sparkles",
                                title: "Scanner Picks",
                                color: .accent
                            )
                            .padding(.horizontal, 20)
                            .padding(.top, 8)

                            if scannerVM.opportunities.isEmpty && !scannerVM.isLoading {
                                emptyCard(icon: "star.slash", text: "No scanner picks yet.")
                                    .padding(.horizontal, 20)
                            } else {
                                ForEach(scannerVM.opportunities) { opp in
                                    OpportunityCard(opportunity: opp)
                                        .padding(.horizontal, 20)
                                }
                            }

                            Spacer().frame(height: 100)
                        }
                    }
                    .refreshable {
                        await predatorVM.refresh()
                        await scannerVM.refresh()
                    }
                }
            }
            .navigationTitle("Opportunities")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .task {
            await predatorVM.refresh()
            await scannerVM.refresh()
        }
    }

    func sectionHeader(icon: String, title: String, color: Color) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.system(size: 13, weight: .semibold))
            Text(title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.textPrimary)
            Spacer()
        }
    }

    func emptyCard(icon: String, text: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundColor(.textSecondary)
                .font(.system(size: 20))
            Text(text)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
            Spacer()
        }
        .padding(14)
        .background(Color.surface)
        .cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.border, lineWidth: 0.5))
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
}

// MARK: - Predator Card

struct PredatorCard: View {
    let alert: PredatorAlert
    var isExpanded: Bool = false

    private let signalNames = [
        ("options",       "Unusual Options", 3),
        ("insider",       "Insider Buy",     2),
        ("short_squeeze", "Short Squeeze",   2),
        ("catalyst",      "Catalyst",        2),
        ("institutional", "Institutions",    1),
        ("breakout",      "Breakout",        2),
    ]

    var scoreColor: Color { Color.forScore(alert.score) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Top row
            HStack(alignment: .top, spacing: 12) {
                // Score badge
                ZStack {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(scoreColor.opacity(0.12))
                        .frame(width: 56, height: 56)
                    VStack(spacing: 1) {
                        Text(String(format: "%.0f", alert.score))
                            .font(.system(size: 22, weight: .bold))
                            .foregroundColor(scoreColor)
                        Text("/10")
                            .font(.system(size: 9, weight: .medium))
                            .foregroundColor(scoreColor.opacity(0.7))
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(alert.ticker)
                        .font(.system(size: 17, weight: .bold))
                        .foregroundColor(.textPrimary)
                    // Top active signals as chips
                    let active = alert.signals.all.filter { $0.detail.score > 0 }
                    if !active.isEmpty {
                        HStack(spacing: 4) {
                            ForEach(active.prefix(2), id: \.name) { item in
                                Text(item.name)
                                    .font(.system(size: 10, weight: .semibold))
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 3)
                                    .background(scoreColor.opacity(0.12))
                                    .foregroundColor(scoreColor)
                                    .cornerRadius(5)
                            }
                        }
                    }
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 4) {
                    if alert.score >= 8 {
                        Text("ALERT")
                            .font(.system(size: 9, weight: .bold))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(Color.negative.opacity(0.15))
                            .foregroundColor(.negative)
                            .cornerRadius(5)
                    }
                    Text(AppDateFormatter.relative(from: alert.alertTime))
                        .font(.system(size: 10))
                        .foregroundColor(.textSecondary)
                }
            }
            .padding(16)

            if isExpanded {
                Divider().background(Color.border).padding(.horizontal, 16)

                VStack(alignment: .leading, spacing: 10) {
                    // All 6 signals
                    ForEach(alert.signals.all, id: \.name) { item in
                        HStack(alignment: .top, spacing: 8) {
                            signalDot(score: item.detail.score, maxScore: maxScore(for: item.name))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.name)
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundColor(item.detail.score > 0 ? .textPrimary : .textSecondary)
                                if !item.detail.reason.isEmpty {
                                    Text(item.detail.reason)
                                        .font(.system(size: 11))
                                        .foregroundColor(.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Spacer()
                            Text("\(item.detail.score)")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(item.detail.score > 0 ? scoreColor : .textSecondary.opacity(0.4))
                        }
                    }

                    Divider().background(Color.border)

                    // Entry / Stop
                    if let entry = alert.entryPrice, let stop = alert.stopPrice {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Entry")
                                    .font(.system(size: 10))
                                    .foregroundColor(.textSecondary)
                                Text("$\(String(format: "%.2f", entry))")
                                    .font(.system(size: 14, weight: .bold))
                                    .foregroundColor(.textPrimary)
                            }
                            Spacer()
                            VStack(alignment: .center, spacing: 2) {
                                Text("Stop")
                                    .font(.system(size: 10))
                                    .foregroundColor(.textSecondary)
                                Text("$\(String(format: "%.2f", stop))")
                                    .font(.system(size: 14, weight: .bold))
                                    .foregroundColor(.negative)
                            }
                            Spacer()
                            if let pos = alert.positionSizeCad, pos > 0 {
                                VStack(alignment: .trailing, spacing: 2) {
                                    Text("Position")
                                        .font(.system(size: 10))
                                        .foregroundColor(.textSecondary)
                                    Text(CurrencyFormatter.formatCAD(pos))
                                        .font(.system(size: 14, weight: .bold))
                                        .foregroundColor(.accent)
                                }
                            }
                        }
                    }

                    // Outcome if resolved
                    if let outcome = alert.outcome {
                        Text(outcome)
                            .font(.system(size: 12, weight: .semibold))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(outcomeColor(outcome).opacity(0.12))
                            .foregroundColor(outcomeColor(outcome))
                            .cornerRadius(8)
                    }
                }
                .padding(16)

                // Analyze button
                Button {
                    HapticManager.selection()
                    NotificationCenter.default.post(
                        name: .analyzeTickerRequested,
                        object: nil,
                        userInfo: ["ticker": alert.ticker]
                    )
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 11, weight: .semibold))
                        Text("Deep Analyze \(alert.ticker.replacingOccurrences(of: ".TO", with: ""))")
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
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(scoreColor.opacity(alert.score >= 8 ? 0.4 : 0.15), lineWidth: 0.5)
        )
    }

    @ViewBuilder
    func signalDot(score: Int, maxScore: Int) -> some View {
        Circle()
            .fill(score == 0 ? Color.textSecondary.opacity(0.2)
                  : score >= maxScore ? Color.positive
                  : Color.warning)
            .frame(width: 8, height: 8)
            .padding(.top, 4)
    }

    func maxScore(for name: String) -> Int {
        switch name {
        case "Unusual Options": return 3
        case "Insider Buy", "Short Squeeze", "Catalyst": return 2
        default: return 1
        }
    }

    func outcomeColor(_ outcome: String) -> Color {
        if outcome.hasPrefix("WIN") { return .positive }
        if outcome.hasPrefix("LOSS") { return .negative }
        return .warning
    }
}

// MARK: - Scanner Opportunity Card (unchanged)

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
            HStack(alignment: .top, spacing: 12) {
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

            HStack(spacing: 0) {
                MetricItem(label: "Entry", value: formatPrice(opportunity))
                Divider().background(Color.border).frame(height: 30)
                MetricItem(label: "Position", value: CurrencyFormatter.formatCompact(opportunity.suggestedPositionCAD))
                Divider().background(Color.border).frame(height: 30)
                MetricItem(label: opportunity.currency, value: opportunity.currency == "USD" ? "→ CAD" : "CA$")
            }
            .padding(.vertical, 12)

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
