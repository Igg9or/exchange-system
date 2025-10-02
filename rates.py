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

# Иконки для активов (маленькие PNG/SVG)
ICON_MAP = {
    # Криптовалюты
    "BTC": "/static/icons/btc.png",
    "ETH": "/static/icons/eth.png",
    "USDT": "https://cryptocurrencyliveprices.com/img/usdt.png",
    "USDC": "https://cryptocurrencyliveprices.com/img/usdc.png",
    "DAI": "https://cryptocurrencyliveprices.com/img/dai.png",
    "BNB": "https://cryptocurrencyliveprices.com/img/bnb.png",
    "TRX": "https://cryptocurrencyliveprices.com/img/trx.png",

    # Валюты
    "RUB": "https://flagcdn.com/w20/ru.png",
    "USD": "https://flagcdn.com/w20/us.png",
    "EUR": "https://flagcdn.com/w20/eu.png",
    "CNY": "https://flagcdn.com/w20/cn.png",

    # Банки
    "SBER_RUB": "/static/icons/sber.png",
    "TINKOFF_RUB": "/static/icons/tinkoff.png",
    "VTB_RUB": "/static/icons/vtb.png",
    "ALFA_RUB": "/static/icons/alfa.png",
    "GAZPROM_RUB": "/static/icons/gazprom.png",
    "RAIFFEISEN_RUB": "/static/icons/raiffeisen.png",
    "PSB_RUB": "/static/icons/psb.png",
    "RNKB_RUB": "/static/icons/rnkb.png",
    "MIR_RUB": "/static/icons/mir.png",
    "VISA_MC_RUB": "/static/icons/visa_mc.png",

    # Платёжки
    "PAYEER_RUB": "/static/icons/payeer.png",
    "PAYEER_USD": "/static/icons/payeer.png",
    "CAPITALIST_RUB": "/static/icons/capitalist.png",
    "CAPITALIST_USD": "/static/icons/capitalist.png",
    "VOLET_RUB": "/static/icons/volet.png",
    "VOLET_USD": "/static/icons/volet.png",
    "VOLET_EUR": "/static/icons/volet.png",
    "MONEYGO_USD": "/static/icons/moneygo.png",

    # Китайские платёжки
    "ALIPAY_CNY": "/static/icons/alipay.png",
    "WECHAT_CNY": "/static/icons/wechat.png",
}

NAME_MAP = {
    # Крипта
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "USDT": "Tether",
    "USDC": "USD Coin",
    "DAI": "Dai",
    "BNB": "Binance Coin",
    "TRX": "TRON",

    # Валюты
    "RUB": "Российский рубль",
    "USD": "Доллар США",
    "EUR": "Евро",
    "CNY": "Юань",

    # Банки
    "SBER_RUB": "Сбербанк",
    "TINKOFF_RUB": "Тинькофф",
    "VTB_RUB": "ВТБ",
    "ALFA_RUB": "Альфа-Банк",
    "MIR_RUB": "Карта «Мир»",
    "VISA_MC_RUB": "Visa / MasterCard",
    "ANYBANK_RUB": "Любой банк",
    "ALLBANKS_RUB": "Все банки",
    "OZON_RUB": "Ozon",
    "SBP_RUB": "СБП",
    "HOME_RUB": "Home Credit",
    "GAZPROM_RUB": "Газпромбанк",
    "RAIFFEISEN_RUB": "Райффайзенбанк",
    "PSB_RUB": "ПСБ",
    "RNKB_RUB": "РНКБ",
    "CASH_RUB": "Наличные рубли",

    # Платежки
    "PAYEER_RUB": "Payeer (₽)",
    "PAYEER_USD": "Payeer ($)",
    "CAPITALIST_RUB": "Capitalist (₽)",
    "CAPITALIST_USD": "Capitalist ($)",
    "VOLET_RUB": "Volet (₽)",
    "VOLET_USD": "Volet ($)",
    "VOLET_EUR": "Volet (€)",
    "MONEYGO_USD": "MoneyGO ($)",

    # Китай
    "ALIPAY_CNY": "Alipay",
    "WECHAT_CNY": "WeChat Pay",
}




def price_rub_for_symbol(symbol: str) -> float:
    """Вернуть цену актива в рублях."""
    symbol = symbol.upper()
    symbol = ALIAS.get(symbol, symbol)  # алиасы (ANYBANK_RUB → RUB и т.д.)

    # 1. RUB всегда равен 1
    if symbol == "RUB":
        return 1.0

    # 2. Доллар (USD) → Binance → MEXC → ЦБ РФ
    # 2. Доллар всегда по ЦБ РФ
    if symbol == "USD":
        return get_usd_rub()

    # 3. Стейблы (USDT, USDC) = доллар по ЦБ
    if symbol in ("USDT", "USDC"):
        return price_rub_for_symbol("USD")

    # 4. Евро
    if symbol == "EUR":
        px = _get_binance_price("EURUSDT") or _get_mexc_price("EURUSDT")
        if px:
            return float(px) * price_rub_for_symbol("USDT")

    # 5. Юань
    if symbol == "CNY":
        px = _get_binance_price("CNYUSDT") or _get_mexc_price("CNYUSDT")
        if px:
            return float(px) * price_rub_for_symbol("USDT")

    # 6. Остальные криптовалюты → <SYMBOL>USDT * курс USDT/RUB
    pair = f"{symbol}USDT"
    px = _get_binance_price(pair) or _get_mexc_price(pair)
    if not px:
        raise ValueError(f"Нет курса для {symbol}")
    return float(px) * price_rub_for_symbol("USDT")
