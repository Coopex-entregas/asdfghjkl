from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
from io import BytesIO
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'super_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default='admin') # admin ou cooperado

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    inss = db.Column(db.String(100), nullable=True)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=True)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_recebido = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='Pendente')  # 'Pendente' ou 'Recebido'
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    cooperado = db.relationship('Cooperado', backref=db.backref('entregas', lazy=True))

# CRIAR USUÁRIO ADMIN PADRÃO SE NÃO EXISTIR
with app.app_context():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        db.session.add(Usuario(nome='coopex', senha='05062721', tipo='admin'))
        db.session.commit()

# LOGIN
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome, senha=senha).first()
        if user:
            session['usuario_id'] = user.id
            session['usuario_nome'] = user.nome
            session['usuario_tipo'] = user.tipo
            if user.tipo == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado', cooperado_id=user.id))
        else:
            flash('Usuário ou senha incorretos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# PAINEL ADMIN
@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

# CADASTRAR COOPERADO
@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        inss = request.form.get('inss')
        db.session.add(Cooperado(nome=nome, inss=inss))
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

# CADASTRAR ENTREGA
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = float(request.form['valor'])
        cooperado_id = request.form.get('cooperado_id')
        entrega = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            cooperado_id=cooperado_id if cooperado_id else None,
            data_envio=datetime.utcnow(),
            status='Pendente'
        )
        db.session.add(entrega)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

# EDITAR ENTREGA
@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.cooperado_id = request.form.get('cooperado_id')
        entrega.status = request.form['status']
        if entrega.status == 'Recebido':
            entrega.data_recebido = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

# PAINEL COOPERADO
@app.route('/painel_cooperado/<int:cooperado_id>')
def painel_cooperado(cooperado_id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(cooperado_id)
    entregas = Entrega.query.filter_by(cooperado_id=cooperado.id).all()
    return render_template('cooperado.html', cooperado=cooperado, entregas=entregas)

# ALTERAR STATUS ENTREGA PELO COOPERADO
@app.route('/alterar_status_entrega/<int:entrega_id>', methods=['POST'])
def alterar_status_entrega(entrega_id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    if entrega.cooperado_id != session['usuario_id']:
        return redirect(url_for('painel_cooperado', cooperado_id=session['usuario_id']))
    entrega.status = request.form['status']
    if entrega.status == 'Recebido':
        entrega.data_recebido = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('painel_cooperado', cooperado_id=session['usuario_id']))

# ESTATÍSTICAS DO COOPERADO
@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    stats = []
    for coop in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=coop.id, status='Recebido').all()
        total_valor = sum(e.valor for e in entregas)
        total_entregas = len(entregas)
        stats.append({
            'cooperado': coop.nome,
            'total_entregas': total_entregas,
            'total_valor': total_valor
        })
    return render_template('estatisticas.html', stats=stats)

# EXPORTAÇÃO .XLSX
@app.route('/exportar')
def exportar():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    writer = pd.ExcelWriter(BytesIO(), engine='xlsxwriter')
    for coop in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=coop.id).all()
        rows = []
        for e in entregas:
            tempo = ""
            if e.data_recebido and e.data_envio:
                tempo = str(e.data_recebido - e.data_envio)
            rows.append({
                'Cliente': e.cliente,
                'Bairro': e.bairro,
                'Valor': e.valor,
                'Data Envio': e.data_envio,
                'Data Recebido': e.data_recebido,
                'Tempo': tempo,
                'Status': e.status,
                'Cooperado': coop.nome
            })
        df = pd.DataFrame(rows)
        df.to_excel(writer, sheet_name=coop.nome[:30] or "Cooperado", index=False)
    writer.close()
    output = writer.book.filename
    output.seek(0)
    return send_file(output, download_name='entregas_cooperados.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
