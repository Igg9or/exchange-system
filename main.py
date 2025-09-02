from flask import Flask, render_template, redirect, url_for, request
from db import SessionLocal, init_db
from models import Service, Asset, Balance, Shift, Order, User, BalanceHistory
from datetime import datetime
from sqlalchemy.orm import Session
from collections import defaultdict
from flask import Flask, render_template, redirect, url_for, request, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "super_secret_key_123"
init_db()


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def get_or_create_service(db: Session, name: str):
    service = db.query(Service).filter(Service.name == name).first()
    if not service:
        service = Service(name=name)
        db.add(service)
        db.commit()
        db.refresh(service)
    return service


def get_or_create_asset(db: Session, symbol: str, name: str):
    asset = db.query(Asset).filter(Asset.symbol == symbol).first()
    if not asset:
        asset = Asset(symbol=symbol, name=name)
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def get_or_create_user(db: Session, login: str, service_id: int, role="operator"):
    user = db.query(User).filter(User.login == login).first()
    if not user:
        user = User(login=login, password_hash="123", role=role, service_id=service_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def update_balance(db: Session, service_id: int, asset_id: int, change: float):
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


# ===== СМЕНЫ =====

def start_shift(db: Session, service_id: int):
    existing = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).first()
    if existing:
        return existing
    shift = Shift(service_id=service_id, start_time=datetime.utcnow())
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


def end_shift(db: Session, service_id: int):
    shift = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).first()
    if not shift:
        return None
    shift.end_time = datetime.utcnow()
    db.commit()
    return shift


# ===== ЗАЯВКИ =====

def create_order(db: Session, service_id: int, user_id: int,
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
        rate_at_execution=rates or {},
        profit_percent=calc_profit(received_amount, given_amount)
    )
    db.add(order)

    update_balance(db, service_id, received_asset_id, received_amount)
    update_balance(db, service_id, given_asset_id, -given_amount)

    db.commit()
    db.refresh(order)
    return order


# ===== АДМИНСКИЕ ОПЕРАЦИИ =====

def admin_change_balance(db: Session, service_id: int, asset_id: int, amount: float, action_type: str, comment: str = ""):
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
    return order


# ===== ВНУТРЕННИЕ ПЕРЕВОДЫ =====

def internal_transfer(db: Session, from_service_id: int, to_service_id: int, asset_id: int, amount: float, user_id: int, comment: str = ""):
    if amount <= 0:
        raise ValueError("Сумма перевода должна быть положительной")

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
    return order


# ===== ОТЧЁТ =====

def get_shift_report(db: Session, service_id: int):
    shift = db.query(Shift).filter(
        Shift.service_id == service_id
    ).order_by(Shift.start_time.desc()).first()

    if not shift:
        return {"error": "Смена не найдена"}

    orders = db.query(Order).filter(Order.shift_id == shift.id).all()

    totals = defaultdict(float)
    total_profit_rub = 0
    details = []

    for o in orders:
        recv_asset = db.query(Asset).get(o.received_asset_id) if o.received_asset_id else None
        give_asset = db.query(Asset).get(o.given_asset_id) if o.given_asset_id else None
        user = db.query(User).get(o.user_id) if o.user_id else None

        details.append({
            "id": o.id,
            "user": user.login if user else "система",
            "type": o.type,
            "recv": f"{o.received_amount} {recv_asset.symbol if recv_asset else '-'}",
            "give": f"{o.given_amount} {give_asset.symbol if give_asset else '-'}",
            "comment": o.comment,
            "profit_percent": o.profit_percent
        })

        if recv_asset:
            totals[recv_asset.symbol] += o.received_amount
        if give_asset:
            totals[give_asset.symbol] -= o.given_amount

    return {
        "shift_id": shift.id,
        "start": str(shift.start_time),
        "end": str(shift.end_time) if shift.end_time else None,
        "orders": details,
        "totals": dict(totals),
        "total_profit_rub": total_profit_rub
    }


# ===== FLASK ROUTES =====

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    user = db.query(User).get(session["user_id"])

    # сервис по умолчанию
    service_id = request.args.get("service_id", type=int)
    if user.role == "admin":
        # админ видит все сервисы
        if service_id:
            service = db.query(Service).get(service_id)
        else:
            service = db.query(Service).first()
        services = db.query(Service).all()
    else:
        # оператор работает только со своим сервисом
        service = db.query(Service).get(user.service_id)
        # ⚡ исправляем: оператору отдаём все сервисы (для "куда"),
        # но в "откуда" в шаблоне останется только его
        services = db.query(Service).all()

    # пагинация
    page = int(request.args.get("page", 1))
    per_page = 10
    offset = (page - 1) * per_page

    balances, orders, assets = [], [], []
    if service:
        balances = (
            db.query(Balance, Asset)
            .join(Asset, Balance.asset_id == Asset.id)
            .filter(Balance.service_id == service.id)
            .all()
        )
        orders = (
            db.query(Order)
            .filter(Order.service_id == service.id)
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(per_page + 1)
            .all()
        )
        assets = db.query(Asset).all()

    db.close()
    has_next = len(orders) > per_page
    orders = orders[:per_page]

    return render_template(
        "index.html",
        service=service,
        balances=balances,
        orders=orders,
        page=page,
        has_next=has_next,
        assets=assets,
        services=services,   # 👈 вот это
        user=user            # чтобы в шаблоне можно было проверить роль
    )

@app.route("/shift/start/<int:service_id>")
def shift_start(service_id):
    db = SessionLocal()
    start_shift(db, service_id)
    db.close()
    return redirect(url_for("index"))


@app.route("/shift/end/<int:service_id>")
def shift_end(service_id):
    db = SessionLocal()
    end_shift(db, service_id)
    db.close()
    return redirect(url_for("index"))


@app.route("/shift/report/<int:service_id>")
def shift_report(service_id):
    db = SessionLocal()
    report = get_shift_report(db, service_id)
    db.close()
    return report

@app.route("/order/add", methods=["POST"])
def add_order():
    db = SessionLocal()
    service_id = int(request.form["service_id"])
    user_id = int(request.form["user_id"])
    received_asset_id = int(request.form["received_asset_id"])
    received_amount = float(request.form["received_amount"])
    given_asset_id = int(request.form["given_asset_id"])
    given_amount = float(request.form["given_amount"])
    comment = request.form.get("comment", "")

    # создаём заявку
    create_order(
        db,
        service_id=service_id,
        user_id=user_id,
        received_asset_id=received_asset_id,
        received_amount=received_amount,
        given_asset_id=given_asset_id,
        given_amount=given_amount,
        comment=comment,
    )

    db.close()
    return redirect(url_for("index"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login = request.form["login"]
        password = request.form["password"]

        db = SessionLocal()
        user = db.query(User).filter(User.login == login).first()
        db.close()

        if user and (user.password_hash == password or check_password_hash(user.password_hash, password)):
            session["user_id"] = user.id
            session["role"] = user.role
            return redirect(url_for("index"))
        else:
            flash("Неверный логин или пароль", "error")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/shift/report_html")
def shift_report_html():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    user = db.query(User).get(session["user_id"])

    # берём последнюю смену этого пользователя (или сервиса)
    shift = (
        db.query(Shift)
        .filter(Shift.service_id == user.service_id)
        .order_by(Shift.start_time.desc())
        .first()
    )

    if not shift:
        db.close()
        return render_template("shift_report.html", shift=None, orders=[], balances=[])

    # заявки в этой смене
    orders = (
        db.query(Order)
        .filter(Order.shift_id == shift.id)
        .order_by(Order.id.asc())
        .all()
    )

    # балансы на конец смены
    balances = (
        db.query(Balance, Asset)
        .join(Asset, Balance.asset_id == Asset.id)
        .filter(Balance.service_id == shift.service_id)
        .all()
    )

    db.close()

    return render_template(
        "shift_report.html",
        shift=shift,
        orders=orders,
        balances=balances
    )

@app.route("/admin_action", methods=["POST"])
def admin_action():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    user = db.query(User).get(session["user_id"])

    if user.role != "admin":
        db.close()
        return "Доступ запрещён", 403

    service_id = int(request.form["service_id"])
    asset_id = int(request.form["asset_id"])
    amount = float(request.form["amount"])
    action_type = request.form["action_type"]  # deposit / withdraw
    comment = request.form.get("comment", "")

    # логика изменения баланса
    balance = (
        db.query(Balance)
        .filter(Balance.service_id == service_id, Balance.asset_id == asset_id)
        .first()
    )
    if not balance:
        balance = Balance(service_id=service_id, asset_id=asset_id, amount=0)
        db.add(balance)
        db.commit()
        db.refresh(balance)

    old_amount = balance.amount
    if action_type == "deposit":
        balance.amount += amount
    elif action_type == "withdraw":
        balance.amount -= amount

    # сохраняем изменение в истории
    hist = BalanceHistory(
        service_id=service_id,
        asset_id=asset_id,
        old_amount=old_amount,
        new_amount=balance.amount,
        change=balance.amount - old_amount,
    )
    db.add(hist)

    # фиксируем как «админскую операцию» в ордерах
    order = Order(
        service_id=service_id,
        user_id=user.id,
        shift_id=None,  # не привязываем к смене
        type="admin_action",
        is_manual=True,
        received_asset_id=None,
        received_amount=0,
        given_asset_id=asset_id,
        given_amount=amount if action_type == "withdraw" else 0,
        comment=comment or f"{action_type} {amount}",
        rate_at_execution={},
        profit_percent=0,
    )
    db.add(order)

    db.commit()
    db.close()
    return redirect(url_for("index"))

@app.route("/internal_transfer", methods=["POST"])
def internal_transfer():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    user = db.query(User).get(session["user_id"])

    from_service_id = int(request.form["from_service_id"])
    to_service_id = int(request.form["to_service_id"])
    asset_id = int(request.form["asset_id"])
    amount = float(request.form["amount"])
    comment = request.form.get("comment", "")

    # --- обновляем баланс отправителя ---
    from_balance = (
        db.query(Balance)
        .filter(Balance.service_id == from_service_id, Balance.asset_id == asset_id)
        .first()
    )
    if not from_balance:
        from_balance = Balance(service_id=from_service_id, asset_id=asset_id, amount=0)
        db.add(from_balance)
        db.commit()
        db.refresh(from_balance)

    old_from_amount = from_balance.amount
    from_balance.amount -= amount
    db.add(BalanceHistory(
        service_id=from_service_id,
        asset_id=asset_id,
        old_amount=old_from_amount,
        new_amount=from_balance.amount,
        change=-amount,
    ))

    # --- обновляем баланс получателя ---
    to_balance = (
        db.query(Balance)
        .filter(Balance.service_id == to_service_id, Balance.asset_id == asset_id)
        .first()
    )
    if not to_balance:
        to_balance = Balance(service_id=to_service_id, asset_id=asset_id, amount=0)
        db.add(to_balance)
        db.commit()
        db.refresh(to_balance)

    old_to_amount = to_balance.amount
    to_balance.amount += amount
    db.add(BalanceHistory(
        service_id=to_service_id,
        asset_id=asset_id,
        old_amount=old_to_amount,
        new_amount=to_balance.amount,
        change=amount,
    ))

    # --- создаём заявку у отправителя ---
    order_out = Order(
        service_id=from_service_id,
        user_id=user.id,
        shift_id=None,
        type="internal_transfer",
        is_manual=True,
        received_asset_id=None,
        received_amount=0.0,
        given_asset_id=asset_id,
        given_amount=amount,
        comment=comment or f"Перевод {amount} актива в сервис {to_service_id}",
        rate_at_execution={},
        profit_percent=0,
    )
    db.add(order_out)

    # --- создаём заявку у получателя ---
    order_in = Order(
        service_id=to_service_id,
        user_id=user.id,
        shift_id=None,
        type="internal_transfer",
        is_manual=True,
        received_asset_id=asset_id,
        received_amount=amount,
        given_asset_id=None,
        given_amount=0.0,
        comment=comment or f"Перевод {amount} актива из сервиса {from_service_id}",
        rate_at_execution={},
        profit_percent=0,
    )
    db.add(order_in)

    db.commit()
    db.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
