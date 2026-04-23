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


def add_or_update_holding(ticker: str, shares: float, price: float) -> dict:
    """
    Adds a new position or updates avg cost via weighted average.
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
        new_avg    = round((old_shares * old_cost + shares * price) / new_shares, 4)
        conn.execute(
            "UPDATE holdings SET shares = ?, avg_cost = ? WHERE ticker = ?",
            (new_shares, new_avg, ticker),
        )
        result = {"ticker": ticker, "shares": new_shares, "avg_cost": new_avg}
    else:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES (?,?,?,?)",
            (ticker, shares, price, now),
        )
        result = {"ticker": ticker, "shares": shares, "avg_cost": price}

    conn.execute(
        "INSERT INTO transactions (ticker, action, shares, price, date) VALUES (?,?,?,?,?)",
        (ticker, "BUY", shares, price, now),
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

    conn.execute(
        "INSERT INTO transactions (ticker, action, shares, price, date, gain_loss) VALUES (?,?,?,?,?,?)",
        (ticker, "SELL", shares, price, now, gain_loss),
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

def get_portfolio_with_prices() -> list[dict]:
    holdings = get_holdings()
    enriched = []
    for h in holdings:
        try:
            price = yf.Ticker(h["ticker"]).history(period="1d")["Close"].iloc[-1]
            price = round(float(price), 2)
        except Exception:
            price = h["avg_cost"]

        cost_basis  = round(h["shares"] * h["avg_cost"], 2)
        curr_value  = round(h["shares"] * price, 2)
        gain        = round(curr_value - cost_basis, 2)
        gain_pct    = round((price / h["avg_cost"] - 1) * 100, 2)

        enriched.append({
            **h,
            "current_price": price,
            "curr_value":    curr_value,
            "gain":          gain,
            "gain_pct":      gain_pct,
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
            lines.append(
                f"  {r['ticker']:<10} {r['shares']} sh  ${r['current_price']:.2f}  "
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
