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

@app.route("/dashboard")
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
        return render_template("admin.html", entregas=entregas, estatisticas=estatisticas,
                               cooperados=cooperados, data_inicio=data_inicio, data_fim=data_fim)

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

        total_geral = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in entregas if "R$" in e.descricao)
        total_pago = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in entregas if e.status_pagamento == "pago" and "R$" in e.descricao)
        total_pendente = sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in entregas if e.status_pagamento == "pendente" and "R$" in e.descricao)

        return render_template("cooperado.html", entregas=entregas,
                               total_periodo=total_periodo,
                               total_periodo_pago=total_periodo_pago,
                               total_periodo_pendente=total_periodo_pendente,
                               total_geral=round(total_geral, 2),
                               total_pago=round(total_pago, 2),
                               total_pendente=round(total_pendente, 2),
                               data_inicio=data_inicio, data_fim=data_fim)

@app.route("/estatisticas")
def estatisticas_cooperado():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    cooperado_id = request.args.get("cooperado_id", "todos")
    hoje = date.today()

    if cooperado_id == "todos":
        cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    else:
        cooperados = Usuario.query.filter_by(id=cooperado_id).all()

    total = pagas = pendentes = total_valor = 0
    for coop in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=coop.id).all()
        total += len(entregas)
        pagas += sum(1 for e in entregas if e.status_pagamento == "pago")
        pendentes += sum(1 for e in entregas if e.status_pagamento == "pendente")
        total_valor += sum(float(e.descricao.split("R$")[-1].replace(",", ".")) for e in entregas if "R$" in e.descricao)

    estatisticas = {
        "total": total,
        "pagas": pagas,
        "pendentes": pendentes,
        "total_valor": total_valor
    }

    return render_template("estatisticas.html", estatisticas=estatisticas, cooperados=cooperados, cooperado_id=cooperado_id)

@app.route("/cadastrar_entrega", methods=["GET", "POST"])
def cadastrar_entrega():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    cooperados = Usuario.query.filter_by(tipo="cooperado").all()

    if request.method == "POST":
        descricao = request.form["descricao"]
        hora_pedido = datetime.strptime(request.form["hora_pedido"], "%Y-%m-%dT%H:%M")
        cooperado_id = request.form.get("cooperado_id")

        entrega = Entrega(
            descricao=descricao,
            hora_pedido=hora_pedido,
            hora_atribuida=hora_pedido if cooperado_id else None,
            cooperado_id=int(cooperado_id) if cooperado_id else None
        )
        db.session.add(entrega)
        db.session.commit()
        flash("Entrega cadastrada com sucesso.")
        return redirect(url_for("dashboard"))

    return render_template("cadastrar_entrega.html", cooperados=cooperados)

@app.route("/cadastrar_cooperado", methods=["GET", "POST"])
def cadastrar_cooperado():
    if "user_id" not in session or session["user_tipo"] != "adm":
        return redirect(url_for("login"))

    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        if Usuario.query.filter_by(nome=nome).first():
            flash("Usuário já existe.")
            return redirect(url_for("cadastrar_cooperado"))

        novo = Usuario(
            nome=nome,
            senha_hash=generate_password_hash(senha),
            tipo="cooperado"
        )
        db.session.add(novo)
        db.session.commit()
        flash("Cooperado cadastrado com sucesso.")
        return redirect(url_for("dashboard"))

    return render_template("cadastrar_cooperado.html")

@app.route("/editar_entrega/<int:entrega_id>", methods=["GET", "POST"])
def editar_entrega(entrega_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Usuario.query.filter_by(tipo="cooperado").all()
    user_tipo = session["user_tipo"]

    if request.method == "POST":
        if user_tipo == "adm":
            entrega.descricao = request.form["descricao"]
            coop_id = request.form.get("cooperado_id")
            entrega.cooperado_id = int(coop_id) if coop_id else None
            entrega.hora_atribuida = datetime.now() if coop_id else None
        entrega.status_pagamento = request.form["status_pagamento"]
        entrega.status_entrega = request.form["status_entrega"]
        db.session.commit()
        flash("Entrega atualizada com sucesso.")
        return redirect(url_for("dashboard"))

    return render_template("editar_entrega.html", entrega=entrega, cooperados=cooperados, user_tipo=user_tipo)

@app.route("/exportar")
def exportar_excel():
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    cooperado_id = request.args.get("cooperado_id", "todos")

    inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
    fim_dt = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    query = Entrega.query.filter(Entrega.hora_pedido >= inicio_dt, Entrega.hora_pedido <= fim_dt)
    if cooperado_id != "todos":
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    entregas = query.all()

    data = []
    for e in entregas:
        data.append({
            "Data": e.hora_pedido.strftime("%d/%m/%Y"),
            "Nome do Cliente": e.descricao,
            "Hora do Pedido": e.hora_pedido.strftime("%H:%M"),
            "Hora Atribuída": e.hora_atribuida.strftime("%H:%M") if e.hora_atribuida else "",
            "Nome do Cooperado": e.cooperado.nome if e.cooperado else "",
            "Status de Pagamento": e.status_pagamento,
            "Status da Entrega": e.status_entrega
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Entregas")
    output.seek(0)

    return send_file(output, as_attachment=True, download_name="entregas.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/excluir_entrega/<int:entrega_id>", methods=["POST"])
def excluir_entrega(entrega_id):
    entrega = Entrega.query.get(entrega_id)
    if entrega:
        db.session.delete(entrega)
        db.session.commit()
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)