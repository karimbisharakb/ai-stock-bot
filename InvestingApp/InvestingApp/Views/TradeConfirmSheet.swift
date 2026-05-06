import SwiftUI

struct TradeConfirmSheet: View {
    @State var trade: ParsedTrade
    let onConfirmed: () -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var isConfirming = false
    @State private var isConfirmed = false
    @State private var errorMessage: String?

    @State private var tickerInput: String = ""
    @State private var sharesInput: String = ""
    @State private var priceInput: String = ""
    @State private var tradeTypeInput: String = "BUY"

    var body: some View {
        ZStack {
            Color.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    // Header
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Image(systemName: "checkmark.seal.fill")
                                .foregroundColor(.positive)
                                .font(.system(size: 20))
                            Text("Trade Parsed")
                                .font(.system(size: 22, weight: .bold))
                                .foregroundColor(.textPrimary)
                        }
                        Text("Review and edit the extracted details before confirming.")
                            .font(.system(size: 13))
                            .foregroundColor(.textSecondary)
                    }

                    // Fields
                    VStack(spacing: 16) {
                        TradeField(label: "Ticker", value: $tickerInput, isCapitalized: true)
                        TradeField(label: "Shares", value: $sharesInput, keyboardType: .decimalPad)
                        TradeField(label: "Price (CAD)", value: $priceInput, keyboardType: .decimalPad)

                        VStack(alignment: .leading, spacing: 6) {
                            Text("Trade Type")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.textSecondary)
                            Picker("", selection: $tradeTypeInput) {
                                Text("BUY").tag("BUY")
                                Text("SELL").tag("SELL")
                            }
                            .pickerStyle(.segmented)
                            .background(Color.surface)
                        }
                    }
                    .padding(16)
                    .background(Color.surface)
                    .cornerRadius(16)
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))

                    // Summary card
                    summaryCard

                    if let error = errorMessage {
                        Text(error)
                            .font(.system(size: 12))
                            .foregroundColor(.negative)
                    }

                    // Buttons
                    HStack(spacing: 12) {
                        Button {
                            HapticManager.impact(.light)
                            dismiss()
                        } label: {
                            Text("Cancel")
                                .font(.system(size: 16, weight: .semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 16)
                                .background(Color.surface)
                                .foregroundColor(.textSecondary)
                                .cornerRadius(14)
                                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.border, lineWidth: 0.5))
                        }

                        Button {
                            HapticManager.impact(.heavy)
                            Task { await confirm() }
                        } label: {
                            Group {
                                if isConfirming {
                                    ProgressView().tint(.black)
                                } else if isConfirmed {
                                    Label("Saved!", systemImage: "checkmark")
                                        .font(.system(size: 16, weight: .semibold))
                                } else {
                                    Text("Confirm \(tradeTypeInput)")
                                        .font(.system(size: 16, weight: .semibold))
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 16)
                            .background(isConfirmed ? Color.positive : Color.positive)
                            .foregroundColor(.black)
                            .cornerRadius(14)
                        }
                        .disabled(isConfirming || isConfirmed)
                    }

                    Spacer().frame(height: 40)
                }
                .padding(20)
            }
        }
        .onAppear {
            tickerInput = trade.ticker
            sharesInput = String(format: "%.4f", trade.shares)
            priceInput = String(format: "%.2f", trade.priceCAD)
            tradeTypeInput = trade.tradeType.uppercased()
        }
    }

    var summaryCard: some View {
        VStack(spacing: 12) {
            HStack {
                Text("Trade Summary")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Text(tradeTypeInput)
                    .font(.system(size: 12, weight: .bold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(tradeTypeInput == "BUY" ? Color.positive.opacity(0.15) : Color.negative.opacity(0.15))
                    .foregroundColor(tradeTypeInput == "BUY" ? .positive : .negative)
                    .cornerRadius(8)
            }

            Divider().background(Color.border)

            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Total Value")
                        .font(.system(size: 11))
                        .foregroundColor(.textSecondary)
                    let total = (Double(sharesInput) ?? 0) * (Double(priceInput) ?? 0)
                    Text(CurrencyFormatter.formatCAD(total))
                        .font(.system(size: 20, weight: .bold))
                        .foregroundColor(.textPrimary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text("Currency")
                        .font(.system(size: 11))
                        .foregroundColor(.textSecondary)
                    Text(trade.currency)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.accent)
                }
            }
        }
        .padding(14)
        .background(Color.surfaceElevated)
        .cornerRadius(14)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.border, lineWidth: 0.5))
    }

    func confirm() async {
        guard let shares = Double(sharesInput),
              let price = Double(priceInput) else {
            errorMessage = "Invalid shares or price"
            return
        }

        isConfirming = true
        errorMessage = nil
        defer { isConfirming = false }

        do {
            try await NetworkManager.shared.confirmTrade(
                ticker: tickerInput.uppercased(),
                shares: shares,
                priceCAD: price,
                type: tradeTypeInput
            )
            withAnimation { isConfirmed = true }
            HapticManager.notification(.success)
            NotificationCenter.default.post(name: .tradeConfirmed, object: nil)
            try? await Task.sleep(nanoseconds: 800_000_000)
            onConfirmed()
        } catch {
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
    }
}

struct TradeField: View {
    let label: String
    @Binding var value: String
    var keyboardType: UIKeyboardType = .default
    var isCapitalized = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.textSecondary)
            TextField(label, text: $value)
                .foregroundColor(.textPrimary)
                .font(.system(size: 15))
                .keyboardType(keyboardType)
                .textInputAutocapitalization(isCapitalized ? .characters : .never)
                .padding(.horizontal, 12)
                .padding(.vertical, 12)
                .background(Color.background)
                .cornerRadius(10)
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.border, lineWidth: 0.5))
        }
    }
}
