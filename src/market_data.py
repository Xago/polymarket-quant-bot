"""Acceso a datos publicos de Polymarket (Gamma API + CLOB API).
Solo lectura, sin autenticacion, sin costo. Compartido por real_data_test.py
y dry_run_loop.py para no duplicar la logica de fetch.
"""

import json
import urllib.request

GAMMA_URL = "https://gamma-api.polymarket.com/markets?closed=false&limit=300&order=volume24hr&ascending=false"
CLOB_BOOK_URL = "https://clob.polymarket.com/book?token_id={}"
CLOB_PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history?market={}&startTs={}&endTs={}&fidelity={}"


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def taker_fee_per_share(price, fee_rate):
    """fee = feeRate * p * (1-p), formula real publicada por Polymarket."""
    return fee_rate * price * (1 - price)


def pick_active_markets(n=12):
    """Mercados activos, con precio no extremo y libro de ordenes habilitado.
    Devuelve (slug, token_id, volume24hr, fee_rate)."""
    data = _get_json(GAMMA_URL)
    picked = []
    for m in data:
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            p0 = float(prices[0])
        except Exception:
            continue
        if 0.1 < p0 < 0.9 and m.get("enableOrderBook"):
            toks = json.loads(m.get("clobTokenIds", "[]"))
            fee_rate = (m.get("feeSchedule") or {}).get("rate", 0.0)
            if toks:
                picked.append((m["slug"], toks[0], m.get("volume24hr", 0), fee_rate))
    picked.sort(key=lambda x: -x[2])
    return picked[:n]


def book_spread(token_id):
    """(best_bid, best_ask, bid_size, ask_size) o None si el libro esta vacio."""
    book = _get_json(CLOB_BOOK_URL.format(token_id))
    bids = sorted(book.get("bids", []), key=lambda x: -float(x["price"]))
    asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))
    if not bids or not asks:
        return None
    best_bid = float(bids[0]["price"])
    best_ask = float(asks[0]["price"])
    return best_bid, best_ask, float(bids[0]["size"]), float(asks[0]["size"])


def price_history(token_id, start_ts, end_ts, fidelity=1):
    """Serie de precio real (minutos de granularidad) entre start_ts y end_ts.
    Se usa para ver si el precio realmente cruzo un nivel, no para operar."""
    url = CLOB_PRICES_HISTORY_URL.format(token_id, int(start_ts), int(end_ts), fidelity)
    data = _get_json(url)
    return [(pt["t"], pt["p"]) for pt in data.get("history", [])]
