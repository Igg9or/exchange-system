from flask import Flask, render_template, redirect, url_for, request
from db import SessionLocal, init_db
from models import Service, Asset, Balance, Shift, Order, User, BalanceHistory, Category
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
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
from rates import price_rub_for_symbol, _get_binance_price, _get_mexc_price
from flask import session
from rates import ICON_MAP, NAME_MAP, ALIAS
from flask import abort
import logging
from datetime import timezone
from datetime import datetime, timedelta




MSK = timezone.utc


app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
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
        s.end_time = datetime.now(MSK)

    # Создаём новую смену
    shift = Shift(service_id=service_id, started_by=user_id, start_time=datetime.now(MSK))
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
    shift.end_time = datetime.now(MSK)
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

from sqlalchemy import func

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])

        # выбор сервиса (для админа — можно переключать, для оператора — всегда свой)
        selected_service_id = request.args.get("service_id", type=int)
        if user.role == "operator":
            service = db.query(Service).get(user.service_id)
        else:
            service = db.query(Service).get(selected_service_id) if selected_service_id else None

        # формируем запрос заказов
        query = (
            db.query(Order)
            .outerjoin(User, User.id == Order.user_id)                # чтобы не терять ордера без user/service
            .outerjoin(Service, Service.id == Order.service_id)       # привязываем сервис по полю в Order, а не через User
        )

        # ✅ показываем все типы операций (обмены, переводы, ввод/вывод)
        query = query.filter(Order.type.in_(["order", "admin_io", "internal_transfer", "admin_action", "admin_set"]))


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

        # 🔹 фильтр по категории
        if request.args.get("category_id"):
            query = query.filter(Order.category_id == int(request.args["category_id"]))

        # --- ✅ активная смена ---
        current_shift = None
        current_profit = 0.0
        prev_profit = 0.0

# --- Определяем текущую смену ДО расчета переводов ---
        if service:
            current_shift = (
                db.query(Shift)
                .filter(Shift.service_id == service.id, Shift.end_time.is_(None))
                .order_by(Shift.start_time.desc())
                .first()
            )

        # --- Получаем переводы между сервисами ---
        # В функции index() замените проблемный блок:
        transfers_sum = 0
        if service and current_shift:  # Добавьте проверку на current_shift
            internal_transfers = (
                db.query(Order)
                .filter(
                    Order.type == "internal_transfer",
                    Order.shift_id == current_shift.id,
                    Order.is_deleted == False
                )
                .all()
            )

            # Простой вариант расчета переводов:
            for t in internal_transfers:
                if t.given_asset_id and t.given_amount > 0 and t.service_id == current_shift.service_id:
                    # Этот сервис отправил средства
                    rub_rate = price_rub_for_asset_id(db, t.given_asset_id) or 0
                    transfers_sum -= t.given_amount * rub_rate
                    
                if t.received_asset_id and t.received_amount > 0 and t.service_id == current_shift.service_id:
                    # Этот сервис получил средства
                    rub_rate = price_rub_for_asset_id(db, t.received_asset_id) or 0
                    transfers_sum += t.received_amount * rub_rate

        if service:
            current_shift = (
                db.query(Shift)
                .filter(Shift.service_id == service.id, Shift.end_time.is_(None))
                .order_by(Shift.start_time.desc())
                .first()
            )

            if current_shift:
                # прибыль текущей смены
                # прибыль текущей смены (только не удалённые заявки)
                orders_in_shift = (
                    db.query(Order)
                    .filter(Order.shift_id == current_shift.id, Order.is_deleted == False)
                    .all()
                )
                current_profit = sum(o.profit_rub or 0 for o in orders_in_shift)

                # предыдущая смена (тоже только не удалённые)
                prev_shift = (
                    db.query(Shift)
                    .filter(Shift.service_id == service.id, Shift.id != current_shift.id)
                    .order_by(Shift.start_time.desc())
                    .first()
                )
                if prev_shift:
                    prev_orders = (
                        db.query(Order)
                        .filter(Order.shift_id == prev_shift.id, Order.is_deleted == False)
                        .all()
                    )
                    prev_profit = sum(o.profit_rub or 0 for o in prev_orders)

        # 🔹 фильтр по смене (перенесён сюда, где уже есть current_shift)
        if request.args.get("my_shift") == "1" and current_shift:
            query = query.filter(Order.shift_id == current_shift.id)

        # --- ✅ пагинация ---
        page = request.args.get("page", 1, type=int)

        # если per_page передан в запросе — сохраняем в сессии
        if "per_page" in request.args:
            session["per_page"] = request.args.get("per_page", type=int)

        # берём из сессии или дефолт
        per_page = session.get("per_page", 15)

        total_orders = query.count()
        orders = (
            query.order_by(Order.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        total_pages = (total_orders + per_page - 1) // per_page

        # --- ✅ балансы ---
        balances = db.query(Balance, Asset).join(Asset, Balance.asset_id == Asset.id)
        if user.role == "operator":
            balances = balances.filter(Balance.service_id == service.id)
        elif service:
            balances = balances.filter(Balance.service_id == service.id)
        balances = balances.all()

        services = db.query(Service).all()
        all_users = db.query(User).all() if user.role == "admin" else [user]
        assets = db.query(Asset).all()
        categories = db.query(Category).all()   # 🔹 загружаем категории

        # --- ✅ топ-активы ---
        saved_top_assets = session.get("top_assets")
        if saved_top_assets:
            # если уже редактировали — берём сохранённый список
            top_assets = saved_top_assets
        else:
            # иначе считаем топ по использованию
            asset_usage = (
                db.query(
                    Asset.id,
                    func.count(Order.id).label("usage_count")
                )
                .outerjoin(Order, (Order.received_asset_id == Asset.id) | (Order.given_asset_id == Asset.id))
                .group_by(Asset.id)
                .all()
            )
            asset_usage_sorted = sorted(asset_usage, key=lambda x: x.usage_count, reverse=True)
            top_assets = [a.id for a in asset_usage_sorted[:12]]

        args = request.args.to_dict(flat=True)
        args.pop("page", None)        # убираем текущую страницу
        args.pop("per_page", None) 

        # --- 💰 Вводы / выводы за текущую смену ---
        # --- 💰 Вводы / выводы за текущую смену ---
        inputs_sum = outputs_sum = 0
        if current_shift:
            inputs_sum = (
                db.query(func.sum(Order.amount))
                .filter(Order.type == "admin_io")
                .filter(Order.direction == "in")
                .filter(Order.shift_id == current_shift.id)
                .filter(Order.is_deleted == False)  # ✅ добавлено
                .scalar()
                or 0
            )

            outputs_sum = (
                db.query(func.sum(Order.amount))
                .filter(Order.type == "admin_io")
                .filter(Order.direction == "out")
                .filter(Order.shift_id == current_shift.id)
                .filter(Order.is_deleted == False)  # ✅ добавлено
                .scalar()
                or 0
            )

        usdt_asset = db.query(Asset).filter(Asset.symbol == "USDT").first()
        usdt_manual_rate = usdt_asset.manual_rate if usdt_asset else None

        # --- рендер страницы ---
        return render_template(
            "index.html",
            user=user,
            balances=balances,
            services=services,
            selected_service_id=selected_service_id,
            orders=orders,
            assets=assets,
            all_users=all_users,
            categories=categories,
            current_shift=current_shift,
            current_profit=round(current_profit) if current_profit else 0,
            prev_profit=round(prev_profit) if prev_profit else 0,
            inputs_sum=round(inputs_sum) if inputs_sum else 0,
            outputs_sum=round(outputs_sum) if outputs_sum else 0,
            page=page,
            total_pages=total_pages,
            top_assets=top_assets,
            per_page=per_page,
            total_orders=total_orders,
            args=args,
            ICON_MAP=ICON_MAP,
            NAME_MAP=NAME_MAP,
            ALIAS=ALIAS,
            usdt_manual_rate=usdt_manual_rate,
            transfers_sum=transfers_sum,
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

@app.route("/add_order", methods=["POST"])
def add_order():
    with get_db() as db:
        user = db.query(User).get(session["user_id"])

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

        shift = (
            db.query(Shift)
            .filter(Shift.service_id == service_id, Shift.end_time.is_(None))
            .order_by(Shift.start_time.desc())
            .first()
        )
        if not shift:
            shift = Shift(
                service_id=service_id,
                number=1,
                start_time=datetime.now(MSK),
                started_by=user.id,
            )
            db.add(shift)
            db.flush()

        try:
            received_asset_id = int(request.form["received_asset_id"])
            given_asset_id = int(request.form["given_asset_id"])
            received_amount = float(request.form["received_amount"])
            given_amount = float(request.form["given_amount"])
        except Exception:
            flash("Проверьте корректность введённых сумм и активов.", "error")
            return redirect(url_for("index"))

        comment = request.form.get("comment", "").strip()
        category_id = request.form.get("category_id")

        # --- курс входящего актива ---
        recv_rub = price_rub_for_asset_id(db, received_asset_id)
        # --- курс исходящего актива ---
        give_rub = price_rub_for_asset_id(db, given_asset_id)

        # --- проверка правильности курсов ---
        if recv_rub is None or give_rub is None:
            flash("⛔ Не удалось получить курс актива. Заявка не создана.", "error")
            return redirect(url_for("index"))

        # Если API вернул мусор типа 0, 0.0001, None → блокируем заявку
        if recv_rub <= 0 or give_rub <= 0:
            flash("⛔ Ошибка: API биржи вернул некорректный курс. Заявка не создана.", "error")
            return redirect(url_for("index"))

        # Дополнительная проверка — если курс слишком маленький
        if recv_rub < 0.01 or give_rub < 0.01:
            flash("⛔ Ошибка: получен неправдоподобно низкий курс. Заявка не создана.", "error")
            return redirect(url_for("index"))

        value_in = received_amount * recv_rub
        value_out = given_amount * give_rub
        profit_rub = value_in - value_out
        base = value_out if value_out else 0.0
        profit_percent = (profit_rub / base * 100.0) if base else 0.0

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
            category_id=int(category_id) if category_id else None,   # ✅ сохраняем категорию
            rate_at_creation=recv_rub,
            rate_at_execution=give_rub,
            profit_rub=profit_rub,
            profit_percent=profit_percent,
        )
        db.add(order)

        inc = db.query(Balance).filter_by(service_id=service_id, asset_id=received_asset_id).first()
        if not inc:
            inc = Balance(service_id=service_id, asset_id=received_asset_id, amount=0.0)
            db.add(inc)
        inc.amount += received_amount

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
        login = request.form.get("login", "").strip()
        password = request.form.get("password", "")

        if not login or not password:
            flash("Введите логин и пароль")
            return render_template("login.html")

        with get_db() as db:
            user = db.query(User).filter(User.login == login).first()

        if not user:
            flash("Пользователь не найден")
            return render_template("login.html")

        ok = False
        if user.password_hash:
            try:
                ok = check_password_hash(user.password_hash, password)
            except Exception:
                # если в колонку попали «голые» пароли
                ok = (user.password_hash == password)

        if not ok:
            flash("Неверный пароль")
            return render_template("login.html")

        # успех
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role   # 👈 добавляем роль в сессию
        session.permanent = True
        return redirect(url_for("index"))

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

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role != "admin":
            flash("Нет прав", "error")
            return redirect(url_for("index"))

        service_id = int(request.form["service_id"])
        asset_id = int(request.form["asset_id"])
        amount = float(request.form["amount"])
        action_type = request.form["action_type"]  # deposit / withdraw
        comment = request.form.get("comment", "")
        category_id = request.form.get("category_id")

        asset = db.query(Asset).get(asset_id)
        if not asset:
            flash("Актив не найден", "error")
            return redirect(url_for("index", service_id=service_id))

        # курс и сумма в рублях
        rate_rub = price_rub_for_symbol(asset.symbol)
        amount_rub = amount * rate_rub

        # обновляем баланс в ед. актива
        balance = (
            db.query(Balance)
            .filter(Balance.service_id == service_id, Balance.asset_id == asset_id)
            .first()
        )
        if not balance:
            balance = Balance(service_id=service_id, asset_id=asset_id, amount=0)
            db.add(balance)
            db.flush()

        old_amount = balance.amount
        if action_type == "deposit":
            balance.amount += amount
            direction = "in"
        elif action_type == "withdraw":
            balance.amount -= amount
            direction = "out"
        else:
            flash("Неверный тип операции", "error")
            return redirect(url_for("index", service_id=service_id))

        db.add(BalanceHistory(
            service_id=service_id,
            asset_id=asset_id,
            old_amount=old_amount,
            new_amount=balance.amount,
            change=balance.amount - old_amount,
            created_at=datetime.utcnow(),
        ))

        # привязываем к активной смене, если есть
        current_shift = (
            db.query(Shift)
            .filter(Shift.service_id == service_id, Shift.end_time.is_(None))
            .order_by(Shift.start_time.desc())
            .first()
        )

        # полноценная запись
        order = Order(
            service_id=service_id,
            user_id=user.id,
            shift_id=current_shift.id if current_shift else None,
            type="admin_action",
            is_manual=True,
            comment=comment or f"{action_type} {amount} {asset.symbol}",
            direction=direction,
            asset_id=asset_id,
            amount=amount_rub,  # рубли
            received_asset_id=asset_id if direction == "in" else None,
            received_amount=amount if direction == "in" else 0,
            given_asset_id=asset_id if direction == "out" else None,
            given_amount=amount if direction == "out" else 0,
            profit_percent=0,
            profit_rub=0,
            category_id=int(category_id) if category_id else None,
            created_at=datetime.utcnow(),
        )
        db.add(order)
        db.commit()

    flash("✅ Операция выполнена", "success")
    return redirect(url_for("index", service_id=service_id))


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
    category_id = request.form.get("category_id")

    # 🔥 НАЙТИ АКТИВНЫЕ СМЕНЫ ДЛЯ ОБОИХ СЕРВИСОВ
    from_shift = (
        db.query(Shift)
        .filter(Shift.service_id == from_service_id, Shift.end_time.is_(None))
        .order_by(Shift.start_time.desc())
        .first()
    )
    
    to_shift = (
        db.query(Shift)
        .filter(Shift.service_id == to_service_id, Shift.end_time.is_(None))
        .order_by(Shift.start_time.desc())
        .first()
    )

    transfer_group = int(datetime.now(MSK).timestamp() * 1000)

    from_balance = db.query(Balance).filter_by(service_id=from_service_id, asset_id=asset_id).first()
    if not from_balance:
        from_balance = Balance(service_id=from_service_id, asset_id=asset_id, amount=0)
        db.add(from_balance)
        db.flush()

    old_from_amount = from_balance.amount
    from_balance.amount -= amount
    db.add(BalanceHistory(
        service_id=from_service_id,
        asset_id=asset_id,
        old_amount=old_from_amount,
        new_amount=from_balance.amount,
        change=-amount,
    ))

    to_balance = db.query(Balance).filter_by(service_id=to_service_id, asset_id=asset_id).first()
    if not to_balance:
        to_balance = Balance(service_id=to_service_id, asset_id=asset_id, amount=0)
        db.add(to_balance)
        db.flush()

    old_to_amount = to_balance.amount
    to_balance.amount += amount
    db.add(BalanceHistory(
        service_id=to_service_id,
        asset_id=asset_id,
        old_amount=old_to_amount,
        new_amount=to_balance.amount,
        change=amount,
    ))

    # Создаем исходящий перевод
    order_out = Order(
        service_id=from_service_id,
        user_id=user.id,
        shift_id=from_shift.id if from_shift else None,  # 🔥 ПРИВЯЗЫВАЕМ К СМЕНЕ ОТПРАВИТЕЛЯ
        type="internal_transfer",
        is_manual=True,
        given_asset_id=asset_id,
        given_amount=amount,
        transfer_group=transfer_group,
        comment=comment or f"Перевод {amount} актива в сервис {to_service_id}",
        category_id=int(category_id) if category_id else None,
        rate_at_creation={
            "to_service_id": to_service_id,
            "asset_id": asset_id,
            "amount": amount
        }
    )
    db.add(order_out)

    # Создаем входящий перевод
    order_in = Order(
        service_id=to_service_id,
        user_id=user.id,
        shift_id=to_shift.id if to_shift else None,  # 🔥 ПРИВЯЗЫВАЕМ К СМЕНЕ ПОЛУЧАТЕЛЯ
        type="internal_transfer",
        is_manual=True,
        received_asset_id=asset_id,
        received_amount=amount,
        transfer_group=transfer_group,
        comment=comment or f"Перевод {amount} актива из сервиса {from_service_id}",
        category_id=int(category_id) if category_id else None,
        rate_at_creation={
            "from_service_id": from_service_id,
            "asset_id": asset_id,
            "amount": amount
        }
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
            last_shift.end_time = datetime.now(MSK)

        # создаём новую смену с правильным service_id и номером
        new_shift = Shift(
            number=requested_number,
            service_id=user.service_id,
            start_time=datetime.now(MSK),
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

    # 1. если зафиксированный курс
    if asset.manual_rate is not None:
        return asset.manual_rate

    # 2. если указана торговая пара
    if asset.pair_symbol:
        px = _get_binance_price(asset.pair_symbol) or _get_mexc_price(asset.pair_symbol)
        if px:
            return float(px) * price_rub_for_symbol("USDT")

    # 3. старое поведение (через symbol)
    return price_rub_for_symbol(asset.symbol)


@app.route("/admin_io", methods=["POST"])
def admin_io():
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role not in ["admin", "operator"]:
            flash("Нет прав", "error")
            return redirect(url_for("index"))

        service_id = int(request.form["service_id"])
        asset_id = int(request.form["asset_id"])
        direction = request.form["direction"]
        amount = float(request.form["amount"])
        comment = request.form.get("comment", "")
        category_id = request.form.get("category_id")

        # --- 💰 Пересчёт суммы в рубли ---
        asset = db.query(Asset).get(asset_id)
        if not asset:
            flash("Актив не найден", "error")
            return redirect(url_for("index", service_id=service_id))

        # --- 💰 Пересчёт суммы в рубли ---
        asset = db.query(Asset).get(asset_id)
        if not asset:
            flash("Актив не найден", "error")
            return redirect(url_for("index", service_id=service_id))

        # --- безопасное определение курса ---
        if asset.manual_rate and asset.manual_rate > 0:
            rate_rub = asset.manual_rate
        else:
            try:
                rate_rub = price_rub_for_symbol(asset.symbol)
            except Exception:
                rate_rub = 1  # fallback — если курса нет

        amount_rub = amount * rate_rub

        # --- Обновляем баланс ---
        balance = db.query(Balance).filter_by(service_id=service_id, asset_id=asset_id).first()
        if not balance:
            balance = Balance(service_id=service_id, asset_id=asset_id, amount=0.0)
            db.add(balance)
            db.flush()

        old_amount = balance.amount
        if direction == "in":
            balance.amount += amount
        elif direction == "out":
            balance.amount -= amount

        db.add(BalanceHistory(
            service_id=service_id,
            asset_id=asset_id,
            old_amount=old_amount,
            new_amount=balance.amount,
            change=(amount if direction == "in" else -amount),
            created_at=datetime.utcnow()
        ))

        current_shift = (
            db.query(Shift)
            .filter(Shift.service_id == service_id, Shift.end_time.is_(None))
            .order_by(Shift.start_time.desc())
            .first()
        )

        order = Order(
            service_id=service_id,
            user_id=user.id,
            shift_id=current_shift.id if current_shift else None,
            type="admin_io",
            is_manual=True,
            received_asset_id=asset_id if direction == "in" else None,
            received_amount=amount if direction == "in" else 0,
            given_asset_id=asset_id if direction == "out" else None,
            given_amount=amount if direction == "out" else 0,
            comment=comment,
            profit_percent=0,
            profit_rub=0,
            category_id=int(category_id) if category_id else None,
            direction=direction,
            asset_id=asset_id,
            amount=amount_rub,   # 💰 Сохраняем в рублях!
            created_at=datetime.utcnow(),
        )
        db.add(order)
        db.commit()

    flash("Операция успешно выполнена!", "success")
    return redirect(url_for("index", service_id=service_id))




@app.route("/add_asset", methods=["POST"])
def add_asset():
    if "user_id" not in session:
        return redirect(url_for("login"))

    symbol = request.form["symbol"].strip()
    name = request.form["name"].strip()
    service_id = int(request.form["service_id"])

    # новые поля из формы
    pair_symbol = request.form.get("pair_symbol") or None
    manual_rate = request.form.get("manual_rate")
    manual_rate = float(manual_rate) if manual_rate else None

    with get_db() as db:
        # ищем актив по symbol
        asset = db.query(Asset).filter_by(symbol=symbol).first()
        if not asset:
            asset = Asset(
                symbol=symbol,
                name=name,
                pair_symbol=pair_symbol,
                manual_rate=manual_rate
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)
        else:
            # если актив уже есть — обновим параметры
            asset.pair_symbol = pair_symbol
            asset.manual_rate = manual_rate
            db.commit()

        # проверяем, есть ли уже баланс у сервиса
        balance = db.query(Balance).filter_by(service_id=service_id, asset_id=asset.id).first()
        if not balance:
            balance = Balance(service_id=service_id, asset_id=asset.id, amount=0)
            db.add(balance)
            db.commit()

    return redirect(url_for("index", service_id=service_id))



@app.route("/initdb")
def initdb():
    from db import init_db
    init_db()
    return "✅ Таблицы успешно созданы!"



@app.route("/update_top_assets", methods=["POST"])
def update_top_assets():
    data = request.get_json()
    main_assets = data.get("main_assets", [])
    session["top_assets"] = [int(x) for x in main_assets]
    return "ok", 200


@app.route("/orders/delete/<int:order_id>", methods=["POST"])
def delete_order(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        order = db.query(Order).get(order_id)

        if not order:
            abort(404)   

        if order.is_deleted:
            flash("Ордер уже удалён", "warning")
            return redirect(url_for("index"))

        # 🔒 оператор может удалять только свои заявки в текущей смене
        if user.role == "operator":
            current_shift = (
                db.query(Shift)
                .filter(Shift.service_id == user.service_id, Shift.end_time.is_(None))
                .first()
            )
            if not current_shift or order.shift_id != current_shift.id or order.user_id != user.id:
                abort(403)

        # помечаем удалённой
        order.is_deleted = True

        # ======= 🔄 откатываем балансы как было =======
        if order.type == "order":
            recv = db.query(Balance).filter_by(service_id=order.service_id, asset_id=order.received_asset_id).first()
            give = db.query(Balance).filter_by(service_id=order.service_id, asset_id=order.given_asset_id).first()
            if recv:
                recv.amount -= order.received_amount
            if give:
                give.amount += order.given_amount

        elif order.type == "internal_transfer":
            twins = db.query(Order).filter(
                Order.type == "internal_transfer",
                Order.transfer_group == order.transfer_group,
                Order.is_deleted == False
            ).all()

            for twin in twins:
                twin.is_deleted = True
                if twin.received_asset_id:
                    bal = db.query(Balance).filter_by(service_id=twin.service_id, asset_id=twin.received_asset_id).first()
                    if bal:
                        bal.amount -= twin.received_amount
                if twin.given_asset_id:
                    bal = db.query(Balance).filter_by(service_id=twin.service_id, asset_id=twin.given_asset_id).first()
                    if bal:
                        bal.amount += twin.given_amount

        elif order.type in ("admin_action", "admin_io"):
            if order.received_asset_id:
                bal = db.query(Balance).filter_by(service_id=order.service_id, asset_id=order.received_asset_id).first()
                if bal:
                    bal.amount -= order.received_amount
            if order.given_asset_id:
                bal = db.query(Balance).filter_by(service_id=order.service_id, asset_id=order.given_asset_id).first()
                if bal:
                    bal.amount += order.given_amount

        db.commit()

    flash("✅ Ордер удалён", "success")
    return redirect(url_for("index"))


@app.route("/categories")
def categories_list():
    if "user_id" not in session:
        return redirect(url_for("login"))
    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role != "admin":
            flash("Доступ запрещён", "error")
            return redirect(url_for("index"))

        categories = db.query(Category).all()
        return render_template("categories.html", categories=categories)


@app.route("/categories/add", methods=["POST"])
def add_category():
    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role != "admin":
            return {"error": "forbidden"}, 403

        name = request.form.get("name")
        if name:
            cat = Category(name=name)
            db.add(cat)
            db.commit()
        return redirect(url_for("index"))


@app.route("/categories/delete/<int:category_id>", methods=["POST"])
def delete_category(category_id):
    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role != "admin":
            return {"error": "forbidden"}, 403

        cat = db.query(Category).get(category_id)
        if cat:
            db.delete(cat)
            db.commit()
        return redirect(url_for("index"))



@app.route("/api/pairs")
def get_pairs():
    import requests
    from flask import jsonify

    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=5)
        data = r.json()
        symbols = [s["symbol"] for s in data["symbols"] if s["status"] == "TRADING"]
        return jsonify(symbols)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin_set_balance", methods=["POST"])
def admin_set_balance():
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if user.role != "admin":
            flash("Нет прав", "error")
            return redirect(url_for("index"))

        service_id = request.form.get("service_id", type=int)
        asset_id = request.form.get("asset_id", type=int)
        new_amount = request.form.get("amount", type=float)
        comment = request.form.get("comment", "")

        if not service_id or not asset_id:
            flash("Ошибка: не выбран сервис или актив", "error")
            return redirect(url_for("index"))

        balance = db.query(Balance).filter_by(service_id=service_id, asset_id=asset_id).first()
        if not balance:
            balance = Balance(service_id=service_id, asset_id=asset_id, amount=0.0)
            db.add(balance)
            db.flush()

        old_amount = balance.amount
        change = new_amount - old_amount
        balance.amount = new_amount

        db.add(BalanceHistory(
            service_id=service_id,
            asset_id=asset_id,
            old_amount=old_amount,
            new_amount=new_amount,
            change=change,
        ))

        order = Order(
            service_id=service_id,
            user_id=user.id,
            type="admin_set",
            is_manual=True,
            comment=comment or f"Корректировка {old_amount} → {new_amount}",
            received_asset_id=asset_id if change > 0 else None,
            received_amount=change if change > 0 else 0,
            given_asset_id=asset_id if change < 0 else None,
            given_amount=abs(change) if change < 0 else 0,
            profit_percent=0,
            profit_rub=0,
        )
        db.add(order)
        db.commit()

    return redirect(url_for("index", service_id=service_id))

@app.route("/edit_order/<int:order_id>", methods=["POST"])
def edit_order(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        order = db.query(Order).get(order_id)

        if not order:
            flash("Заявка не найдена", "error")
            return redirect(url_for("index"))

        # оператор может редактировать только свои заявки и только в своей смене
        if user.role == "operator":
            current_shift = (
                db.query(Shift)
                .filter(Shift.service_id == user.service_id, Shift.end_time.is_(None))
                .first()
            )
            if not current_shift or order.shift_id != current_shift.id or order.user_id != user.id:
                flash("⛔ Вы можете редактировать только свои заявки в текущей смене", "error")
                return redirect(url_for("index"))

        # сохраняем старые значения
        old_received_asset = order.received_asset_id
        old_received_amount = order.received_amount or 0
        old_given_asset = order.given_asset_id
        old_given_amount = order.given_amount or 0

        # обновляем заявку
        order.received_asset_id = request.form.get("received_asset_id", type=int)
        order.received_amount = request.form.get("received_amount", type=float)
        order.given_asset_id = request.form.get("given_asset_id", type=int)
        order.given_amount = request.form.get("given_amount", type=float)
        order.comment = request.form.get("comment")
        order.category_id = request.form.get("category_id", type=int)

        # --- пересчёт прибыли ---
        recv_rub = price_rub_for_asset_id(db, order.received_asset_id)
        give_rub = price_rub_for_asset_id(db, order.given_asset_id)
        if recv_rub and give_rub:
            value_in = (order.received_amount or 0) * recv_rub
            value_out = (order.given_amount or 0) * give_rub
            order.profit_rub = value_in - value_out
            base = value_out if value_out else 0.0
            order.profit_percent = (order.profit_rub / base * 100.0) if base else 0.0
        else:
            order.profit_rub = 0
            order.profit_percent = 0

        # --- функция обновления баланса ---
        def update_balance(service_id, asset_id, delta, order_id=None):
            if not asset_id or not delta:
                return
            balance = (
                db.query(Balance)
                .filter_by(service_id=service_id, asset_id=asset_id)
                .first()
            )
            if not balance:
                balance = Balance(service_id=service_id, asset_id=asset_id, amount=0)
                db.add(balance)

            old_amount = balance.amount
            new_amount = old_amount + delta
            balance.amount = new_amount

            db.add(BalanceHistory(
                service_id=service_id,
                asset_id=asset_id,
                order_id=order_id,
                old_amount=old_amount,
                new_amount=new_amount,
                change=delta,
            ))

        # --- откатываем старое ---
        if old_received_asset and old_received_amount:
            update_balance(order.service_id, old_received_asset, -old_received_amount, order.id)

        if old_given_asset and old_given_amount:
            update_balance(order.service_id, old_given_asset, old_given_amount, order.id)

        # --- применяем новое ---
        if order.received_asset_id and order.received_amount:
            update_balance(order.service_id, order.received_asset_id, order.received_amount, order.id)

        if order.given_asset_id and order.given_amount:
            update_balance(order.service_id, order.given_asset_id, -order.given_amount, order.id)

        db.commit()
        flash("Заявка обновлена", "success")

        service_id = order.service_id
        return redirect(url_for("index", service_id=service_id))


@app.template_filter('trim_float')
def trim_float(value, precision=8):
    if value is None:
        return "-"
    try:
        return f"{value:.{precision}f}".rstrip('0').rstrip('.')
    except Exception:
        return str(value)

@app.template_filter("to_moscow")
def to_moscow(dt):
    """Преобразует UTC во время Москвы (UTC+3)"""
    if not dt:
        return ""
    try:
        return (dt + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt)



@app.route("/edit_io/<int:io_id>", methods=["POST"])
def edit_io(io_id):
    with get_db() as db:
        io = db.query(Order).get(io_id)
        if not io or io.type != "admin_io":
            return "Not found", 404

        # --- откат старого ---
        if io.received_asset_id:
            bal = db.query(Balance).filter_by(service_id=io.service_id, asset_id=io.received_asset_id).first()
            if bal:
                bal.amount -= io.received_amount

        if io.given_asset_id:
            bal = db.query(Balance).filter_by(service_id=io.service_id, asset_id=io.given_asset_id).first()
            if bal:
                bal.amount += io.given_amount

        # --- новые данные ---
        new_asset_id = request.form.get("asset_id", type=int)
        new_amount = request.form.get("amount", type=float)
        new_direction = request.form.get("direction")
        new_comment = request.form.get("comment")

        # обновляем сам объект
        io.received_asset_id = new_asset_id if new_direction == "in" else None
        io.received_amount = new_amount if new_direction == "in" else 0
        io.given_asset_id = new_asset_id if new_direction == "out" else None
        io.given_amount = new_amount if new_direction == "out" else 0
        io.comment = new_comment
        io.direction = new_direction
        io.asset_id = new_asset_id
        io.amount = new_amount

        # --- применяем новое ---
        bal = db.query(Balance).filter_by(service_id=io.service_id, asset_id=new_asset_id).first()
        if not bal:
            bal = Balance(service_id=io.service_id, asset_id=new_asset_id, amount=0)
            db.add(bal)

        if new_direction == "in":
            bal.amount += new_amount
        else:
            bal.amount -= new_amount

        db.commit()
    return redirect(url_for("index"))



@app.route("/delete_io/<int:io_id>", methods=["POST"])
def delete_io(io_id):
    with get_db() as db:
        io = db.query(Order).get(io_id)
        if not io or io.type != "admin_io":
            return "Not found", 404

        # 🔄 откат операции
        if io.received_asset_id:
            bal = db.query(Balance).filter_by(service_id=io.service_id, asset_id=io.received_asset_id).first()
            if bal:
                bal.amount -= io.received_amount

        if io.given_asset_id:
            bal = db.query(Balance).filter_by(service_id=io.service_id, asset_id=io.given_asset_id).first()
            if bal:
                bal.amount += io.given_amount

        io.is_deleted = True
        db.commit()
    return redirect(url_for("index"))   

@app.route("/delete_asset/<int:asset_id>", methods=["POST"])
def delete_asset(asset_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.get(User, user_id)
        if not user or user.role != "admin":
            return "Forbidden", 403

        asset = db.get(Asset, asset_id)
        if asset:
            db.delete(asset)   # теперь это безопасно
            db.commit()

    return redirect(url_for("index"))


@app.after_request
def add_header(response):
    # Полностью отключаем кэширование страниц
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.errorhandler(403)
def handle_403(e):
    return render_template("403.html"), 403

@app.errorhandler(404)
def handle_404(e):
    return render_template("404.html"), 404


# === Кастомный фильтр: разделение тысяч пробелами ===
def format_number(value):
    try:
        num = float(value)
        if num.is_integer():
            return f"{int(num):,}".replace(",", " ")
        else:
            # до пяти знаков после запятой
            return f"{num:,.5f}".replace(",", " ").rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return value

app.jinja_env.filters["format_number"] = format_number



@app.route("/admin/analytics", methods=["GET"])
def admin_analytics():
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        # 🔹 Проверка прав
        user = db.get(User, session["user_id"])
        if not user or user.role != "admin":
            flash("Нет доступа", "error")
            return redirect(url_for("index"))

        services = db.query(Service).all()
        selected_service_id = request.args.get("service_id", type=int)

        # --- дата по умолчанию — сегодня ---
        selected_date_str = request.args.get("date")
        if selected_date_str:
            try:
                selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d")
            except ValueError:
                selected_date = datetime.now()
        else:
            selected_date = datetime.now()
            selected_date_str = selected_date.strftime("%Y-%m-%d")

        selected_shift_id = request.args.get("shift_id", type=int)

        # --- список смен около выбранной даты ---
        shifts = []
        if selected_service_id:
            date_start = selected_date - timedelta(days=1)
            date_end = selected_date + timedelta(days=1)
            from sqlalchemy import or_, and_

            shifts = (
                db.query(Shift)
                .filter(
                    Shift.service_id == selected_service_id,
                    or_(
                        # началась в диапазоне
                        and_(Shift.start_time >= date_start, Shift.start_time <= date_end),
                        # закончилась в диапазоне
                        and_(Shift.end_time != None, Shift.end_time >= date_start, Shift.end_time <= date_end),
                        # или пересекает диапазон (началась до и закончилась после)
                        and_(Shift.start_time <= date_start, Shift.end_time != None, Shift.end_time >= date_end)
                    )
                )
                .order_by(Shift.start_time.desc())
                .all()
            )

        # --- выбранная смена ---
        selected_shift = db.get(Shift, selected_shift_id) if selected_shift_id else None

        orders = []
        totals = {}
        operators = []

        if selected_shift:
            # заявки в смене
            orders = (
                db.query(Order)
                .filter(Order.shift_id == selected_shift.id, Order.is_deleted == False)
                .all()
            )

            # прибыль и ввод/вывод
            profit_sum = sum(o.profit_rub or 0 for o in orders)
            inputs_sum = sum(o.amount or 0 for o in orders if o.direction == "in")
            outputs_sum = sum(o.amount or 0 for o in orders if o.direction == "out")

            totals = {
                "profit": round(profit_sum, 2),
                "inputs": round(inputs_sum, 2),
                "outputs": round(outputs_sum, 2)
            }

            # операторы, работавшие в смене
            operators = (
                db.query(User.login)
                .join(Order, Order.user_id == User.id)
                .filter(Order.shift_id == selected_shift.id, Order.is_deleted == False)
                .distinct()
                .all()
            )
            operators = [o[0] for o in operators]

        # --- 📊 Формируем данные для графиков ---
        chart_data = {
            "labels": [],
            "profits": [],
            "inputs": [],
            "outputs": [],
            "shift_ids": []  
        }

        if selected_service_id:
            all_shifts = (
                db.query(Shift)
                .filter(
                    Shift.service_id == selected_service_id,
                    Shift.is_deleted == False  # ✅ исключаем удалённые смены
                )
                .order_by(Shift.start_time.asc())
                .all()
            )
            for sh in all_shifts:
                chart_data["labels"].append(sh.start_time.strftime("%d.%m.%Y"))
                chart_data["shift_ids"].append(sh.id)

                # вычисляем данные по каждой смене
                orders_in_shift = (
                    db.query(Order)
                    .filter(Order.shift_id == sh.id, Order.is_deleted == False)
                    .all()
                )

                total_profit = sum(o.profit_rub or 0 for o in orders_in_shift)
                total_inputs = sum(o.amount or 0 for o in orders_in_shift if o.direction == "in")
                total_outputs = sum(o.amount or 0 for o in orders_in_shift if o.direction == "out")

                chart_data["profits"].append(round(total_profit, 2))
                chart_data["inputs"].append(round(total_inputs, 2))
                chart_data["outputs"].append(round(total_outputs, 2))

        # --- отдаём шаблон ---
        return render_template(
            "admin_analytics.html",
            user=user,
            services=services,
            selected_service_id=selected_service_id,
            selected_date=selected_date_str,
            shifts=shifts,
            selected_shift=selected_shift,
            orders=orders,
            totals=totals,
            operators=operators,
            chart_data=chart_data,
        )


@app.route("/admin/shift/delete/<int:shift_id>", methods=["POST"])
def admin_delete_shift(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.get(User, session["user_id"])
        if not user or user.role != "admin":
            flash("Нет доступа", "error")
            return redirect(url_for("index"))

        shift = db.get(Shift, shift_id)
        if not shift:
            flash("Смена не найдена", "error")
            return redirect(url_for("admin_analytics"))

        # ✅ помечаем смену удалённой
        shift.is_deleted = True
        db.commit()

        flash(f"Смена #{shift.number or shift.id} помечена как удалённая", "success")
        return redirect(url_for("admin_analytics", service_id=shift.service_id))


@app.route("/set_manual_usdt_rate", methods=["POST"])
def set_manual_usdt_rate():
    if "user_id" not in session:
        return redirect(url_for("login"))

    rate = request.form.get("rate", type=float)
    if not rate or rate <= 0:
        flash("Некорректный курс", "error")
        return redirect(url_for("index"))

    with get_db() as db:
        usdt = db.query(Asset).filter(Asset.symbol == "USDT").first()
        if not usdt:
            flash("Актива USDT нет в системе", "error")
            return redirect(url_for("index"))

        usdt.manual_rate = rate
        db.commit()

    flash(f"Установлен ручной курс: {rate} ₽", "success")
    return redirect(url_for("index"))

@app.post("/delete_transfer/<int:order_id>")
def delete_transfer(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db() as db:
        user = db.query(User).get(session["user_id"])
        if not user:
            flash("Пользователь не найден", "error")
            return redirect(url_for("index"))

        # Только админ может удалять переводы
        if user.role != "admin":
            flash("Недостаточно прав для удаления переводов", "error")
            return redirect(url_for("index"))

        order = db.query(Order).filter(Order.id == order_id).first()

        if not order:
            flash("Перевод не найден", "error")
            return redirect(url_for("index"))

        if order.is_deleted:
            flash("Перевод уже удалён", "warning")
            return redirect(url_for("index"))

        if order.type != "internal_transfer":
            flash("Это не перевод между сервисами", "error")
            return redirect(url_for("index"))

        # Находим ВСЕ ордера этой группы перевода
        transfer_orders = db.query(Order).filter(
            Order.transfer_group == order.transfer_group,
            Order.is_deleted == False
        ).all()

        if len(transfer_orders) != 2:
            flash("Ошибка: некорректная группа перевода", "error")
            return redirect(url_for("index"))

        # Определяем from_service и to_service
        from_order = None
        to_order = None
        
        for o in transfer_orders:
            if o.given_asset_id and o.given_amount > 0:  # исходящий
                from_order = o
            elif o.received_asset_id and o.received_amount > 0:  # входящий
                to_order = o

        if not from_order or not to_order:
            flash("Ошибка: не удалось определить направление перевода", "error")
            return redirect(url_for("index"))

        from_service_id = from_order.service_id
        to_service_id = to_order.service_id
        asset_id = from_order.given_asset_id
        amount = from_order.given_amount

        # Проверяем, что данные согласованы
        if (asset_id != to_order.received_asset_id or 
            amount != to_order.received_amount or
            from_service_id == to_service_id):
            flash("Ошибка: несоответствие данных перевода", "error")
            return redirect(url_for("index"))

        try:
            # ---- Откатываем баланс на сервисе получателя ----
            bal_to = db.query(Balance).filter(
                Balance.service_id == to_service_id,
                Balance.asset_id == asset_id
            ).first()

            if bal_to:
                if bal_to.amount < amount:
                    flash("Ошибка: недостаточно средств для отката перевода", "error")
                    return redirect(url_for("index"))
                bal_to.amount -= amount

            # ---- Возвращаем деньги на сервис отправителя ----
            bal_from = db.query(Balance).filter(
                Balance.service_id == from_service_id,
                Balance.asset_id == asset_id
            ).first()

            if not bal_from:
                bal_from = Balance(service_id=from_service_id, asset_id=asset_id, amount=0)
                db.add(bal_from)
            
            bal_from.amount += amount

            # ---- Помечаем ОБА перевода как удаленные ----
            for o in transfer_orders:
                o.is_deleted = True

            # ---- Добавляем записи в историю балансов ----
            if bal_from:
                db.add(BalanceHistory(
                    service_id=from_service_id,
                    asset_id=asset_id,
                    order_id=order.id,
                    old_amount=(bal_from.amount - amount),
                    new_amount=bal_from.amount,
                    change=amount
                ))

            if bal_to:
                db.add(BalanceHistory(
                    service_id=to_service_id,
                    asset_id=asset_id,
                    order_id=order.id,
                    old_amount=(bal_to.amount + amount),
                    new_amount=bal_to.amount,
                    change=-amount
                ))

            db.commit()
            flash("Перевод успешно удалён", "success")

        except Exception as e:
            db.rollback()
            flash(f"Ошибка при удалении перевода: {str(e)}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
