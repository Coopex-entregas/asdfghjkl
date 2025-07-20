from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import os

app = Flask(__name__)
app.secret_key = 'chave_super_secreta'

# Banco de dados PostgreSQL via Render
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# MODELOS

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), default='cooperado')  # 'adm' ou 'cooperado'

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    valor = db.Column(db.Float, default=0.0)
    status_pagamento = db.Column(db.String(20), default='pendente')
    status_entrega = db.Column(db.String(20), default='pendente')
    cooperado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    cooperado = db.relationship('Usuario', backref='entregas')

# ROTA DE LOGIN
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def autenticar():
    nome = request.form.get('nome')
    senha = request.form.get('senha')

    usuario = Usuario.query.filter_by(nome=nome).first()
    if usuario and check_password_hash(usuario.senha_hash, senha):
        session['usuario_id'] = usuario.id
        session['nome'] = usuario.nome
        session['tipo'] = usuario.tipo
        if usuario.tipo == 'adm':
            return redirect(url_for('admin'))
        else:
            return redirect(url_for('painel_cooperado'))
    else:
        flash('Usuário ou senha inválidos.')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# PAINEL ADMIN
@app.route('/admin')
def admin():
    if session.get('tipo') != 'adm':
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Usuario.query.filter_by(tipo='cooperado').all()

    # Filtros (data e cooperado)
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    cooperado_id = request.args.get('cooperado_id', 'todos')

    query = Entrega.query

    if cooperado_id != 'todos':
        query = query.filter_by(cooperado_id=cooperado_id)

    if data_inicio:
        query = query.filter(Entrega.hora_pedido >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(Entrega.hora_pedido <= datetime.strptime(data_fim + " 23:59:59", '%Y-%m-%d %H:%M:%S'))

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()

    # Estatísticas
    hoje = date.today()
    este_mes = hoje.month
    este_ano = hoje.year

    estatisticas = {
        "total_dia": Entrega.query.filter(db.func.date(Entrega.hora_pedido) == hoje).count(),
        "total_mes": Entrega.query.filter(db.extract('month', Entrega.hora_pedido) == este_mes).count(),
        "total_ano": Entrega.query.filter(db.extract('year', Entrega.hora_pedido) == este_ano).count(),
        "valores_dia": round(db.session.query(db.func.sum(Entrega.valor)).filter(db.func.date(Entrega.hora_pedido) == hoje).scalar() or 0.0, 2),
        "valores_mes": round(db.session.query(db.func.sum(Entrega.valor)).filter(db.extract('month', Entrega.hora_pedido) == este_mes).scalar() or 0.0, 2),
        "valores_ano": round(db.session.query(db.func.sum(Entrega.valor)).filter(db.extract('year', Entrega.hora_pedido) == este_ano).scalar() or 0.0, 2)
    }

    return render_template('admin.html', entregas=entregas, cooperados=cooperados, estatisticas=estatisticas,
                           data_inicio=data_inicio, data_fim=data_fim)

# PAINEL COOPERADO
@app.route('/cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('tipo') != 'cooperado':
        return redirect('/')
    entregas = Entrega.query.filter_by(cooperado_id=session['usuario_id']).order_by(Entrega.hora_pedido.desc()).all()
    return render_template('cooperado.html', entregas=entregas)

# CADASTRAR COOPERADO
@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        novo = Usuario(nome=nome, senha_hash=generate_password_hash(senha), tipo='cooperado')
        db.session.add(novo)
        db.session.commit()
        return redirect('/admin')
    return render_template('cadastrar_cooperado.html')

# CADASTRAR ENTREGA
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    cooperados = Usuario.query.filter_by(tipo='cooperado').all()
    if request.method == 'POST':
        descricao = request.form['descricao']
        valor = float(request.form.get('valor', 0.0))
        cooperado_id = request.form.get('cooperado_id')
        nova = Entrega(
            descricao=descricao,
            valor=valor,
            cooperado_id=cooperado_id if cooperado_id else None,
            hora_atribuida=datetime.utcnow() if cooperado_id else None
        )
        db.session.add(nova)
        db.session.commit()
        return redirect('/admin')
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

# EDITAR ENTREGA
@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Usuario.query.filter_by(tipo='cooperado').all()
    if request.method == 'POST':
        entrega.descricao = request.form['descricao']
        entrega.valor = float(request.form['valor'])
        entrega.status_entrega = request.form['status_entrega']
        entrega.status_pagamento = request.form['status_pagamento']
        entrega.cooperado_id = request.form.get('cooperado_id')
        db.session.commit()
        return redirect('/admin')
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

# EXCLUIR ENTREGA
@app.route('/excluir_entrega/<int:entrega_id>', methods=['POST'])
def excluir_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    db.session.delete(entrega)
    db.session.commit()
    return redirect('/admin')

# CRIAR TABELAS NO BANCO
@app.route('/setup')
def setup():
    db.create_all()
    return "Tabelas criadas com sucesso!"

# INICIALIZAÇÃO LOCAL
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
