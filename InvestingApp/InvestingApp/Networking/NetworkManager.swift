import Foundation
import UIKit

enum NetworkError: LocalizedError {
    case invalidURL
    case noData
    case decodingError(Error)
    case serverError(Int, String)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .noData: return "No data received"
        case .decodingError(let e): return "Decode error: \(e.localizedDescription)"
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let e): return e.localizedDescription
        }
    }
}

final class NetworkManager {
    static let shared = NetworkManager()

    private let session: URLSession
    private let decoder = JSONDecoder()

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 120
        session = URLSession(configuration: config)
    }

    // MARK: - Portfolio

    func fetchPortfolio() async throws -> Portfolio {
        return try await get(url: APIEndpoints.portfolio)
    }

    // MARK: - Opportunities

    func fetchOpportunities() async throws -> [Opportunity] {
        struct Wrapper: Decodable { let opportunities: [Opportunity] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.opportunities)
        return wrapper.opportunities
    }

    // MARK: - Signals

    func fetchSignals() async throws -> [Signal] {
        struct Wrapper: Decodable { let signals: [Signal] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.signals)
        return wrapper.signals
    }

    // MARK: - Analyze

    func analyzeStock(ticker: String) async throws -> AnalysisResult {
        let body = ["ticker": ticker]
        return try await post(url: APIEndpoints.analyze, body: body)
    }

    // MARK: - Screenshot

    func parseScreenshot(image: UIImage) async throws -> ParsedTrade {
        guard let imageData = image.jpegData(compressionQuality: 0.85) else {
            throw NetworkError.noData
        }
        let base64 = imageData.base64EncodedString()
        let body = ["image": base64]
        return try await post(url: APIEndpoints.parseScreenshot, body: body)
    }

    // MARK: - Confirm Trade

    func confirmTrade(ticker: String, shares: Double, priceCAD: Double, type: String) async throws {
        let body: [String: Any] = [
            "ticker": ticker,
            "shares": shares,
            "price_cad": priceCAD,
            "type": type
        ]
        struct ConfirmResponse: Decodable { let success: Bool; let error: String? }
        let response: ConfirmResponse = try await postAny(url: APIEndpoints.confirmTrade, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Trade failed on server")
        }
    }

    // MARK: - Market Data

    func fetchMarketData() async throws -> MarketData {
        return try await get(url: APIEndpoints.market)
    }

    // MARK: - Cash

    func fetchCash() async throws -> Double {
        struct CashResponse: Decodable { let availableCash: Double }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let reqURL = URL(string: APIEndpoints.cash) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw NetworkError.serverError(0, "Failed to fetch cash")
        }
        return (try decoder.decode(CashResponse.self, from: data)).availableCash
    }

    func updateCash(amount: Double) async throws {
        let body: [String: Any] = ["cash": amount]
        struct OKResponse: Decodable { let success: Bool; let error: String? }
        let response: OKResponse = try await postAny(url: APIEndpoints.cash, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to update cash")
        }
    }

    // MARK: - Generic GET

    private func get<T: Decodable>(url: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(request: request)
    }

    // MARK: - Generic POST (Encodable body)

    private func post<T: Decodable, B: Encodable>(url: String, body: B) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(body)
        return try await perform(request: request)
    }

    // MARK: - Generic POST (Any body)

    private func postAny<T: Decodable>(url: String, body: [String: Any]) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await perform(request: request)
    }

    // MARK: - Perform

    private func perform<T: Decodable>(request: URLRequest) async throws -> T {
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw NetworkError.noData }
            guard (200...299).contains(http.statusCode) else {
                let message = String(data: data, encoding: .utf8) ?? "Unknown error"
                throw NetworkError.serverError(http.statusCode, message)
            }
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw NetworkError.decodingError(error)
            }
        } catch let error as NetworkError {
            throw error
        } catch {
            throw NetworkError.networkError(error)
        }
    }
}
