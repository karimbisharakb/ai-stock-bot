import SwiftUI

struct ResearchView: View {
    @StateObject private var vm = ResearchViewModel()

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()
                VStack(spacing: 0) {
                    // Tab picker
                    researchTabBar
                    Divider().background(Color.surfaceSecondary)

                    // Content
                    Group {
                        switch vm.selectedTab {
                        case .chat:     ChatSubView(vm: vm.chatVM)
                        case .compare:  CompareSubView(vm: vm.compareVM)
                        case .trending: TrendingSubView(vm: vm.trendingVM)
                        case .news:     NewsImpactSubView(vm: vm.newsVM)
                        case .library:  LibrarySubView(vm: vm.savedVM)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle("Research")
            .navigationBarTitleDisplayMode(.inline)
        }
        .task { await vm.chatVM.loadPersonas() }
    }

    var researchTabBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(ResearchTab.allCases, id: \.self) { tab in
                    Button {
                        HapticManager.selection()
                        withAnimation(.spring(response: 0.3)) { vm.selectedTab = tab }
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: tab.systemImage)
                                .font(.system(size: 12, weight: .semibold))
                            Text(tab.rawValue)
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(vm.selectedTab == tab ? .black : .textSecondary)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .background(
                            Capsule()
                                .fill(vm.selectedTab == tab ? Color.accent : Color.surfacePrimary)
                        )
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
    }
}

// MARK: - Chat Sub View

struct ChatSubView: View {
    @ObservedObject var vm: ResearchChatViewModel
    @State private var scrollProxy: ScrollViewProxy? = nil

    var body: some View {
        VStack(spacing: 0) {
            // Persona selector
            if !vm.personas.isEmpty {
                personaSelector
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
            }

            // Messages
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        if vm.messages.isEmpty && !vm.isLoadingHistory {
                            personaWelcome
                                .padding(.top, 24)
                        }
                        ForEach(vm.messages) { msg in
                            ChatBubble(message: msg)
                                .id(msg.id)
                        }
                        if vm.isSending {
                            typingIndicator
                                .id("typing")
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                }
                .onChange(of: vm.messages.count) { _ in
                    if let last = vm.messages.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
                .onChange(of: vm.isSending) { sending in
                    if sending { withAnimation { proxy.scrollTo("typing", anchor: .bottom) } }
                }
            }

            if let err = vm.errorMessage {
                Text(err).font(.caption).foregroundColor(.red).padding(.horizontal, 16)
            }

            // Input bar
            inputBar
        }
        .task {
            await vm.loadPersonas()
            await vm.loadHistory()
        }
    }

    var personaSelector: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(vm.personas) { persona in
                    Button {
                        HapticManager.selection()
                        vm.switchPersona(persona.key)
                    } label: {
                        HStack(spacing: 4) {
                            Text(persona.emoji).font(.system(size: 14))
                            Text(persona.name).font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(vm.selectedPersona == persona.key ? .black : .textSecondary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 20)
                                .fill(vm.selectedPersona == persona.key ? Color.accent : Color.surfacePrimary)
                        )
                    }
                }
            }
        }
    }

    var personaWelcome: some View {
        let persona = vm.personas.first(where: { $0.key == vm.selectedPersona })
            ?? PersonaInfo.defaults.first(where: { $0.key == vm.selectedPersona })
        return VStack(spacing: 12) {
            Text(persona?.emoji ?? "🏰").font(.system(size: 48))
            Text(persona?.name ?? "AI Analyst")
                .font(.system(size: 18, weight: .bold)).foregroundColor(.textPrimary)
            Text(persona?.intro ?? "Ask me anything about stocks and markets.")
                .font(.system(size: 14)).foregroundColor(.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
    }

    var typingIndicator: some View {
        HStack(spacing: 6) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(Color.textSecondary)
                    .frame(width: 7, height: 7)
                    .opacity(0.5)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.surfacePrimary)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    var inputBar: some View {
        HStack(spacing: 10) {
            TextField("Ask your analyst...", text: $vm.inputText, axis: .vertical)
                .lineLimit(1...4)
                .font(.system(size: 15))
                .foregroundColor(.textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Color.surfacePrimary)
                .clipShape(RoundedRectangle(cornerRadius: 20))

            Button {
                HapticManager.impact(.medium)
                Task { await vm.send() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundColor(vm.inputText.isEmpty || vm.isSending ? Color.textSecondary : Color.accent)
            }
            .disabled(vm.inputText.isEmpty || vm.isSending)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.background)
    }
}

struct ChatBubble: View {
    let message: ChatMessage
    var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            if !isUser {
                Text(message.personaEmoji ?? "🤖")
                    .font(.system(size: 22))
            }
            Text(message.content)
                .font(.system(size: 15))
                .foregroundColor(isUser ? .black : .textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(isUser ? Color.accent : Color.surfacePrimary)
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .frame(maxWidth: UIScreen.main.bounds.width * 0.75, alignment: isUser ? .trailing : .leading)
            if isUser { Spacer(minLength: 0) }
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
    }
}

// MARK: - Compare Sub View

struct CompareSubView: View {
    @ObservedObject var vm: ResearchCompareViewModel

    private let perspectives = ["ALL", "VALUE", "MOMENTUM", "RISK", "MACRO"]

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Input
                VStack(alignment: .leading, spacing: 8) {
                    Text("Tickers (comma-separated)")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.textSecondary)
                    TextField("e.g. AAPL, MSFT, NVDA", text: $vm.tickerInput)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .font(.system(size: 15))
                        .padding(12)
                        .background(Color.surfacePrimary)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                // Perspective picker
                VStack(alignment: .leading, spacing: 8) {
                    Text("Perspective")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.textSecondary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(perspectives, id: \.self) { p in
                                Button {
                                    HapticManager.selection()
                                    vm.perspective = p
                                } label: {
                                    Text(p)
                                        .font(.system(size: 13, weight: .medium))
                                        .foregroundColor(vm.perspective == p ? .black : .textSecondary)
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 7)
                                        .background(
                                            Capsule().fill(vm.perspective == p ? Color.accent : Color.surfacePrimary)
                                        )
                                }
                            }
                        }
                    }
                }

                Button {
                    HapticManager.impact(.medium)
                    Task { await vm.compare() }
                } label: {
                    HStack {
                        Spacer()
                        if vm.isLoading {
                            ProgressView().tint(.black)
                        } else {
                            Text("Compare").font(.system(size: 15, weight: .bold))
                        }
                        Spacer()
                    }
                    .foregroundColor(.black)
                    .padding(.vertical, 13)
                    .background(Color.accent)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .disabled(vm.isLoading)

                if let err = vm.errorMessage {
                    Text(err).font(.caption).foregroundColor(.red)
                }

                if let result = vm.result {
                    compareTable(result)
                    if !result.verdict.isEmpty {
                        verdictCard(result.verdict)
                    }
                }
            }
            .padding(16)
        }
    }

    func compareTable(_ result: CompareResult) -> some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Metric").font(.caption.bold()).foregroundColor(.textSecondary).frame(maxWidth: .infinity, alignment: .leading)
                ForEach(result.tickers, id: \.self) { ticker in
                    Text(ticker).font(.caption.bold()).foregroundColor(.textPrimary)
                        .frame(width: 72, alignment: .center)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color.surfaceSecondary)

            ForEach(Array(result.rows.enumerated()), id: \.element.id) { idx, row in
                HStack {
                    Text(row.metric)
                        .font(.system(size: 13))
                        .foregroundColor(.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ForEach(result.tickers, id: \.self) { ticker in
                        Text(row.values[ticker] ?? "—")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(.textPrimary)
                            .frame(width: 72, alignment: .center)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(idx % 2 == 0 ? Color.background : Color.surfacePrimary)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.surfaceSecondary, lineWidth: 1))
    }

    func verdictCard(_ verdict: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("AI Verdict", systemImage: "sparkles").font(.system(size: 13, weight: .semibold)).foregroundColor(.accent)
            Text(verdict).font(.system(size: 14)).foregroundColor(.textPrimary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.surfacePrimary)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Trending Sub View

struct TrendingSubView: View {
    @ObservedObject var vm: SocialTrendingViewModel

    var body: some View {
        Group {
            if vm.isLoading && vm.trending.isEmpty {
                ProgressView("Scanning Reddit…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let err = vm.errorMessage, vm.trending.isEmpty {
                emptyState(icon: "exclamationmark.circle", title: "Scan failed", subtitle: err)
            } else if vm.trending.isEmpty {
                emptyState(icon: "flame", title: "No trending tickers", subtitle: "Tap refresh to scan Reddit.")
            } else {
                trendingList
            }
        }
        .task { await vm.load() }
        .refreshable { await vm.refresh() }
    }

    var trendingList: some View {
        ScrollView {
            VStack(spacing: 0) {
                if let scanned = vm.scannedAt {
                    Text("Last scanned: \(scanned.prefix(16))")
                        .font(.caption)
                        .foregroundColor(.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                        .padding(.horizontal, 16)
                        .padding(.top, 8)
                }
                LazyVStack(spacing: 1) {
                    ForEach(vm.trending) { trend in
                        TrendRow(trend: trend)
                    }
                }
                .padding(.top, 8)
            }
        }
    }

    func emptyState(icon: String, title: String, subtitle: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 40)).foregroundColor(.textSecondary)
            Text(title).font(.headline).foregroundColor(.textPrimary)
            Text(subtitle).font(.subheadline).foregroundColor(.textSecondary).multilineTextAlignment(.center).padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct TrendRow: View {
    let trend: SocialTrend

    var sentimentColor: Color {
        switch trend.sentimentLabel.uppercased() {
        case "POSITIVE": return .positive
        case "NEGATIVE": return .negative
        default: return .textSecondary
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(trend.ticker).font(.system(size: 16, weight: .bold)).foregroundColor(.textPrimary)
                if let sub = trend.subreddit, !sub.isEmpty {
                    Text("r/\(sub)").font(.caption).foregroundColor(.textSecondary)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text("\(trend.mentionCount) mentions").font(.caption.bold()).foregroundColor(.textSecondary)
                Text(trend.sentimentLabel.capitalized).font(.caption.bold()).foregroundColor(sentimentColor)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color.surfacePrimary)
        .overlay(Divider().background(Color.surfaceSecondary), alignment: .bottom)
    }
}

// MARK: - News Impact Sub View

struct NewsImpactSubView: View {
    @ObservedObject var vm: NewsImpactViewModel

    var body: some View {
        Group {
            if vm.isLoading && vm.items.isEmpty {
                ProgressView("Loading news impact…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let err = vm.errorMessage, vm.items.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.circle").font(.system(size: 40)).foregroundColor(.textSecondary)
                    Text("Failed to load").font(.headline)
                    Text(err).font(.caption).foregroundColor(.textSecondary).multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if vm.items.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "newspaper").font(.system(size: 40)).foregroundColor(.textSecondary)
                    Text("No news impact yet").font(.headline)
                    Text("News is analyzed automatically every hour during market hours.").font(.subheadline).foregroundColor(.textSecondary).multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(vm.items) { item in NewsImpactCard(item: item) }
                    }
                    .padding(16)
                }
            }
        }
        .task { await vm.load() }
        .refreshable { await vm.refresh() }
    }
}

struct NewsImpactCard: View {
    let item: NewsImpactItem

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(item.headline)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.textPrimary)
                .lineLimit(3)

            if !item.affected.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(item.affected) { aff in
                            AffectedChip(affected: aff)
                        }
                    }
                }
            }

            if !item.impactAnalysis.isEmpty {
                Text(item.impactAnalysis)
                    .font(.system(size: 13))
                    .foregroundColor(.textSecondary)
                    .lineLimit(4)
            }

            if let ts = item.timestamp {
                Text(ts.prefix(16))
                    .font(.caption)
                    .foregroundColor(.textSecondary)
            }
        }
        .padding(14)
        .background(Color.surfacePrimary)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct AffectedChip: View {
    let affected: NewsImpactAffected
    var chipColor: Color {
        switch affected.direction.uppercased() {
        case "POSITIVE": return Color.positive.opacity(0.2)
        case "NEGATIVE": return Color.negative.opacity(0.2)
        default: return Color.surfaceSecondary
        }
    }
    var textColor: Color {
        switch affected.direction.uppercased() {
        case "POSITIVE": return .positive
        case "NEGATIVE": return .negative
        default: return .textSecondary
        }
    }

    var body: some View {
        HStack(spacing: 4) {
            Text(affected.ticker).font(.system(size: 11, weight: .bold))
            Image(systemName: affected.direction == "POSITIVE" ? "arrow.up" : affected.direction == "NEGATIVE" ? "arrow.down" : "minus")
                .font(.system(size: 9, weight: .bold))
        }
        .foregroundColor(textColor)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(chipColor)
        .clipShape(Capsule())
    }
}

// MARK: - Library Sub View

struct LibrarySubView: View {
    @ObservedObject var vm: SavedResearchViewModel
    @State private var expandedId: String? = nil

    var body: some View {
        Group {
            if vm.isLoading && vm.items.isEmpty {
                ProgressView("Loading library…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if vm.items.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "bookmark").font(.system(size: 40)).foregroundColor(.textSecondary)
                    Text("Library is empty").font(.headline)
                    Text("Save AI chat responses here for future reference.").font(.subheadline).foregroundColor(.textSecondary).multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(vm.items) { item in
                            LibraryCard(item: item, isExpanded: expandedId == item.id) {
                                withAnimation(.spring(response: 0.3)) {
                                    expandedId = expandedId == item.id ? nil : item.id
                                }
                            } onDelete: {
                                if let id = item.dbId { Task { await vm.delete(id: id) } }
                            }
                        }
                    }
                    .padding(16)
                }
            }
        }
        .task { await vm.load() }
        .refreshable { await vm.refresh() }
    }
}

struct LibraryCard: View {
    let item: SavedResearchItem
    let isExpanded: Bool
    let onTap: () -> Void
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(item.personaEmoji).font(.system(size: 20))
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title).font(.system(size: 14, weight: .semibold)).foregroundColor(.textPrimary).lineLimit(2)
                    Text(item.personaName).font(.caption).foregroundColor(.textSecondary)
                }
                Spacer()
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.caption.bold())
                    .foregroundColor(.textSecondary)
            }
            .contentShape(Rectangle())
            .onTapGesture(perform: onTap)

            if !item.tickers.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(item.tickers, id: \.self) { ticker in
                            Text(ticker)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(.accent)
                                .padding(.horizontal, 9)
                                .padding(.vertical, 4)
                                .background(Color.accent.opacity(0.15))
                                .clipShape(Capsule())
                        }
                    }
                }
            }

            if isExpanded {
                Text(item.content)
                    .font(.system(size: 13))
                    .foregroundColor(.textSecondary)
                    .transition(.opacity)

                HStack {
                    if let ts = item.createdAt {
                        Text(ts.prefix(10)).font(.caption).foregroundColor(.textSecondary)
                    }
                    Spacer()
                    Button(role: .destructive) { onDelete() } label: {
                        Label("Delete", systemImage: "trash").font(.caption)
                    }
                }
            }
        }
        .padding(14)
        .background(Color.surfacePrimary)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
