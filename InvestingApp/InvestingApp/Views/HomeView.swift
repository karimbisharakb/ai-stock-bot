import SwiftUI

struct HomeView: View {
    @StateObject private var vm = PortfolioViewModel()
    @State private var showValue = true

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
