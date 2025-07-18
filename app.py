from pathlib import Path
import shutil

# Caminho para o diretório do app completo
app_dir = Path("/mnt/data/painel_coopex_app_completo")

# Criar a estrutura do app com os arquivos principais
app_dir.mkdir(parents=True, exist_ok=True)

# Criar arquivos principais do app Flask
(app_dir / "app.py").write_text('''\
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import os
import io
import pandas as pd

app = Flask(__name__)
app.secret_key = "sua_chave_secreta"

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///entregas.db").replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    hora_pedido = db.Column(db.DateTime, nullable=False)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    status_pagamento = db.Column(db.String(20), default="pendente")
    status_entrega = db.Column(db.String(20), default="pendente")
    cooperado_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True)
    cooperado = db.relationship("Usuario")

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

@app.route("/dashboard", methods=["GET"])
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    user_tipo = session["user_tipo"]

    if user_tipo == "adm":
        data_inicio = request.args.get("data_inicio")
        data_fim = request.args.get("data_fim")
        cooperado_id = request.args.get("cooperado_id")

        hoje = datetime.now().date()
        if not data_inicio:
            data_inicio = hoje.strftime("%Y-%m-%d")
        if not data_fim:
            data_fim = hoje.strftime("%Y-%m-%d")

        inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
        fim_dt = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

        query = Entrega.query.filter(Entrega.hora_pedido >= inicio_dt, Entrega.hora_pedido <= fim_dt)
        if cooperado_id and cooperado_id != "todos":
            query = query.filter(Entrega.cooperado_id == int(cooperado_id))
        entregas = query.order_by(Entrega.hora_pedido.desc()).all()

        hoje_data = date.today()
        total_dia = Entrega.query.filter(db.func.date(Entrega.hora_pedido) == hoje_data).count()
        total_mes = Entrega.query.filter(db.extract("month", Entrega.hora_pedido) == hoje_data.month).count()
        total_ano = Entrega.query.filter(db.extract("year", Entrega.hora_pedido) == hoje_data.year).count()

        valores_dia = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in Entrega.query.filter(db.func.date(Entrega.hora_pedido) == hoje_data).all() if "R$" in e.descricao)
        valores_mes = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in Entrega.query.filter(db.extract("month", Entrega.hora_pedido) == hoje_data.month).all() if "R$" in e.descricao)
        valores_ano = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in Entrega.query.filter(db.extract("year", Entrega.hora_pedido) == hoje_data.year).all() if "R$" in e.descricao)

        estatisticas = {
            "total_dia": total_dia,
            "total_mes": total_mes,
            "total_ano": total_ano,
            "valores_dia": round(valores_dia, 2),
            "valores_mes": round(valores_mes, 2),
            "valores_ano": round(valores_ano, 2)
        }

        cooperados = Usuario.query.filter_by(tipo="cooperado").all()
        return render_template("admin.html", cooperados=cooperados, entregas=entregas, estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim)

    else:
        data_inicio = request.args.get("data_inicio")
        data_fim = request.args.get("data_fim")
        hoje = datetime.now().date()
        if not data_inicio:
            data_inicio = hoje.strftime("%Y-%m-%d")
        if not data_fim:
            data_fim = hoje.strftime("%Y-%m-%d")

        inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
        fim_dt = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

        entregas = Entrega.query.filter(
            Entrega.cooperado_id == user_id,
            Entrega.hora_pedido >= inicio_dt,
            Entrega.hora_pedido <= fim_dt
        ).order_by(Entrega.hora_pedido.desc()).all()

        total_periodo = len(entregas)
        total_periodo_pago = sum(1 for e in entregas if e.status_pagamento == "pago")
        total_periodo_pendente = sum(1 for e in entregas if e.status_pagamento == "pendente")

        return render_template("cooperado.html",
                               entregas=entregas,
                               total_periodo=total_periodo,
                               total_periodo_pago=total_periodo_pago,
                               total_periodo_pendente=total_periodo_pendente,
                               data_inicio=data_inicio,
                               data_fim=data_fim)

if __name__ == "__main__":
    app.run(debug=True)
''')

# Compactar em ZIP
zip_path = shutil.make_archive("/mnt/data/app_flask_completo", 'zip', app_dir)
zip_path
