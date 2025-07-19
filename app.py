import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'segredo'

# Conexão com o banco via variável de ambiente
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# -------------------- MODELOS -------------------- #
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_cliente = db.Column(db.String(100), nullable=False)
    nome_cooperado = db.Column(db.String(100), nullable=True)
    bairro = db.Column(db.String(100), nullable=True)
    valor = db.Column(db.Float, nullable=True)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    status_pagamento = db.Column(db.String(50), default='Pendente')
    status_entrega = db.Column(db.String(50), default='Pendente')

# -------------------- ROTAS -------------------- #
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    user = request.form['nome']
    password = request.form['senha']
    if user == 'coopex' and password == '05062721':
        session['admin'] = True
        return redirect(url_for('admin'))
    else:
        flash('Login incorreto')
        return redirect(url_for('index'))

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('index'))

    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()

    hoje = datetime.now().date()
    ano_atual = hoje.year
    mes_atual = hoje.month

    total_dia = sum(e.valor for e in entregas if e.hora_pedido.date() == hoje)
    total_mes = sum(e.valor for e in entregas if e.hora_pedido.month == mes_atual and e.hora_pedido.year == ano_atual)
    total_ano = sum(e.valor for e in entregas if e.hora_pedido.year == ano_atual)
    total_entregas = len(entregas)

    return render_template('admin.html', entregas=entregas,
                           total_dia=total_dia, total_mes=total_mes,
                           total_ano=total_ano, total_entregas=total_entregas)

@app.route('/cadastrar_entrega', methods=['POST'])
def cadastrar_entrega():
    if 'admin' not in session:
        return redirect(url_for('index'))

    nome_cliente = request.form['nome_cliente']
    nome_cooperado = request.form.get('nome_cooperado')
    bairro = request.form.get('bairro')
    valor = float(request.form.get('valor') or 0.0)

    nova_entrega = Entrega(
        nome_cliente=nome_cliente,
        nome_cooperado=nome_cooperado,
        bairro=bairro,
        valor=valor,
        hora_pedido=datetime.now(),
        hora_atribuida=datetime.now() if nome_cooperado else None
    )
    db.session.add(nova_entrega)
    db.session.commit()

    return redirect(url_for('admin'))

@app.route('/atualizar_status/<int:id>', methods=['POST'])
def atualizar_status(id):
    if 'admin' not in session:
        return redirect(url_for('index'))

    entrega = Entrega.query.get_or_404(id)
    entrega.status_pagamento = request.form.get('status_pagamento', entrega.status_pagamento)
    entrega.status_entrega = request.form.get('status_entrega', entrega.status_entrega)
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/excluir_entrega/<int:id>', methods=['POST'])
def excluir_entrega(id):
    if 'admin' not in session:
        return redirect(url_for('index'))

    entrega = Entrega.query.get_or_404(id)
    db.session.delete(entrega)
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/exportar_excel')
def exportar_excel():
    if 'admin' not in session:
        return redirect(url_for('index'))

    entregas = Entrega.query.all()

    data = [{
        'Data': e.hora_pedido.strftime('%d/%m/%Y %H:%M'),
        'Nome do Cliente': e.nome_cliente,
        'Hora do Pedido': e.hora_pedido.strftime('%H:%M'),
        'Hora Atribuída': e.hora_atribuida.strftime('%H:%M') if e.hora_atribuida else '',
        'Nome do Cooperado': e.nome_cooperado or '',
        'Status de Pagamento': e.status_pagamento,
        'Status da Entrega': e.status_entrega
    } for e in entregas]

    df = pd.DataFrame(data)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Entregas')

    output.seek(0)
    return send_file(output, download_name='entregas.xlsx', as_attachment=True)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

# -------------------- INICIAR -------------------- #
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
