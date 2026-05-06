import SwiftUI

struct SparklineView: View {
    let points: [Double]
    let color: Color
    @State private var trimEnd: CGFloat = 0

    private var normalized: [Double] {
        guard let min = points.min(), let max = points.max(), max != min else {
            return points.map { _ in 0.5 }
        }
        return points.map { ($0 - min) / (max - min) }
    }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                // Gradient fill
                SparklinePath(points: normalized, size: geo.size)
                    .fill(
                        LinearGradient(
                            gradient: Gradient(colors: [color.opacity(0.25), color.opacity(0.0)]),
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )

                // Line
                SparklinePath(points: normalized, size: geo.size, fillPath: false)
                    .trim(from: 0, to: trimEnd)
                    .stroke(color, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2)) {
                trimEnd = 1
            }
        }
    }
}

struct SparklinePath: Shape {
    let points: [Double]
    let size: CGSize
    var fillPath = true

    func path(in rect: CGRect) -> Path {
        guard points.count > 1 else { return Path() }
        let step = rect.width / CGFloat(points.count - 1)
        var path = Path()

        if fillPath {
            path.move(to: CGPoint(x: 0, y: rect.height))
        }

        for (i, point) in points.enumerated() {
            let x = CGFloat(i) * step
            let y = rect.height - CGFloat(point) * rect.height

            if i == 0 {
                if fillPath {
                    path.addLine(to: CGPoint(x: x, y: y))
                } else {
                    path.move(to: CGPoint(x: x, y: y))
                }
            } else {
                let prevX = CGFloat(i - 1) * step
                let prevY = rect.height - CGFloat(points[i - 1]) * rect.height
                let cp1 = CGPoint(x: prevX + step / 2, y: prevY)
                let cp2 = CGPoint(x: x - step / 2, y: y)
                path.addCurve(to: CGPoint(x: x, y: y), control1: cp1, control2: cp2)
            }
        }

        if fillPath {
            path.addLine(to: CGPoint(x: rect.width, y: rect.height))
            path.closeSubpath()
        }

        return path
    }
}
