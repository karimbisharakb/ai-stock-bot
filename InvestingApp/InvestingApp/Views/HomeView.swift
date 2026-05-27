import SwiftUI

struct HomeView: View {
    @StateObject private var vm = PortfolioViewModel()
    @StateObject private var healthVM = PortfolioHealthViewModel()
    @StateObject private var researchVM = ResearchViewModel()
    @State private var showValue = true
    @State private var healthExpanded = false
    @State private var sectorsExpanded = false

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                ScrollView {
                    LazyVStack(spacing: 0) {
                        headerSection
                        if vm.isLoading && vm.portfolio == nil {
                            skeletonSection
                        } else if let portfolio = vm.portfolio {
                            portfolioContent(portfolio)
                        } else if let error = vm.errorMessage {
                            errorView(error)
                        }
                    }
                }
                .refreshable {
                    await vm.refresh()
                }
            }
            .navigationBarHidden(true)
        }
        .task {
            if vm.isStale {
                await vm.refresh()
            }
            await healthVM.load()
            await researchVM.loadMarketBrief()
            await researchVM.loadSectors()
        }
        .onReceive(NotificationCenter.default.publisher(for: .tradeConfirmed)) { _ in
            Task { await vm.refresh() }
        }
    }

    // MARK: - Header

    var headerSection: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Good \(greeting()),")
                        .font(.system(size: 14))
                        .foregroundColor(.textSecondary)
                    Text("Portfolio")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundColor(.textPrimary)
                }
                Spacer()
                Button {
                    HapticManager.selection()
                    showValue.toggle()
                } label: {
                    Image(systemName: showValue ? "eye.fill" : "eye.slash.fill")
                        .foregroundColor(.textSecondary)
                        .font(.system(size: 18))
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 8)

            if let market = vm.marketData {
                marketTickerBar(market)
            }
        }
    }

    func marketTickerBar(_ market: MarketData) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 16) {
                MarketPill(label: "S&P 500", value: market.sp500Price, change: market.sp500Change)
                MarketPill(label: "TSX", value: market.tsxPrice, change: market.tsxChange)
                MarketPill(label: "NASDAQ", value: market.nasdaqPrice, change: market.nasdaqChange)
                MarketPill(label: "VIX", value: market.vix, change: 0, showChange: false)
                MarketPill(label: "USD/CAD", value: market.usdCadRate, change: 0, showChange: false)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
        }
    }

    // MARK: - Portfolio Content

    func portfolioContent(_ portfolio: Portfolio) -> some View {
        VStack(spacing: 20) {
            // Main value card
            valueCard(portfolio)
                .padding(.horizontal, 20)
                .padding(.top, 8)

            // Sparkline
            if let history = portfolio.historyPoints, history.count > 1 {
                sparklineCard(history: history, gain: portfolio.dailyPnL)
                    .padding(.horizontal, 20)
            }

            // Portfolio Health Card
            if let health = healthVM.health {
                healthCard(health)
                    .padding(.horizontal, 20)
            } else if healthVM.isLoading {
                RoundedRectangle(cornerRadius: 16)
                    .fill(Color.surface)
                    .frame(height: 56)
                    .shimmer(isActive: true)
                    .padding(.horizontal, 20)
            }

            // Holdings
            if !portfolio.holdings.isEmpty {
                VStack(spacing: 0) {
                    HStack {
                        Text("Holdings")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(.textPrimary)
                        Spacer()
                        Text("\(portfolio.holdings.count) positions")
                            .font(.system(size: 12))
                            .foregroundColor(.textSecondary)
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 12)

                    VStack(spacing: 8) {
                        ForEach(portfolio.holdings) { holding in
                            HoldingRowView(holding: holding)
                                .padding(.horizontal, 20)
                        }
                    }
                }
            }

            // Market Brief card
            if researchVM.isBriefLoading {
                RoundedRectangle(cornerRadius: 16)
                    .fill(Color.surface)
                    .frame(height: 88)
                    .shimmer(isActive: true)
                    .padding(.horizontal, 20)
            } else if let brief = researchVM.marketBrief {
                marketBriefCard(brief)
                    .padding(.horizontal, 20)
            }

            // Sector Heatmap (collapsible)
            if !researchVM.sectors.isEmpty {
                sectorHeatmapSection
                    .padding(.horizontal, 20)
            }

            if let updated = vm.lastUpdated {
                Text("Updated \(AppDateFormatter.relative(from: ISO8601DateFormatter().string(from: updated)))")
                    .font(.system(size: 11))
                    .foregroundColor(.textSecondary)
                    .padding(.bottom, 100)
            } else {
                Spacer().frame(height: 100)
            }
        }
    }

    // MARK: - Market Brief Card

    func marketBriefCard(_ brief: MarketBriefData) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Market Brief", systemImage: "globe.americas.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Text(brief.fromCache ? "cached" : "live")
                    .font(.system(size: 10))
                    .foregroundColor(.textSecondary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.surfaceSecondary)
                    .clipShape(Capsule())
            }

            if !brief.keyMetrics.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(brief.keyMetrics.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                            VStack(spacing: 2) {
                                Text(key).font(.system(size: 10)).foregroundColor(.textSecondary)
                                Text(value).font(.system(size: 12, weight: .bold)).foregroundColor(.textPrimary)
                            }
                        }
                    }
                }
            }

            Text(brief.briefText)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
                .lineLimit(4)
        }
        .padding(14)
        .background(Color.surfacePrimary)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    // MARK: - Sector Heatmap Section

    var sectorHeatmapSection: some View {
        VStack(spacing: 0) {
            Button {
                HapticManager.selection()
                withAnimation(.spring(response: 0.3)) { sectorsExpanded.toggle() }
            } label: {
                HStack {
                    Label("Sectors", systemImage: "chart.bar.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.textPrimary)
                    Spacer()
                    Image(systemName: sectorsExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption.bold())
                        .foregroundColor(.textSecondary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(Color.surfacePrimary)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            if sectorsExpanded {
                VStack(spacing: 1) {
                    ForEach(researchVM.sectors) { sector in
                        HStack {
                            Text(sector.sectorName)
                                .font(.system(size: 13))
                                .foregroundColor(.textPrimary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Text(String(format: "%+.2f%%", sector.changePct))
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(sector.changePct >= 0 ? .positive : .negative)
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(Color.surfacePrimary)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    func valueCard(_ portfolio: Portfolio) -> some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text(showValue ? CurrencyFormatter.formatCAD(portfolio.totalValueCAD) : "••••••")
                    .font(.system(size: 40, weight: .bold, design: .rounded))
                    .foregroundColor(.textPrimary)
                    .contentTransition(.numericText())

                HStack(spacing: 12) {
                    // Daily P&L
                    HStack(spacing: 4) {
                        Image(systemName: portfolio.dailyPnL >= 0 ? "arrow.up.right" : "arrow.down.right")
                            .font(.system(size: 11, weight: .bold))
                        Text(showValue ? "\(CurrencyFormatter.formatCAD(abs(portfolio.dailyPnL))) today" : "•••")
                            .font(.system(size: 13, weight: .semibold))
                        Text(CurrencyFormatter.formatPercent(portfolio.dailyPnLPercent))
                            .font(.system(size: 12))
                    }
                    .foregroundColor(Color.forGainLoss(portfolio.dailyPnL))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(Color.forGainLoss(portfolio.dailyPnL).opacity(0.12))
                    .cornerRadius(8)
                }
            }

            Divider().background(Color.border)

            HStack {
                StatPill(label: "All-time Gain", value: showValue ? CurrencyFormatter.formatCAD(portfolio.allTimeGain) : "•••", color: Color.forGainLoss(portfolio.allTimeGain))
                Spacer()
                StatPill(label: "Available Cash", value: showValue ? CurrencyFormatter.formatCAD(portfolio.availableCash) : "•••", color: .accent)
            }
        }
        .padding(20)
        .background(Color.surface)
        .cornerRadius(20)
        .overlay(
            RoundedRectangle(cornerRadius: 20)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }

    func sparklineCard(history: [HistoryPoint], gain: Double) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("30-Day Performance")
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.textSecondary)
                .padding(.horizontal, 16)
                .padding(.top, 14)

            SparklineView(
                points: history.map { $0.valueCAD },
                color: gain >= 0 ? Color.positive : Color.negative
            )
            .frame(height: 80)
            .padding(.horizontal, 16)
            .padding(.bottom, 14)
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }

    // MARK: - Health Card

    func healthCard(_ health: PortfolioHealth) -> some View {
        VStack(spacing: 0) {
            Button {
                HapticManager.selection()
                withAnimation(.easeInOut(duration: 0.25)) {
                    healthExpanded.toggle()
                }
            } label: {
                HStack(spacing: 12) {
                    // Grade badge
                    ZStack {
                        RoundedRectangle(cornerRadius: 10)
                            .fill(healthGradeColor(health.grade).opacity(0.15))
                            .frame(width: 40, height: 40)
                        Text(health.grade)
                            .font(.system(size: 18, weight: .bold))
                            .foregroundColor(healthGradeColor(health.grade))
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text("Portfolio Health")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.textPrimary)
                        Text(health.summary)
                            .font(.system(size: 11))
                            .foregroundColor(.textSecondary)
                            .lineLimit(1)
                    }

                    Spacer()

                    // Score gauge
                    ZStack {
                        Circle()
                            .stroke(Color.border, lineWidth: 3)
                            .frame(width: 36, height: 36)
                        Circle()
                            .trim(from: 0, to: CGFloat(health.overallScore) / 100)
                            .stroke(healthGradeColor(health.grade), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            .frame(width: 36, height: 36)
                            .rotationEffect(.degrees(-90))
                        Text("\(health.overallScore)")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.textPrimary)
                    }

                    Image(systemName: healthExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.textSecondary)
                }
                .padding(14)
            }
            .buttonStyle(.plain)

            if healthExpanded && !health.holdingScores.isEmpty {
                Divider().background(Color.border)
                VStack(spacing: 0) {
                    ForEach(health.holdingScores) { hs in
                        HStack(spacing: 10) {
                            Text(hs.ticker)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.textPrimary)
                                .frame(width: 72, alignment: .leading)
                            // Score bar
                            GeometryReader { geo in
                                ZStack(alignment: .leading) {
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(Color.border)
                                        .frame(height: 5)
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(healthScoreColor(hs.score))
                                        .frame(width: geo.size.width * CGFloat(hs.score) / 10, height: 5)
                                }
                            }
                            .frame(height: 5)
                            Text("\(hs.score)/10")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(healthScoreColor(hs.score))
                                .frame(width: 36, alignment: .trailing)
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        if hs.id != health.holdingScores.last?.id {
                            Divider().background(Color.border).padding(.leading, 86)
                        }
                    }
                }
            }
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }

    func healthGradeColor(_ grade: String) -> Color {
        switch grade {
        case "A": return .positive
        case "B": return .accent
        case "C": return .warning
        default:  return .negative
        }
    }

    func healthScoreColor(_ score: Int) -> Color {
        switch score {
        case 8...10: return .positive
        case 6...7:  return .accent
        case 4...5:  return .warning
        default:     return .negative
        }
    }

    // MARK: - Skeleton / Error

    var skeletonSection: some View {
        VStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 20)
                .fill(Color.surface)
                .frame(height: 160)
                .shimmer(isActive: true)
                .padding(.horizontal, 20)
                .padding(.top, 8)

            ForEach(0..<4, id: \.self) { _ in
                SkeletonRow()
                    .padding(.horizontal, 20)
            }
        }
    }

    func errorView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 40))
                .foregroundColor(.textSecondary)
            Text("Connection Error")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(.textPrimary)
            Text(msg)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
            Button("Retry") {
                Task { await vm.refresh() }
            }
            .font(.system(size: 15, weight: .semibold))
            .foregroundColor(.accent)
        }
        .padding(.top, 60)
    }

    func greeting() -> String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<12: return "morning"
        case 12..<17: return "afternoon"
        default: return "evening"
        }
    }
}

struct MarketPill: View {
    let label: String
    let value: Double
    let change: Double
    var showChange = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9, weight: .medium))
                .foregroundColor(.textSecondary)
            Text(formatValue())
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(.textPrimary)
            if showChange {
                Text(CurrencyFormatter.formatPercent(change))
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Color.forGainLoss(change))
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color.surface)
        .cornerRadius(10)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }

    func formatValue() -> String {
        if value > 1000 {
            return String(format: "%.0f", value)
        }
        return String(format: "%.4f", value)
    }
}

struct StatPill: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 10))
                .foregroundColor(.textSecondary)
            Text(value)
                .font(.system(size: 15, weight: .bold))
                .foregroundColor(color)
        }
    }
}
