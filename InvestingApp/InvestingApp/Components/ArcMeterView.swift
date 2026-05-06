import SwiftUI

struct ArcMeterView: View {
    let label: String
    let value: Int
    let color: Color
    @State private var animatedValue: Double = 0

    var body: some View {
        VStack(spacing: 8) {
            ZStack {
                // Track
                Circle()
                    .trim(from: 0, to: 0.75)
                    .stroke(Color.border, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .rotationEffect(.degrees(135))
                    .frame(width: 88, height: 88)

                // Fill
                Circle()
                    .trim(from: 0, to: min(0.75 * animatedValue / 100, 0.75))
                    .stroke(
                        AngularGradient(
                            gradient: Gradient(colors: [color.opacity(0.6), color]),
                            center: .center,
                            startAngle: .degrees(135),
                            endAngle: .degrees(135 + 270 * animatedValue / 100)
                        ),
                        style: StrokeStyle(lineWidth: 8, lineCap: .round)
                    )
                    .rotationEffect(.degrees(135))
                    .frame(width: 88, height: 88)

                // Value
                VStack(spacing: 2) {
                    Text("\(value)")
                        .font(.system(size: 26, weight: .bold, design: .rounded))
                        .foregroundColor(.textPrimary)
                    Text("/100")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.textSecondary)
                }
            }

            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.textSecondary)
        }
        .onAppear {
            withAnimation(.spring(response: 1.2, dampingFraction: 0.8).delay(0.1)) {
                animatedValue = Double(value)
            }
        }
        .onChange(of: value) { newVal in
            withAnimation(.spring(response: 1.0, dampingFraction: 0.8)) {
                animatedValue = Double(newVal)
            }
        }
    }
}

#Preview {
    HStack(spacing: 24) {
        ArcMeterView(label: "Overall", value: 78, color: .positive)
        ArcMeterView(label: "Risk", value: 45, color: .warning)
        ArcMeterView(label: "Growth", value: 82, color: .accent)
    }
    .padding()
    .background(Color.background)
}
