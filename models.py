from sqlalchemy import Column, Integer, String, Float, ForeignKey, Boolean, DateTime, JSON
from datetime import datetime
from db import Base
from sqlalchemy.orm import relationship
from sqlalchemy import BigInteger
from datetime import datetime, timezone, timedelta

# часовой пояс UTC+3 (Москва)
MSK = timezone.utc

class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    users = relationship("User", back_populates="service")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    login = Column(String, unique=True)
    password_hash = Column(String)
    role = Column(String)  # "operator" | "admin"
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True)
    service = relationship("Service", back_populates="users")


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True)
    name = Column(String)
    pair_symbol = Column(String, nullable=True)   # торговая пара с биржи
    manual_rate = Column(Float, nullable=True) 
    


class Balance(Base):
    __tablename__ = "balances"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    amount = Column(Float, default=0.0)


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True)
    number = Column(Integer, nullable=False)  # 1, 2 или 3
    service_id = Column(Integer, ForeignKey("services.id"))
    start_time = Column(DateTime, default=lambda: datetime.now(MSK))
    end_time = Column(DateTime, nullable=True)
    started_by = Column(Integer, ForeignKey("users.id"))
    user = relationship("User")
    is_deleted = Column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    shift_id = Column(Integer, ForeignKey("shifts.id"))
    type = Column(String)  # "order" | "internal_transfer" | "admin_action" | "admin_io"class Asset(
    is_manual = Column(Boolean, default=True)

    received_asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    received_amount = Column(Float, default=0.0)
    given_asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    given_amount = Column(Float, default=0.0)

    received_asset = relationship("Asset", foreign_keys=[received_asset_id])
    given_asset = relationship("Asset", foreign_keys=[given_asset_id])

     # --- 🔹 новое для операций Внести/Вывести ---
    direction = Column(String, nullable=True)   # "in" или "out"
    amount = Column(Float, default=0.0)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)  # для admin_io
    asset = relationship("Asset", foreign_keys=[asset_id])

    comment = Column(String, nullable=True)
    rate_at_creation = Column(JSON, nullable=True)
    rate_at_execution = Column(JSON, nullable=True)
    profit_percent = Column(Float, nullable=True)
    profit_rub = Column(Float, default=0)
    user = relationship("User")
    created_at = Column(DateTime, default=lambda: datetime.now(MSK))
    is_deleted = Column(Boolean, default=False)
    transfer_group = Column(BigInteger, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    category = relationship("Category", back_populates="orders")


class BalanceHistory(Base):
    __tablename__ = "balances_history"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    old_amount = Column(Float, default=0.0)
    new_amount = Column(Float, default=0.0)
    change = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(MSK))

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)

    orders = relationship("Order", back_populates="category")