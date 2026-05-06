import SwiftUI

struct ConfidenceRingView: View {
    let confidence: Int
    @State private var animatedValue: Double = 0

    var ringColor: Color {
        switch confidence {
        case 80...: return .positive
        case 60..<80: return .warning
        default: return .negative
        }
    }

    var body: some View {
        ZStack {
            Circle()
                .stroke(ringColor.opacity(0.2), lineWidth: 5)
                .frame(width: 52, height: 52)

            Circle()
                .trim(from: 0, to: animatedValue / 100)
                .stroke(ringColor, style: StrokeStyle(lineWidth: 5, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .frame(width: 52, height: 52)

            Text("\(confidence)%")
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(ringColor)
        }
        .onAppear {
            withAnimation(.spring(response: 1.0, dampingFraction: 0.8).delay(0.2)) {
                animatedValue = Double(confidence)
            }
        }
    }
}
