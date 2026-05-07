import SwiftUI

struct WatchlistView: View {
    @StateObject private var vm = WatchlistViewModel()
    @State private var showAddSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header row
            HStack {
                Text("Price Alerts")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(.textPrimary)
                Spacer()
                Button {
                    HapticManager.selection()
                    showAddSheet = true
                } label: {
                    Image(systemName: "plus.circle.fill")
                        .foregroundColor(.accent)
                        .font(.system(size: 22))
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 12)

            if vm.isLoading && vm.alerts.isEmpty {
                HStack { Spacer(); ProgressView().tint(.accent); Spacer() }
                    .padding(.top, 20)
            } else if vm.alerts.isEmpty {
                emptyState
            } else {
                VStack(spacing: 8) {
                    ForEach(vm.alerts) { alert in
                        WatchlistAlertRow(alert: alert) {
                            Task { await vm.deleteAlert(id: alert.id) }
                        }
                        .padding(.horizontal, 20)
                    }
                }
            }

            if let err = vm.errorMessage {
                Text(err)
                    .font(.system(size: 12))
                    .foregroundColor(.negative)
                    .padding(.horizontal, 20)
                    .padding(.top, 8)
            }
        }
        .task { await vm.load() }
        .sheet(isPresented: $showAddSheet) {
            AddWatchlistAlertSheet(vm: vm, isPresented: $showAddSheet)
        }
    }

    var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "bell.slash")
                .font(.system(size: 32))
                .foregroundColor(.textSecondary)
            Text("No price alerts set")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(.textSecondary)
            Text("Tap + to add an alert for any ticker")
                .font(.system(size: 12))
                .foregroundColor(.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }
}

struct WatchlistAlertRow: View {
    let alert: WatchlistAlert
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Status indicator
            Circle()
                .fill(alert.isTriggered ? Color.positive : Color.accent)
                .frame(width: 8, height: 8)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(alert.ticker)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.textPrimary)
                    Text("\(alert.directionLabel) \(CurrencyFormatter.formatCAD(alert.alertPrice))")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(alert.isTriggered ? .positive : .accent)
                }
                if let note = alert.note, !note.isEmpty {
                    Text(note)
                        .font(.system(size: 11))
                        .foregroundColor(.textSecondary)
                        .lineLimit(1)
                }
                if alert.isTriggered {
                    Text("Triggered")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.positive)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.positive.opacity(0.15))
                        .cornerRadius(4)
                }
            }

            Spacer()

            Button {
                onDelete()
            } label: {
                Image(systemName: "trash")
                    .font(.system(size: 14))
                    .foregroundColor(.negative.opacity(0.7))
            }
        }
        .padding(14)
        .background(Color.surface)
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(alert.isTriggered ? Color.positive.opacity(0.3) : Color.border, lineWidth: 0.5)
        )
    }
}

struct AddWatchlistAlertSheet: View {
    @ObservedObject var vm: WatchlistViewModel
    @Binding var isPresented: Bool

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()
                ScrollView {
                    VStack(spacing: 20) {
                        formSection
                        if let err = vm.errorMessage {
                            Text(err)
                                .font(.system(size: 12))
                                .foregroundColor(.negative)
                                .padding(.horizontal, 20)
                        }
                        addButton
                    }
                    .padding(.top, 16)
                }
            }
            .navigationTitle("Add Price Alert")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Cancel") { isPresented = false }
                        .foregroundColor(.accent)
                }
            }
        }
    }

    var formSection: some View {
        VStack(spacing: 0) {
            // Ticker
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.accent.opacity(0.15))
                        .frame(width: 32, height: 32)
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.accent)
                        .font(.system(size: 14))
                }
                Text("Ticker")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Spacer()
                TextField("AAPL", text: $vm.newTicker)
                    .multilineTextAlignment(.trailing)
                    .foregroundColor(.textPrimary)
                    .autocapitalization(.allCharacters)
                    .frame(width: 80)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            Divider().background(Color.border).padding(.leading, 56)

            // Alert price
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.positive.opacity(0.15))
                        .frame(width: 32, height: 32)
                    Image(systemName: "dollarsign")
                        .foregroundColor(.positive)
                        .font(.system(size: 14))
                }
                Text("Alert Price")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Spacer()
                TextField("0.00", text: $vm.newAlertPrice)
                    .multilineTextAlignment(.trailing)
                    .foregroundColor(.textPrimary)
                    .keyboardType(.decimalPad)
                    .frame(width: 80)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            Divider().background(Color.border).padding(.leading, 56)

            // Direction picker
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.warning.opacity(0.15))
                        .frame(width: 32, height: 32)
                    Image(systemName: "arrow.up.arrow.down")
                        .foregroundColor(.warning)
                        .font(.system(size: 14))
                }
                Text("Trigger When")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Spacer()
                Picker("", selection: $vm.newDirection) {
                    Text("Price ≥").tag("above")
                    Text("Price ≤").tag("below")
                }
                .pickerStyle(.menu)
                .tint(.accent)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            Divider().background(Color.border).padding(.leading, 56)

            // Note
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.textSecondary.opacity(0.15))
                        .frame(width: 32, height: 32)
                    Image(systemName: "note.text")
                        .foregroundColor(.textSecondary)
                        .font(.system(size: 14))
                }
                Text("Note")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Spacer()
                TextField("Optional", text: $vm.newNote)
                    .multilineTextAlignment(.trailing)
                    .foregroundColor(.textPrimary)
                    .frame(width: 140)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .background(Color.surface)
        .cornerRadius(16)
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))
        .padding(.horizontal, 20)
    }

    var addButton: some View {
        Button {
            Task {
                await vm.addAlert()
                if vm.errorMessage == nil {
                    isPresented = false
                }
            }
        } label: {
            HStack {
                if vm.isSaving {
                    ProgressView().tint(.black)
                } else {
                    Image(systemName: "bell.badge.fill")
                    Text("Set Alert")
                        .font(.system(size: 16, weight: .semibold))
                }
            }
            .foregroundColor(.black)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(Color.accent)
            .cornerRadius(14)
        }
        .padding(.horizontal, 20)
        .disabled(vm.isSaving)
    }
}
