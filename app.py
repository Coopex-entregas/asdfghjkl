from flask import Flask, render_template, request, redirect, session, send_file
from flask_sqlalchemy import SQLAlchemy
from models import db, Usuario, Cooperado, Entrega
from datetime import datetime
import pandas as pd
import io

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'secreto'
db.init_app(app)

@app.before_first_request
def create_tables():
    db.create_all()

@app.route('/', methods=["GET", "POST"])
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]
        user = Usuario.query.filter_by(nome=nome, senha=senha).first()
        if user:
            session["user"] = user.nome
            return redirect("/admin")
        else:
            return render_template("login.html", error="Usuário ou senha incorretos.")
    return render_template("login.html")

@app.route('/admin')
def admin():
    if "user" not in session:
        return redirect("/login")
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    return render_template("admin.html", entregas=entregas)

@app.route('/cooperado/<nome>')
def cooperado(nome):
    entregas = Entrega.query.filter_by(cooperado=nome).order_by(Entrega.data_criacao.desc()).all()
    return render_template("cooperado.html", entregas=entregas, nome=nome)

@app.route('/cadastrar_entrega', methods=["GET", "POST"])
def cadastrar_entrega():
    if request.method == "POST":
        cliente = request.form["cliente"]
        bairro = request.form["bairro"]
        valor = request.form["valor"]
        cooperado = request.form.get("cooperado", None)
        nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, cooperado=cooperado, status="Pendente")
        db.session.add(nova)
        db.session.commit()
        return redirect("/admin")
    cooperados = Cooperado.query.all()
    return render_template("cadastrar_entrega.html", cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=["GET", "POST"])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    if request.method == "POST":
        entrega.status = request.form["status"]
        entrega.cooperado = request.form["cooperado"]
        entrega.data_entrega = datetime.utcnow()
        db.session.commit()
        return redirect("/admin")
    cooperados = Cooperado.query.all()
    return render_template("editar_entrega.html", entrega=entrega, cooperados=cooperados)

@app.route('/cadastrar_cooperado', methods=["GET", "POST"])
def cadastrar_cooperado():
    if request.method == "POST":
        nome = request.form["nome"]
        novo = Cooperado(nome=nome)
        db.session.add(novo)
        db.session.commit()
        return redirect("/admin")
    return render_template("cadastrar_cooperado.html")

@app.route('/exportar')
def exportar():
    entregas = Entrega.query.all()
    dados = {}
    for e in entregas:
        nome = e.cooperado or "Sem cooperado"
        if nome not in dados:
            dados[nome] = []
        dados[nome].append({
            "Cliente": e.cliente,
            "Bairro": e.bairro,
            "Valor": e.valor,
            "Status": e.status,
            "Data Coleta": e.data_criacao,
            "Data Entrega": e.data_entrega,
            "Duração (min)": ((e.data_entrega - e.data_criacao).total_seconds() / 60) if e.data_entrega else None
        })
    with pd.ExcelWriter("entregas.xlsx") as writer:
        for cooperado, lista in dados.items():
            df = pd.DataFrame(lista)
            df.to_excel(writer, sheet_name=cooperado[:31], index=False)
    return send_file("entregas.xlsx", as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
