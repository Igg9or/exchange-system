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


# Маппинг "дублирующих" активов → базовые
ALIAS = {
    # Банки и фиат
    "ANYBANK_RUB": "RUB",
    "ALLBANKS_RUB": "RUB",
    "TINKOFF_QR_RUB": "RUB",
    "SBER_QR_RUB": "RUB",
    "OZON_RUB": "RUB",
    "SBER_RUB": "RUB",
    "TINKOFF_RUB": "RUB",
    "ALFA_RUB": "RUB",
    "SBP_RUB": "RUB",
    "VISA_MC_RUB": "RUB",
    "MIR_RUB": "RUB",
    "HOME_RUB": "RUB",
    "GAZPROM_RUB": "RUB",
    "RAIFFEISEN_RUB": "RUB",
    "PSB_RUB": "RUB",
    "VTB_RUB": "RUB",
    "RNKB_RUB": "RUB",
    "CASH_RUB": "RUB",

    # Платёжки
    "VOLET_RUB": "RUB",
    "VOLET_USD": "USD",
    "VOLET_EUR": "EUR",
    "PAYEER_RUB": "RUB",
    "PAYEER_USD": "USD",
    "CAPITALIST_RUB": "RUB",
    "CAPITALIST_USD": "USD",
    "MONEYGO_USD": "USD",

    # CNY
    "ALIPAY_CNY": "CNY",
    "WECHAT_CNY": "CNY",

    # USDT-варианты (все сети сводим в один баланс)
    "TETHER_TRC20": "USDT",
    "TETHER_ERC20": "USDT",
    "TETHER_BEP20": "USDT",
    "TETHER_TON": "USDT",
    "TETHER_POLYGON": "USDT",
    "TETHER_SOL": "USDT",
    "TETHER_ARBITRUM": "USDT",
    "TETHER_OPTIMISM": "USDT",

    # USDC-варианты
    "USDC_ERC20": "USDC",
    "USDC_BEP20": "USDC",
    "DAI": "USDC",
}

def price_rub_for_symbol(symbol: str) -> float:
    symbol = symbol.upper()
    symbol = ALIAS.get(symbol, symbol)  # ✅ заменяем на базовый актив

    # RUB всегда = 1
    if symbol == "RUB":
        return 1.0

    # USD/EUR/CNY можно получать через пары к USDT
    if symbol == "USD":
        return _get_binance_price("USDTTRY") and _get_binance_price("USDTRUB")
    if symbol == "EUR":
        return _get_binance_price("EURUSDT") * price_rub_for_symbol("USDT")
    if symbol == "CNY":
        return _get_binance_price("CNYUSDT") * price_rub_for_symbol("USDT")

    # Стейблы считаем как доллар
    if symbol in ("USDT", "USDC"):
        return price_rub_for_symbol("USD")

    # Криптовалюты → пробуем Binance, потом MEXC
    pair = f"{symbol}USDT"
    px = _get_binance_price(pair) or _get_mexc_price(pair)
    if not px:
        raise ValueError(f"Нет курса для {symbol}")
    return float(px) * price_rub_for_symbol("USD")