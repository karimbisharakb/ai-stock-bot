"""
Portfolio CRUD — SQLite-backed.
All monetary values stored in native currency (CAD or USD as entered).
"""
import yfinance as yf
from datetime import datetime
from database import get_connection


# ──────────────────────────────────────────────
# Holdings
# ──────────────────────────────────────────────

def get_holdings() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM holdings ORDER BY ticker").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_or_update_holding(
    ticker: str,
    shares: float,
    price_cad: float,
    notes: str = None,
) -> dict:
    """
    Adds a new position or updates avg cost via weighted average.
    price_cad must already be in CAD (including any FX conversion + fees).
    Returns the updated holding dict.
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM holdings WHERE ticker = ?", (ticker,)
    ).fetchone()

    now = datetime.now().isoformat()
    if existing:
        old_shares = existing["shares"]
        old_cost   = existing["avg_cost"]
        new_shares = old_shares + shares
        new_avg    = round((old_shares * old_cost + shares * price_cad) / new_shares, 4)
        conn.execute(
            "UPDATE holdings SET shares = ?, avg_cost = ? WHERE ticker = ?",
            (new_shares, new_avg, ticker),
        )
        result = {"ticker": ticker, "shares": new_shares, "avg_cost": new_avg}
    else:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES (?,?,?,?)",
            (ticker, shares, price_cad, now),
        )
        result = {"ticker": ticker, "shares": shares, "avg_cost": price_cad}

    total_cad = round(shares * price_cad, 4)
    conn.execute(
        "INSERT INTO transactions (ticker, type, shares, price_cad, total_cad, date, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (ticker, "BUY", shares, price_cad, total_cad, now, notes),
    )
    conn.commit()
    conn.close()
    return result


def add_dividend(ticker: str, shares: float, price_cad: float) -> dict:
    """
    Records a dividend reinvestment and updates the holding.
    Returns the updated holding dict.
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM holdings WHERE ticker = ?", (ticker,)
    ).fetchone()

    now = datetime.now().isoformat()
    total_cad = round(shares * price_cad, 4)

    if existing:
        old_shares = existing["shares"]
        old_cost   = existing["avg_cost"]
        new_shares = old_shares + shares
        new_avg    = round((old_shares * old_cost + shares * price_cad) / new_shares, 4)
        conn.execute(
            "UPDATE holdings SET shares = ?, avg_cost = ? WHERE ticker = ?",
            (new_shares, new_avg, ticker),
        )
        result = {"ticker": ticker, "shares": new_shares, "avg_cost": new_avg}
    else:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES (?,?,?,?)",
            (ticker, shares, price_cad, now),
        )
        result = {"ticker": ticker, "shares": shares, "avg_cost": price_cad}

    conn.execute(
        "INSERT INTO transactions (ticker, type, shares, price_cad, total_cad, date, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (ticker, "DIVIDEND", shares, price_cad, total_cad, now, "Dividend reinvestment"),
    )
    conn.commit()
    conn.close()
    return result


def reduce_or_remove_holding(ticker: str, shares: float, price: float) -> dict:
    """
    Removes shares from holding, calculates gain/loss.
    Returns summary dict.
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM holdings WHERE ticker = ?", (ticker,)
    ).fetchone()

    if not existing:
        conn.close()
        return {"error": f"No holding found for {ticker}"}

    avg_cost   = existing["avg_cost"]
    old_shares = existing["shares"]
    gain_loss  = round((price - avg_cost) * shares, 2)
    pct        = round((price / avg_cost - 1) * 100, 2)
    proceeds   = round(price * shares, 2)
    now        = datetime.now().isoformat()

    new_shares = old_shares - shares
    if new_shares <= 0.0001:
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))
    else:
        conn.execute(
            "UPDATE holdings SET shares = ? WHERE ticker = ?",
            (round(new_shares, 6), ticker),
        )

    total_cad = round(price * shares, 4)
    sign = "+" if gain_loss >= 0 else ""
    sell_notes = f"Gain: {sign}${gain_loss:.2f} ({sign}{pct:.1f}%)"
    conn.execute(
        "INSERT INTO transactions (ticker, type, shares, price_cad, total_cad, date, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (ticker, "SELL", shares, price, total_cad, now, sell_notes),
    )

    # Free up cash
    cash_row = conn.execute("SELECT available_cash FROM cash WHERE id = 1").fetchone()
    new_cash = (cash_row["available_cash"] if cash_row else 0) + proceeds
    conn.execute("UPDATE cash SET available_cash = ? WHERE id = 1", (round(new_cash, 2),))

    conn.commit()
    conn.close()
    return {
        "ticker":    ticker,
        "shares":    shares,
        "price":     price,
        "avg_cost":  avg_cost,
        "gain_loss": gain_loss,
        "pct":       pct,
        "proceeds":  proceeds,
    }


# ──────────────────────────────────────────────
# Cash & TFSA room
# ──────────────────────────────────────────────

def get_cash() -> float:
    conn = get_connection()
    row = conn.execute("SELECT available_cash FROM cash WHERE id = 1").fetchone()
    conn.close()
    return row["available_cash"] if row else 0.0


def set_cash(amount: float):
    conn = get_connection()
    conn.execute("UPDATE cash SET available_cash = ? WHERE id = 1", (round(amount, 2),))
    conn.commit()
    conn.close()


def set_cash_exact(amount: float):
    """Overwrite cash balance to exactly amount, ignoring transaction history."""
    set_cash(amount)


def add_cash(amount: float):
    current = get_cash()
    set_cash(current + amount)


def get_tfsa_room() -> float:
    conn = get_connection()
    row = conn.execute("SELECT contribution_room FROM tfsa_info WHERE id = 1").fetchone()
    conn.close()
    return row["contribution_room"] if row else 0.0


def set_tfsa_room(amount: float):
    conn = get_connection()
    conn.execute(
        "UPDATE tfsa_info SET contribution_room = ?, last_updated = ? WHERE id = 1",
        (round(amount, 2), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# Live portfolio value
# ──────────────────────────────────────────────

def _avg_cost_from_transactions(ticker: str) -> tuple[float, float]:
    """
    Recomputes (shares, avg_cost_cad) from full transaction history.
    BUY and DIVIDEND add shares at price_cad; SELL reduces at running avg.
    """
    conn = get_connection()
    txns = conn.execute(
        "SELECT type, shares, price_cad FROM transactions WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    conn.close()

    total_shares = 0.0
    total_cost   = 0.0
    for t in txns:
        if t["type"] in ("BUY", "DIVIDEND"):
            total_shares += t["shares"]
            total_cost   += t["shares"] * t["price_cad"]
        elif t["type"] == "SELL" and total_shares > 0:
            avg = total_cost / total_shares
            sold = min(t["shares"], total_shares)
            total_cost   -= sold * avg
            total_shares -= sold

    total_shares = max(0.0, round(total_shares, 6))
    avg_cost = round(total_cost / total_shares, 4) if total_shares > 0 else 0.0
    return total_shares, avg_cost


def get_portfolio_with_prices() -> list[dict]:
    holdings = get_holdings()
    from strategy import get_usd_cad_rate
    usdcad = get_usd_cad_rate()

    enriched = []
    for h in holdings:
        is_usd = not h["ticker"].endswith(".TO")

        try:
            price_native = yf.Ticker(h["ticker"]).history(period="1d")["Close"].iloc[-1]
            price_native = round(float(price_native), 2)
        except Exception:
            price_native = h["avg_cost"]

        # Convert to CAD for all value calculations; avg_cost is always stored in CAD
        price_cad = round(price_native * usdcad, 2) if is_usd else price_native

        # Recompute shares and avg cost from transaction history for accuracy
        txn_shares, avg_cost = _avg_cost_from_transactions(h["ticker"])
        shares = txn_shares if txn_shares > 0 else h["shares"]
        if avg_cost == 0:
            avg_cost = h["avg_cost"]

        cost_basis = round(shares * avg_cost, 2)
        curr_value = round(shares * price_cad, 2)
        gain       = round(curr_value - cost_basis, 2)
        gain_pct   = round((price_cad / avg_cost - 1) * 100, 2) if avg_cost > 0 else 0.0

        enriched.append({
            **h,
            "shares":            shares,
            "avg_cost":          avg_cost,
            "current_price":     price_native,  # native currency (USD or CAD)
            "current_price_cad": price_cad,     # always CAD
            "is_usd":            is_usd,
            "curr_value":        curr_value,    # always CAD
            "gain":              gain,          # always CAD
            "gain_pct":          gain_pct,
        })
    return enriched


def format_portfolio_summary() -> str:
    rows    = get_portfolio_with_prices()
    cash    = get_cash()
    room    = get_tfsa_room()

    if not rows:
        total_value = cash
        body = "  (no positions yet)"
    else:
        lines = []
        total_value = cash
        for r in rows:
            total_value += r["curr_value"]
            sign = "+" if r["gain"] >= 0 else ""
            price_str = (
                f"${r['current_price']:.2f}USD→${r['current_price_cad']:.2f}"
                if r["is_usd"]
                else f"${r['current_price']:.2f}"
            )
            lines.append(
                f"  {r['ticker']:<10} {r['shares']} sh  {price_str}  "
                f"{sign}${r['gain']:.2f} ({sign}{r['gain_pct']:.1f}%)"
            )
        body = "\n".join(lines)

    total_gain = sum(r["gain"] for r in rows) if rows else 0
    total_cost = sum(r["shares"] * r["avg_cost"] for r in rows) if rows else 0
    total_pct  = round((total_gain / total_cost * 100), 2) if total_cost > 0 else 0
    sign = "+" if total_gain >= 0 else ""

    return (
        "----------------------------------------\n"
        "📊 MY TFSA PORTFOLIO\n\n"
        f"{body}\n\n"
        f"Total Value:    ${total_value:,.2f}\n"
        f"Total Gain:     {sign}${total_gain:,.2f} ({sign}{total_pct:.1f}%)\n"
        f"Cash:           ${cash:,.2f}\n"
        f"TFSA Room Left: ${room:,.2f}\n"
        "----------------------------------------"
    )
