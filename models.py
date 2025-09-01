from sqlalchemy import Column, Integer, String, Float, ForeignKey, Boolean, DateTime, JSON
from datetime import datetime
from db import Base


class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    login = Column(String, unique=True)
    password_hash = Column(String)
    role = Column(String)  # "operator" | "admin"
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True)


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True)
    name = Column(String)


class Balance(Base):
    __tablename__ = "balances"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    asset_id = Column(Integer, ForeignKey("assets.id"))
    amount = Column(Float, default=0.0)


class Shift(Base):
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    shift_id = Column(Integer, ForeignKey("shifts.id"))
    type = Column(String)  # "order" | "internal_transfer" | "admin_action"
    is_manual = Column(Boolean, default=True)

    received_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    received_amount = Column(Float, default=0.0)
    given_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    given_amount = Column(Float, default=0.0)

    comment = Column(String, nullable=True)
    rate_at_creation = Column(JSON, nullable=True)
    rate_at_execution = Column(JSON, nullable=True)
    profit_percent = Column(Float, nullable=True)


class BalanceHistory(Base):
    __tablename__ = "balances_history"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    asset_id = Column(Integer, ForeignKey("assets.id"))
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    old_amount = Column(Float, default=0.0)
    new_amount = Column(Float, default=0.0)
    change = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
