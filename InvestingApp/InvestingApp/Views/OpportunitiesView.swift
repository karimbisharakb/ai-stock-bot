import SwiftUI

// MARK: - Predator model (self-contained; PredatorAlert.swift is intentionally empty)

struct PredatorAlert: Codable, Identifiable {
    let id: Int
    let ticker: String
    let score: Double
    let signals: PredatorSignals
    let entryPrice: Double?
    let stopPrice: Double?
    let positionSizeCad: Double?
    let alertTime: String
    let price7dLater: Double?
    let price14dLater: Double?
    let price30dLater: Double?
    let outcome: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker, score, signals, outcome
        case entryPrice      = "entry_price"
        case stopPrice       = "stop_price"
        case positionSizeCad = "position_size_cad"
        case alertTime       = "alert_time"
        case price7dLater    = "price_7d_later"
        case price14dLater   = "price_14d_later"
        case price30dLater   = "price_30d_later"
    }
}

struct SignalItem: Identifiable {
    let id: String
    let name: String
    let detail: PredatorSignals.Detail
    let maxScore: Int
}

struct PredatorSignals: Codable {
    struct Detail: Codable {
        let score: Int
        let reason: String
    }

    let options: Detail
    let insider: Detail
    let shortSqueeze: Detail
    let catalyst: Detail
    let institutional: Detail
    let breakout: Detail

    enum CodingKeys: String, CodingKey {
        case options, insider, catalyst, institutional, breakout
        case shortSqueeze = "short_squeeze"
    }

    var all: [SignalItem] {
        [
            SignalItem(id: "options",       name: "Unusual Options", detail: options,       maxScore: 3),
            SignalItem(id: "insider",       name: "Insider Buy",     detail: insider,       maxScore: 2),
            SignalItem(id: "short_squeeze", name: "Short Squeeze",   detail: shortSqueeze,  maxScore: 2),
            SignalItem(id: "catalyst",      name: "Catalyst",        detail: catalyst,      maxScore: 2),
            SignalItem(id: "institutional", name: "Institutions",    detail: institutional, maxScore: 1),
            SignalItem(id: "breakout",      name: "Breakout",        detail: breakout,      maxScore: 2),
        ]
    }
}

extension PredatorSignals {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let empty = Detail(score: 0, reason: "")
        options       = (try? c.decode(Detail.self, forKey: .options))       ?? empty
        insider       = (try? c.decode(Detail.self, forKey: .insider))       ?? empty
        shortSqueeze  = (try? c.decode(Detail.self, forKey: .shortSqueeze))  ?? empty
        catalyst      = (try? c.decode(Detail.self, forKey: .catalyst))      ?? empty
        institutional = (try? c.decode(Detail.self, forKey: .institutional)) ?? empty
        breakout      = (try? c.decode(Detail.self, forKey: .breakout))      ?? empty
    }
}

func colorForScore(_ score: Double) -> Color {
    if score >= 8 { return .positive }
    if score >= 6 { return .warning }
    return .textSecondary
}

// MARK: - ViewModel (self-contained; PredatorViewModel.swift is intentionally empty)

@MainActor
final class PredatorViewModel: ObservableObject {
    @Published var alerts: [PredatorAlert] = []
    @Published var isLoading = false
    @Published var error: String?

    private let cacheKey = "cached_predator_alerts"

    init() { loadFromCache() }

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

// MARK: - OpportunitiesView

struct OpportunitiesView: View {
    @StateObject private var predatorVM = PredatorViewModel()
    @StateObject private var scannerVM  = OpportunityViewModel()
    @State private var expandedID: Int?

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
                            sectionHeader(icon: "bolt.fill", title: "Pre-Explosion Alerts", color: Color.red)
                                .padding(.horizontal, 20)
                                .padding(.top, 8)

                            if predatorVM.alerts.isEmpty && !predatorVM.isLoading {
                                emptyCard(icon: "chart.xyaxis.line",
                                          text: "Scanning markets — alerts fire when score ≥ 8/10.")
                                    .padding(.horizontal, 20)
                            } else {
                                ForEach(predatorVM.alerts) { alert in
                                    PredatorCard(alert: alert, isExpanded: expandedID == alert.id)
                                        .padding(.horizontal, 20)
                                        .onTapGesture {
                                            HapticManager.selection()
                                            withAnimation(.spring(response: 0.3)) {
                                                expandedID = (expandedID == alert.id) ? nil : alert.id
                                            }
                                        }
                                }
                            }

                            sectionHeader(icon: "sparkles", title: "Scanner Picks", color: .accent)
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
                        await predatorVM.fetch()
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
            await predatorVM.fetch()
            await scannerVM.refresh()
        }
    }

    private func sectionHeader(icon: String, title: String, color: Color) -> some View {
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

    private func emptyCard(icon: String, text: String) -> some View {
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

    private var skeletonView: some View {
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

// MARK: - PredatorCard

struct PredatorCard: View {
    let alert: PredatorAlert
    var isExpanded: Bool = false

    private var scoreColor: Color { colorForScore(alert.score) }
    private var scoreInt: Int { Int(alert.score) }
    private var activeSignals: [SignalItem] { alert.signals.all.filter { $0.detail.score > 0 } }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            topRow.padding(16)
            if isExpanded {
                Divider().background(Color.border).padding(.horizontal, 16)
                expandedSection.padding(16)
                analyzeButton
            }
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(scoreColor.opacity(alert.score >= 8 ? 0.4 : 0.15), lineWidth: 0.5)
        )
    }

    private var topRow: some View {
        HStack(alignment: .top, spacing: 12) {
            // Score badge
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(scoreColor.opacity(0.12))
                    .frame(width: 56, height: 56)
                VStack(spacing: 1) {
                    Text("\(scoreInt)")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(scoreColor)
                    Text("/10")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(scoreColor.opacity(0.7))
                }
            }
            // Ticker + signal chips
            VStack(alignment: .leading, spacing: 4) {
                Text(alert.ticker)
                    .font(.system(size: 17, weight: .bold))
                    .foregroundColor(.textPrimary)
                if !activeSignals.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(Array(activeSignals.prefix(2))) { item in
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
            // Alert badge + timestamp
            VStack(alignment: .trailing, spacing: 4) {
                if alert.score >= 8 {
                    Text("ALERT")
                        .font(.system(size: 9, weight: .bold))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(Color.red.opacity(0.15))
                        .foregroundColor(Color.red)
                        .cornerRadius(5)
                }
                Text(AppDateFormatter.relative(from: alert.alertTime))
                    .font(.system(size: 10))
                    .foregroundColor(.textSecondary)
            }
        }
    }

    private var expandedSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            // All 6 signal rows
            ForEach(alert.signals.all) { item in
                HStack(alignment: .top, spacing: 8) {
                    Circle()
                        .fill(
                            item.detail.score == 0     ? Color.textSecondary.opacity(0.2)
                            : item.detail.score >= item.maxScore ? Color.positive
                            : Color.warning
                        )
                        .frame(width: 8, height: 8)
                        .padding(.top, 4)
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
                        .foregroundColor(item.detail.score > 0 ? scoreColor : Color.textSecondary.opacity(0.4))
                }
            }

            Divider().background(Color.border)

            // Entry / Stop / Position
            if let entry = alert.entryPrice, let stop = alert.stopPrice {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Entry").font(.system(size: 10)).foregroundColor(.textSecondary)
                        Text("$\(String(format: "%.2f", entry))")
                            .font(.system(size: 14, weight: .bold)).foregroundColor(.textPrimary)
                    }
                    Spacer()
                    VStack(alignment: .center, spacing: 2) {
                        Text("Stop").font(.system(size: 10)).foregroundColor(.textSecondary)
                        Text("$\(String(format: "%.2f", stop))")
                            .font(.system(size: 14, weight: .bold)).foregroundColor(Color.red)
                    }
                    Spacer()
                    if let pos = alert.positionSizeCad, pos > 0 {
                        VStack(alignment: .trailing, spacing: 2) {
                            Text("Position").font(.system(size: 10)).foregroundColor(.textSecondary)
                            Text(CurrencyFormatter.formatCAD(pos))
                                .font(.system(size: 14, weight: .bold)).foregroundColor(.accent)
                        }
                    }
                }
            }

            // Outcome badge if resolved
            if let outcome = alert.outcome {
                let oc: Color = outcome.hasPrefix("WIN") ? .positive : outcome.hasPrefix("LOSS") ? Color.red : .warning
                Text(outcome)
                    .font(.system(size: 12, weight: .semibold))
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(oc.opacity(0.12))
                    .foregroundColor(oc)
                    .cornerRadius(8)
            }
        }
    }

    private var analyzeButton: some View {
        Button {
            HapticManager.selection()
            NotificationCenter.default.post(
                name: .analyzeTickerRequested,
                object: nil,
                userInfo: ["ticker": alert.ticker]
            )
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").font(.system(size: 11, weight: .semibold))
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

// MARK: - Scanner Opportunity Card

struct OpportunityCard: View {
    let opportunity: Opportunity

    private var confidenceColor: Color {
        switch opportunity.confidenceLevel {
        case .high:   return .positive
        case .medium: return .warning
        case .low:    return Color.red
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
                MetricItem(label: "Entry",    value: formattedEntry)
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
                .padding(.horizontal, 16).padding(.bottom, 8)
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
                    Image(systemName: "magnifyingglass").font(.system(size: 11, weight: .semibold))
                    Text("Analyze \(opportunity.ticker.replacingOccurrences(of: ".TO", with: ""))")
                        .font(.system(size: 12, weight: .semibold))
                }
                .foregroundColor(.accent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(Color.accent.opacity(0.1))
                .cornerRadius(10)
            }
            .padding(.horizontal, 16).padding(.bottom, 14)
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(confidenceColor.opacity(0.2), lineWidth: 0.5))
    }

    private var formattedEntry: String {
        let symbol = opportunity.currency == "CAD" ? "CA$" : "$"
        return "\(symbol)\(String(format: "%.2f", opportunity.entryPrice))"
    }
}

// MARK: - MetricItem (shared by OpportunityCard)

struct MetricItem: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 3) {
            Text(label).font(.system(size: 10)).foregroundColor(.textSecondary)
            Text(value).font(.system(size: 13, weight: .semibold)).foregroundColor(.textPrimary)
        }
        .frame(maxWidth: .infinity)
    }
}
