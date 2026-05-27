import SwiftUI

struct ContentView: View {
    @State private var selectedTab: Int = 0
    @State private var showScreenshotImport = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            TabView(selection: $selectedTab) {
                HomeView()
                    .tabItem {
                        Label("Home", systemImage: "house.fill")
                    }
                    .tag(0)

                ResearchView()
                    .tabItem {
                        Label("Research", systemImage: "magnifyingglass.circle.fill")
                    }
                    .tag(1)

                OpportunitiesView()
                    .tabItem {
                        Label("Opportunities", systemImage: "star.fill")
                    }
                    .tag(2)

                AnalyzeView()
                    .tabItem {
                        Label("Analyze", systemImage: "chart.bar.xaxis")
                    }
                    .tag(3)

                FeedView()
                    .tabItem {
                        Label("Feed", systemImage: "waveform")
                    }
                    .tag(4)

                WorkspaceView()
                    .tabItem {
                        Label("Workspace", systemImage: "rectangle.3.group")
                    }
                    .tag(5)

                OperatorView()
                    .tabItem {
                        Label("Operator", systemImage: "terminal")
                    }
                    .tag(6)

                SettingsView()
                    .tabItem {
                        Label("Settings", systemImage: "gearshape.fill")
                    }
                    .tag(7)
            }
            .accentColor(Color.accent)

            // Floating camera button
            Button {
                HapticManager.impact(.medium)
                showScreenshotImport = true
            } label: {
                ZStack {
                    Circle()
                        .fill(Color.accent)
                        .frame(width: 56, height: 56)
                        .shadow(color: Color.accent.opacity(0.5), radius: 12, x: 0, y: 4)
                    Image(systemName: "camera.fill")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundColor(.black)
                }
            }
            .padding(.trailing, 20)
            .padding(.bottom, 80)
        }
        .sheet(isPresented: $showScreenshotImport) {
            ScreenshotImportView()
        }
        .onReceive(NotificationCenter.default.publisher(for: .openOpportunitiesTab)) { _ in
            selectedTab = 2
        }
        .onReceive(NotificationCenter.default.publisher(for: .analyzeTickerRequested)) { _ in
            selectedTab = 3
        }
    }
}
