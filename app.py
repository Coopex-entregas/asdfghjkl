from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import io
import pandas as pd

app = Flask(__name__)
app.secret_key = 'chave-secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coopex.db'  # ✅ Altere para PostgreSQL se necessário
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# MODELOS
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100))
    senha = db.Column(db.String(100))

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200))
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'))
    cooperado = db.relationship('Cooperado', backref='entregas')
    status_pagamento = db.Column(db.String(20), default='pendente')
    status_entrega = db.Column(db.String(20), default='pendente')

# ROTA RAIZ
@app.route('/')
def index():
    return render_template('login.html')

# LOGIN
@app.route('/login', methods=['POST'])
def login():
    nome = request.form['nome']
    senha = request.form['senha']
    if nome == 'coopex' and senha == '05062721':
        session['user_tipo'] = 'adm'
        session['user_nome'] = 'Administrador'
        return redirect('/admin')
    cooperado = Cooperado.query.filter_by(nome=nome, senha=senha).first()
    if cooperado:
        session['user_tipo'] = 'cooperado'
        session['user_id'] = cooperado.id
        session['user_nome'] = cooperado.nome
        return redirect('/dashboard')
    flash('Usuário ou senha inválidos.')
    return redirect('/')

# DASHBOARD COOPERADO
@app.route('/dashboard')
def dashboard():
    if session.get('user_tipo') != 'cooperado':
        return redirect('/')
    cooperado_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter_by(cooperado_id=cooperado_id)
    if inicio:
        query = query.filter(Entrega.hora_pedido >= inicio)
    if fim:
        query = query.filter(Entrega.hora_pedido <= fim + " 23:59:59")
    entregas = query.order_by(Entrega.hora_pedido.desc()).all()

    total_geral = sum([1 for e in entregas])
    total_pago = sum(1 for e in entregas if e.status_pagamento == 'pago')
    total_pendente = sum(1 for e in entregas if e.status_pagamento == 'pendente')

    return render_template('dashboard_cooperado.html',
        entregas=entregas,
        total_geral=total_geral,
        total_pago=total_pago,
        total_pendente=total_pendente
    )

# DASHBOARD ADMIN
@app.route('/admin')
def admin():
    if session.get('user_tipo') != 'adm':
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

# CADASTRO ENTREGA
@app.route('/cadastrar', methods=['POST'])
def cadastrar():
    if session.get('user_tipo') != 'adm':
        return redirect('/')
    descricao = request.form['descricao']
    cooperado_id = request.form.get('cooperado_id') or None
    if cooperado_id:
        hora_atribuida = datetime.utcnow()
    else:
        hora_atribuida = None

    entrega = Entrega(
        descricao=descricao,
        cooperado_id=cooperado_id,
        hora_atribuida=hora_atribuida
    )
    db.session.add(entrega)
    db.session.commit()
    return redirect('/admin')

# EDITAR ENTREGA
@app.route('/editar/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    if request.method == 'POST':
        if session.get('user_tipo') == 'adm':
            entrega.descricao = request.form['descricao']
            cooperado_id = request.form.get('cooperado_id') or None
            entrega.cooperado_id = cooperado_id
            if cooperado_id and not entrega.hora_atribuida:
                entrega.hora_atribuida = datetime.utcnow()
        entrega.status_pagamento = request.form['status_pagamento']
        entrega.status_entrega = request.form['status_entrega']
        db.session.commit()
        return redirect('/admin' if session['user_tipo'] == 'adm' else '/dashboard')

    cooperados = Cooperado.query.all()
    return render_template('editar_entrega.html',
        entrega=entrega,
        cooperados=cooperados,
        user_tipo=session.get('user_tipo')
    )

# EXPORTAR EXCEL
@app.route('/exportar')
def exportar():
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    dados = []
    for e in entregas:
        dados.append({
            'ID': e.id,
            'Descrição': e.descricao,
            'Hora do Pedido': e.hora_pedido.strftime('%Y-%m-%d %H:%M:%S'),
            'Hora Atribuída': e.hora_atribuida.strftime('%Y-%m-%d %H:%M:%S') if e.hora_atribuida else '',
            'Nome do Cooperado': e.cooperado.nome if e.cooperado else '',
            'Status Pagamento': e.status_pagamento,
            'Status Entrega': e.status_entrega,
        })
    df = pd.DataFrame(dados)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Entregas')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='entregas.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# LOGOUT
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# INICIALIZAR DB
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
