"""
Predator — pre-explosion scanner.
Scores each watchlist ticker across 6 signals (max 10 pts total).
Fires a WhatsApp alert and stores a DB record when score >= 8.

Signal weights:
  options       : 3 pts  (unusual call flow via yfinance option_chain)
  insider       : 2 pts  (insider purchases via yfinance)
  short_squeeze : 2 pts  (high short float + rising price + vol)
  catalyst      : 2 pts  (earnings/FDA within 30 days via yfinance calendar)
  institutional : 1 pt   (large institution in recent 13F snapshot)
  breakout      : 2 pts  (52wk-high proximity, vol spike, MA200 recent cross event)
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional

import pytz
import yfinance as yf

from alerts import send_sms
from database import get_connection
from market_data import get_ticker_data, ma200_recent_cross
import portfolio

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")
ALERT_THRESHOLD = 6

# Prevents concurrent predator scans and concurrent outcome updates from
# overlapping each other's SQLite writes. Non-blocking for the scan job
# (skip if busy), short-timeout for the outcomes job (wait briefly then skip).
_PREDATOR_LOCK = threading.Lock()


class SignalResult(NamedTuple):
    """Return type for individual predator signal scorers.

    score        — raw points awarded (0–max for that signal)
    reason       — human-readable explanation string
    data_quality — "HIGH" | "MEDIUM" | "LOW"
                   HIGH:   all confirmations present, no earnings risk
                   MEDIUM: base signal found but missing a confirmation
                   LOW:    earnings within 5 days or insufficient data
    """
    score:        int
    reason:       str
    data_quality: str


PREDATOR_WATCHLIST = [
    # Semiconductors
    "NVDA", "AMD", "AVGO", "MRVL", "SMCI", "TSM", "AMAT", "LRCX", "SOXL",
    # AI / quantum
    "PLTR", "BBAI", "SOUN", "IONQ", "RGTI", "QBTS", "AI", "UPST",
    # Crypto proxy
    "MARA", "RIOT",
    # High-short-interest candidates
    "GME", "CVNA", "BYND",
    # Fintech momentum
    "HOOD", "AFRM",
    # Canadian
    "MDA.TO", "SHOP.TO",
]


# ── Dedup ─────────────────────────────────────────────────────────────────────

def _already_alerted(ticker: str) -> bool:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM predator_alerts WHERE ticker = ? AND alert_time >= ? AND score >= ?",
        (ticker, cutoff, ALERT_THRESHOLD),
    ).fetchone()
    conn.close()
    return row is not None


def _record_alert(ticker: str, score: float, signals: dict, entry: float, stop: float, position: float):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO predator_alerts
            (ticker, score, signals_json, entry_price, stop_price, position_size_cad, alert_time)
        VALUES (?,?,?,?,?,?,?)
        """,
        (ticker, score, json.dumps(signals), entry, stop, position, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ── Signal 1: Unusual options activity (0–3 pts) ─────────────────────────────

def _score_options(ticker: str) -> SignalResult:
    """Score unusual options activity and return a SignalResult.

    Scoring tiers (before caps):
      3 pts — unusual OTM call volume, expiry urgency, or ratio >= 3
      1 pt  — elevated ratio >= 2
      + 1   — strike concentration boost (top-3 strikes >= 70% of vol)

    Caps applied in order:
      1. OI delta cap: score >= 3 requires call OI >= 2× put OI; else capped at 2
      2. Earnings cap: earnings within 5 days → max score = 1

    data_quality:
      LOW    — earnings within 5 days, or no option data
      MEDIUM — base signal present but OI delta unconfirmed
      HIGH   — OI delta confirmed and no earnings proximity risk
    """
    _low = SignalResult(0, "", "LOW")

    if ticker.endswith(".TO"):
        return _low
    try:
        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return _low

        cutoff = (date.today() + timedelta(days=30)).isoformat()
        near_expiries = [d for d in expiries if d <= cutoff][:3]
        if not near_expiries:
            return _low

        # ── Earnings proximity ────────────────────────────────────────────────
        earnings_within_5d = False
        try:
            cal = t.calendar
            if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"].iloc[0]
                if hasattr(ed, "date"):
                    ed = ed.date()
                earnings_within_5d = 0 <= (ed - date.today()).days <= 5
        except Exception:
            pass

        total_call_vol = 0.0
        total_put_vol  = 0.0
        total_call_oi  = 0.0
        total_put_oi   = 0.0
        strike_volumes: dict = {}   # strike → aggregate call volume across expiries
        unusual = []
        urgency_reason = ""

        info_price = 0.0
        try:
            info_price = t.info.get("regularMarketPrice") or t.info.get("currentPrice") or 0.0
        except Exception:
            pass

        for exp in near_expiries:
            try:
                chain = t.option_chain(exp)
            except Exception:
                continue

            calls = chain.calls
            puts  = chain.puts

            call_vol = calls["volume"].fillna(0).sum()
            put_vol  = puts["volume"].fillna(0).sum()
            call_oi  = calls["openInterest"].fillna(0).sum()
            put_oi   = puts["openInterest"].fillna(0).sum()

            total_call_vol += call_vol
            total_put_vol  += put_vol
            total_call_oi  += call_oi
            total_put_oi   += put_oi

            # Accumulate per-strike call volumes for concentration analysis
            for _, row in calls.iterrows():
                strike = row.get("strike", 0)
                vol    = row.get("volume", 0) or 0
                if vol > 0:
                    strike_volumes[strike] = strike_volumes.get(strike, 0) + vol

            # Expiry urgency: calls expiring within 7 days with vol > 10× OI
            if not urgency_reason:
                days_to_exp = (date.fromisoformat(exp) - date.today()).days
                if days_to_exp <= 7 and call_oi > 0 and call_vol >= 10 * call_oi:
                    urgency_reason = (
                        f"Expiry urgency: {call_vol/call_oi:.0f}x OI on calls exp {exp}"
                    )

            if info_price > 0:
                otm = calls[calls["strike"] > info_price * 1.03]
                for _, row in otm.iterrows():
                    vol = row.get("volume", 0) or 0
                    oi  = row.get("openInterest", 0) or 0
                    if vol > 0 and oi > 0 and vol >= 10 * oi:
                        unusual.append(
                            f"${row['strike']:.0f} calls {int(vol):,} vol vs {int(oi):,} OI (exp {exp})"
                        )

        ratio = total_call_vol / max(total_put_vol, 1)

        # ── OI delta confirmation ─────────────────────────────────────────────
        # Call OI >= 2× put OI confirms bullish positioning is built into open
        # interest, not just today's volume — required for a score of 3.
        oi_delta_confirmed = (
            total_call_oi > 0 and
            total_call_oi / max(total_put_oi, 1) >= 2.0
        )

        # ── Strike concentration boost ────────────────────────────────────────
        concentration_note = ""
        if strike_volumes:
            total_sv = sum(strike_volumes.values())
            top3_vol = sum(sorted(strike_volumes.values(), reverse=True)[:3])
            if total_sv > 0 and top3_vol / total_sv >= 0.70:
                top_strike = max(strike_volumes, key=strike_volumes.get)
                concentration_note = (
                    f"${top_strike:.0f} strike concentration "
                    f"({top3_vol / total_sv * 100:.0f}% of call vol)"
                )

        # ── Raw score assembly ────────────────────────────────────────────────
        reasons = []
        if unusual:
            raw_score = 3
            reasons.append(f"Unusual calls: {unusual[0]}")
            if urgency_reason:
                reasons.append(urgency_reason)
        elif urgency_reason:
            raw_score = 3
            reasons.append(urgency_reason)
        elif ratio >= 3:
            raw_score = 3
            reasons.append(f"Call/put ratio {ratio:.1f}:1 on near-term options")
        elif ratio >= 2:
            raw_score = 1
            reasons.append(f"Elevated call buying (ratio {ratio:.1f}:1)")
        else:
            raw_score = 0

        # Concentration boost: +1 when there is already a base signal
        if concentration_note and raw_score >= 1:
            raw_score = min(raw_score + 1, 3)
            reasons.append(concentration_note)

        # ── Apply caps ────────────────────────────────────────────────────────
        if raw_score >= 3 and not oi_delta_confirmed:
            raw_score = 2
            reasons.append("(OI delta unconfirmed)")

        if earnings_within_5d:
            raw_score = min(raw_score, 1)
            if reasons:
                reasons.append("(earnings ≤5d — capped)")
            data_quality = "LOW"
        elif oi_delta_confirmed:
            data_quality = "HIGH"
        else:
            data_quality = "MEDIUM"

        return SignalResult(raw_score, "; ".join(reasons), data_quality)

    except Exception as exc:
        log.debug("Options score error for %s: %s", ticker, exc)
        return SignalResult(0, "", "LOW")


# ── Signal 2: Insider buying via SEC EDGAR Form 4 (0–2 pts) ───────────────────

_EDGAR_HEADERS = {
    "User-Agent": "investing-agent karim.bishara.kb@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_EDGAR_TIMEOUT = 8  # seconds

# Module-level CIK cache — avoids repeat company_tickers.json fetches per scan.
_cik_cache: dict[str, str] = {}


def _fetch_json(url: str):
    """Thin urllib wrapper that returns parsed JSON — isolated so tests can patch it."""
    import urllib.request, json as _json
    req = urllib.request.Request(url, headers=_EDGAR_HEADERS)
    with urllib.request.urlopen(req, timeout=_EDGAR_TIMEOUT) as resp:
        return _json.loads(resp.read())


def _fetch_xml(url: str) -> str:
    """Thin urllib wrapper that returns raw text — isolated so tests can patch it."""
    import urllib.request
    req = urllib.request.Request(url, headers=_EDGAR_HEADERS)
    with urllib.request.urlopen(req, timeout=_EDGAR_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _edgar_cik(ticker: str) -> Optional[str]:
    """Return zero-padded 10-digit CIK for a US ticker via EDGAR company_tickers.json."""
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        data = _fetch_json("https://www.sec.gov/files/company_tickers.json")
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                cik = str(entry["cik_str"]).zfill(10)
                _cik_cache[ticker] = cik
                return cik
    except Exception as exc:
        log.debug("EDGAR CIK lookup failed for %s: %s", ticker, exc)
    return None


def _parse_form4_purchases(xml: str, filed: date) -> list[dict]:
    """Parse a Form 4 XML string; return list of {date, shares, amount, name} for
    transaction code 'P' (open-market purchase) only."""
    import re
    name_m = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml, re.DOTALL)
    name = name_m.group(1).strip() if name_m else "Unknown"

    results = []
    for block in re.finditer(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.DOTALL
    ):
        blk = block.group(1)
        code_m = re.search(r"<transactionCode>\s*(.*?)\s*</transactionCode>", blk)
        if not code_m or code_m.group(1).strip() != "P":
            continue
        shares_m = re.search(
            r"<transactionShares>\s*<value>\s*([\d.]+)\s*</value>", blk, re.DOTALL
        )
        price_m = re.search(
            r"<transactionPricePerShare>\s*<value>\s*([\d.]+)\s*</value>", blk, re.DOTALL
        )
        shares = float(shares_m.group(1)) if shares_m else 0.0
        price  = float(price_m.group(1))  if price_m  else 0.0
        results.append({"date": filed, "shares": shares, "amount": shares * price, "name": name})
    return results


def _edgar_form4_purchases(cik: str, days: int = 60) -> list[dict]:
    """Fetch recent Form 4 filings for the given CIK and return all P-type
    (open-market purchase) transactions filed within the last `days` days.
    Filings from EDGAR are newest-first; iteration stops at the cutoff date."""
    try:
        sub = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception as exc:
        log.debug("EDGAR submissions fetch failed CIK=%s: %s", cik, exc)
        return []

    recent    = sub.get("filings", {}).get("recent", {})
    cutoff    = (datetime.now() - timedelta(days=days)).date()
    purchases: list[dict] = []

    for form, date_str, acc, doc in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form != "4":
            continue
        try:
            filed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if filed < cutoff:
            break  # newest-first — nothing older will qualify

        acc_nodash = acc.replace("-", "")
        xml_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
            f"/{acc_nodash}/{doc}"
        )
        try:
            xml = _fetch_xml(xml_url)
            purchases.extend(_parse_form4_purchases(xml, filed))
        except Exception as exc:
            log.debug("EDGAR Form4 XML fetch failed %s: %s", xml_url, exc)

    return purchases


def _score_insider(ticker: str) -> SignalResult:
    """Score recent insider open-market purchases (Form 4, transaction code P)
    via SEC EDGAR.  Sales, grants, and awards are ignored."""
    _low = SignalResult(0, "", "LOW")

    if ticker.endswith(".TO"):
        return _low  # EDGAR covers US issuers only

    try:
        cik = _edgar_cik(ticker)
        if not cik:
            log.info("Insider %s: CIK not found in EDGAR", ticker)
            return _low

        purchases = _edgar_form4_purchases(cik, days=60)
        if not purchases:
            return _low

        cutoff_30 = datetime.now().date() - timedelta(days=30)
        recent_30 = [p for p in purchases if p["date"] >= cutoff_30]
        recent_60 = [p for p in purchases if p["date"] <  cutoff_30]

        if recent_30:
            unique_n     = len({p["name"] for p in recent_30})
            total_shares = sum(p["shares"] for p in recent_30)
            total_amount = sum(p["amount"] for p in recent_30)

            if unique_n >= 2:
                return SignalResult(
                    2,
                    f"{unique_n} insiders bought last 30d "
                    f"({total_shares:,.0f} sh, ${total_amount:,.0f})",
                    "HIGH",
                )
            if total_amount >= 50_000 or total_shares >= 500:
                return SignalResult(
                    2,
                    f"Insider bought last 30d: {total_shares:,.0f} sh (${total_amount:,.0f})",
                    "HIGH",
                )
            return SignalResult(
                1,
                f"Insider purchase last 30d: {total_shares:,.0f} sh",
                "MEDIUM",
            )

        if recent_60:
            unique_n     = len({p["name"] for p in recent_60})
            total_shares = sum(p["shares"] for p in recent_60)
            return SignalResult(
                1,
                f"Insider buying 31–60d ago ({unique_n} insider(s), {total_shares:,.0f} sh)",
                "MEDIUM",
            )

        return _low

    except Exception as exc:
        log.warning("Insider score error for %s: %s", ticker, exc, exc_info=True)
        return _low


# ── Signal 3: Short squeeze setup (0–2 pts) ────────────────────────────────────

def _score_short_squeeze(ticker: str, data: dict) -> tuple[int, str]:
    try:
        t = yf.Ticker(ticker)
        info = t.info

        short_pct = float(info.get("shortPercentOfFloat") or 0)
        vol_ratio = data.get("vol_ratio", 1.0)

        import yfinance as yf2
        hist = t.history(period="5d")
        pct_5d = 0.0
        if len(hist) >= 2:
            pct_5d = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100

        if short_pct > 0.20 and pct_5d > 5 and vol_ratio > 2:
            return 2, (
                f"Squeeze: {short_pct*100:.0f}% float short, "
                f"+{pct_5d:.1f}% this week, {vol_ratio:.1f}x vol"
            )
        if short_pct > 0.15 and pct_5d > 0:
            return 1, f"Short setup: {short_pct*100:.0f}% float short, rising"

        return 0, ""
    except Exception as exc:
        log.debug("Short squeeze score error for %s: %s", ticker, exc)
        return 0, ""


# ── Signal 4: Catalyst calendar (0–2 pts) ──────────────────────────────────────

def _score_catalyst(ticker: str) -> tuple[int, str]:
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return 0, ""

        # yfinance can return dict or DataFrame
        earnings_date = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                first = ed[0] if hasattr(ed, "__getitem__") else ed
                if hasattr(first, "date"):
                    earnings_date = first.date()
                else:
                    try:
                        from datetime import date as dt
                        earnings_date = dt.fromisoformat(str(first)[:10])
                    except Exception:
                        pass

        if earnings_date is None:
            return 0, ""

        days = (earnings_date - date.today()).days
        if 0 <= days <= 14:
            return 2, f"Earnings in {days} days"
        if 15 <= days <= 30:
            return 1, f"Earnings in {days} days"

        return 0, ""
    except Exception as exc:
        log.debug("Catalyst score error for %s: %s", ticker, exc)
        return 0, ""


# ── Signal 5: Institutional accumulation (0–1 pt) ──────────────────────────────

def _score_institutional(ticker: str) -> tuple[int, str]:
    if ticker.endswith(".TO"):
        return 0, ""
    try:
        import pandas as pd
        t = yf.Ticker(ticker)
        holders = t.institutional_holders
        if holders is None or holders.empty:
            log.info("Institutional %s: no data returned", ticker)
            return 0, ""

        holders.columns = [str(c).strip() for c in holders.columns]
        log.info("Institutional %s: columns=%s  sample=%s",
                 ticker, list(holders.columns), holders.head(2).to_dict("records"))

        pct_col = next(
            (c for c in holders.columns if "%" in c or "pct" in c.lower() or "out" in c.lower()),
            None,
        )
        name_col = next(
            (c for c in holders.columns if "holder" in c.lower() or "name" in c.lower()),
            None,
        )

        if pct_col is None or name_col is None:
            log.info("Institutional %s: missing pct_col=%s name_col=%s",
                     ticker, pct_col, name_col)
            return 0, ""

        holders = holders.copy()
        holders[pct_col] = pd.to_numeric(holders[pct_col], errors="coerce")
        holders = holders.dropna(subset=[pct_col])
        if holders.empty:
            return 0, ""

        # yfinance sometimes returns fractions (0.08 = 8%) — normalise to percentage
        if holders[pct_col].max() < 1.0:
            holders[pct_col] = holders[pct_col] * 100

        top = holders.nlargest(1, pct_col).iloc[0]
        pct = float(top[pct_col])
        name = str(top[name_col])
        log.info("Institutional %s: top holder %s %.2f%%", ticker, name, pct)

        # 3% threshold — large caps (NVDA, AMD) won't show >5% for any single institution
        if pct >= 3:
            return 1, f"{name} holds {pct:.1f}%"

        return 0, ""
    except Exception as exc:
        log.warning("Institutional score error for %s: %s", ticker, exc, exc_info=True)
        return 0, ""


# ── Signal 6: Technical breakout setup (0–2 pts) ───────────────────────────────

def _score_breakout(ticker: str, data: dict) -> tuple[int, str]:
    try:
        t = yf.Ticker(ticker)
        info = t.info

        current = data.get("price", 0)
        high_52w = float(info.get("fiftyTwoWeekHigh") or 0)
        ma200 = data.get("ma200", 0)
        vol_ratio = data.get("vol_ratio", 1.0)
        pct_1d = data.get("pct_1d", 0)

        hits = 0
        reasons = []

        if high_52w > 0 and 0 < current <= high_52w:
            pct_from_high = max((high_52w - current) / high_52w * 100, 0.0)
            if pct_from_high <= 3:
                hits += 1
                reasons.append(f"within {pct_from_high:.1f}% of 52wk high ${high_52w:.2f}")

        if vol_ratio >= 5 and pct_1d >= 3:
            hits += 1
            reasons.append(f"vol {vol_ratio:.1f}x avg, +{pct_1d:.1f}% today")

        closes = data.get("closes")
        if closes is not None and ma200:
            crossed, days_ago = ma200_recent_cross(closes)
            if crossed:
                hits += 1
                when = "today" if days_ago == 0 else f"{days_ago}d ago"
                reasons.append(f"crossed above 200d MA ${ma200:.2f} ({when})")

        if hits >= 2:
            return 2, "Breakout: " + ", ".join(reasons[:2])
        if hits == 1:
            return 1, "Breakout: " + reasons[0]

        return 0, ""
    except Exception as exc:
        log.debug("Breakout score error for %s: %s", ticker, exc)
        return 0, ""


# ── Scoring orchestrator ────────────────────────────────────────────────────────

def _score_ticker(ticker: str):
    data = get_ticker_data(ticker)
    if data is None:
        log.info("Predator: no data for %s — skip", ticker)
        return None

    opts_sig                    = _score_options(ticker)
    ins_sig                     = _score_insider(ticker)
    sq_score,   sq_reason       = _score_short_squeeze(ticker, data)
    cat_score,  cat_reason      = _score_catalyst(ticker)
    inst_score, inst_reason     = _score_institutional(ticker)
    brk_score,  brk_reason      = _score_breakout(ticker, data)

    total = min(opts_sig.score + ins_sig.score + sq_score + cat_score + inst_score + brk_score, 10)

    signals = {
        "options":       {"score": opts_sig.score, "reason": opts_sig.reason},
        "insider":       {"score": ins_sig.score,  "reason": ins_sig.reason},
        "short_squeeze": {"score": sq_score,        "reason": sq_reason},
        "catalyst":      {"score": cat_score,       "reason": cat_reason},
        "institutional": {"score": inst_score,      "reason": inst_reason},
        "breakout":      {"score": brk_score,       "reason": brk_reason},
    }
    return {"ticker": ticker, "score": total, "price": data["price"], "signals": signals}


# ── WhatsApp alert formatter ────────────────────────────────────────────────────

_SIGNAL_LABELS = {
    "options":       ("Unusual Options", 3),
    "insider":       ("Insider Buy",     2),
    "short_squeeze": ("Short Squeeze",   2),
    "catalyst":      ("Catalyst",        2),
    "institutional": ("Institutions",    1),
    "breakout":      ("Breakout",        2),
}


def _dot(score: int, max_score: int) -> str:
    if score == 0:
        return "⚫"
    if score >= max_score:
        return "🔴"
    return "🟡"


def _format_alert(ticker: str, score: int, price: float, signals: dict,
                  stop: float, position: float) -> str:
    lines = [f"🎯 PRE-EXPLOSION ALERT: ${ticker}", f"Score: {score}/10", ""]
    for key, (label, max_s) in _SIGNAL_LABELS.items():
        sig = signals.get(key, {})
        s   = sig.get("score", 0)
        r   = sig.get("reason", "") or "—"
        if s > 0:
            lines.append(f"{_dot(s, max_s)} {label}: {r}")
    lines += [
        "",
        f"Entry: ${price:.2f} | Stop: ${stop:.2f} (-9%)",
        f"Position size: ${position:,.0f} CAD (25% of cash)",
    ]
    return "\n".join(lines)


# ── Outcome updater (call daily) ────────────────────────────────────────────────

def update_outcomes():
    if not _PREDATOR_LOCK.acquire(blocking=True, timeout=15):
        log.warning("Outcomes: predator lock still held after 15s — skipping outcome update")
        return

    _start = time.monotonic()
    log.info("Outcomes: lock acquired — updating")
    try:
        conn = get_connection()

        for days, col in ((7, "price_7d_later"), (14, "price_14d_later"), (30, "price_30d_later")):
            cutoff_start = (datetime.now() - timedelta(days=days + 1)).isoformat()
            cutoff_end   = (datetime.now() - timedelta(days=days - 1)).isoformat()
            rows = conn.execute(
                f"SELECT id, ticker, entry_price FROM predator_alerts "
                f"WHERE alert_time BETWEEN ? AND ? AND {col} IS NULL",
                (cutoff_start, cutoff_end),
            ).fetchall()

            # Gather all updates first, then commit once per time-window.
            # Previously: one conn.commit() per row → hundreds of WAL flushes.
            updates = []
            for row in rows:
                try:
                    import yfinance as yf3
                    hist = yf3.Ticker(row["ticker"]).history(period="1d")
                    if hist.empty:
                        continue
                    px = round(float(hist["Close"].iloc[-1]), 2)
                    entry = row["entry_price"] or 0
                    outcome = None
                    if entry > 0:
                        pct = (px / entry - 1) * 100
                        if pct >= 10:
                            outcome = "WIN"
                        elif pct <= -9:
                            outcome = "LOSS"
                        else:
                            outcome = "HOLD"
                    updates.append((px, outcome, row["id"]))
                    log.info("Outcome queued %s id=%d: %s=%.2f (%s)",
                             row["ticker"], row["id"], col, px, outcome)
                except Exception as exc:
                    log.warning("Outcome update failed for %s: %s", row["ticker"], exc)

            if updates:
                conn.executemany(
                    f"UPDATE predator_alerts SET {col} = ?, outcome = ? WHERE id = ?",
                    updates,
                )
                conn.commit()
                log.info("Outcomes: committed %d %s updates", len(updates), col)

        conn.close()
    finally:
        _PREDATOR_LOCK.release()
        log.info("Outcomes: complete in %.1fs — lock released", time.monotonic() - _start)


# ── Parallel scoring (for debug/API) ─────────────────────────────────────────

def score_tickers(tickers: list[str]) -> list[dict]:
    """Score a specific list of tickers in parallel.

    Uses up to 5 worker threads; each ticker is abandoned after 8 seconds.
    No dedup, no DB writes, no WhatsApp alerts.
    Returns results sorted by score descending.
    """
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(_score_ticker, t): t for t in tickers}
        for future, ticker in future_to_ticker.items():
            try:
                result = future.result(timeout=8)
            except _FuturesTimeout:
                log.warning("Predator: %s timed out — skip", ticker)
                result = None
            except Exception:
                log.exception("Predator: error for %s", ticker)
                result = None
            if result is not None:
                results.append(result)
    return sorted(results, key=lambda r: r["score"], reverse=True)


def score_all_tickers() -> list[dict]:
    """Score every ticker in PREDATOR_WATCHLIST in parallel."""
    return score_tickers(PREDATOR_WATCHLIST)


def save_scan_results(results: list[dict]):
    """Persist a list of scored results to predator_latest without sending alerts."""
    _batch_upsert_latest(results)


# ── Main job ───────────────────────────────────────────────────────────────────

def run_predator():
    if not _PREDATOR_LOCK.acquire(blocking=False):
        log.warning("Predator: previous run still active — skipping scheduled fire @ %s",
                    datetime.now(EASTERN).strftime("%H:%M"))
        return

    _start = time.monotonic()
    log.info("Predator: lock acquired — starting scan @ %s", datetime.now(EASTERN).strftime("%H:%M"))
    try:
        cash = portfolio.get_cash()
        position_size = round(cash * 0.25, 2)

        latest_rows = []  # collected for single batch upsert at end of run

        for ticker in PREDATOR_WATCHLIST:
            if _already_alerted(ticker):
                log.debug("Predator: %s already alerted — skip", ticker)
                continue
            try:
                result = _score_ticker(ticker)
            except Exception:
                log.exception("Predator: unhandled error for %s", ticker)
                time.sleep(1)
                continue

            if result is None:
                time.sleep(1)
                continue

            score   = result["score"]
            price   = result["price"]
            signals = result["signals"]

            log.info("Predator: %s score=%d/%d", ticker, score, 10)

            if score >= ALERT_THRESHOLD:
                stop = round(price * 0.91, 2)
                msg  = _format_alert(ticker, score, price, signals, stop, position_size)
                if send_sms(msg):
                    _record_alert(ticker, score, signals, price, stop, position_size)
                    log.info("Predator alert sent for %s (score %d)", ticker, score)

            # Always collect for predator_latest — one batch write at end of run
            latest_rows.append(result)
            time.sleep(1)

        # Single commit for all latest scores — replaces 27 individual INSERTs+commits
        _batch_upsert_latest(latest_rows)

    finally:
        _PREDATOR_LOCK.release()
        log.info("Predator: scan complete in %.0fs — lock released",
                 time.monotonic() - _start)


def _batch_upsert_latest(results: list) -> None:
    """Upsert latest scan scores into predator_latest in a single transaction.

    Replaces the old _record_alert_passive pattern that did one INSERT + one
    COMMIT per ticker (27 separate write transactions per hourly run → ~350 new
    rows/day that were never individually useful).  Now it's one UPSERT batch,
    one commit, zero table growth.
    """
    if not results:
        return
    now = datetime.now().isoformat()
    rows = [
        (
            r["ticker"],
            r["score"],
            json.dumps(r["signals"]),
            r.get("price"),
            round(r["price"] * 0.91, 2) if r.get("price") else None,
            now,
        )
        for r in results
    ]
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO predator_latest
                (ticker, score, signals_json, entry_price, stop_price, scan_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                score        = excluded.score,
                signals_json = excluded.signals_json,
                entry_price  = excluded.entry_price,
                stop_price   = excluded.stop_price,
                scan_time    = excluded.scan_time
            """,
            rows,
        )
        conn.commit()
        log.info("Predator: upserted %d latest scores into predator_latest", len(rows))
    finally:
        conn.close()
