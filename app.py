from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'segredo_super_seguro'

# Render usa banco PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///meubanco.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # 'adm' ou 'coop'

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status_pagamento = db.Column(db.String(20), default='Pendente')
    status_entrega = db.Column(db.String(20), default='Pendente')
    motoboy = db.Column(db.String(100))
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime)

# ROTA PRINCIPAL
@app.route('/')
def index():
    return redirect(url_for('login'))

# LOGIN
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']

        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and check_password_hash(usuario.senha, senha):
            session['usuario'] = usuario.nome
            session['tipo'] = usuario.tipo
            if usuario.tipo == 'adm':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))
        else:
            flash("Usuário ou senha inválidos.")
            return redirect(url_for('login'))

    return render_template('login.html')

# PAINEL DO ADMIN
@app.route('/admin')
def admin():
    if 'usuario' not in session or session.get('tipo') != 'adm':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    return render_template('admin.html', entregas=entregas)

# PAINEL DO COOPERADO
@app.route('/cooperado')
def painel_cooperado():
    if 'usuario' not in session or session.get('tipo') != 'coop':
        return redirect(url_for('login'))
    entregas = Entrega.query.filter_by(motoboy=session['usuario']).all()
    return render_template('cooperado.html', entregas=entregas)

# LOGOUT
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# SETUP INICIAL
@app.route('/setup')
def setup():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        novo = Usuario(
            nome='coopex',
            senha=generate_password_hash('05062721'),
            tipo='adm'
        )
        db.session.add(novo)
        db.session.commit()
        return "✅ Admin criado com sucesso"
    return "⚠️ Admin já existe"

if __name__ == '__main__':
    app.run(debug=True)
