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
  breakout      : 2 pts  (52wk-high proximity, vol spike, MA200 cross)
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from datetime import date, datetime, timedelta

import pytz
import yfinance as yf

from alerts import send_sms
from database import get_connection
from market_data import get_ticker_data
import portfolio

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")
ALERT_THRESHOLD = 8

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
        "SELECT id FROM predator_alerts WHERE ticker = ? AND alert_time >= ?",
        (ticker, cutoff),
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

def _score_options(ticker: str) -> tuple[int, str]:
    if ticker.endswith(".TO"):
        return 0, ""
    try:
        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return 0, ""

        cutoff = (date.today() + timedelta(days=30)).isoformat()
        near_expiries = [d for d in expiries if d <= cutoff][:3]
        if not near_expiries:
            return 0, ""

        total_call_vol = 0.0
        total_put_vol = 0.0
        unusual = []

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
            puts = chain.puts

            call_vol = calls["volume"].fillna(0).sum()
            put_vol = puts["volume"].fillna(0).sum()
            total_call_vol += call_vol
            total_put_vol += put_vol

            if info_price > 0:
                otm = calls[calls["strike"] > info_price * 1.03]
                for _, row in otm.iterrows():
                    vol = row.get("volume", 0) or 0
                    oi = row.get("openInterest", 0) or 0
                    if vol > 0 and oi > 0 and vol >= 10 * oi:
                        unusual.append(
                            f"${row['strike']:.0f} calls {int(vol):,} vol vs {int(oi):,} OI (exp {exp})"
                        )

        ratio = total_call_vol / max(total_put_vol, 1)

        if unusual:
            return 3, f"Unusual calls: {unusual[0]}"
        if ratio >= 3:
            return 3, f"Call/put ratio {ratio:.1f}:1 on near-term options"
        if ratio >= 2:
            return 1, f"Elevated call buying (ratio {ratio:.1f}:1)"

        return 0, ""
    except Exception as exc:
        log.debug("Options score error for %s: %s", ticker, exc)
        return 0, ""


# ── Signal 2: Insider buying (0–2 pts) ────────────────────────────────────────

def _score_insider(ticker: str) -> tuple[int, str]:
    if ticker.endswith(".TO"):
        return 0, ""
    try:
        t = yf.Ticker(ticker)
        txns = t.insider_transactions
        if txns is None or txns.empty:
            return 0, ""

        # Normalise column names
        txns.columns = [c.strip() for c in txns.columns]
        buy_keywords = ("purchase", "buy", "bought")

        text_col = next(
            (c for c in txns.columns if c.lower() in ("text", "transaction", "type")), None
        )
        if text_col is None:
            return 0, ""

        buys = txns[txns[text_col].str.lower().str.contains("|".join(buy_keywords), na=False)]
        if buys.empty:
            return 0, ""

        date_col = next(
            (c for c in buys.columns if c.lower() in ("start date", "date", "startdate", "transactiondate")),
            None,
        )
        if date_col is None:
            return 1, "Insider buying detected"

        import pandas as pd
        buys = buys.copy()
        buys[date_col] = pd.to_datetime(buys[date_col], errors="coerce")
        now = datetime.now()

        recent_30 = buys[buys[date_col] >= now - timedelta(days=30)]
        recent_60 = buys[buys[date_col] >= now - timedelta(days=60)]

        if not recent_30.empty:
            shares_col = next((c for c in recent_30.columns if "share" in c.lower()), None)
            total = int(recent_30[shares_col].sum()) if shares_col else 0
            suffix = f" ({total:,} sh)" if total > 0 else ""
            return 2, f"Insider bought in last 30d{suffix}"
        if not recent_60.empty:
            return 1, "Insider buying in last 60d"

        return 0, ""
    except Exception as exc:
        log.debug("Insider score error for %s: %s", ticker, exc)
        return 0, ""


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
        t = yf.Ticker(ticker)
        holders = t.institutional_holders
        if holders is None or holders.empty:
            return 0, ""

        holders.columns = [c.strip() for c in holders.columns]
        pct_col = next(
            (c for c in holders.columns if "%" in c or "pct" in c.lower() or "out" in c.lower()),
            None,
        )
        name_col = next(
            (c for c in holders.columns if "holder" in c.lower() or "name" in c.lower()),
            None,
        )

        if pct_col and name_col and not holders.empty:
            import pandas as pd
            holders[pct_col] = pd.to_numeric(holders[pct_col], errors="coerce")
            top = holders.nlargest(1, pct_col).iloc[0]
            pct = top[pct_col]
            name = top[name_col]
            if pct >= 5:
                return 1, f"{name} holds {pct:.1f}%"

        return 0, ""
    except Exception as exc:
        log.debug("Institutional score error for %s: %s", ticker, exc)
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

        if high_52w and current > 0:
            pct_from_high = (high_52w - current) / high_52w * 100
            if pct_from_high <= 3:
                hits += 1
                reasons.append(f"within {pct_from_high:.1f}% of 52wk high ${high_52w:.2f}")

        if vol_ratio >= 5 and pct_1d >= 3:
            hits += 1
            reasons.append(f"vol {vol_ratio:.1f}x avg, +{pct_1d:.1f}% today")

        if ma200 and current > 0 and current > ma200 * 1.01:
            hits += 1
            reasons.append(f"above 200d MA ${ma200:.2f}")

        if hits >= 2:
            return 2, "Breakout: " + ", ".join(reasons[:2])
        if hits == 1:
            return 1, "Breakout: " + reasons[0]

        return 0, ""
    except Exception as exc:
        log.debug("Breakout score error for %s: %s", ticker, exc)
        return 0, ""


# ── Scoring orchestrator ────────────────────────────────────────────────────────

def _score_ticker(ticker: str) -> dict | None:
    data = get_ticker_data(ticker)
    if data is None:
        log.info("Predator: no data for %s — skip", ticker)
        return None

    opts_score, opts_reason     = _score_options(ticker)
    ins_score,  ins_reason      = _score_insider(ticker)
    sq_score,   sq_reason       = _score_short_squeeze(ticker, data)
    cat_score,  cat_reason      = _score_catalyst(ticker)
    inst_score, inst_reason     = _score_institutional(ticker)
    brk_score,  brk_reason      = _score_breakout(ticker, data)

    total = min(opts_score + ins_score + sq_score + cat_score + inst_score + brk_score, 10)

    signals = {
        "options":       {"score": opts_score, "reason": opts_reason},
        "insider":       {"score": ins_score,  "reason": ins_reason},
        "short_squeeze": {"score": sq_score,   "reason": sq_reason},
        "catalyst":      {"score": cat_score,  "reason": cat_reason},
        "institutional": {"score": inst_score, "reason": inst_reason},
        "breakout":      {"score": brk_score,  "reason": brk_reason},
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
    conn = get_connection()
    now_str = datetime.now().isoformat()

    for days, col in ((7, "price_7d_later"), (14, "price_14d_later"), (30, "price_30d_later")):
        cutoff_start = (datetime.now() - timedelta(days=days + 1)).isoformat()
        cutoff_end   = (datetime.now() - timedelta(days=days - 1)).isoformat()
        rows = conn.execute(
            f"SELECT id, ticker, entry_price FROM predator_alerts "
            f"WHERE alert_time BETWEEN ? AND ? AND {col} IS NULL",
            (cutoff_start, cutoff_end),
        ).fetchall()
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
                        outcome = f"WIN +{pct:.1f}%"
                    elif pct <= -9:
                        outcome = f"LOSS {pct:.1f}%"
                    else:
                        outcome = f"HOLD {pct:+.1f}%"
                conn.execute(
                    f"UPDATE predator_alerts SET {col} = ?, outcome = ? WHERE id = ?",
                    (px, outcome, row["id"]),
                )
                conn.commit()
                log.info("Outcome update %s id=%d: %s=%s", row["ticker"], row["id"], col, px)
            except Exception as exc:
                log.warning("Outcome update failed for %s: %s", row["ticker"], exc)

    conn.close()


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
    """Persist a list of scored results to the DB without sending alerts."""
    for r in results:
        _record_alert_passive(r["ticker"], r["score"], r["signals"], r["price"])


# ── Main job ───────────────────────────────────────────────────────────────────

def run_predator():
    log.info("🎯 Predator scan @ %s", datetime.now(EASTERN).strftime("%H:%M"))

    cash = portfolio.get_cash()
    position_size = round(cash * 0.25, 2)

    for ticker in PREDATOR_WATCHLIST:
        if _already_alerted(ticker):
            log.debug("Predator: %s already alerted — skip", ticker)
            continue
        try:
            result = _score_ticker(ticker)
        except Exception:
            log.exception("Predator: unhandled error for %s", ticker)
            continue

        if result is None:
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
        else:
            # Record non-alerting scores so watchlist endpoint can show them
            _record_alert_passive(ticker, score, signals, price)

        time.sleep(1)   # gentle rate-limit between tickers


def _record_alert_passive(ticker: str, score: float, signals: dict, price: float):
    """Record a scored ticker without sending an alert (for watchlist display)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO predator_alerts
            (ticker, score, signals_json, entry_price, stop_price, position_size_cad, alert_time)
        VALUES (?,?,?,?,?,?,?)
        """,
        (ticker, score, json.dumps(signals), price, round(price * 0.91, 2), 0.0,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
