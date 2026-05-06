import SwiftUI

extension Color {
    static let background = Color(hex: "#080c12")
    static let surface = Color(hex: "#0d1520")
    static let surfaceElevated = Color(hex: "#111d2e")
    static let accent = Color(hex: "#00d4ff")
    static let positive = Color(hex: "#00e676")
    static let negative = Color(hex: "#ff3d57")
    static let warning = Color(hex: "#ffab00")
    static let textPrimary = Color.white
    static let textSecondary = Color(white: 0.55)
    static let border = Color(white: 0.15)

    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 6:
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8:
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            (a, r, g, b) = (255, 0, 0, 0)
        }
        self.init(.sRGB, red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255, opacity: Double(a) / 255)
    }

    static func forGainLoss(_ value: Double) -> Color {
        value >= 0 ? .positive : .negative
    }

    static func forRating(_ rating: MetricRating) -> Color {
        switch rating {
        case .good: return .positive
        case .neutral: return .warning
        case .poor: return .negative
        }
    }
}

extension View {
    func cardStyle() -> some View {
        self
            .background(Color.surface)
            .cornerRadius(16)
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(Color.border, lineWidth: 0.5)
            )
    }

    func shimmer(isActive: Bool) -> some View {
        self.modifier(ShimmerModifier(isActive: isActive))
    }
}

struct ShimmerModifier: ViewModifier {
    let isActive: Bool
    @State private var phase: CGFloat = -1

    func body(content: Content) -> some View {
        if isActive {
            content
                .overlay(
                    GeometryReader { geo in
                        LinearGradient(
                            gradient: Gradient(stops: [
                                .init(color: Color.clear, location: 0),
                                .init(color: Color.white.opacity(0.08), location: 0.3),
                                .init(color: Color.white.opacity(0.15), location: 0.5),
                                .init(color: Color.white.opacity(0.08), location: 0.7),
                                .init(color: Color.clear, location: 1)
                            ]),
                            startPoint: .init(x: phase, y: 0),
                            endPoint: .init(x: phase + 1, y: 0)
                        )
                        .frame(width: geo.size.width * 2)
                        .offset(x: geo.size.width * phase)
                    }
                )
                .clipped()
                .onAppear {
                    withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                        phase = 1
                    }
                }
        } else {
            content
        }
    }
}

struct SkeletonRow: View {
    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.surface)
                .frame(width: 48, height: 48)
                .shimmer(isActive: true)
            VStack(alignment: .leading, spacing: 6) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.surface)
                    .frame(height: 14)
                    .shimmer(isActive: true)
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.surface)
                    .frame(width: 100, height: 10)
                    .shimmer(isActive: true)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 6) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.surface)
                    .frame(width: 70, height: 14)
                    .shimmer(isActive: true)
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.surface)
                    .frame(width: 50, height: 10)
                    .shimmer(isActive: true)
            }
        }
        .padding()
        .cardStyle()
    }
}
