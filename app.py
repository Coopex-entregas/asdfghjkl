from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import pandas as pd
import io

app = Flask(__name__)
app.secret_key = "secreto"

# Altere a seguir para sua URL do PostgreSQL do Render
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://seu_usuario:senha@host:porta/banco"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# MODELOS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100))
    senha = db.Column(db.String(100))
    tipo = db.Column(db.String(20))  # 'adm' ou 'cooperado'

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_cliente = db.Column(db.String(100))
    cooperado_id = db.Column(db.Integer, db.ForeignKey("usuario.id"))
    valor = db.Column(db.Float)
    hora_pedido = db.Column(db.DateTime)
    hora_atribuida = db.Column(db.DateTime)
    status_pagamento = db.Column(db.String(20))
    status_entrega = db.Column(db.String(20))

class Espera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100))

# ROTAS
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]
        user = Usuario.query.filter_by(nome=nome, senha=senha).first()
        if user:
            session["user_id"] = user.id
            session["user_nome"] = user.nome
            session["user_tipo"] = user.tipo
            return redirect(url_for("dashboard")) if user.tipo == "adm" else redirect(url_for("painel_cooperado"))
        else:
            return "Login inválido"
    return render_template("index.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    data_inicio = request.form.get("data_inicio")
    data_fim = request.form.get("data_fim")
    cooperado_id = request.form.get("cooperado_id")

    query = Entrega.query
    if data_inicio and data_fim:
        data_i = datetime.strptime(data_inicio, "%Y-%m-%d")
        data_f = datetime.strptime(data_fim, "%Y-%m-%d")
        query = query.filter(Entrega.hora_pedido.between(data_i, data_f))
    if cooperado_id and cooperado_id != "todos":
        query = query.filter_by(cooperado_id=cooperado_id)

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()

    hoje = date.today()
    entregas_hoje = Entrega.query.filter(db.func.date(Entrega.hora_pedido) == hoje).all()
    total_hoje = sum(e.valor for e in entregas_hoje)
    mes_atual = datetime.now().month
    entregas_mes = Entrega.query.filter(db.extract("month", Entrega.hora_pedido) == mes_atual).all()
    total_mes = sum(e.valor for e in entregas_mes)
    ano_atual = datetime.now().year
    entregas_ano = Entrega.query.filter(db.extract("year", Entrega.hora_pedido) == ano_atual).all()
    total_ano = sum(e.valor for e in entregas_ano)

    estatisticas = {
        "total_hoje": total_hoje,
        "total_mes": total_mes,
        "total_ano": total_ano,
    }

    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    espera = Espera.query.all()

    return render_template("admin.html", entregas=entregas, estatisticas=estatisticas, cooperados=cooperados, espera=espera, data_inicio=data_inicio, data_fim=data_fim)

@app.route("/cadastrar-entrega", methods=["POST"])
def cadastrar_entrega():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))
    
    nome_cliente = request.form["nome_cliente"]
    cooperado_id = request.form.get("cooperado_id")
    valor = float(request.form["valor"])
    hora_pedido = datetime.now()
    hora_atribuida = datetime.now() if cooperado_id else None

    entrega = Entrega(
        nome_cliente=nome_cliente,
        cooperado_id=cooperado_id if cooperado_id else None,
        valor=valor,
        hora_pedido=hora_pedido,
        hora_atribuida=hora_atribuida,
        status_pagamento="pendente",
        status_entrega="pendente"
    )
    db.session.add(entrega)
    db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/excluir-entrega/<int:id>")
def excluir_entrega(id):
    entrega = Entrega.query.get(id)
    if entrega:
        db.session.delete(entrega)
        db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/painel-cooperado")
def painel_cooperado():
    if "user_id" not in session or session["user_tipo"] != "cooperado":
        return redirect(url_for("login"))
    user_id = session["user_id"]
    entregas = Entrega.query.filter_by(cooperado_id=user_id).order_by(Entrega.hora_pedido.desc()).all()

    hoje = date.today()
    dia = [e.valor for e in entregas if e.hora_pedido.date() == hoje]
    mes = [e.valor for e in entregas if e.hora_pedido.month == datetime.now().month]

    return render_template("cooperado.html", entregas=entregas, total_dia=sum(dia), total_mes=sum(mes))

@app.route("/exportar-excel", methods=["POST"])
def exportar_excel():
    data_inicio = request.form.get("data_inicio")
    data_fim = request.form.get("data_fim")
    cooperado_id = request.form.get("cooperado_id")

    query = Entrega.query
    if data_inicio and data_fim:
        data_i = datetime.strptime(data_inicio, "%Y-%m-%d")
        data_f = datetime.strptime(data_fim, "%Y-%m-%d")
        query = query.filter(Entrega.hora_pedido.between(data_i, data_f))
    if cooperado_id and cooperado_id != "todos":
        query = query.filter_by(cooperado_id=cooperado_id)

    entregas = query.all()

    data = []
    for e in entregas:
        cooperado = Usuario.query.get(e.cooperado_id)
        data.append({
            "Data": e.hora_pedido.strftime("%Y-%m-%d"),
            "Nome do Cliente": e.nome_cliente,
            "Hora do Pedido": e.hora_pedido.strftime("%H:%M"),
            "Hora Atribuída": e.hora_atribuida.strftime("%H:%M") if e.hora_atribuida else "",
            "Nome do Cooperado": cooperado.nome if cooperado else "",
            "Status de Pagamento": e.status_pagamento,
            "Status de Entrega": e.status_entrega,
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Entregas")
    output.seek(0)

    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

@app.route("/adicionar-espera", methods=["POST"])
def adicionar_espera():
    nome = request.form["nome"]
    if nome.strip():
        db.session.add(Espera(nome=nome.strip()))
        db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/remover-espera/<int:id>")
def remover_espera(id):
    item = Espera.query.get(id)
    if item:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for("dashboard"))

# CRIA TABELAS
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
