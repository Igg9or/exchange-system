import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash
from models import Base, User, Service, Asset  # твои модели

# 👉 сюда вставь DATABASE_URL из Render
DATABASE_URL = "postgresql://exchange_db_wf0q_user:qeXKWVC4qrc9A6MgxCh9twwkRZTKm5fS@dpg-d38mlkndiees73cn02s0-a.oregon-postgres.render.com/exchange_db_wf0q"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

def run_seed():
    Base.metadata.create_all(bind=engine)  # создаём таблицы, если их нет

    db = SessionLocal()

    # 1. Админ
    if not db.query(User).filter_by(login="admin").first():
        admin = User(
            login="admin",
            password_hash=generate_password_hash("admin123"),
            role="admin",
        )
        db.add(admin)
        print("✅ Админ создан")

    # 2. Сервисы
    service1 = db.query(Service).filter_by(name="Service A").first()
    if not service1:
        service1 = Service(name="Service A")
        db.add(service1)
        db.flush()

    service2 = db.query(Service).filter_by(name="Service B").first()
    if not service2:
        service2 = Service(name="Service B")
        db.add(service2)
        db.flush()

    # 3. Операторы
    if not db.query(User).filter_by(login="operator1").first():
        op1 = User(
            login="operator1",
            password_hash=generate_password_hash("op123"),
            role="operator",
            service_id=service1.id,
        )
        db.add(op1)

    if not db.query(User).filter_by(login="operator2").first():
        op2 = User(
            login="operator2",
            password_hash=generate_password_hash("op123"),
            role="operator",
            service_id=service2.id,
        )
        db.add(op2)

    # 4. Активы
    assets = [
        ("RUB", "Российский рубль"),
        ("USDT", "Tether"),
        ("BTC", "Bitcoin"),
        ("ETH", "Ethereum"),
    ]
    for symbol, name in assets:
        if not db.query(Asset).filter_by(symbol=symbol).first():
            asset = Asset(symbol=symbol, name=name)
            db.add(asset)

    db.commit()
    db.close()
    print("🎉 Сидинг завершён")

if __name__ == "__main__":
    run_seed()
