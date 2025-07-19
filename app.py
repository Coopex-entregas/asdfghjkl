from flask import Flask, render_template, request, redirect, session, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# ✅ Substitua pela sua string do banco:
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://banco_de_dados_qus2_user:o9OsVK4SDOxYahEyNI8DvrkTyji0nLLo@dpg-d1per5c9c44c738iiqr0-a.oregon-postgres.render.com/banco_de_dados_qus2'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ✅ MODELO DE ENTREGA
class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_cliente = db.Column(db.String(120))
    nome_cooperado = db.Column(db.String(120))
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime)
    status_pagamento = db.Column(db.String(50), default="Pendente")
    status_entrega = db.Column(db.String(50), default="Pendente")

# ✅ ROTAS
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    user = request.form['usuario']
    senha = request.form['senha']
    if user == 'coopex' and senha == '05289':
        session['admin'] = True
        return redirect('/admin')
    return 'Login inválido'

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    return render_template('admin.html', entregas=entregas)

@app.route('/cadastrar', methods=['POST'])
def cadastrar():
    nome_cliente = request.form['nome_cliente']
    nome_cooperado = request.form['nome_cooperado']
    bairro = request.form['bairro']
    valor = float(request.form['valor'])
    hora_pedido = datetime.utcnow()
    nova = Entrega(
        nome_cliente=nome_cliente,
        nome_cooperado=nome_cooperado,
        bairro=bairro,
        valor=valor,
        hora_pedido=hora_pedido,
        hora_atribuida=datetime.utcnow()
    )
    db.session.add(nova)
    db.session.commit()
    return redirect('/admin')

@app.route('/exportar')
def exportar():
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    data = [{
        'Data': e.hora_pedido.strftime('%Y-%m-%d'),
        'Nome do Cliente': e.nome_cliente,
        'Hora do Pedido': e.hora_pedido.strftime('%H:%M:%S'),
        'Hora Atribuída': e.hora_atribuida.strftime('%H:%M:%S') if e.hora_atribuida else '',
        'Nome do Cooperado': e.nome_cooperado,
        'Status de Pagamento': e.status_pagamento,
        'Status da Entrega': e.status_entrega,
    } for e in entregas]
    df = pd.DataFrame(data)
    df.to_excel('entregas_exportadas.xlsx', index=False)
    return 'Exportado com sucesso!'

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
