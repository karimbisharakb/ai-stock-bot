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
        case entryPrice = "entry_price"
        case stopPrice = "stop_price"
        case positionSizeCad = "position_size_cad"
        case alertTime = "alert_time"
        case price7dLater = "price_7d_later"
        case price14dLater = "price_14d_later"
        case price30dLater = "price_30d_later"
    }
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

    var all: [(name: String, detail: Detail)] {
        [
            ("Unusual Options", options),
            ("Insider Buy",     insider),
            ("Short Squeeze",   shortSqueeze),
            ("Catalyst",        catalyst),
            ("Institutions",    institutional),
            ("Breakout",        breakout),
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

extension Color {
    static func forScore(_ score: Double) -> Color {
        switch score {
        case 8...: return .positive
        case 6..<8: return .warning
        default: return .textSecondary
        }
    }
}
