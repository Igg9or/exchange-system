import requests
from datetime import datetime

# Курс доллара ЦБ РФ
def get_usd_rub() -> float:
    """Курс $ в рублях по ЦБ РФ."""
    try:
        r = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=5)
        data = r.json()
        return float(data["Valute"]["USD"]["Value"])
    except Exception:
        # простой безопасный дефолт, чтобы не падать
        return 100.0
def _get_binance_price(symbol: str) -> float | None:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        return float(r.json()["price"])
    except Exception:
        return None

def _get_mexc_price(symbol: str) -> float | None:
    try:
        r = requests.get(
            "https://api.mexc.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        return float(r.json()["price"])
    except Exception:
        return None

def price_rub_for_symbol(symbol: str) -> float | None:
    """
    Возвращает стоимость 1 единицы актива в RUB.
    - RUB → 1
    - USDT → usd_rub
    - Любой другой тикер → (тикер/USDT) * usd_rub (Binance → MEXC как фоллбек)
    """
    symbol = symbol.upper()
    usd_rub = get_usd_rub()

    if symbol == "RUB":
        return 1.0
    if symbol in ("USDT", "USD"):
        return usd_rub

    pair = f"{symbol}USDT"
    px = _get_binance_price(pair) or _get_mexc_price(pair)
    if px is None:
        return None
    return px * usd_rub