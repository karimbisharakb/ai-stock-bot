import SwiftUI

enum Sensitivity: String, CaseIterable {
    case conservative = "Conservative"
    case moderate = "Moderate"
    case aggressive = "Aggressive"
}

struct SettingsView: View {
    @State private var cashAmount = ""
    @State private var editingCash = false
    @State private var sensitivity: Sensitivity = .moderate
    @State private var notificationsEnabled = true
    @State private var isSavingCash = false
    @State private var cashSaved = false
    @State private var errorMessage: String?

    var storedCash: Double {
        Double(UserDefaults.standard.string(forKey: "available_cash") ?? "0") ?? 0
    }

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 20) {
                        // Account section
                        settingsSection(title: "Account") {
                            cashRow
                            Divider().background(Color.border).padding(.leading, 56)
                            whatsAppRow
                        }

                        // Signals section
                        settingsSection(title: "Signal Settings") {
                            sensitivityRow
                            Divider().background(Color.border).padding(.leading, 56)
                            notificationsRow
                        }

                        // Info section
                        settingsSection(title: "About") {
                            infoRow(icon: "server.rack", label: "Backend", value: "Railway Production")
                            Divider().background(Color.border).padding(.leading, 56)
                            infoRow(icon: "iphone", label: "App Version", value: "1.0.0")
                            Divider().background(Color.border).padding(.leading, 56)
                            infoRow(icon: "building.columns.fill", label: "Account", value: "TFSA")
                        }

                        if let error = errorMessage {
                            Text(error)
                                .font(.system(size: 12))
                                .foregroundColor(.negative)
                                .padding(.horizontal, 20)
                        }

                        // Sandbox info
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Image(systemName: "bubble.left.fill")
                                    .foregroundColor(.accent)
                                    .font(.system(size: 14))
                                Text("WhatsApp Sandbox")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(.textPrimary)
                            }
                            Text("Sandbox expires every 72 hours. To reconnect, send the join code to the sandbox number.")
                                .font(.system(size: 12))
                                .foregroundColor(.textSecondary)
                                .lineSpacing(4)

                            VStack(alignment: .leading, spacing: 6) {
                                infoChip(label: "Number", value: "+1 415 523 8886")
                                infoChip(label: "Join Code", value: "join independent-dangerous")
                            }
                        }
                        .padding(16)
                        .background(Color.surface)
                        .cornerRadius(16)
                        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.accent.opacity(0.2), lineWidth: 0.5))
                        .padding(.horizontal, 20)

                        Spacer().frame(height: 100)
                    }
                    .padding(.top, 8)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .onAppear {
            cashAmount = String(format: "%.2f", storedCash)
        }
    }

    // MARK: - Rows

    var cashRow: some View {
        HStack(spacing: 14) {
            settingsIcon("dollarsign.circle.fill", color: .positive)
            VStack(alignment: .leading, spacing: 2) {
                Text("Available Cash")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                if !editingCash {
                    Text("CA\(CurrencyFormatter.formatCAD(Double(cashAmount) ?? 0))")
                        .font(.system(size: 12))
                        .foregroundColor(.textSecondary)
                }
            }
            Spacer()
            if editingCash {
                HStack(spacing: 8) {
                    HStack {
                        Text("$")
                            .foregroundColor(.textSecondary)
                            .font(.system(size: 14))
                        TextField("0.00", text: $cashAmount)
                            .keyboardType(.decimalPad)
                            .foregroundColor(.textPrimary)
                            .frame(width: 80)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.surfaceElevated)
                    .cornerRadius(8)

                    Button {
                        Task { await saveCash() }
                    } label: {
                        if isSavingCash {
                            ProgressView().tint(.black).scaleEffect(0.8)
                        } else if cashSaved {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundColor(.black)
                        } else {
                            Text("Save")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.black)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(cashSaved ? Color.positive : Color.accent)
                    .cornerRadius(8)
                }
            } else {
                Button {
                    editingCash = true
                    cashSaved = false
                } label: {
                    Image(systemName: "pencil.circle.fill")
                        .foregroundColor(.accent)
                        .font(.system(size: 22))
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    var whatsAppRow: some View {
        HStack(spacing: 14) {
            settingsIcon("message.fill", color: .positive)
            VStack(alignment: .leading, spacing: 2) {
                Text("WhatsApp Number")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Text(UserDefaults.standard.string(forKey: "whatsapp_number") ?? "Not configured")
                    .font(.system(size: 12))
                    .foregroundColor(.textSecondary)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    var sensitivityRow: some View {
        HStack(spacing: 14) {
            settingsIcon("slider.horizontal.3", color: .warning)
            Text("Signal Sensitivity")
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(.textPrimary)
            Spacer()
            Picker("", selection: $sensitivity) {
                ForEach(Sensitivity.allCases, id: \.self) { s in
                    Text(s.rawValue).tag(s)
                }
            }
            .pickerStyle(.menu)
            .tint(.accent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .onChange(of: sensitivity) { _ in
            HapticManager.selection()
            UserDefaults.standard.set(sensitivity.rawValue, forKey: "signal_sensitivity")
        }
    }

    var notificationsRow: some View {
        HStack(spacing: 14) {
            settingsIcon("bell.fill", color: .accent)
            Text("Push Notifications")
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(.textPrimary)
            Spacer()
            Toggle("", isOn: $notificationsEnabled)
                .tint(Color.accent)
                .onChange(of: notificationsEnabled) { enabled in
                    HapticManager.impact(.light)
                    if enabled {
                        NotificationManager.shared.requestAuthorization()
                    }
                    UserDefaults.standard.set(enabled, forKey: "notifications_enabled")
                }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    func infoRow(icon: String, label: String, value: String) -> some View {
        HStack(spacing: 14) {
            settingsIcon(icon, color: .textSecondary)
            Text(label)
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(.textPrimary)
            Spacer()
            Text(value)
                .font(.system(size: 13))
                .foregroundColor(.textSecondary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    // MARK: - Helpers

    func settingsSection<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.textSecondary)
                .padding(.horizontal, 20)
                .padding(.bottom, 6)

            VStack(spacing: 0) {
                content()
            }
            .background(Color.surface)
            .cornerRadius(16)
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))
            .padding(.horizontal, 20)
        }
    }

    func settingsIcon(_ name: String, color: Color) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(color.opacity(0.15))
                .frame(width: 32, height: 32)
            Image(systemName: name)
                .foregroundColor(color)
                .font(.system(size: 14))
        }
    }

    func infoChip(label: String, value: String) -> some View {
        HStack(spacing: 6) {
            Text(label + ":")
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(.textSecondary)
            Text(value)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.accent)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.background)
        .cornerRadius(8)
    }

    func saveCash() async {
        guard let amount = Double(cashAmount) else { return }
        isSavingCash = true
        defer { isSavingCash = false }
        do {
            try await NetworkManager.shared.updateCash(amount: amount)
            UserDefaults.standard.set(cashAmount, forKey: "available_cash")
            withAnimation { cashSaved = true }
            editingCash = false
            HapticManager.notification(.success)
        } catch {
            errorMessage = error.localizedDescription
            HapticManager.notification(.error)
        }
    }
}
