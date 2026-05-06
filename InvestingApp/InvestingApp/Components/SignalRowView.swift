import SwiftUI

struct SignalRowView: View {
    let signal: Signal
    var isExpanded: Bool = false

    var verdictColor: Color {
        switch signal.verdict.uppercased() {
        case "CONFIRMED": return .positive
        case "REJECTED": return .negative
        default: return .warning
        }
    }

    var directionColor: Color {
        signal.direction.lowercased().contains("buy") ? .positive : .negative
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                // Ticker badge
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(directionColor.opacity(0.12))
                        .frame(width: 46, height: 46)
                    Text(signal.ticker.replacingOccurrences(of: ".TO", with: ""))
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(directionColor)
                }

                VStack(alignment: .leading, spacing: 3) {
                    Text(signal.ticker)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.textPrimary)
                    Text(signal.direction.capitalized)
                        .font(.system(size: 11))
                        .foregroundColor(directionColor)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 4) {
                    Text(signal.verdict.capitalized)
                        .font(.system(size: 10, weight: .bold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(verdictColor.opacity(0.15))
                        .foregroundColor(verdictColor)
                        .cornerRadius(6)

                    Text(AppDateFormatter.relative(from: signal.timestamp))
                        .font(.system(size: 10))
                        .foregroundColor(.textSecondary)
                }
            }
            .padding(14)

            if isExpanded {
                Divider().background(Color.border).padding(.horizontal, 14)

                VStack(alignment: .leading, spacing: 10) {
                    // Indicators
                    ForEach(signal.indicators) { indicator in
                        HStack {
                            Image(systemName: indicator.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundColor(indicator.passed ? .positive : .negative)
                                .font(.system(size: 13))
                            Text(indicator.name)
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textPrimary)
                            Spacer()
                            Text(indicator.value)
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundColor(.textSecondary)
                        }
                    }

                    Divider().background(Color.border)

                    // Confidence
                    HStack {
                        Text("Confidence")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(.textSecondary)
                        Spacer()
                        Text("\(signal.confidence)%")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundColor(.accent)
                    }

                    // Reasoning
                    Text(signal.reasoning)
                        .font(.system(size: 12))
                        .foregroundColor(.textSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)

                    // Outcome badges
                    if let outcome3d = signal.outcomePercent3d {
                        HStack(spacing: 8) {
                            OutcomeBadge(label: "3d", percent: outcome3d)
                            if let outcome7d = signal.outcomePercent7d {
                                OutcomeBadge(label: "7d", percent: outcome7d)
                            }
                        }
                    }
                }
                .padding(14)
            }
        }
        .background(Color.surface)
        .cornerRadius(14)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }
}

struct OutcomeBadge: View {
    let label: String
    let percent: Double

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: percent >= 0 ? "arrow.up" : "arrow.down")
                .font(.system(size: 9, weight: .bold))
            Text("\(label): \(CurrencyFormatter.formatPercent(percent))")
                .font(.system(size: 10, weight: .semibold))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.forGainLoss(percent).opacity(0.15))
        .foregroundColor(Color.forGainLoss(percent))
        .cornerRadius(6)
    }
}
