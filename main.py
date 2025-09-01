from datetime import datetime
from db import engine, SessionLocal, Base
from models import Service, Asset, Balance, Shift, Order, BalanceHistory, User
from collections import defaultdict

# --- Утилиты ---
def init_db():
    Base.metadata.create_all(engine)


def get_or_create_asset(db, symbol: str, name: str):
    asset = db.query(Asset).filter(Asset.symbol == symbol).first()
    if not asset:
        asset = Asset(symbol=symbol, name=name)
        db.add(asset)
        db.commit()
        db.refresh(asset)
        print(f"Добавлен актив {symbol}")
    return asset


def get_or_create_service(db, name: str):
    service = db.query(Service).filter(Service.name == name).first()
    if not service:
        service = Service(name=name)
        db.add(service)
        db.commit()
        db.refresh(service)
        print(f"Добавлен сервис {name}")
    return service


def get_or_create_user(db, login: str, service_id: int, role="operator"):
    user = db.query(User).filter(User.login == login).first()
    if not user:
        user = User(login=login, password_hash="123", role=role, service_id=service_id)
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Добавлен пользователь {login}")
    return user


# --- Смены ---
def start_shift(db, service_id: int):
    existing = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).first()
    if existing:
        print(f"В сервисе {service_id} уже есть открытая смена {existing.id}")
        return existing

    shift = Shift(service_id=service_id, start_time=datetime.utcnow())
    db.add(shift)
    db.commit()
    db.refresh(shift)
    print(f"Смена {shift.id} начата в сервисе {service_id}")
    return shift


def end_shift(db, service_id: int):
    shift = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).first()
    if not shift:
        print("Открытая смена не найдена")
        return
    shift.end_time = datetime.utcnow()
    db.commit()
    print(f"Смена {shift.id} завершена в сервисе {service_id}")
    return shift


# --- Заявки ---
def create_order(db, service_id: int, user_id: int,
                 received_asset_id: int, received_amount: float,
                 given_asset_id: int, given_amount: float,
                 comment: str = "", is_manual=True, rates: dict = None):

    shift = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).first()
    if not shift:
        raise Exception("Нет активной смены для сервиса!")

    order = Order(
        service_id=service_id,
        user_id=user_id,
        shift_id=shift.id,
        type="order",
        is_manual=is_manual,
        received_asset_id=received_asset_id,
        received_amount=received_amount,
        given_asset_id=given_asset_id,
        given_amount=given_amount,
        comment=comment,
        rate_at_execution=rates or {},  # фиксируем курс
        profit_percent=calc_profit(received_amount, given_amount)
    )
    db.add(order)

    update_balance(db, service_id, received_asset_id, received_amount)
    update_balance(db, service_id, given_asset_id, -given_amount)

    db.commit()
    db.refresh(order)
    print(f"Заявка {order.id} создана оператором {user_id} в сервисе {service_id}")
    return order


# --- Админские операции ---
def admin_change_balance(db, service_id: int, asset_id: int, amount: float, action_type: str, comment: str = ""):
    if action_type == "withdraw":
        change = -abs(amount)
    elif action_type == "deposit":
        change = abs(amount)
    else:
        raise ValueError("action_type должен быть 'deposit' или 'withdraw'")

    order = Order(
        service_id=service_id,
        user_id=None,
        shift_id=None,
        type="admin_action",
        is_manual=True,
        received_asset_id=None,
        received_amount=0.0,
        given_asset_id=asset_id if change < 0 else None,
        given_amount=abs(change) if change < 0 else 0.0,
        comment=comment,
        rate_at_execution={},
        profit_percent=0
    )
    db.add(order)

    update_balance(db, service_id, asset_id, change)

    db.commit()
    db.refresh(order)
    print(f"Админская операция: {action_type} {amount} {asset_id} в сервис {service_id}")
    return order


# --- Переводы между сервисами ---
def internal_transfer(db, from_service_id: int, to_service_id: int, asset_id: int, amount: float, user_id: int, comment: str = ""):
    if amount <= 0:
        raise ValueError("Сумма перевода должна быть положительной")

    # если комментарий пустой → автогенерация
    comment = comment or f"Перевод {amount} {asset_id} из сервиса {from_service_id} → {to_service_id}"

    order = Order(
        service_id=from_service_id,
        user_id=user_id,
        shift_id=None,
        type="internal_transfer",
        is_manual=True,
        received_asset_id=None,
        received_amount=0.0,
        given_asset_id=asset_id,
        given_amount=amount,
        comment=comment,
        rate_at_execution={},
        profit_percent=0
    )
    db.add(order)

    update_balance(db, from_service_id, asset_id, -amount)
    update_balance(db, to_service_id, asset_id, amount)

    db.commit()
    db.refresh(order)
    print(f"Перевод: {amount} {asset_id} из сервиса {from_service_id} → {to_service_id}")
    return order


# --- Балансы ---
def update_balance(db, service_id: int, asset_id: int, change: float):
    balance = db.query(Balance).filter(
        Balance.service_id == service_id,
        Balance.asset_id == asset_id
    ).first()

    old_amount = balance.amount if balance else 0.0
    new_amount = old_amount + change

    if not balance:
        balance = Balance(service_id=service_id, asset_id=asset_id, amount=new_amount)
        db.add(balance)
    else:
        balance.amount = new_amount

    history = BalanceHistory(
        service_id=service_id,
        asset_id=asset_id,
        order_id=None,
        old_amount=old_amount,
        new_amount=new_amount,
        change=change
    )
    db.add(history)


def calc_profit(received: float, given: float) -> float:
    if given == 0:
        return 0
    return round(((received - given) / given) * 100, 2)


def get_shift_report(db, service_id: int):
    shift = db.query(Shift).filter(
        Shift.service_id == service_id
    ).order_by(Shift.start_time.desc()).first()

    if not shift:
        print("Смена не найдена")
        return

    print(f"\n--- Отчёт по смене {shift.id} (сервис {service_id}) ---")
    print(f"Начало: {shift.start_time}, Конец: {shift.end_time or 'ещё открыта'}")

    orders = db.query(Order).filter(Order.shift_id == shift.id).all()
    if not orders:
        print("Заявок в смене нет")
        return

    from collections import defaultdict
    totals = defaultdict(float)
    total_profit_percent = 0
    total_profit_rub = 0

    for o in orders:
        recv_asset = db.query(Asset).get(o.received_asset_id) if o.received_asset_id else None
        give_asset = db.query(Asset).get(o.given_asset_id) if o.given_asset_id else None
        user = db.query(User).get(o.user_id) if o.user_id else None

        print(f"\nЗаявка {o.id} | Оператор: {user.login if user else 'система'} | Тип: {o.type}")
        print(f"Получили: {o.received_amount} {recv_asset.symbol if recv_asset else '-'}")
        print(f"Отдали: {o.given_amount} {give_asset.symbol if give_asset else '-'}")
        print(f"Комментарий: {o.comment}")
        print(f"Прибыль %: {o.profit_percent}")

        if o.rate_at_execution:
            print(f"Курсы при создании: {o.rate_at_execution}")
            if "RUB" in o.rate_at_execution:
                recv_value = o.received_amount * o.rate_at_execution.get("RUB", 0)
                give_value = o.given_amount * o.rate_at_execution.get("RUB", 0)
                total_profit_rub += recv_value - give_value

        if recv_asset:
            totals[recv_asset.symbol] += o.received_amount
        if give_asset:
            totals[give_asset.symbol] -= o.given_amount

        total_profit_percent += o.profit_percent

    print("\n--- Итог по активам ---")
    for asset, value in totals.items():
        print(f"{asset}: {value}")

    print(f"\nИтоговая прибыль (сумма %): {round(total_profit_percent, 2)}%")
    print(f"Итоговая прибыль в рублях (если указан курс): {round(total_profit_rub, 2)} RUB")


# --- Тестовый сценарий ---
if __name__ == "__main__":
    init_db()
    db = SessionLocal()

    # 1. Гарантируем, что сервисы есть
    service1 = get_or_create_service(db, "Сервис 1")
    service2 = get_or_create_service(db, "Сервис 2")

    # 2. Гарантируем, что активы есть
    btc = get_or_create_asset(db, "BTC", "Bitcoin")
    eth = get_or_create_asset(db, "ETH", "Ethereum")
    usdt = get_or_create_asset(db, "USDT", "Tether")
    rub = get_or_create_asset(db, "RUB", "Российский рубль")
    usd = get_or_create_asset(db, "USD", "Доллар США")

    # 3. Создаём пользователей
    user1 = get_or_create_user(db, "operator1", service1.id)
    user2 = get_or_create_user(db, "operator2", service2.id)

    # 4. Начинаем смену для сервиса 1
    shift = start_shift(db, service1.id)

    # 5. Создаём заявку
    create_order(db, service1.id, user1.id,
             received_asset_id=btc.id, received_amount=1.0,
             given_asset_id=rub.id, given_amount=100000,
             comment="Тестовая заявка",
             rates={"RUB": 5500000})  # курс BTC в рублях

    # 6. Админская операция
    admin_change_balance(db, service1.id, rub.id, 500000, action_type="deposit", comment="Пополнение кассы")

    # 7. Перевод между сервисами
    internal_transfer(db, service1.id, service2.id, btc.id, 0.5, user1.id, comment="Перегон BTC")

    # 8. Закрываем смену
    end_shift(db, service1.id)

    # 9. Отчёт по смене
    get_shift_report(db, service1.id)

    # 9. Проверяем балансы
    balances = db.query(Balance).all()
    for b in balances:
        asset = db.query(Asset).get(b.asset_id)
        print(f"Service {b.service_id}, Asset {asset.symbol}, Amount {b.amount}")
