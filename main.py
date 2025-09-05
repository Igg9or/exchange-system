from flask import Flask, render_template, redirect, url_for, request
from db import SessionLocal, init_db
from models import Service, Asset, Balance, Shift, Order, User, BalanceHistory
from datetime import datetime
from sqlalchemy.orm import Session
from collections import defaultdict
from flask import Flask, render_template, redirect, url_for, request, session, flash
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload
from db import get_db
from rates import price_rub_for_symbol

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

def start_shift(db: Session, service_id: int, user_id: int):
    # Закрываем все активные смены
    active_shifts = db.query(Shift).filter(
        Shift.service_id == service_id,
        Shift.end_time == None
    ).all()
    for s in active_shifts:
        s.end_time = datetime.utcnow()

    # Создаём новую смену
    shift = Shift(service_id=service_id, started_by=user_id, start_time=datetime.utcnow())
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

    with get_db() as db:
        user = db.query(User).get(session["user_id"])

        # выбор сервиса (для админа — можно переключать, для оператора — свой)
        selected_service_id = request.args.get("service_id", type=int)
        if user.role == "operator":
            service = db.query(Service).get(user.service_id)
        else:
            service = db.query(Service).get(selected_service_id) if selected_service_id else None

        # формируем запрос заказов
        query = db.query(Order).join(User).join(Service)

        if user.role == "operator":
            query = query.filter(Order.service_id == service.id)
        elif service:
            query = query.filter(Order.service_id == service.id)

        # 🔹 применяем фильтры
        if request.args.get("type"):
            query = query.filter(Order.type == request.args["type"])

        if request.args.get("asset_id"):
            asset_id = int(request.args["asset_id"])
            query = query.filter(
                (Order.received_asset_id == asset_id) |
                (Order.given_asset_id == asset_id)
            )

        if request.args.get("operator_id"):
            query = query.filter(Order.user_id == int(request.args["operator_id"]))

        if request.args.get("comment"):
            query = query.filter(Order.comment.ilike(f"%{request.args['comment']}%"))

        orders = query.order_by(Order.id.desc()).all()

        # остальное (балансы, смены и т.д.)
        balances = db.query(Balance, Asset).join(Asset, Balance.asset_id == Asset.id)
        if user.role == "operator":
            balances = balances.filter(Balance.service_id == service.id)
        elif service:
            balances = balances.filter(Balance.service_id == service.id)
        balances = balances.all()

        services = db.query(Service).all()
        all_users = db.query(User).all() if user.role == "admin" else [user]
        assets = db.query(Asset).all()

        # активная смена
        current_shift = None
        current_profit = 0.0
        prev_profit = 0.0
        if service:
            shift_key = f"current_shift_{service.id}"
            shift_id = session.get(shift_key)
            current_shift = db.query(Shift).get(shift_id) if shift_id else None

            if current_shift:
                # считаем прибыль текущей смены
                orders_in_shift = db.query(Order).filter(Order.shift_id == current_shift.id).all()
                current_profit = sum(o.profit_rub or 0 for o in orders_in_shift)

                # ищем предыдущую смену этого сервиса
                prev_shift = (
                    db.query(Shift)
                    .filter(Shift.service_id == service.id, Shift.id != current_shift.id)
                    .order_by(Shift.start_time.desc())
                    .first()
                )
                if prev_shift:
                    prev_orders = db.query(Order).filter(Order.shift_id == prev_shift.id).all()
                    prev_profit = sum(o.profit_rub or 0 for o in prev_orders)

        return render_template(
            "index.html",
            user=user,
            balances=balances,
            services=services,
            selected_service_id=selected_service_id,
            orders=orders,
            assets=assets,
            all_users=all_users,
            current_shift=current_shift,
            current_profit=current_profit,
            prev_profit=prev_profit,
        )



@app.route("/shift/start/<int:service_id>")
def shift_start(service_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    shift = start_shift(db, service_id, session["user_id"])
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

@app.route("/add_order", methods=["POST"])

def add_order():
    with get_db() as db:
        user = db.query(User).get(session["user_id"])

        # Определяем сервис контекста:
        # - оператор всегда работает в своём сервисе
        # - админ — по выбранному на странице (selected_service_id) или первому
        if user.role == "operator":
            service_id = user.service_id
        else:
            selected_service_id = request.args.get("service_id", type=int) or session.get("selected_service_id")
            if selected_service_id:
                service_id = selected_service_id
            else:
                first_service = db.query(Service).order_by(Service.id.asc()).first()
                service_id = first_service.id if first_service else None

        if not service_id:
            flash("Нет доступного сервиса для создания заявки.", "error")
            return redirect(url_for("index"))

        # Достаём активную смену для сервиса, если нет — создаём
        shift = (
            db.query(Shift)
            .filter(Shift.service_id == service_id, Shift.end_time.is_(None))
            .order_by(Shift.start_time.desc())
            .first()
        )
        if not shift:
            # если смена не запущена — поднимем автоматически (можно убрать автосоздание, если нужно)
            shift = Shift(
                service_id=service_id,
                number=1,
                start_time=datetime.utcnow(),
                started_by=user.id,
            )
            db.add(shift)
            db.flush()

        # Парсим форму
        try:
            received_asset_id = int(request.form["received_asset_id"])
            given_asset_id = int(request.form["given_asset_id"])
            received_amount = float(request.form["received_amount"])
            given_amount = float(request.form["given_amount"])
        except Exception:
            flash("Проверьте корректность введённых сумм и активов.", "error")
            return redirect(url_for("index"))

        comment = request.form.get("comment", "").strip()

        # Получаем цены в рублях для обоих активов на момент создания
        recv_rub = price_rub_for_asset_id(db, received_asset_id)
        give_rub = price_rub_for_asset_id(db, given_asset_id)
        if recv_rub is None or give_rub is None:
            flash("Не удалось получить курс(ы) для расчёта прибыли.", "error")
            return redirect(url_for("index"))

        # Считаем прибыль:
        # value_in  = сколько рублей «зашло» по цене получаемого актива
        # value_out = сколько рублей «вышло» по цене отдаваемого актива
        value_in = received_amount * recv_rub
        value_out = given_amount * give_rub
        profit_rub = value_in - value_out
        base = value_out if value_out else 0.0
        profit_percent = (profit_rub / base * 100.0) if base else 0.0

        # Создаём Order и сохраняем цены-снимки:
        order = Order(
            service_id=service_id,
            user_id=user.id,
            shift_id=shift.id,
            type="order",
            is_manual=True,
            received_asset_id=received_asset_id,
            received_amount=received_amount,
            given_asset_id=given_asset_id,
            given_amount=given_amount,
            comment=comment,
            # сохраняем "снимок" цен в рублях — так прибыль зафиксирована на момент создания заявки
            rate_at_creation=recv_rub,    # RUB за 1 единицу получаемого актива
            rate_at_execution=give_rub,   # RUB за 1 единицу отдаваемого актива
            profit_rub=profit_rub,
            profit_percent=profit_percent,
        )
        db.add(order)

        # Обновляем балансы сервиса:
        # +получили -> плюс к соответствующему активу
        inc = db.query(Balance).filter_by(service_id=service_id, asset_id=received_asset_id).first()
        if not inc:
            inc = Balance(service_id=service_id, asset_id=received_asset_id, amount=0.0)
            db.add(inc)
        inc.amount += received_amount

        # -отдали -> минус к соответствующему активу
        dec = db.query(Balance).filter_by(service_id=service_id, asset_id=given_asset_id).first()
        if not dec:
            dec = Balance(service_id=service_id, asset_id=given_asset_id, amount=0.0)
            db.add(dec)
        dec.amount -= given_amount

        db.commit()
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


@app.route("/users")
def users_list():
    db = SessionLocal()
    users = db.query(User).options(joinedload(User.service)).all()
    services = db.query(Service).all()
    db.close()
    return render_template("users.html", users=users, services=services)


@app.route("/users/add", methods=["POST"])
def add_user():
    if session.get("role") != "admin":
        return redirect(url_for("index"))

    login = request.form["login"]
    password = request.form["password"]
    role = request.form["role"]
    service_id = request.form.get("service_id")

    db = SessionLocal()

    # если оператор, но сервис не выбран
    if role == "operator" and not service_id:
        db.close()
        return "Ошибка: оператор должен быть привязан к сервису", 400

    new_user = User(
        login=login,
        password_hash=generate_password_hash(password),
        role=role,
        service_id=int(service_id) if service_id else None,
    )
    db.add(new_user)
    db.commit()
    db.close()
    return redirect(url_for("users_list"))  

@app.route("/users/edit/<int:user_id>", methods=["POST"])
def edit_user(user_id):
    if session.get("role") != "admin":
        return redirect(url_for("index"))

    db = SessionLocal()
    user = db.query(User).get(user_id)

    if user:
        user.role = request.form["role"]
        service_id = request.form.get("service_id")
        user.service_id = int(service_id) if service_id else None

        if request.form.get("password"):  # если ввели новый пароль
            user.password_hash = generate_password_hash(request.form["password"])

        db.commit()
    db.close()
    return redirect(url_for("users_list"))

@app.route("/users/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if session.get("role") != "admin":
        return redirect(url_for("index"))

    db = SessionLocal()
    user = db.query(User).get(user_id)
    if user:
        db.delete(user)
        db.commit()
    db.close()
    return redirect(url_for("users_list"))

@app.route("/set_shift", methods=["POST"])
def set_shift():
    # менеджер контекста из db.py
    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if not user:
            return redirect(url_for("login"))

        # читаем номер смены из формы (совмещаем оба варианта имён)
        requested_number = int(
            request.form.get("shift_number") or  # как в index.html
            request.form.get("shift_id") or      # если где-то осталось старое имя
            1
        )

        # закрываем только активную смену ТЕКУЩЕГО сервиса
        last_shift = (
            db.query(Shift)
            .filter(Shift.service_id == user.service_id, Shift.end_time.is_(None))
            .first()
        )
        if last_shift:
            last_shift.end_time = datetime.utcnow()

        # создаём новую смену с правильным service_id и номером
        new_shift = Shift(
            number=requested_number,
            service_id=user.service_id,
            start_time=datetime.utcnow(),
            started_by=user.id,
        )
        db.add(new_shift)
        db.commit()
        db.refresh(new_shift)

        # сохраняем активную смену в сессии ПО СЕРВИСУ
        session[f"current_shift_{user.service_id}"] = new_shift.id

    return redirect(url_for("index"))

def price_rub_for_asset_id(db, asset_id: int) -> float | None:
    asset = db.query(Asset).get(asset_id)
    if not asset:
        return None
    return price_rub_for_symbol(asset.symbol)


if __name__ == "__main__":
    app.run(debug=True)
