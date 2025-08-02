import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, time
import pandas as pd
import io
import holidays
import pytz

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

# ====== MODELS ======
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    senha_hash = db.Column(db.String(128), nullable=False)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(50), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)  # "Pago" ou "Pendente"
    status = db.Column(db.String(20), nullable=True)            # "recebido"/"pendente"
    pagamento = db.Column(db.String(20), nullable=False, default="Dinheiro")
    recebido_por = db.Column(db.String(50), nullable=True)
    cooperado = db.relationship('Cooperado', backref='entregas')

# ====== Funções Auxiliares ======
def to_brasilia(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(BRAZIL_TZ)

def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

def verifica_feriado(data=None):
    if data is None:
        data = datetime.now(BRAZIL_TZ).date()
    feriados_brasil = holidays.Brazil(years=data.year)
    feriados_rn = holidays.Brazil(state='RN', years=data.year)
    feriados_natal = {
        datetime(data.year, 12, 25).date(): "Natal (Municipal)",
    }
    feriados_hoje = []
    if data in feriados_brasil:
        feriados_hoje.append("Feriado Nacional: " + feriados_brasil.get(data))
    if data in feriados_rn and feriados_rn.get(data) != feriados_brasil.get(data):
        feriados_hoje.append("Feriado Estadual RN: " + feriados_rn.get(data))
    if data in feriados_natal:
        feriados_hoje.append("Feriado Municipal Natal: " + feriados_natal[data])
    return " | ".join(feriados_hoje) if feriados_hoje else None

# ====== ROTAS ======
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        senha = request.form.get('senha')
        if usuario.lower() == 'coopex':
            if senha == '05062721':
                session['user_id'] = 0
                session['user_nome'] = 'Coopex'
                session['is_admin'] = True
                return redirect(url_for('admin'))
            else:
                flash('Usuário ou senha incorretos.')
        else:
            cooperado = Cooperado.query.filter(func.lower(Cooperado.nome) == usuario.lower()).first()
            if cooperado and cooperado.check_senha(senha):
                session['user_id'] = cooperado.id
                session['user_nome'] = cooperado.nome
                session['is_admin'] = False
                return redirect(url_for('painel_cooperado'))
            else:
                flash('Usuário ou senha incorretos.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    cooperado_id = request.args.get('cooperado_id', 'todos')
    status_pagamento = request.args.get('status_pagamento', 'todos')
    query = Entrega.query

    # Sempre filtrar pelo dia do Brasil, caso não tenha filtro manual
    if not data_inicio and not data_fim:
        hoje_brasil = datetime.now(BRAZIL_TZ).date()
        inicio_brasil = BRAZIL_TZ.localize(datetime.combine(hoje_brasil, time.min))
        fim_brasil = BRAZIL_TZ.localize(datetime.combine(hoje_brasil, time.max))
        inicio_utc = inicio_brasil.astimezone(pytz.utc)
        fim_utc = fim_brasil.astimezone(pytz.utc)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)
    if cooperado_id and cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        data_inicio_br = BRAZIL_TZ.localize(datetime.strptime(data_inicio, "%Y-%m-%d"))
        query = query.filter(Entrega.data_envio >= data_inicio_br.astimezone(pytz.utc))
    if data_fim:
        data_fim_br = BRAZIL_TZ.localize(datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
        query = query.filter(Entrega.data_envio <= data_fim_br.astimezone(pytz.utc))
    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))
    entregas_all = query.order_by(Entrega.data_envio.desc()).all()
    nao_atribuidos = [e for e in entregas_all if not e.cooperado_id]
    atribuidos = [e for e in entregas_all if e.cooperado_id]
    entregas = nao_atribuidos + atribuidos
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    hoje = datetime.now(BRAZIL_TZ).date()
    total_dia = Entrega.query.filter(
        Entrega.data_envio >= BRAZIL_TZ.localize(datetime.combine(hoje, time.min)).astimezone(pytz.utc),
        Entrega.data_envio <= BRAZIL_TZ.localize(datetime.combine(hoje, time.max)).astimezone(pytz.utc)
    ).count()
    # Correção: retirado .astimezone() das colunas para evitar erro
    total_mes = Entrega.query.filter(
        func.extract('month', Entrega.data_envio) == hoje.month,
        func.extract('year', Entrega.data_envio) == hoje.year
    ).count()
    total_ano = Entrega.query.filter(
        func.extract('year', Entrega.data_envio) == hoje.year
    ).count()
    estatisticas = {"total_dia": total_dia, "total_mes": total_mes, "total_ano": total_ano}
    feriado_hoje = verifica_feriado(hoje)
    tem_pendente = Entrega.query.filter(
        Entrega.data_envio >= BRAZIL_TZ.localize(datetime.combine(hoje, time.min)).astimezone(pytz.utc),
        Entrega.data_envio <= BRAZIL_TZ.localize(datetime.combine(hoje, time.max)).astimezone(pytz.utc),
        (Entrega.status_pagamento == None) | (Entrega.status_pagamento.ilike('pendente'))
    ).count() > 0
    return render_template('admin.html', entregas=entregas, cooperados=cooperados,
                           estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim,
                           to_brasilia=to_brasilia, request=request, now=lambda: datetime.now(BRAZIL_TZ),
                           feriado_hoje=feriado_hoje, tem_pendente=tem_pendente)

@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('user_id') is None or session.get('is_admin'):
        return redirect(url_for('login'))
    user_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == user_id)
    # Sempre mostra só as entregas do dia Brasil (salvo se filtrar)
    if not inicio and not fim:
        hoje_brasil = datetime.now(BRAZIL_TZ).date()
        inicio_brasil = BRAZIL_TZ.localize(datetime.combine(hoje_brasil, time.min))
        fim_brasil = BRAZIL_TZ.localize(datetime.combine(hoje_brasil, time.max))
        inicio_utc = inicio_brasil.astimezone(pytz.utc)
        fim_utc = fim_brasil.astimezone(pytz.utc)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)
    if inicio:
        data_inicio_br = BRAZIL_TZ.localize(datetime.strptime(inicio, "%Y-%m-%d"))
        query = query.filter(Entrega.data_envio >= data_inicio_br.astimezone(pytz.utc))
    if fim:
        data_fim_br = BRAZIL_TZ.localize(datetime.strptime(fim, "%Y-%m-%d") + timedelta(days=1))
        query = query.filter(Entrega.data_envio <= data_fim_br.astimezone(pytz.utc))
    entregas = query.order_by(Entrega.data_envio.desc()).all()
    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago')
    total_pendente = total_geral - total_pago
    return render_template('painel_cooperado.html', entregas=entregas, total_geral=total_geral,
                           total_pago=total_pago, total_pendente=total_pendente, request=request,
                           to_brasilia=to_brasilia)

# ... as demais rotas ficam idênticas, não precisam ser alteradas para isso ...

def criar_bd():
    with app.app_context():
        db.create_all()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
