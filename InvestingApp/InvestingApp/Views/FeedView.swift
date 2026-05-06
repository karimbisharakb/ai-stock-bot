import SwiftUI

struct FeedView: View {
    @StateObject private var vm = FeedViewModel()
    @State private var expandedSignalID: String?

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    filterBar

                    if vm.isLoading && vm.signals.isEmpty {
                        skeletonView
                    } else if vm.filteredSignals.isEmpty {
                        emptyView
                    } else {
                        signalList
                    }
                }
            }
            .navigationTitle("Signal Feed")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    if vm.isLoading {
                        ProgressView()
                            .tint(.accent)
                            .scaleEffect(0.8)
                    } else {
                        Button {
                            Task { await vm.refresh() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                                .foregroundColor(.accent)
                        }
                    }
                }
            }
        }
        .task { await vm.refresh() }
    }

    var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(FeedFilter.allCases, id: \.self) { filter in
                    FilterPill(title: filter.rawValue, isSelected: vm.filter == filter) {
                        HapticManager.selection()
                        withAnimation(.spring(response: 0.3)) {
                            vm.filter = filter
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
        }
    }

    var signalList: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                if let error = vm.errorMessage {
                    errorBanner(error).padding(.horizontal, 20)
                }

                ForEach(vm.filteredSignals) { signal in
                    SignalRowView(
                        signal: signal,
                        isExpanded: expandedSignalID == signal.id
                    )
                    .padding(.horizontal, 20)
                    .onTapGesture {
                        HapticManager.selection()
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                            expandedSignalID = expandedSignalID == signal.id ? nil : signal.id
                        }
                    }
                }

                Spacer().frame(height: 100)
            }
            .padding(.top, 4)
        }
        .refreshable { await vm.refresh() }
    }

    var skeletonView: some View {
        ScrollView {
            VStack(spacing: 10) {
                ForEach(0..<5, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 14)
                        .fill(Color.surface)
                        .frame(height: 74)
                        .shimmer(isActive: true)
                        .padding(.horizontal, 20)
                }
            }
            .padding(.top, 8)
        }
    }

    var emptyView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "waveform.slash")
                .font(.system(size: 44))
                .foregroundColor(.textSecondary)
            Text("No signals match")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(.textPrimary)
            Text("Try a different filter or pull to refresh")
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
            Spacer()
        }
    }

    func errorBanner(_ msg: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.warning)
            Text(msg)
                .font(.system(size: 12))
                .foregroundColor(.textSecondary)
                .lineLimit(2)
        }
        .padding(12)
        .background(Color.warning.opacity(0.08))
        .cornerRadius(10)
    }
}

struct FilterPill: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: isSelected ? .semibold : .regular))
                .foregroundColor(isSelected ? .black : .textSecondary)
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(isSelected ? Color.accent : Color.surface)
                .cornerRadius(20)
                .overlay(
                    RoundedRectangle(cornerRadius: 20)
                        .stroke(isSelected ? Color.clear : Color.border, lineWidth: 0.5)
                )
        }
    }
}
