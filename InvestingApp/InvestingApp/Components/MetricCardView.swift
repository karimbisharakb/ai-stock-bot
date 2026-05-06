import SwiftUI

struct MetricCardView: View {
    let metric: AnalysisMetric

    var ratingColor: Color {
        Color.forRating(metric.rating)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(metric.label)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.textSecondary)
                .lineLimit(1)
            Text(metric.value)
                .font(.system(size: 14, weight: .bold))
                .foregroundColor(ratingColor)
                .lineLimit(1)
            Circle()
                .fill(ratingColor)
                .frame(width: 6, height: 6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(ratingColor.opacity(0.08))
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(ratingColor.opacity(0.25), lineWidth: 0.5)
        )
    }
}
