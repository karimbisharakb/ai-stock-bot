import Foundation

enum CurrencyFormatter {
    static let cadFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "CAD"
        f.currencySymbol = "$"
        f.maximumFractionDigits = 2
        f.minimumFractionDigits = 2
        return f
    }()

    static let usdFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.currencySymbol = "US$"
        f.maximumFractionDigits = 2
        f.minimumFractionDigits = 2
        return f
    }()

    static let percentFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .percent
        f.maximumFractionDigits = 2
        f.minimumFractionDigits = 2
        f.positivePrefix = "+"
        return f
    }()

    static func formatCAD(_ value: Double) -> String {
        cadFormatter.string(from: NSNumber(value: value)) ?? "$\(String(format: "%.2f", value))"
    }

    static func formatUSD(_ value: Double) -> String {
        usdFormatter.string(from: NSNumber(value: value)) ?? "$\(String(format: "%.2f", value))"
    }

    static func formatPercent(_ value: Double) -> String {
        let sign = value >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", value))%"
    }

    static func formatCompact(_ value: Double) -> String {
        let abs = Swift.abs(value)
        let sign = value < 0 ? "-" : ""
        switch abs {
        case 1_000_000_000...:
            return "\(sign)$\(String(format: "%.1f", abs / 1_000_000_000))B"
        case 1_000_000...:
            return "\(sign)$\(String(format: "%.1f", abs / 1_000_000))M"
        case 1_000...:
            return "\(sign)$\(String(format: "%.1f", abs / 1_000))K"
        default:
            return "\(sign)$\(String(format: "%.2f", abs))"
        }
    }
}
