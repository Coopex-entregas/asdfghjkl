from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Banco de dados PostgreSQL no Render ou local
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///local.db").replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# MODELOS

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    senha_hash = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(10), default="coop")  # 'adm' ou 'coop'

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    status_pagamento = db.Column(db.String(50), default='pendente')
    status_entrega = db.Column(db.String(50), default='pendente')
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    hora_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    valor = db.Column(db.Float, default=0.0)

# ROTAS

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['email']
        senha = request.form['senha']
        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and check_password_hash(usuario.senha_hash, senha):
            session['usuario_id'] = usuario.id
            session['nome'] = usuario.nome
            if usuario.tipo == 'adm':
                return redirect('/admin')
            else:
                return redirect('/cooperado')
        return render_template('login.html', erro='Credenciais inválidas.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/admin')
def admin():
    if 'usuario_id' not in session:
        return redirect('/')
    usuario = Usuario.query.get(session['usuario_id'])
    if usuario.tipo != 'adm':
        return redirect('/')
    entregas = Entrega.query.all()
    cooperados = Usuario.query.filter_by(tipo='coop').all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cooperado')
def cooperado():
    if 'usuario_id' not in session:
        return redirect('/')
    entregas = Entrega.query.filter_by(cooperado_id=session['usuario_id']).all()
    return render_template('cooperado.html', entregas=entregas)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        novo = Usuario(
            nome=nome,
            email=email,
            senha_hash=generate_password_hash(senha),
            tipo="coop"
        )
        db.session.add(novo)
        db.session.commit()
        return redirect('/admin')
    return render_template('cadastrar_cooperado.html')

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    cooperados = Usuario.query.filter_by(tipo='coop').all()
    if request.method == 'POST':
        descricao = request.form['descricao']
        cooperado_id = request.form.get('cooperado_id')
        valor = float(request.form.get('valor', 0.0))
        nova = Entrega(
            descricao=descricao,
            cooperado_id=cooperado_id if cooperado_id else None,
            valor=valor,
            hora_atribuida=datetime.utcnow() if cooperado_id else None
        )
        db.session.add(nova)
        db.session.commit()
        return redirect('/admin')
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Usuario.query.filter_by(tipo='coop').all()
    if request.method == 'POST':
        entrega.descricao = request.form['descricao']
        entrega.status_pagamento = request.form['status_pagamento']
        entrega.status_entrega = request.form['status_entrega']
        entrega.cooperado_id = request.form.get('cooperado_id')
        entrega.valor = float(request.form.get('valor', 0.0))
        db.session.commit()
        return redirect('/admin')
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

@app.route('/estatisticas')
def estatisticas():
    total_entregas = Entrega.query.count()
    total_recebidas = Entrega.query.filter_by(status_pagamento='recebido').count()
    total_pendentes = Entrega.query.filter_by(status_pagamento='pendente').count()
    total_valor = db.session.query(db.func.sum(Entrega.valor)).scalar() or 0.0
    return render_template('estatisticas.html', total_entregas=total_entregas, total_recebidas=total_recebidas, total_pendentes=total_pendentes, total_valor=total_valor)

@app.route('/setup')
def setup():
    db.create_all()
    return "Tabelas criadas com sucesso!"

@app.route('/criar_admin')
def criar_admin():
    if Usuario.query.filter_by(nome="coopex").first():
        return "⚠️ Admin 'coopex' já existe."
    admin = Usuario(
        nome="coopex",
        email="adm@adm.com",
        senha_hash=generate_password_hash("05062721"),
        tipo="adm"
    )
    db.session.add(admin)
    db.session.commit()
    return "✅ Admin 'coopex' criado com sucesso! Senha: 05062721"

# Início local (desabilitado no Render)
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
