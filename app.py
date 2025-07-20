import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = 'secretao'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///dados.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), default='admin')

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True)
    inss_complemento = db.Column(db.String(50), nullable=True)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))
    cooperado = db.Column(db.String(100), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_entrega = db.Column(db.DateTime, nullable=True)

# CRIAÇÃO DE TABELAS E USUÁRIO PADRÃO
with app.app_context():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        senha_hash = generate_password_hash('05062721')
        novo = Usuario(nome='coopex', senha=senha_hash, tipo='admin')
        db.session.add(novo)
        db.session.commit()

# ROTAS
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and check_password_hash(usuario.senha, senha):
            session['usuario'] = usuario.nome
            session['tipo'] = usuario.tipo
            return redirect(url_for('admin' if usuario.tipo == 'admin' else 'painel_cooperado'))
        else:
            flash('Login inválido')
    return render_template('login.html')

@app.route('/admin')
def admin():
    if session.get('tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/nova_entrega', methods=['POST'])
def nova_entrega():
    if session.get('tipo') != 'admin':
        return redirect(url_for('login'))
    cliente = request.form['cliente']
    bairro = request.form['bairro']
    valor = float(request.form['valor'])
    cooperado = request.form.get('cooperado') or None
    nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, status='pendente', cooperado=cooperado)
    db.session.add(nova)
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/editar_entrega/<int:id>', methods=['POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    entrega.cooperado = request.form['cooperado']
    entrega.status = request.form['status']
    entrega.data_entrega = datetime.utcnow() if entrega.status == 'recebido' else None
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/cadastrar_cooperado', methods=['POST'])
def cadastrar_cooperado():
    nome = request.form['nome']
    email = request.form['email']
    inss = request.form.get('inss_complemento')
    novo = Cooperado(nome=nome, email=email, inss_complemento=inss)
    db.session.add(novo)
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('tipo') != 'cooperado':
        return redirect(url_for('login'))
    nome = session.get('usuario')
    entregas = Entrega.query.filter_by(cooperado=nome).order_by(Entrega.data_criacao.desc()).all()
    return render_template('cooperado.html', entregas=entregas)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/exportar')
def exportar():
    entregas = Entrega.query.all()
    df = pd.DataFrame([{
        'Cliente': e.cliente,
        'Bairro': e.bairro,
        'Valor': e.valor,
        'Status': e.status,
        'Cooperado': e.cooperado,
        'Data Coleta': e.data_criacao,
        'Data Entrega': e.data_entrega,
        'Duração': str(e.data_entrega - e.data_criacao) if e.data_entrega else ''
    } for e in entregas])
    caminho = 'export.xlsx'
    df.to_excel(caminho, index=False)
    return send_file(caminho, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
