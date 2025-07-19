from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = 'chave_secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coopex.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    senha = db.Column(db.String(100), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), default='pendente')
    status_entrega = db.Column(db.String(20), default='pendente')

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        if nome == 'coopex' and senha == '05062721':
            session['user'] = 'adm'
            return redirect(url_for('admin'))
        cooperado = Cooperado.query.filter_by(nome=nome, senha=senha).first()
        if cooperado:
            session['user'] = 'cooperado'
            session['id'] = cooperado.id
            return redirect(url_for('cooperado'))
        flash('UsuÃ¡rio ou senha invÃ¡lidos.')
    return render_template('login.html')

@app.route('/admin')
def admin():
    if session.get('user') != 'adm':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cooperado')
def cooperado():
    if session.get('user') != 'cooperado':
        return redirect(url_for('login'))
    entregas = Entrega.query.filter_by(cooperado_id=session['id']).all()
    return render_template('cooperado.html', entregas=entregas)

@app.route('/cadastro_entrega', methods=['GET', 'POST'])
def cadastro_entrega():
    if session.get('user') != 'adm':
        return redirect(url_for('login'))
    if request.method == 'POST':
        desc = request.form['descricao']
        coop_id = request.form.get('cooperado_id')
        nova = Entrega(descricao=desc, cooperado_id=coop_id if coop_id else None)
        db.session.add(nova)
        db.session.commit()
        return redirect(url_for('admin'))
    cooperados = Cooperado.query.all()
    return render_template('cadastro_entrega.html', cooperados=cooperados)

@app.route('/cadastro_cooperado', methods=['GET', 'POST'])
def cadastro_cooperado():
    if session.get('user') != 'adm':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        novo = Cooperado(nome=nome, senha=senha)
        db.session.add(novo)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastro_cooperado.html')

@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    if request.method == 'POST':
        if session.get('user') == 'adm':
            entrega.descricao = request.form['descricao']
            entrega.cooperado_id = request.form.get('cooperado_id') or None
        entrega.status_pagamento = request.form['status_pagamento']
        entrega.status_entrega = request.form['status_entrega']
        db.session.commit()
        return redirect(url_for('admin' if session['user'] == 'adm' else 'cooperado'))
    cooperados = Cooperado.query.all()
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados, user_tipo=session.get('user'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
