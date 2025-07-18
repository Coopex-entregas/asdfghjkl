from app import app, db, Usuario
from werkzeug.security import generate_password_hash

with app.app_context():
    if not Usuario.query.filter_by(nome="coopex").first():
        admin = Usuario(
            nome="coopex",
            senha_hash=generate_password_hash("05062721"),
            tipo="adm"
        )
        db.session.add(admin)
        db.session.commit()
        print("✅ Admin criado com sucesso!")
    else:
        print("⚠️ Admin já existe.")
