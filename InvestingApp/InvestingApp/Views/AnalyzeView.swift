import SwiftUI

struct AnalyzeView: View {
    @StateObject private var vm = AnalyzeViewModel()
    @FocusState private var searchFocused: Bool

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    // Search bar
                    HStack(spacing: 12) {
                        HStack(spacing: 10) {
                            Image(systemName: "magnifyingglass")
                                .foregroundColor(.textSecondary)
                            TextField("Ticker (e.g. NVDA, SHOP.TO)", text: $vm.query)
                                .foregroundColor(.textPrimary)
                                .font(.system(size: 16))
                                .autocorrectionDisabled()
                                .textInputAutocapitalization(.characters)
                                .focused($searchFocused)
                                .submitLabel(.search)
                                .onSubmit {
                                    Task { await vm.analyze() }
                                }
                            if !vm.query.isEmpty {
                                Button {
                                    vm.query = ""
                                    vm.result = nil
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundColor(.textSecondary)
                                }
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 13)
                        .background(Color.surface)
                        .cornerRadius(14)
                        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.border, lineWidth: 0.5))

                        Button {
                            searchFocused = false
                            Task { await vm.analyze() }
                            HapticManager.impact(.medium)
                        } label: {
                            Text("Analyze")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(.black)
                                .padding(.horizontal, 16)
                                .padding(.vertical, 13)
                                .background(vm.query.isEmpty ? Color.accent.opacity(0.4) : Color.accent)
                                .cornerRadius(14)
                        }
                        .disabled(vm.query.isEmpty || vm.isLoading)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)

                    if vm.isLoading {
                        loadingView
                    } else if let result = vm.result {
                        AnalysisResultView(result: result)
                    } else if let error = vm.errorMessage {
                        errorView(error)
                    } else {
                        placeholderView
                    }
                }
            }
            .navigationTitle("Analyze")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .onReceive(NotificationCenter.default.publisher(for: .analyzeTickerRequested)) { notification in
            if let ticker = notification.userInfo?["ticker"] as? String {
                vm.query = ticker
                Task { await vm.analyze() }
            }
        }
    }

    var loadingView: some View {
        VStack(spacing: 32) {
            Spacer()

            ZStack {
                Circle()
                    .stroke(Color.accent.opacity(0.1), lineWidth: 3)
                    .frame(width: 100, height: 100)
                Circle()
                    .trim(from: 0, to: 0.3)
                    .stroke(Color.accent, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .frame(width: 100, height: 100)
                    .rotationEffect(.degrees(-90))
                    .modifier(SpinModifier())
                Image(systemName: "chart.bar.xaxis")
                    .font(.system(size: 28))
                    .foregroundColor(.accent)
            }

            VStack(spacing: 8) {
                Text(vm.currentStep.rawValue)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(.textPrimary)
                    .animation(.easeInOut, value: vm.currentStep)

                HStack(spacing: 8) {
                    ForEach(0..<3) { i in
                        Circle()
                            .fill(i == vm.stepIndex ? Color.accent : Color.accent.opacity(0.3))
                            .frame(width: 6, height: 6)
                            .scaleEffect(i == vm.stepIndex ? 1.3 : 1.0)
                            .animation(.spring(), value: vm.stepIndex)
                    }
                }
            }

            Spacer()
        }
    }

    var placeholderView: some View {
        VStack(spacing: 20) {
            Spacer()

            Image(systemName: "chart.xyaxis.line")
                .font(.system(size: 56))
                .foregroundColor(.textSecondary.opacity(0.3))

            VStack(spacing: 8) {
                Text("AI Stock Analysis")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Text("Enter any ticker for a deep AI analysis\nof fundamentals, technicals, and sentiment.")
                    .font(.system(size: 14))
                    .foregroundColor(.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(4)
            }

            // Quick tickers
            VStack(alignment: .leading, spacing: 8) {
                Text("Quick pick")
                    .font(.system(size: 12))
                    .foregroundColor(.textSecondary)
                    .padding(.horizontal, 20)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(["NVDA", "SHOP.TO", "AAPL", "MSFT", "VFV.TO", "PLTR", "AMD", "META"], id: \.self) { ticker in
                            Button {
                                vm.query = ticker
                                Task { await vm.analyze() }
                                HapticManager.selection()
                            } label: {
                                Text(ticker)
                                    .font(.system(size: 13, weight: .semibold))
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 8)
                                    .background(Color.surface)
                                    .foregroundColor(.accent)
                                    .cornerRadius(10)
                                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.accent.opacity(0.3), lineWidth: 0.5))
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                }
            }

            Spacer()
        }
    }

    func errorView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundColor(.warning)
            Text("Analysis Failed")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(.textPrimary)
            Text(msg)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
            Spacer()
        }
    }
}

struct SpinModifier: ViewModifier {
    @State private var isSpinning = false

    func body(content: Content) -> some View {
        content
            .rotationEffect(.degrees(isSpinning ? 360 : 0))
            .onAppear {
                withAnimation(.linear(duration: 1.0).repeatForever(autoreverses: false)) {
                    isSpinning = true
                }
            }
    }
}

struct AnalysisResultView: View {
    let result: AnalysisResult
    @State private var shareImage: UIImage?
    @State private var showShareSheet = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Ticker header
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(result.ticker)
                            .font(.system(size: 28, weight: .bold))
                            .foregroundColor(.textPrimary)
                        Text(result.verdict)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.accent)
                    }
                    Spacer()
                    Button {
                        HapticManager.impact(.light)
                        shareAnalysis()
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                            .foregroundColor(.accent)
                            .font(.system(size: 18))
                    }
                }

                // Arc meters
                HStack(spacing: 0) {
                    ArcMeterView(label: "Overall", value: result.overallScore, color: scoreColor(result.overallScore))
                    Spacer()
                    ArcMeterView(label: "Risk", value: result.riskScore, color: .warning)
                    Spacer()
                    ArcMeterView(label: "Growth", value: result.growthScore, color: .positive)
                }
                .padding(20)
                .background(Color.surface)
                .cornerRadius(16)
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))

                // KPI strip
                HStack(spacing: 8) {
                    KPICell(label: "Revenue", value: result.revenue)
                    KPICell(label: "Growth", value: result.revenueGrowth)
                    KPICell(label: "EPS", value: result.eps)
                    KPICell(label: "P/E", value: result.peRatio)
                }

                // Metric grid
                let columns = [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())]
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(result.metrics) { metric in
                        MetricCardView(metric: metric)
                    }
                }

                // Business model
                SectionCard(title: "Business Model", icon: "building.2.fill", iconColor: .accent) {
                    Text(result.businessModel)
                        .font(.system(size: 13))
                        .foregroundColor(.textSecondary)
                        .lineSpacing(4)
                }

                // Moat
                SectionCard(title: "Competitive Moat", icon: "shield.fill", iconColor: .positive) {
                    Text(result.moat)
                        .font(.system(size: 13))
                        .foregroundColor(.textSecondary)
                        .lineSpacing(4)
                }

                // Catalysts
                if !result.catalysts.isEmpty {
                    SectionCard(title: "Catalysts", icon: "bolt.fill", iconColor: .warning) {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(result.catalysts, id: \.self) { catalyst in
                                HStack(alignment: .top, spacing: 8) {
                                    Image(systemName: "plus.circle.fill")
                                        .foregroundColor(.positive.opacity(0.8))
                                        .font(.system(size: 12))
                                        .padding(.top, 1)
                                    Text(catalyst)
                                        .font(.system(size: 13))
                                        .foregroundColor(.textSecondary)
                                }
                            }
                        }
                    }
                }

                // Bull / Bear
                HStack(alignment: .top, spacing: 12) {
                    SectionCard(title: "Bull Case", icon: "arrow.up.circle.fill", iconColor: .positive) {
                        Text(result.bullCase)
                            .font(.system(size: 12))
                            .foregroundColor(.textSecondary)
                            .lineSpacing(4)
                    }
                    SectionCard(title: "Bear Case", icon: "arrow.down.circle.fill", iconColor: .negative) {
                        Text(result.bearCase)
                            .font(.system(size: 12))
                            .foregroundColor(.textSecondary)
                            .lineSpacing(4)
                    }
                }

                // Claude reasoning
                SectionCard(title: "Claude's Verdict", icon: "brain.fill", iconColor: .accent) {
                    Text(result.claudeReasoning)
                        .font(.system(size: 13))
                        .foregroundColor(.textSecondary)
                        .lineSpacing(5)
                }

                Spacer().frame(height: 100)
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
        }
        .sheet(isPresented: $showShareSheet) {
            if let img = shareImage {
                ShareSheet(items: [img])
            }
        }
    }

    func scoreColor(_ score: Int) -> Color {
        switch score {
        case 70...: return .positive
        case 40..<70: return .warning
        default: return .negative
        }
    }

    func shareAnalysis() {
        // Render the analysis as an image
        let renderer = ImageRenderer(content:
            AnalysisSummaryCard(result: result)
                .background(Color.background)
                .frame(width: 360)
        )
        renderer.scale = 3.0
        if let img = renderer.uiImage {
            shareImage = img
            showShareSheet = true
        }
    }
}

struct KPICell: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 4) {
            Text(label)
                .font(.system(size: 10))
                .foregroundColor(.textSecondary)
            Text(value)
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(.textPrimary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color.surface)
        .cornerRadius(10)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.border, lineWidth: 0.5))
    }
}

struct AnalysisSummaryCard: View {
    let result: AnalysisResult

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(result.ticker)
                    .font(.system(size: 22, weight: .bold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Text("Investing Assistant")
                    .font(.system(size: 10))
                    .foregroundColor(.textSecondary)
            }

            HStack(spacing: 20) {
                ScoreBadge(label: "Overall", value: result.overallScore)
                ScoreBadge(label: "Growth", value: result.growthScore)
                ScoreBadge(label: "Risk", value: result.riskScore)
            }

            Text(result.verdict)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
                .lineLimit(3)
        }
        .padding(16)
    }
}

struct ScoreBadge: View {
    let label: String
    let value: Int

    var body: some View {
        VStack(spacing: 2) {
            Text("\(value)")
                .font(.system(size: 20, weight: .bold))
                .foregroundColor(.accent)
            Text(label)
                .font(.system(size: 10))
                .foregroundColor(.textSecondary)
        }
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
