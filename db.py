from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ⚠️ проверь логин/пароль и имя БД под свой Postgres
SQLALCHEMY_DATABASE_URL = "postgresql+psycopg2://postgres:1501@localhost/exchange_db"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    import models  # подтянет Service, Asset, User и т.д.
    Base.metadata.create_all(bind=engine)
