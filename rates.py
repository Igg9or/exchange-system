import requests
from datetime import datetime

# Курс доллара ЦБ РФ
def get_usd_rub():
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        data = requests.get(url, timeout=5).json()
        return data["Valute"]["USD"]["Value"]  # float
    except Exception as e:
        print("Ошибка при получении курса ЦБ:", e)
        return None

# Курс крипты с Binance (пример: BTC/USDT)
def get_binance_price(symbol="BTCUSDT"):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        data = requests.get(url, timeout=5).json()
        return float(data["price"])
    except Exception as e:
        print("Ошибка при получении курса Binance:", e)
        return None

# Курс крипты с MEXC (пример: BTC/USDT)
def get_mexc_price(symbol="BTCUSDT"):
    url = f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol}"
    try:
        data = requests.get(url, timeout=5).json()
        return float(data["price"])
    except Exception as e:
        print("Ошибка при получении курса MEXC:", e)
        return None

# Универсальная функция: получить курс актива в RUB
def get_asset_price(symbol="BTC"):
    usd_rub = get_usd_rub() or 100  # fallback 100, если ЦБ не ответил
    crypto_price = get_binance_price(symbol + "USDT")  # или get_mexc_price()
    if crypto_price:
        return crypto_price * usd_rub
    return None
