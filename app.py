from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://banco_de_dados_umjo_user:RhyjcVd65ByuboYnBhTR5O4za6CkQbWZ@dpg-d1ukc36mcj7s73ek6v00-a.oregon-postgres.render.com:5432/banco_de_dados_umjo'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default="admin")  # admin ou cooperado

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    data_recebido = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="pendente")
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True, default="Pendente")

def checar_e_adicionar_coluna_status_pagamento():
    with app.app_context():
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        colunas = [c['name'] for c in insp.get_columns('entrega')]
        if 'status_pagamento' not in colunas:
            try:
                db.session.execute(text('ALTER TABLE entrega ADD COLUMN status_pagamento VARCHAR(20) DEFAULT \'Pendente\';'))
                db.session.commit()
            except Exception as e:
                db.session.rollback()

def inicializar_banco():
    with app.app_context():
        db.create_all()
        checar_e_adicionar_coluna_status_pagamento()
        if not Usuario.query.filter_by(nome='coopex').first():
            db.session.add(Usuario(nome='coopex', senha='05062721', tipo='admin'))
            db.session.commit()

inicializar_banco()

def to_brasilia(dt):
    if dt:
        return dt - timedelta(hours=3)
    return None

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
            session['user_nome'] = usuario.nome
            if usuario.tipo == "admin":
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))

        cooperado = Cooperado.query.filter_by(nome=nome).first()
        if cooperado and cooperado.senha == senha:
            session['usuario_id'] = cooperado.id
            session['usuario_tipo'] = "cooperado"
            session['user_nome'] = cooperado.nome
            return redirect(url_for('painel_cooperado'))

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

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    query = Entrega.query

    if cooperado_id != 'todos':
        try:
            cooperado_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == cooperado_id_int)
        except:
            pass

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.all()

    now_brasilia = datetime.utcnow() - timedelta(hours=3)
    total_dia = sum(e.valor for e in entregas if to_brasilia(e.data_envio).date() == now_brasilia.date())
    total_mes = sum(e.valor for e in entregas if to_brasilia(e.data_envio).month == now_brasilia.month and to_brasilia(e.data_envio).year == now_brasilia.year)
    total_ano = sum(e.valor for e in entregas if to_brasilia(e.data_envio).year == now_brasilia.year)

    estatisticas = {
        'total_dia': total_dia,
        'total_mes': total_mes,
        'total_ano': total_ano
    }

    return render_template('admin.html', entregas=entregas, cooperados=cooperados, estatisticas=estatisticas,
                           data_inicio=data_inicio, data_fim=data_fim, to_brasilia=to_brasilia)

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    query = Entrega.query

    if cooperado_id != 'todos':
        try:
            cooperado_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == cooperado_id_int)
        except:
            pass

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.all()
    cooperados = Cooperado.query.all()

    total = len(entregas)
    pagas = len([e for e in entregas if e.status == 'recebido'])
    pendentes = total - pagas
    total_valor = sum(e.valor for e in entregas)

    estatisticas = {
        'total': total,
        'pagas': pagas,
        'pendentes': pendentes,
        'total_valor': total_valor
    }

    return render_template('estatisticas_cooperado.html', cooperados=cooperados, estatisticas=estatisticas,
                           cooperado_id=cooperado_id, data_inicio=data_inicio, data_fim=data_fim, to_brasilia=to_brasilia)

@app.route('/painel_cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get(session['usuario_id'])
    if not cooperado:
        flash('Cooperado não encontrado!')
        return redirect(url_for('login'))
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == cooperado.id)

    if inicio:
        try:
            dt_inicio = datetime.strptime(inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if fim:
        try:
            dt_fim = datetime.strptime(fim, '%Y-%m-%d')
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.data_envio.desc()).all()

    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if getattr(e, 'status', 'pendente') == 'recebido')
    total_pendente = total_geral - total_pago

    return render_template('painel_cooperado.html', entregas=entregas, total_geral=total_geral,
                           total_pago=total_pago, total_pendente=total_pendente, to_brasilia=to_brasilia)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        if Cooperado.query.filter_by(nome=nome).first():
            flash('Já existe um cooperado com esse nome.')
        else:
            db.session.add(Cooperado(nome=nome, senha=senha))
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
        nova_entrega = Entrega(cliente=cliente, bairro=bairro, valor=valor, data_envio=datetime.utcnow(), cooperado_id=cooperado_id if cooperado_id else None)
        db.session.add(nova_entrega)
        db.session.commit()
        flash('Entrega cadastrada com sucesso!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega_admin/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega_admin(entrega_id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.status = request.form.get('status', entrega.status)
        entrega.cooperado_id = request.form.get('cooperado_id', entrega.cooperado_id)
        db.session.commit()
        flash('Entrega editada com sucesso!')
        return redirect(url_for('admin'))
    return render_template('editar_entrega_admin.html', entrega=entrega, cooperados=cooperados)

@app.route('/editar_entrega_cooperado/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega_cooperado(entrega_id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    if entrega.cooperado_id != session['usuario_id']:
        flash('Acesso negado!')
        return redirect(url_for('painel_cooperado'))
    if request.method == 'POST':
        entrega.status = request.form.get('status', entrega.status)
        entrega.data_recebido = datetime.utcnow() if entrega.status == 'recebido' else None
        db.session.commit()
        flash('Entrega atualizada com sucesso!')
        return redirect(url_for('painel_cooperado'))
    return render_template('editar_entrega_cooperado.html', entrega=entrega)

if __name__ == '__main__':
    app.run(debug=True)
