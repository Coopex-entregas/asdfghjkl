from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://banco_de_dados_qus2_user:o9OsVK4SDOxYahEyNI8DvrkTyji0nLLo@dpg-d1per5c9c44c738iiqr0-a.oregon-postgres.render.com/banco_de_dados_qus2')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default="admin")  # ou "cooperado"

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_recebido = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="pendente")  # 'pendente' ou 'recebido'
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)

with app.app_context():
    db.create_all()

# ROTAS

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['usuario']
        senha = request.form['senha']
        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and usuario.senha == senha:
            session['usuario_id'] = usuario.id
            session['usuario_tipo'] = usuario.tipo
            if usuario.tipo == "admin":
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha inválidos')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        if Cooperado.query.filter_by(nome=nome).first():
            flash('Já existe um cooperado com esse nome!')
            return redirect(url_for('cadastrar_cooperado'))
        novo = Cooperado(nome=nome)
        db.session.add(novo)
        db.session.commit()
        flash('Cooperado cadastrado com sucesso!')
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
        valor = float(request.form['valor'])
        cooperado_id = request.form.get('cooperado_id')
        if cooperado_id == '' or cooperado_id is None:
            cooperado_id = None
        nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, data_envio=datetime.utcnow(), status='pendente', cooperado_id=cooperado_id)
        db.session.add(nova)
        db.session.commit()
        flash('Entrega cadastrada!')
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
        entrega.valor = float(request.form['valor'])
        cooperado_id = request.form.get('cooperado_id')
        if cooperado_id == '' or cooperado_id is None:
            entrega.cooperado_id = None
        else:
            entrega.cooperado_id = int(cooperado_id)
        status = request.form.get('status')
        entrega.status = status
        if status == 'recebido' and not entrega.data_recebido:
            entrega.data_recebido = datetime.utcnow()
        elif status == 'pendente':
            entrega.data_recebido = None
        db.session.commit()
        flash('Entrega atualizada!')
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

@app.route('/painel_cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    usuario = Usuario.query.get(session['usuario_id'])
    cooperado = Cooperado.query.filter_by(nome=usuario.nome).first()
    entregas = Entrega.query.filter_by(cooperado_id=cooperado.id).order_by(Entrega.data_envio.desc()).all() if cooperado else []
    return render_template('cooperado.html', entregas=entregas, cooperado=cooperado)

@app.route('/marcar_recebido/<int:id>')
def marcar_recebido(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status = 'recebido'
    entrega.data_recebido = datetime.utcnow()
    db.session.commit()
    flash('Entrega marcada como recebida!')
    if session.get('usuario_tipo') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('painel_cooperado'))

@app.route('/marcar_pendente/<int:id>')
def marcar_pendente(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status = 'pendente'
    entrega.data_recebido = None
    db.session.commit()
    flash('Entrega marcada como pendente!')
    if session.get('usuario_tipo') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('painel_cooperado'))

@app.route('/estatisticas')
def estatisticas():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    # Dados resumidos para painel admin
    entregas = Entrega.query.all()
    total = sum(e.valor for e in entregas)
    recebidas = [e for e in entregas if e.status == 'recebido']
    total_recebidas = sum(e.valor for e in recebidas)
    return render_template('estatisticas.html', total=total, total_recebidas=total_recebidas, entregas=entregas)

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    dados = []
    for c in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=c.id).all()
        total = sum(e.valor for e in entregas)
        recebidas = [e for e in entregas if e.status == 'recebido']
        total_recebidas = sum(e.valor for e in recebidas)
        dados.append({
            'cooperado': c.nome,
            'total': total,
            'total_recebidas': total_recebidas,
            'quantidade': len(entregas)
        })
    return render_template('estatisticas_cooperado.html', dados=dados)

@app.route('/exportar_xlsx')
def exportar_xlsx():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.all()

    # Cada motoboy vira uma aba
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for cooperado in cooperados:
            entregas_coop = [e for e in entregas if e.cooperado_id == cooperado.id]
            data = []
            for e in entregas_coop:
                tempo = (e.data_recebido - e.data_envio).total_seconds() / 60 if e.data_recebido else None
                data.append({
                    'Cliente': e.cliente,
                    'Bairro': e.bairro,
                    'Valor': e.valor,
                    'Data Envio': e.data_envio.strftime('%d/%m/%Y %H:%M'),
                    'Data Recebido': e.data_recebido.strftime('%d/%m/%Y %H:%M') if e.data_recebido else '',
                    'Status': e.status,
                    'Tempo (min)': round(tempo, 1) if tempo else '',
                })
            df = pd.DataFrame(data)
            df.to_excel(writer, sheet_name=cooperado.nome[:31], index=False)
    output.seek(0)
    return send_file(output, download_name="relatorio_entregas.xlsx", as_attachment=True)

# Cria usuário admin padrão, se não existir
with app.app_context():
    if not Usuario.query.filter_by(nome='coopex').first():
        db.session.add(Usuario(nome='coopex', senha='05062721', tipo='admin'))
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)
