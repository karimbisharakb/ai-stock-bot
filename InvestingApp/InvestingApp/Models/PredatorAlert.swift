import Foundation
import SwiftUI

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
        case entryPrice       = "entry_price"
        case stopPrice        = "stop_price"
        case positionSizeCad  = "position_size_cad"
        case alertTime        = "alert_time"
        case price7dLater     = "price_7d_later"
        case price14dLater    = "price_14d_later"
        case price30dLater    = "price_30d_later"
    }
}

// MARK: - Signal breakdown

struct SignalItem: Identifiable {
    let id: String       // unique key, e.g. "options"
    let name: String     // display label, e.g. "Unusual Options"
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

// Lenient decoding: missing or null signal keys fall back to score=0 rather than throwing.
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

// MARK: - Color helper

func colorForScore(_ score: Double) -> Color {
    if score >= 8 { return .positive }
    if score >= 6 { return .warning }
    return .textSecondary
}
