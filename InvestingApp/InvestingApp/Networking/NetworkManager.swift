import Foundation
import UIKit

enum NetworkError: LocalizedError {
    case invalidURL
    case noData
    case decodingError(Error)
    case serverError(Int, String)
    case networkError(Error)
    case offline
    case timeout

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .noData: return "No data received"
        case .decodingError(let e): return "Decode error: \(e.localizedDescription)"
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let e): return e.localizedDescription
        case .offline: return "Offline. Showing cached data when available."
        case .timeout: return "Request timed out. Try again in a moment."
        }
    }
}

final class NetworkManager {
    static let shared = NetworkManager()

    private let session: URLSession
    private let decoder = JSONDecoder()

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 18
        config.timeoutIntervalForResource = 30
        config.waitsForConnectivity = false
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

    func confirmTrade(trade: ParsedTrade, ticker: String, shares: Double, priceCAD: Double, totalCAD: Double, type: String) async throws {
        var body: [String: Any] = [
            "ticker": ticker,
            "shares": shares,
            "price_cad": priceCAD,
            "total_cad": totalCAD,
            "type": type,
            "currency": trade.currency
        ]
        if let value = trade.pricePerShareUSD { body["price_per_share_usd"] = value }
        if let value = trade.pricePerShareCAD { body["price_per_share_cad"] = value }
        if let value = trade.exchangeRate { body["exchange_rate"] = value }
        if let value = trade.notes { body["notes"] = value }
        struct ConfirmResponse: Decodable { let success: Bool; let error: String? }
        let response: ConfirmResponse = try await postAny(url: APIEndpoints.confirmTrade, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Trade failed on server")
        }
    }

    // MARK: - Portfolio Health

    func fetchPortfolioHealth() async throws -> PortfolioHealth {
        return try await get(url: APIEndpoints.portfolioHealth)
    }

    // MARK: - Watchlist

    func fetchWatchlist() async throws -> [WatchlistAlert] {
        struct Wrapper: Decodable { let watchlist: [WatchlistAlert] }
        let wrapper: Wrapper = try await get(url: APIEndpoints.watchlist)
        return wrapper.watchlist
    }

    func addWatchlistAlert(ticker: String, alertPrice: Double, direction: String, note: String) async throws -> Int {
        let body: [String: Any] = [
            "ticker": ticker,
            "alert_price": alertPrice,
            "direction": direction,
            "note": note
        ]
        struct AddResponse: Decodable { let success: Bool; let id: Int?; let error: String? }
        let response: AddResponse = try await postAny(url: APIEndpoints.watchlistAdd, body: body)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to add alert")
        }
        return response.id ?? 0
    }

    func deleteWatchlistAlert(id: Int) async throws {
        guard let reqURL = URL(string: APIEndpoints.watchlistDelete(id)) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "DELETE"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        struct DeleteResponse: Decodable { let success: Bool; let error: String? }
        let response: DeleteResponse = try await perform(request: request)
        if !response.success {
            throw NetworkError.serverError(400, response.error ?? "Failed to delete alert")
        }
    }

    // MARK: - Planner

    func fetchPlannerNextDeployment() async throws -> PaycheckDeployment {
        return try await get(url: APIEndpoints.plannerNextDeployment)
    }

    func savePlannerSetup(paycheckAmount: Double, paycheckDay: Int, allocationPercent: Double) async throws -> PaycheckDeployment {
        let body: [String: Any] = [
            "paycheck_amount": paycheckAmount,
            "paycheck_day": paycheckDay,
            "allocation_percent": allocationPercent
        ]
        return try await postAny(url: APIEndpoints.plannerSetup, body: body)
    }

    // MARK: - Predator

    func fetchPredatorAlerts() async throws -> [PredatorAlert] {
        struct AlertsWrapper: Decodable { let alerts: [PredatorAlert] }
        let wrapper: AlertsWrapper = try await get(url: APIEndpoints.predatorAlerts)
        return wrapper.alerts
    }

    func fetchPredatorWatchlist() async throws -> [PredatorAlert] {
        struct WatchlistWrapper: Decodable { let watchlist: [PredatorAlert] }
        let wrapper: WatchlistWrapper = try await get(url: APIEndpoints.predatorWatchlist)
        return wrapper.watchlist
    }

    // MARK: - Market Data

    func fetchMarketData() async throws -> MarketData {
        return try await get(url: APIEndpoints.market)
    }

    // MARK: - Operator / Alpha

    func fetchBackendHealth() async throws -> BackendHealth {
        return try await getV1(url: APIEndpoints.v1Health)
    }

    func fetchRootHealth() async throws -> RootHealth {
        return try await get(url: "\(APIEndpoints.base)/health")
    }

    func fetchAlphaTop() async throws -> AlphaTopResponse {
        return try await getV1(url: APIEndpoints.alphaTop)
    }

    func fetchAlphaReport() async throws -> AlphaReport {
        return try await getV1(url: APIEndpoints.alphaReport)
    }

    func fetchAlphaOutcomes() async throws -> AlphaOutcomesResponse {
        return try await getV1(url: APIEndpoints.alphaOutcomes)
    }

    func fetchAlphaLearning() async throws -> AlphaLearning {
        return try await getV1(url: APIEndpoints.alphaLearning)
    }

    func fetchAlphaLearningRecommendations() async throws -> AlphaLearningRecommendations {
        return try await getV1(url: APIEndpoints.alphaLearningRecommendations)
    }

    func fetchAlphaShadowPolicy() async throws -> AlphaShadowPolicy {
        return try await getV1(url: APIEndpoints.alphaShadowPolicy)
    }

    // MARK: - Operator / Alpha L3 Proposals

    func fetchAlphaProposals(includeHistorical: Bool = false) async throws -> AlphaProposalsResponse {
        var url = APIEndpoints.alphaProposals
        if includeHistorical { url += "?include_historical=true" }
        return try await getV1(url: url)
    }

    func generateAlphaProposals(secret: String) async throws -> AlphaProposalsGenerateResponse {
        return try await postV1Auth(url: APIEndpoints.alphaProposalsGenerate, body: [:], secret: secret)
    }

    func approveAlphaProposal(id: String, note: String?, secret: String) async throws -> AlphaProposalActionResponse {
        var body: [String: Any] = [:]
        if let note { body["note"] = note }
        return try await postV1Auth(url: APIEndpoints.alphaProposalsApproveShadow(id), body: body, secret: secret)
    }

    func rejectAlphaProposal(id: String, reason: String?, secret: String) async throws -> AlphaProposalActionResponse {
        var body: [String: Any] = [:]
        if let reason { body["reason"] = reason }
        return try await postV1Auth(url: APIEndpoints.alphaProposalsReject(id), body: body, secret: secret)
    }

    func fetchAlphaProposalShadowResults(id: String) async throws -> AlphaProposalShadowResults {
        return try await getV1(url: APIEndpoints.alphaProposalShadowResults(id))
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

    private func getV1<T: Decodable>(url: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let envelope: APIEnvelope<T> = try await perform(request: request)
        guard envelope.ok, let data = envelope.data else {
            throw NetworkError.serverError(envelope.error?.code ?? 500, envelope.error?.message ?? "API request failed")
        }
        return data
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

    // MARK: - Authenticated POST for v1 envelope endpoints (auth = Bearer token)

    private func postV1Auth<T: Decodable>(url: String, body: [String: Any], secret: String) async throws -> T {
        guard let reqURL = URL(string: url) else { throw NetworkError.invalidURL }
        var request = URLRequest(url: reqURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
        request.httpBody = body.isEmpty ? "{}".data(using: .utf8) : try JSONSerialization.data(withJSONObject: body)
        let envelope: APIEnvelope<T> = try await perform(request: request)
        guard envelope.ok, let data = envelope.data else {
            throw NetworkError.serverError(envelope.error?.code ?? 500, envelope.error?.message ?? "API request failed")
        }
        return data
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
            return try await performOnce(request: request)
        } catch {
            guard shouldRetry(error) else { throw error }
            try? await Task.sleep(nanoseconds: 350_000_000)
            return try await performOnce(request: request)
        }
    }

    private func performOnce<T: Decodable>(request: URLRequest) async throws -> T {
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
        } catch let error as URLError where error.code == .timedOut {
            throw NetworkError.timeout
        } catch let error as URLError where error.code == .notConnectedToInternet || error.code == .networkConnectionLost {
            throw NetworkError.offline
        } catch {
            throw NetworkError.networkError(error)
        }
    }

    private func shouldRetry(_ error: Error) -> Bool {
        if case NetworkError.timeout = error { return true }
        if case NetworkError.offline = error { return false }
        if case NetworkError.networkError = error { return true }
        if case NetworkError.serverError(let code, _) = error { return code == 408 || code == 429 || code >= 500 }
        return false
    }
}

struct APIEnvelope<T: Decodable>: Decodable {
    let ok: Bool
    let data: T?
    let error: APIEnvelopeError?
}

struct APIEnvelopeError: Decodable {
    let code: Int
    let message: String
}
