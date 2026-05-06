import SwiftUI

struct HoldingRowView: View {
    let holding: Holding

    var body: some View {
        HStack(spacing: 12) {
            // Ticker badge
            ZStack {
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.accent.opacity(0.12))
                    .frame(width: 50, height: 50)
                Text(holding.ticker.replacingOccurrences(of: ".TO", with: ""))
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.accent)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(holding.ticker)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Text("\(formatShares(holding.shares)) shares")
                    .font(.system(size: 12))
                    .foregroundColor(.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 3) {
                if !holding.isCanadian, let usdPrice = holding.currentPriceUSD {
                    VStack(alignment: .trailing, spacing: 1) {
                        Text(CurrencyFormatter.formatUSD(usdPrice))
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.textSecondary)
                        Text("→ \(CurrencyFormatter.formatCAD(holding.currentPriceCAD))")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(.textPrimary)
                    }
                } else {
                    Text(CurrencyFormatter.formatCAD(holding.currentPriceCAD))
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.textPrimary)
                }
                HStack(spacing: 4) {
                    Image(systemName: holding.gainLossPercent >= 0 ? "arrow.up.right" : "arrow.down.right")
                        .font(.system(size: 9, weight: .bold))
                    Text(CurrencyFormatter.formatPercent(holding.gainLossPercent))
                        .font(.system(size: 12, weight: .semibold))
                }
                .foregroundColor(Color.forGainLoss(holding.gainLossPercent))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color.surface)
        .cornerRadius(14)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Color.border, lineWidth: 0.5)
        )
    }

    private func formatShares(_ shares: Double) -> String {
        if shares == shares.rounded() {
            return String(Int(shares))
        }
        return String(format: "%.4f", shares)
    }
}
