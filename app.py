from flask import Flask, render_template, request, redirect, url_for, send_file
from models import db, Usuario, Cooperado, Entrega
from datetime import datetime
import pandas as pd
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SECRET_KEY'] = 'seu_segredo'
db.init_app(app)

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/setup')
def setup():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        user = Usuario(nome='coopex', senha='05062721', tipo='admin')
        db.session.add(user)
        db.session.commit()
    return 'Setup concluído! Usuário admin criado.'

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome, senha=senha).first()
        if user:
            return redirect(url_for('admin'))
        return 'Login incorreto.'
    return render_template('login.html')

@app.route('/admin')
def admin():
    entregas = Entrega.query.all()
    return render_template('admin.html', entregas=entregas)

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if request.method == 'POST':
        nova = Entrega(
            cliente=request.form['cliente'],
            bairro=request.form['bairro'],
            valor=float(request.form['valor']),
            status='pendente',
            cooperado=request.form.get('cooperado'),
            data_criacao=datetime.now()
        )
        db.session.add(nova)
        db.session.commit()
        return redirect(url_for('admin'))
    cooperados = Cooperado.query.all()
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.status = request.form['status']
        entrega.cooperado = request.form.get('cooperado')
        db.session.commit()
        return redirect(url_for('admin'))
    cooperados = Cooperado.query.all()
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if request.method == 'POST':
        cooperado = Cooperado(nome=request.form['nome'])
        db.session.add(cooperado)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

@app.route('/exportar')
def exportar():
    entregas = Entrega.query.all()
    dados = [{
        'Cliente': e.cliente,
        'Bairro': e.bairro,
        'Valor': e.valor,
        'Status': e.status,
        'Cooperado': e.cooperado,
        'Data Criacao': e.data_criacao
    } for e in entregas]
    df = pd.DataFrame(dados)
    arquivo = 'entregas.xlsx'
    df.to_excel(arquivo, index=False)
    return send_file(arquivo, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
