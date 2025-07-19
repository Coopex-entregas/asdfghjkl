import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import pandas as pd

app = Flask(__name__)
app.secret_key = "secreto"

# Configuração do banco de dados
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = "postgresql://usuario:senha@host:5432/nome_do_banco"
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Modelos
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100))
    login = db.Column(db.String(100), unique=True)
    senha = db.Column(db.String(100))
    tipo = db.Column(db.String(20))

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100))
    descricao = db.Column(db.String(200))
    cooperado_id = db.Column(db.Integer, db.ForeignKey("usuario.id"))
    cooperado = db.relationship("Usuario", backref="entregas")
    hora_pedido = db.Column(db.DateTime)
    hora_atribuida = db.Column(db.DateTime)
    status_pagamento = db.Column(db.String(20))
    status_entrega = db.Column(db.String(20))

# Simples lista de espera (memória temporária)
lista_espera = []

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login = request.form.get("login")
        senha = request.form.get("senha")
        user = Usuario.query.filter_by(login=login, senha=senha).first()
        if user:
            session["user_id"] = user.id
            session["user_tipo"] = user.tipo
            return redirect(url_for("painel"))
        else:
            return "Login inválido"
    return render_template("login.html")

@app.route("/painel")
def painel():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = Usuario.query.get(session["user_id"])
    return redirect(url_for("painel_admin" if user.tipo == "adm" else "painel_cooperado"))

@app.route("/painel_admin", methods=["GET", "POST"])
def painel_admin():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    if request.method == "POST":
        cliente = request.form.get("cliente")
        descricao = request.form.get("descricao")
        cooperado_id = request.form.get("cooperado_id")
        now = datetime.now()
        entrega = Entrega(
            cliente=cliente,
            descricao=descricao,
            cooperado_id=cooperado_id,
            hora_pedido=now,
            hora_atribuida=now,
            status_pagamento="pendente",
            status_entrega="pendente"
        )
        db.session.add(entrega)
        db.session.commit()
        return redirect(url_for("painel_admin"))

    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    return render_template("admin.html", entregas=entregas, cooperados=cooperados, lista_espera=lista_espera)

@app.route("/deletar_entrega/<int:id>")
def deletar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    db.session.delete(entrega)
    db.session.commit()
    return redirect(url_for("painel_admin"))

@app.route("/painel_cooperado")
def painel_cooperado():
    if "user_id" not in session or session["user_tipo"] != "cooperado":
        return redirect(url_for("login"))
    entregas = Entrega.query.filter_by(cooperado_id=session["user_id"]).order_by(Entrega.hora_pedido.desc()).all()
    return render_template("cooperado.html", entregas=entregas)

@app.route("/estatisticas")
def estatisticas():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    cooperado_id = request.args.get("cooperado_id", "todos")

    hoje = date.today()
    if not data_inicio:
        data_inicio = hoje.replace(day=1).strftime("%Y-%m-%d")
    if not data_fim:
        data_fim = hoje.strftime("%Y-%m-%d")

    inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
    fim_dt = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    query = Entrega.query.filter(Entrega.hora_pedido >= inicio_dt, Entrega.hora_pedido <= fim_dt)
    if cooperado_id != "todos":
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    entregas = query.all()
    total = len(entregas)
    pagas = sum(1 for e in entregas if e.status_pagamento == "pago")
    pendentes = total - pagas
    total_valor = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in entregas if "R$" in e.descricao)

    estatisticas = {
        "total": total,
        "pagas": pagas,
        "pendentes": pendentes,
        "total_valor": round(total_valor, 2)
    }

    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    return render_template("estatisticas.html", estatisticas=estatisticas, cooperados=cooperados,
                           data_inicio=data_inicio, data_fim=data_fim, cooperado_id=cooperado_id)

@app.route("/exportar")
def exportar():
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    dados = []
    for e in entregas:
        dados.append({
            "Cliente": e.cliente,
            "Descrição": e.descricao,
            "Data": e.hora_pedido.strftime("%d/%m/%Y %H:%M"),
            "Atribuída": e.hora_atribuida.strftime("%d/%m/%Y %H:%M") if e.hora_atribuida else "",
            "Cooperado": e.cooperado.nome if e.cooperado else "",
            "Pagamento": e.status_pagamento,
            "Entrega": e.status_entrega
        })
    df = pd.DataFrame(dados)
    caminho = "entregas.xlsx"
    df.to_excel(caminho, index=False)
    return send_file(caminho, as_attachment=True)

@app.route("/adicionar_espera", methods=["POST"])
def adicionar_espera():
    nome = request.form.get("nome_espera")
    if nome and nome not in lista_espera:
        lista_espera.append(nome)
    return redirect(url_for("painel_admin"))

@app.route("/remover_espera/<string:nome>")
def remover_espera(nome):
    if nome in lista_espera:
        lista_espera.remove(nome)
    return redirect(url_for("painel_admin"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.cli.command("criar-banco")
def criar_banco():
    db.create_all()
    print("Banco criado!")

if __name__ == "__main__":
    app.run(debug=True)
