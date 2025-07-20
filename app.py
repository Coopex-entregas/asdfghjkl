from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = 'chave_super_segura'

# Configuração do banco (Render usa DATABASE_URL)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///local.db").replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# MODELOS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), default='coop')  # adm ou coop
    entregas = db.relationship('Entrega', backref='cooperado', lazy=True)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    valor = db.Column(db.Float, default=0.0)
    status_pagamento = db.Column(db.String(20), default='pendente')
    status_entrega = db.Column(db.String(20), default='pendente')
    cooperado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)

# LOGIN
@app.route('/')
def login():
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
            return redirect('/admin')
        else:
            return redirect('/cooperado')
    return "Login inválido"

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ADMIN
@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('tipo') != 'adm':
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Usuario.query.filter_by(tipo='coop').all()
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    cooperado_id = request.args.get('cooperado_id')
    if data_inicio and data_fim:
        entregas = Entrega.query.filter(
            Entrega.hora_pedido >= data_inicio,
            Entrega.hora_pedido <= data_fim
        ).all()
    if cooperado_id and cooperado_id != 'todos':
        entregas = [e for e in entregas if str(e.cooperado_id) == cooperado_id]
    return render_template('admin.html', entregas=entregas, cooperados=cooperados, data_inicio=data_inicio, data_fim=data_fim)

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if 'usuario_id' not in session or session.get('tipo') != 'adm':
        return redirect('/')
    cooperado_id = request.args.get('cooperado_id')
    entregas = Entrega.query.all()
    now = datetime.utcnow()
    def filtrar_por_tempo(qs, cond):
        return sum(e.valor for e in qs if cond(e.hora_pedido))
    estatisticas = {
        'total_dia': len([e for e in entregas if e.hora_pedido.date() == now.date()]),
        'total_mes': len([e for e in entregas if e.hora_pedido.month == now.month]),
        'total_ano': len([e for e in entregas if e.hora_pedido.year == now.year]),
        'valores_dia': filtrar_por_tempo(entregas, lambda d: d.date() == now.date()),
        'valores_mes': filtrar_por_tempo(entregas, lambda d: d.month == now.month),
        'valores_ano': filtrar_por_tempo(entregas, lambda d: d.year == now.year)
    }
    return render_template('estatisticas.html', estatisticas=estatisticas)

@app.route('/exportar')
def exportar_entregas():
    entregas = Entrega.query.all()
    dados = []
    for e in entregas:
        dados.append({
            'ID': e.id,
            'Descrição': e.descricao,
            'Hora Pedido': e.hora_pedido.strftime("%d/%m/%Y %H:%M"),
            'Hora Atribuída': e.hora_atribuida.strftime("%d/%m/%Y %H:%M") if e.hora_atribuida else '',
            'Valor': e.valor,
            'Status Pagamento': e.status_pagamento,
            'Status Entrega': e.status_entrega,
            'Cooperado': e.cooperado.nome if e.cooperado else ''
        })
    df = pd.DataFrame(dados)
    df.to_excel("entregas.xlsx", index=False)
    return "Exportado com sucesso"

@app.route('/excluir/<int:entrega_id>', methods=['POST'])
def excluir_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    db.session.delete(entrega)
    db.session.commit()
    return redirect('/admin')

# COOPERADO
@app.route('/cooperado')
def cooperado():
    if 'usuario_id' not in session or session.get('tipo') != 'coop':
        return redirect('/')
    entregas = Entrega.query.filter_by(cooperado_id=session['usuario_id']).order_by(Entrega.hora_pedido.desc()).all()
    return render_template('cooperado.html', entregas=entregas)

# CRIAÇÃO DO ADMIN INICIAL
@app.route('/criar_admin')
def criar_admin():
    if not Usuario.query.filter_by(nome='coopex').first():
        hash_senha = generate_password_hash("05062721")
        admin = Usuario(nome='coopex', senha_hash=hash_senha, tipo='adm')
        db.session.add(admin)
        db.session.commit()
        return "Admin criado com sucesso!"
    return "Admin já existe."

# ROTA PARA CRIAR TABELAS
@app.route('/setup')
def setup():
    db.create_all()
    return "Tabelas criadas com sucesso!"

# EXECUÇÃO LOCAL
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
