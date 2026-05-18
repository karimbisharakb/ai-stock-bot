"""
Phase A3 — Alpha discovery universe.

Broader ticker list beyond the Predator watchlist, scanned in shadow mode
for opportunity discovery. No alerts. Feature-flagged via ALPHA_SHADOW_ENABLED.
"""
import logging
import time

log = logging.getLogger(__name__)

ALPHA_UNIVERSE: list[str] = [
    # High-beta tech
    "TSLA", "AMD", "SMCI", "MSTR",
    # AI / Semis
    "ARM", "AVGO", "MRVL", "MU", "ON", "KLAC", "LRCX", "ALAB",
    # Crypto miners / digital assets
    "RIOT", "MARA", "CLSK", "CORZ", "CIFR",
    # Quantum computing
    "IONQ", "RGTI", "QUBT", "QBTS",
    # High-momentum growth
    "CAVA", "DUOL", "APP", "HIMS", "RKLB", "LUNR", "ACHR", "JOBY",
    "RXRX", "CELH",
    # Fintech / consumer growth
    "HOOD", "SOFI", "NU", "AFRM", "UPST", "COIN",
    # US mega-cap growth (also in Predator — cross-scored for alpha tier)
    "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOG", "PLTR", "TSM",
    # Canadian additions
    "SHOP.TO", "WELL.TO", "IMG.TO",
]


def get_alpha_universe() -> list[str]:
    return list(ALPHA_UNIVERSE)


def scan_alpha_universe() -> int:
    """Shadow-score every ticker in ALPHA_UNIVERSE.

    Persists results to alpha_shadow_log. No alerts sent.
    Feature-flagged behind ALPHA_SHADOW_ENABLED.
    Returns count of tickers successfully scored.
    """
    from feature_flags import alpha_shadow_enabled
    from alpha_shadow import get_shadow_manager

    if not alpha_shadow_enabled():
        log.info("alpha_universe: scan skipped — ALPHA_SHADOW_ENABLED is off")
        return 0

    mgr = get_shadow_manager()
    scored = 0
    universe = get_alpha_universe()
    log.info("alpha_universe: starting scan of %d tickers", len(universe))

    for ticker in universe:
        try:
            result = mgr.run_shadow_score(ticker, {})
            if result is not None:
                scored += 1
        except Exception:
            log.warning("alpha_universe: error scoring %s (non-fatal)", ticker, exc_info=True)
        time.sleep(0.5)

    log.info("alpha_universe: scan complete — %d/%d scored", scored, len(universe))
    return scored
