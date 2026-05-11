"""
Isolated unit tests for the Phase 1B insider-signal redesign.

Tests cover:
  - Canadian ticker bypass            (.TO → SignalResult(0,"","LOW"))
  - Return type                       (always SignalResult, never tuple)
  - CIK not found                     (→ LOW)
  - No Form 4 filings in window       (→ LOW)
  - Sales / grants ignored            (only code 'P' counted)
  - Scoring tiers:
      30d, small purchase             → score=1, MEDIUM
      30d, large shares (≥500)        → score=2, HIGH
      30d, large amount (≥$50k)       → score=2, HIGH
      30d, multiple insiders (≥2)     → score=2, HIGH
      31–60d window                   → score=1, MEDIUM
  - Date boundaries                   (30d cutoff, 61d ignored)
  - _parse_form4_purchases unit       (pure XML parsing, no HTTP)
  - Network error → graceful LOW

All HTTP calls (_fetch_json, _fetch_xml) are mocked — no internet required.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import date, datetime, timedelta
from typing import Optional
from unittest.mock import patch, MagicMock

import predator
from predator import SignalResult, _score_insider, _parse_form4_purchases


# ─────────────────────────────────────────────
# XML / JSON builder helpers
# ─────────────────────────────────────────────

def _form4_xml(transactions: list[dict], name: str = "DOE JOHN") -> str:
    """
    Build a minimal Form 4 XML string.
    Each transaction dict: {"code": "P"|"S"|..., "shares": float, "price": float}
    """
    blocks = ""
    for t in transactions:
        blocks += f"""
    <nonDerivativeTransaction>
      <transactionCoding>
        <transactionCode>{t["code"]}</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{t["shares"]}</value></transactionShares>
        <transactionPricePerShare><value>{t["price"]}</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>"""
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>{blocks}
  </nonDerivativeTable>
</ownershipDocument>"""


def _tickers_json(ticker: str = "AAPL", cik_int: int = 320193) -> dict:
    return {"0": {"cik_str": cik_int, "ticker": ticker, "title": "Test Corp"}}


def _submissions_json(entries: list[tuple]) -> dict:
    """entries = list of (date_str, accession, primaryDoc)  — all assumed to be form '4'."""
    return {
        "filings": {
            "recent": {
                "form":            ["4"] * len(entries),
                "filingDate":      [e[0] for e in entries],
                "accessionNumber": [e[1] for e in entries],
                "primaryDocument": [e[2] for e in entries],
            }
        }
    }


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _make_fetchers(
    *,
    ticker: str = "AAPL",
    cik_int: int = 320193,
    filing_entries: Optional[list] = None,
    xml_map: Optional[dict] = None,
):
    """
    Return (json_fetcher, xml_fetcher) mocks wired to synthetic data.
    filing_entries: list of (date_str, accession_no, primaryDoc)
    xml_map:        {accession_nodash: xml_string}
    """
    submissions = _submissions_json(filing_entries or [])
    jsons = {
        "company_tickers": _tickers_json(ticker, cik_int),
        "submissions":     submissions,
    }
    xmls = xml_map or {}

    def json_fetcher(url: str):
        if "company_tickers" in url:
            return jsons["company_tickers"]
        if "submissions" in url:
            return jsons["submissions"]
        raise ValueError(f"Unexpected JSON URL: {url}")

    def xml_fetcher(url: str):
        for key, xml in xmls.items():
            if key in url:
                return xml
        raise ValueError(f"No XML registered for URL: {url}")

    return json_fetcher, xml_fetcher


# ─────────────────────────────────────────────
# Cache isolation
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_cik_cache():
    predator._cik_cache.clear()
    yield
    predator._cik_cache.clear()


# ─────────────────────────────────────────────
# Canadian bypass
# ─────────────────────────────────────────────

class TestCanadianBypass:
    def test_to_suffix_returns_low_without_network_call(self):
        # No patches needed — .TO short-circuits before any HTTP call
        result = _score_insider("SHOP.TO")
        assert result == SignalResult(0, "", "LOW")

    def test_xiu_to_returns_low(self):
        result = _score_insider("XIU.TO")
        assert result == SignalResult(0, "", "LOW")


# ─────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────

class TestReturnType:
    def test_always_returns_signal_result(self):
        # Even with no data the return type must be SignalResult, not a bare tuple
        with patch("predator._fetch_json", return_value={}):
            result = _score_insider("NVDA")
        assert isinstance(result, SignalResult)

    def test_has_three_fields(self):
        with patch("predator._fetch_json", return_value={}):
            result = _score_insider("NVDA")
        score, reason, quality = result  # unpacks correctly
        assert isinstance(score, int)
        assert isinstance(reason, str)
        assert quality in ("HIGH", "MEDIUM", "LOW")


# ─────────────────────────────────────────────
# CIK not found
# ─────────────────────────────────────────────

class TestCIKNotFound:
    def test_missing_ticker_in_edgar_returns_low(self):
        # company_tickers.json contains no matching entry
        with patch("predator._fetch_json", return_value={}):
            result = _score_insider("NVDA")
        assert result == SignalResult(0, "", "LOW")

    def test_network_error_on_cik_lookup_returns_low(self):
        with patch("predator._fetch_json", side_effect=OSError("timeout")):
            result = _score_insider("NVDA")
        assert result == SignalResult(0, "", "LOW")


# ─────────────────────────────────────────────
# No purchases / sales-only
# ─────────────────────────────────────────────

class TestNoPurchases:
    def test_no_form4_filings_returns_low(self):
        jf, xf = _make_fetchers(filing_entries=[])
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")

    def test_only_sales_returns_low(self):
        # Filing exists but every transaction is a sale (code 'S')
        acc = "0000320193-26-000001"
        xml = _form4_xml([{"code": "S", "shares": 5000, "price": 200.0}])
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(10), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")

    def test_grants_and_awards_ignored(self):
        # Transaction code 'A' (grant/award) must not score
        acc = "0000320193-26-000002"
        xml = _form4_xml([{"code": "A", "shares": 10000, "price": 0.0}])
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(5), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")

    def test_form_type_10k_not_parsed(self):
        # Only form='4' entries are processed; 10-K should be ignored
        submissions = {
            "filings": {
                "recent": {
                    "form":            ["10-K"],
                    "filingDate":      [_days_ago(5)],
                    "accessionNumber": ["0000320193-26-000003"],
                    "primaryDocument": ["form10k.htm"],
                }
            }
        }
        def jf(url):
            if "company_tickers" in url:
                return _tickers_json()
            return submissions
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=lambda u: (_ for _ in ()).throw(AssertionError("should not fetch XML"))):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")


# ─────────────────────────────────────────────
# 30-day scoring tiers
# ─────────────────────────────────────────────

class TestRecentPurchases30d:
    def _run(self, transactions, name="DOE JOHN", ticker="AAPL"):
        acc = "0000320193-26-000010"
        xml = _form4_xml(transactions, name=name)
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(10), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            return _score_insider(ticker)

    def test_small_purchase_scores_1_medium(self):
        # 100 shares at $10 = $1,000 — below both thresholds
        result = self._run([{"code": "P", "shares": 100, "price": 10.0}])
        assert result.score == 1
        assert result.data_quality == "MEDIUM"
        assert "30d" in result.reason

    def test_large_shares_threshold_scores_2_high(self):
        # 500 shares (exactly at threshold) → score=2 HIGH
        result = self._run([{"code": "P", "shares": 500, "price": 10.0}])
        assert result.score == 2
        assert result.data_quality == "HIGH"

    def test_large_amount_threshold_scores_2_high(self):
        # 100 shares at $600 = $60,000 ≥ $50k → score=2 HIGH
        result = self._run([{"code": "P", "shares": 100, "price": 600.0}])
        assert result.score == 2
        assert result.data_quality == "HIGH"

    def test_multiple_insiders_scores_2_high(self):
        # Two separate Form 4 filings (different insiders) within 30d
        acc1 = "0000320193-26-000011"
        acc2 = "0000320193-26-000012"
        xml1 = _form4_xml([{"code": "P", "shares": 200, "price": 50.0}], name="DOE JOHN")
        xml2 = _form4_xml([{"code": "P", "shares": 200, "price": 50.0}], name="SMITH JANE")
        jf, xf = _make_fetchers(
            filing_entries=[
                (_days_ago(5),  acc1, "form4a.xml"),
                (_days_ago(15), acc2, "form4b.xml"),
            ],
            xml_map={
                acc1.replace("-", ""): xml1,
                acc2.replace("-", ""): xml2,
            },
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result.score == 2
        assert result.data_quality == "HIGH"
        assert "2 insiders" in result.reason

    def test_mixed_p_and_s_only_p_counted(self):
        # Same filing has both a purchase and a sale; only purchase should score
        result = self._run([
            {"code": "S", "shares": 5000, "price": 150.0},
            {"code": "P", "shares": 100,  "price": 150.0},
        ])
        assert result.score == 1
        assert result.data_quality == "MEDIUM"


# ─────────────────────────────────────────────
# 31–60 day window
# ─────────────────────────────────────────────

class TestPurchases60dWindow:
    def test_purchase_31d_ago_scores_1_medium(self):
        acc = "0000320193-26-000020"
        xml = _form4_xml([{"code": "P", "shares": 1000, "price": 100.0}])
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(45), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result.score == 1
        assert result.data_quality == "MEDIUM"
        assert "31" in result.reason or "60d" in result.reason

    def test_purchase_60d_ago_still_in_window(self):
        # Exactly 60 days ago is the boundary — should still be included
        acc = "0000320193-26-000021"
        xml = _form4_xml([{"code": "P", "shares": 200, "price": 50.0}])
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(60), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result.score >= 1

    def test_purchase_61d_ago_outside_window_returns_low(self):
        # 61 days old: _edgar_form4_purchases cuts off at 60d
        acc = "0000320193-26-000022"
        xml = _form4_xml([{"code": "P", "shares": 1000, "price": 100.0}])
        jf, xf = _make_fetchers(
            filing_entries=[(_days_ago(61), acc, "form4.xml")],
            xml_map={acc.replace("-", ""): xml},
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=xf):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")


# ─────────────────────────────────────────────
# _parse_form4_purchases — pure unit tests (no HTTP)
# ─────────────────────────────────────────────

class TestParseForm4Purchases:
    TODAY = date.today()

    def test_single_purchase_returned(self):
        xml = _form4_xml([{"code": "P", "shares": 1000, "price": 55.5}])
        results = _parse_form4_purchases(xml, self.TODAY)
        assert len(results) == 1
        assert results[0]["shares"] == 1000.0
        assert results[0]["amount"] == pytest.approx(55_500.0)
        assert results[0]["date"] == self.TODAY

    def test_sales_not_returned(self):
        xml = _form4_xml([{"code": "S", "shares": 999, "price": 100.0}])
        assert _parse_form4_purchases(xml, self.TODAY) == []

    def test_multiple_transactions_only_p_returned(self):
        xml = _form4_xml([
            {"code": "A", "shares": 5000, "price": 0.0},
            {"code": "P", "shares": 300,  "price": 40.0},
            {"code": "S", "shares": 200,  "price": 50.0},
            {"code": "P", "shares": 700,  "price": 40.0},
        ])
        results = _parse_form4_purchases(xml, self.TODAY)
        assert len(results) == 2
        assert sum(r["shares"] for r in results) == 1000.0

    def test_insider_name_extracted(self):
        xml = _form4_xml([{"code": "P", "shares": 100, "price": 10.0}], name="SMITH JANE")
        results = _parse_form4_purchases(xml, self.TODAY)
        assert results[0]["name"] == "SMITH JANE"

    def test_missing_price_defaults_to_zero_amount(self):
        # XML with no transactionPricePerShare → amount = 0
        xml = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>TEST</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""
        results = _parse_form4_purchases(xml, self.TODAY)
        assert len(results) == 1
        assert results[0]["shares"] == 500.0
        assert results[0]["amount"] == 0.0

    def test_empty_xml_returns_empty_list(self):
        assert _parse_form4_purchases("", self.TODAY) == []

    def test_no_non_derivative_transactions_returns_empty(self):
        # Derivative-only form (options exercise etc.) → empty
        xml = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>TEST</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <derivativeTable>
    <derivativeTransaction>
      <transactionCode>M</transactionCode>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>"""
        assert _parse_form4_purchases(xml, self.TODAY) == []


# ─────────────────────────────────────────────
# Error handling
# ─────────────────────────────────────────────

class TestErrorHandling:
    def test_submissions_network_error_returns_low(self):
        # CIK resolves fine, but submissions fetch throws
        def jf(url):
            if "company_tickers" in url:
                return _tickers_json()
            raise OSError("network error")

        with patch("predator._fetch_json", side_effect=jf):
            result = _score_insider("AAPL")
        assert result == SignalResult(0, "", "LOW")

    def test_xml_fetch_error_skips_filing_gracefully(self):
        # One filing's XML fails to fetch; should return LOW rather than crash
        acc = "0000320193-26-000030"
        jf, _ = _make_fetchers(
            filing_entries=[(_days_ago(10), acc, "form4.xml")],
        )
        with patch("predator._fetch_json", side_effect=jf), \
             patch("predator._fetch_xml",  side_effect=OSError("timeout")):
            result = _score_insider("AAPL")
        assert isinstance(result, SignalResult)
        assert result.score == 0
