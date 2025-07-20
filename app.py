from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from io import BytesIO
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

# URL do PostgreSQL: substitua pelos seus dados se necessário!
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or "sqlite:///banco.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # 'admin' ou 'cooperado'

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))
    cooperado = db.Column(db.String(100), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_atribuicao = db.Column(db.DateTime, nullable=True)
    data_recebimento = db.Column(db.DateTime, nullable=True)

# CRIAR O BANCO E USUÁRIO ADMIN NO PRIMEIRO START
with app.app_context():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        user = Usuario(nome='coopex', senha=generate_password_hash('05062721'), tipo='admin')
        db.session.add(user)
        db.session.commit()

# ROTAS

@app.route('/')
def home():
    if 'usuario_id' in session:
        user = Usuario.query.get(session['usuario_id'])
        if user.tipo == 'admin':
            return redirect(url_for('admin'))
        else:
            return redirect(url_for('painel_cooperado'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and check_password_hash(usuario.senha, senha):
            session['usuario_id'] = usuario.id
            session['usuario_tipo'] = usuario.tipo
            if usuario.tipo == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha incorretos.')
            return render_template('login.html')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        if not Cooperado.query.filter_by(nome=nome).first():
            cooperado = Cooperado(nome=nome)
            db.session.add(cooperado)
            db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = request.form['valor']
        cooperado = request.form.get('cooperado') or None
        entrega = Entrega(cliente=cliente, bairro=bairro, valor=valor, status='pendente', cooperado=cooperado)
        if cooperado:
            entrega.data_atribuicao = datetime.utcnow()
        db.session.add(entrega)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = request.form['valor']
        entrega.status = request.form['status']
        cooperado_novo = request.form.get('cooperado') or None
        if cooperado_novo and cooperado_novo != entrega.cooperado:
            entrega.cooperado = cooperado_novo
            entrega.data_atribuicao = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

@app.route('/marcar_recebido/<int:id>')
def marcar_recebido(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status = 'recebido'
    entrega.data_recebimento = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('painel_cooperado' if session['usuario_tipo'] == 'cooperado' else 'admin'))

@app.route('/painel_cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    user = Usuario.query.get(session['usuario_id'])
    entregas = Entrega.query.filter_by(cooperado=user.nome).all()
    return render_template('cooperado.html', entregas=entregas, nome=user.nome)

@app.route('/exportar')
def exportar():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    cooperados = Cooperado.query.all()
    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    for cooperado in cooperados:
        entregas_coop = Entrega.query.filter_by(cooperado=cooperado.nome).all()
        data = []
        for e in entregas_coop:
            tempo_envio = None
            if e.data_atribuicao and e.data_criacao:
                tempo_envio = (e.data_atribuicao - e.data_criacao).total_seconds() / 60  # minutos
            tempo_entrega = None
            if e.data_recebimento and e.data_atribuicao:
                tempo_entrega = (e.data_recebimento - e.data_atribuicao).total_seconds() / 60  # minutos
            data.append([
                e.id, e.cliente, e.bairro, e.valor, e.status, e.cooperado,
                e.data_criacao.strftime('%d/%m/%Y %H:%M:%S') if e.data_criacao else '',
                e.data_atribuicao.strftime('%d/%m/%Y %H:%M:%S') if e.data_atribuicao else '',
                e.data_recebimento.strftime('%d/%m/%Y %H:%M:%S') if e.data_recebimento else '',
                tempo_envio, tempo_entrega
            ])
        df = pd.DataFrame(data, columns=[
            'ID', 'Cliente', 'Bairro', 'Valor', 'Status', 'Cooperado',
            'Data Criação', 'Data Atribuição', 'Data Recebimento',
            'Tempo até atribuir (min)', 'Tempo entrega (min)'
        ])
        df.to_excel(writer, sheet_name=cooperado.nome, index=False)
    writer.close()
    output.seek(0)
    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

# --------- ESTATÍSTICAS (Exemplo Rota, pode melhorar se quiser) ---------
@app.route('/estatisticas')
def estatisticas():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    stats = []
    for coop in cooperados:
        total = Entrega.query.filter_by(cooperado=coop.nome, status='recebido').count()
        stats.append((coop.nome, total))
    return render_template('estatisticas.html', stats=stats)

# --------- ERROS ---------
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

# --------- INÍCIO ---------
if __name__ == '__main__':
    app.run(debug=True)
