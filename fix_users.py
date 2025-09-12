from db import get_db
from models import User
from werkzeug.security import generate_password_hash

users = [
    ("admin", "admin123", "admin", None),
    ("operator1", "op123", "operator", 1),
    ("operator2", "op123", "operator", 2),
    ("operator3", "op123", "operator", 2),
]

with get_db() as db:
    db.query(User).delete()  # полностью очистим таблицу
    for login, pw, role, service_id in users:
        u = User(
            login=login,
            role=role,
            service_id=service_id,
            password_hash=generate_password_hash(pw),
        )
        db.add(u)
        print(f"✅ создан {login} / {pw}")
    db.commit()
