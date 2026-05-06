import Foundation

struct Portfolio: Codable {
    let totalValueCAD: Double
    let dailyPnL: Double
    let dailyPnLPercent: Double
    let allTimeGain: Double
    let allTimeGainPercent: Double
    let availableCash: Double
    let holdings: [Holding]
    let historyPoints: [HistoryPoint]?

    enum CodingKeys: String, CodingKey {
        case totalValueCAD = "total_value_cad"
        case dailyPnL = "daily_pnl"
        case dailyPnLPercent = "daily_pnl_percent"
        case allTimeGain = "all_time_gain"
        case allTimeGainPercent = "all_time_gain_percent"
        case availableCash = "available_cash"
        case holdings
        case historyPoints = "history_points"
    }
}

struct Holding: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let shares: Double
    let avgCostCAD: Double
    let currentPriceCAD: Double
    let currentPriceUSD: Double?
    let currency: String
    let totalValueCAD: Double
    let gainLossCAD: Double
    let gainLossPercent: Double
    let isCanadian: Bool

    enum CodingKeys: String, CodingKey {
        case ticker
        case shares
        case avgCostCAD = "avg_cost_cad"
        case currentPriceCAD = "current_price_cad"
        case currentPriceUSD = "current_price_usd"
        case currency
        case totalValueCAD = "total_value_cad"
        case gainLossCAD = "gain_loss_cad"
        case gainLossPercent = "gain_loss_percent"
        case isCanadian = "is_canadian"
    }
}

struct HistoryPoint: Codable, Identifiable {
    let id = UUID()
    let date: String
    let valueCAD: Double

    enum CodingKeys: String, CodingKey {
        case date
        case valueCAD = "value_cad"
    }
}

struct ParsedTrade: Codable {
    var ticker: String
    var shares: Double
    var priceCAD: Double
    var currency: String
    var totalCAD: Double
    var tradeType: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case shares
        case priceCAD = "price_cad"
        case currency
        case totalCAD = "total_cad"
        case tradeType = "trade_type"
    }
}
