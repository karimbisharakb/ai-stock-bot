import SwiftUI
import PhotosUI

struct ScreenshotImportView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selectedItem: PhotosPickerItem?
    @State private var selectedImage: UIImage?
    @State private var showCamera = false
    @State private var isParsing = false
    @State private var parsedTrade: ParsedTrade?
    @State private var errorMessage: String?
    @State private var showActionSheet = false

    var body: some View {
        NavigationView {
            ZStack {
                Color.background.ignoresSafeArea()

                if isParsing {
                    parsingView
                } else if let trade = parsedTrade {
                    TradeConfirmSheet(trade: trade) {
                        dismiss()
                    }
                } else {
                    importPickerView
                }
            }
            .navigationTitle("Import Screenshot")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Cancel") { dismiss() }
                        .foregroundColor(.accent)
                }
            }
            .toolbarBackground(Color.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .sheet(isPresented: $showCamera) {
            CameraView { image in
                if let img = image {
                    selectedImage = img
                    Task { await parseImage(img) }
                }
            }
        }
        .onChange(of: selectedItem) { item in
            Task {
                if let data = try? await item?.loadTransferable(type: Data.self),
                   let img = UIImage(data: data) {
                    selectedImage = img
                    await parseImage(img)
                }
            }
        }
    }

    // MARK: - Views

    var importPickerView: some View {
        VStack(spacing: 32) {
            Spacer()

            VStack(spacing: 8) {
                Image(systemName: "camera.viewfinder")
                    .font(.system(size: 64))
                    .foregroundColor(.accent)
                Text("Import Wealthsimple Screenshot")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundColor(.textPrimary)
                Text("Take a photo or choose from your library.\nThe AI will read your trade details.")
                    .font(.system(size: 14))
                    .foregroundColor(.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(4)
            }
            .padding(.horizontal, 40)

            if let error = errorMessage {
                HStack {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundColor(.negative)
                    Text(error)
                        .font(.system(size: 13))
                        .foregroundColor(.negative)
                }
                .padding(.horizontal, 40)
            }

            VStack(spacing: 12) {
                // Camera button
                Button {
                    HapticManager.impact(.medium)
                    showCamera = true
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "camera.fill")
                            .font(.system(size: 18))
                        Text("Take Photo")
                            .font(.system(size: 16, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Color.accent)
                    .foregroundColor(.black)
                    .cornerRadius(16)
                }
                .padding(.horizontal, 24)

                // Photo library button
                PhotosPicker(selection: $selectedItem, matching: .images) {
                    HStack(spacing: 12) {
                        Image(systemName: "photo.on.rectangle.angled")
                            .font(.system(size: 18))
                        Text("Choose from Library")
                            .font(.system(size: 16, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Color.surface)
                    .foregroundColor(.textPrimary)
                    .cornerRadius(16)
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.border, lineWidth: 0.5))
                }
                .padding(.horizontal, 24)
            }

            Spacer()
        }
    }

    var parsingView: some View {
        VStack(spacing: 24) {
            Spacer()

            if let img = selectedImage {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 220)
                    .cornerRadius(16)
                    .overlay(
                        RoundedRectangle(cornerRadius: 16)
                            .stroke(Color.accent.opacity(0.5), lineWidth: 2)
                    )
                    .padding(.horizontal, 40)
            }

            VStack(spacing: 12) {
                ProgressView()
                    .tint(.accent)
                    .scaleEffect(1.4)
                Text("Reading your Wealthsimple screenshot...")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.textPrimary)
                Text("Claude is extracting trade details")
                    .font(.system(size: 12))
                    .foregroundColor(.textSecondary)
            }

            Spacer()
        }
    }

    // MARK: - Logic

    func parseImage(_ image: UIImage) async {
        isParsing = true
        errorMessage = nil
        do {
            let trade = try await NetworkManager.shared.parseScreenshot(image: image)
            withAnimation(.spring()) {
                parsedTrade = trade
                isParsing = false
            }
            HapticManager.notification(.success)
        } catch {
            errorMessage = "Could not read screenshot: \(error.localizedDescription)"
            isParsing = false
            HapticManager.notification(.error)
        }
    }
}

struct CameraView: UIViewControllerRepresentable {
    let onCapture: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onCapture: onCapture)
    }

    class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onCapture: (UIImage?) -> Void

        init(onCapture: @escaping (UIImage?) -> Void) {
            self.onCapture = onCapture
        }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            picker.dismiss(animated: true)
            onCapture(info[.originalImage] as? UIImage)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.dismiss(animated: true)
            onCapture(nil)
        }
    }
}
