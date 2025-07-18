
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
app.secret_key = "sua_chave_secreta"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///entregas.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # 'adm' ou 'cooperado'

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    hora_pedido = db.Column(db.DateTime, nullable=False)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    status_pagamento = db.Column(db.String(20), default="pendente")  # pendente ou pago
    status_entrega = db.Column(db.String(20), default="pendente")    # pendente, em rota, entregue
    cooperado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    cooperado = db.relationship('Usuario')

@app.before_first_request
def criar_admin():
    admin = Usuario.query.filter_by(nome="coopex").first()
    if not admin:
        senha_hash = generate_password_hash("05062721")
        admin = Usuario(nome="coopex", senha_hash=senha_hash, tipo="adm")
        db.session.add(admin)
        db.session.commit()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]
        user = Usuario.query.filter_by(nome=nome).first()
        if user and check_password_hash(user.senha_hash, senha):
            session["user_id"] = user.id
            session["user_nome"] = user.nome
            session["user_tipo"] = user.tipo
            return redirect(url_for("dashboard"))
        else:
            flash("Usuário ou senha inválidos.")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["user_tipo"] == "adm":
        cooperados = Usuario.query.filter_by(tipo="cooperado").all()
        entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
        return render_template("admin.html", cooperados=cooperados, entregas=entregas)
    else:
        entregas = Entrega.query.filter_by(cooperado_id=session["user_id"]).order_by(Entrega.hora_pedido.desc()).all()
        return render_template("cooperado.html", entregas=entregas)

@app.route("/cadastrar_cooperado", methods=["GET", "POST"])
def cadastrar_cooperado():
    if "user_tipo" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))
    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]
        if Usuario.query.filter_by(nome=nome).first():
            flash("Cooperado já existe!")
            return redirect(url_for("cadastrar_cooperado"))
        senha_hash = generate_password_hash(senha)
        cooperado = Usuario(nome=nome, senha_hash=senha_hash, tipo="cooperado")
        db.session.add(cooperado)
        db.session.commit()
        flash("Cooperado cadastrado com sucesso!")
        return redirect(url_for("dashboard"))
    return render_template("cadastrar_cooperado.html")

@app.route("/cadastrar_entrega", methods=["GET", "POST"])
def cadastrar_entrega():
    if "user_tipo" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))
    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    if request.method == "POST":
        descricao = request.form["descricao"]
        hora_pedido_str = request.form["hora_pedido"]
        cooperado_id = request.form.get("cooperado_id")
        hora_pedido = datetime.strptime(hora_pedido_str, "%Y-%m-%dT%H:%M")
        entrega = Entrega(descricao=descricao, hora_pedido=hora_pedido, status_pagamento="pendente", status_entrega="pendente")
        if cooperado_id and cooperado_id != "":
            entrega.cooperado_id = int(cooperado_id)
            entrega.hora_atribuida = datetime.now()
        db.session.add(entrega)
        db.session.commit()
        flash("Entrega cadastrada com sucesso!")
        return redirect(url_for("dashboard"))
    return render_template("cadastrar_entrega.html", cooperados=cooperados)

@app.route("/editar_entrega/<int:entrega_id>", methods=["GET", "POST"])
def editar_entrega(entrega_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    entrega = Entrega.query.get_or_404(entrega_id)
    user_tipo = session["user_tipo"]
    user_id = session["user_id"]

    if user_tipo == "cooperado" and entrega.cooperado_id != user_id:
        flash("Você não pode editar essa entrega.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        if user_tipo == "cooperado":
            status_pag = request.form.get("status_pagamento")
            status_ent = request.form.get("status_entrega")
            if status_pag in ["pendente", "pago"]:
                entrega.status_pagamento = status_pag
            if status_ent in ["pendente", "em rota", "entregue"]:
                entrega.status_entrega = status_ent
            db.session.commit()
            flash("Status atualizado.")
            return redirect(url_for("dashboard"))
        elif user_tipo == "adm":
            descricao = request.form["descricao"]
            cooperado_id = request.form.get("cooperado_id")
            status_pag = request.form.get("status_pagamento")
            status_ent = request.form.get("status_entrega")
            entrega.descricao = descricao
            if cooperado_id and cooperado_id != "":
                entrega.cooperado_id = int(cooperado_id)
                entrega.hora_atribuida = datetime.now()
            else:
                entrega.cooperado_id = None
                entrega.hora_atribuida = None
            if status_pag in ["pendente", "pago"]:
                entrega.status_pagamento = status_pag
            if status_ent in ["pendente", "em rota", "entregue"]:
                entrega.status_entrega = status_ent
            db.session.commit()
            flash("Entrega atualizada.")
            return redirect(url_for("dashboard"))

    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    return render_template("editar_entrega.html", entrega=entrega, cooperados=cooperados, user_tipo=user_tipo)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
