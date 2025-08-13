import os
import io
import json
from datetime import datetime, timedelta, time, date
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import text
from werkzeug.security import generate_password_hash, check_password_hash

import pandas as pd
import holidays
import pytz

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')

# Banco
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Performance de conexão (especialmente em Postgres)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": 5,
    "max_overflow": 10,
}

db = SQLAlchemy(app)

# Fuso do Brasil (Natal/RN segue America/Sao_Paulo)
BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

# ====== MODELS ======
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
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
    # guardado em UTC naive
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)  # "Pago"/"Pendente"
    status = db.Column(db.String(20), nullable=True)            # "recebido"/"pendente"
    pagamento = db.Column(db.String(50), nullable=False)        # forma de pagamento
    recebido_por = db.Column(db.String(100), nullable=True)

    cooperado = db.relationship('Cooperado', backref='entregas')

class ListaEspera(db.Model):
    """
    Compatível com schema antigo (id, nome NOT NULL).
    Agora usamos também:
      - cooperado_id: FK pro cooperado
      - pos: posição na fila
      - created_at: data de criação
    Mantemos 'nome' para não quebrar telas que já usam item.nome.
    """
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)  # Mantido p/ compatibilidade
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    pos = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)

    cooperado = db.relationship('Cooperado', lazy='joined')

# ====== helpers datas ======
def to_brasilia(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(BRAZIL_TZ)

def local_date_window_to_utc_range(local_date: date):
    inicio_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.min))
    fim_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.max))
    inicio_utc = inicio_brasil.astimezone(pytz.utc).replace(tzinfo=None)
    fim_utc = fim_brasil.astimezone(pytz.utc).replace(tzinfo=None)
    return inicio_utc, fim_utc

def month_range_utc(local_date: date):
    first = local_date.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year+1, month=1, day=1)
    else:
        next_first = first.replace(month=first.month+1, day=1)
    return local_date_window_to_utc_range(first)[0], local_date_window_to_utc_range(next_first - timedelta(days=1))[1]

def year_range_utc(local_date: date):
    first = local_date.replace(month=1, day=1)
    next_first = first.replace(year=first.year+1)
    return local_date_window_to_utc_range(first)[0], local_date_window_to_utc_range(next_first - timedelta(days=1))[1]

def parse_local_datetime_to_utc_naive(data_str: str):
    dt_local_naive = datetime.strptime(data_str, '%Y-%m-%dT%H:%M')
    dt_local = BRAZIL_TZ.localize(dt_local_naive)
    dt_utc = dt_local.astimezone(pytz.utc)
    return dt_utc.replace(tzinfo=None)

def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

# ====== feriados (Nacional + RN + Natal) ======
MUNICIPAIS_NATAL = {(11, 21): "Nossa Senhora da Apresentação (Municipal - Natal/RN)"}

def verifica_feriado(data_ref=None):
    if data_ref is None:
        data_ref = datetime.now(BRAZIL_TZ).date()
    feriados_nac = holidays.Brazil(years=data_ref.year)
    feriados_est = holidays.Brazil(state='RN', years=data_ref.year)
    nomes = []
    if data_ref in feriados_nac:
        nomes.append(f"Feriado Nacional – {feriados_nac.get(data_ref)}")
    if data_ref in feriados_est and feriados_est.get(data_ref) != feriados_nac.get(data_ref):
        nomes.append(f"Feriado Estadual (RN) – {feriados_est.get(data_ref)}")
    key = (data_ref.month, data_ref.day)
    if key in MUNICIPAIS_NATAL:
        nomes.append(f"Feriado Municipal (Natal/RN) – {MUNICIPAIS_NATAL[key]}")
    return " | ".join(nomes) if nomes else None

def periodo_legivel_str(di_str, df_str):
    if di_str and df_str:
        di = datetime.strptime(di_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        df = datetime.strptime(df_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        return f"{di} a {df}"
    if di_str:
        di = datetime.strptime(di_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        return f"desde {di}"
    if df_str:
        df = datetime.strptime(df_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        return f"até {df}"
    return "todo o período"

# ====== Preservar filtros do /admin ======
@app.before_request
def remember_admin_filters():
    # guarda últimos filtros do /admin na sessão
    if request.endpoint == "admin" and request.method == "GET":
        keys = ["cooperado_id", "data_inicio", "data_fim", "status_pagamento", "cliente"]
        session["last_filters"] = {k: request.args.get(k) for k in keys if request.args.get(k)}

def _build_admin_url_from_referrer():
    ref = request.headers.get("Referer") or ""
    if not ref:
        return None
    try:
        p = urlparse(ref)
        if not p.path.endswith("/admin"):
            return None
        qs = parse_qs(p.query)
        params = {k: v[0] for k, v in qs.items() if v}
        return url_for("admin", **params)
    except Exception:
        return None

def redirect_back_to_admin():
    next_url = request.args.get("next") or request.form.get("next")
    if next_url:
        return redirect(next_url)
    from_ref = _build_admin_url_from_referrer()
    if from_ref:
        return redirect(from_ref)
    params = session.get("last_filters") or {}
    return redirect(url_for("admin", **params))

# ====== ROTAS ======
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        senha = request.form.get('senha')
        if usuario and usuario.lower() == 'coopex':
            if senha == '05062721':
                session['user_id'] = 0
                session['user_nome'] = 'Coopex'
                session['is_admin'] = True
                return redirect(url_for('admin'))
            else:
                flash('Usuário ou senha incorretos.')
        else:
            cooperado = Cooperado.query.filter(func.lower(Cooperado.nome) == (usuario or '').lower()).first()
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
    cliente = (request.args.get('cliente') or '').strip()

    query = Entrega.query

    if not data_inicio and not data_fim:
        hoje_brasil = datetime.now(BRAZIL_TZ).date()
        inicio_utc, fim_utc = local_date_window_to_utc_range(hoje_brasil)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)

    if cooperado_id and cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)

    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    entregas_all = (
        query
        .options(joinedload(Entrega.cooperado))      # performance
        .order_by(Entrega.data_envio.desc())
        .all()
    )
    nao_atribuidos = [e for e in entregas_all if not e.cooperado_id]
    atribuidos = [e for e in entregas_all if e.cooperado_id]
    entregas = nao_atribuidos + atribuidos

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    hoje = datetime.now(BRAZIL_TZ).date()
    inicio_dia_utc, fim_dia_utc = local_date_window_to_utc_range(hoje)
    # Contagens usando ranges (aproveita índice em data_envio)
    total_dia = Entrega.query.filter(Entrega.data_envio >= inicio_dia_utc,
                                     Entrega.data_envio <= fim_dia_utc).count()
    mes_ini_utc, mes_fim_utc = month_range_utc(hoje)
    total_mes = Entrega.query.filter(Entrega.data_envio >= mes_ini_utc,
                                     Entrega.data_envio <= mes_fim_utc).count()
    ano_ini_utc, ano_fim_utc = year_range_utc(hoje)
    total_ano = Entrega.query.filter(Entrega.data_envio >= ano_ini_utc,
                                     Entrega.data_envio <= ano_fim_utc).count()
    estatisticas = {"total_dia": total_dia, "total_mes": total_mes, "total_ano": total_ano}

    feriado_hoje = verifica_feriado(hoje)
    tem_pendente = Entrega.query.filter(
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc,
        (Entrega.status_pagamento == None) | (Entrega.status_pagamento.ilike('pendente'))
    ).count() > 0

    # Fila de espera
    lista_espera = (
        ListaEspera.query
        .order_by(ListaEspera.pos.asc().nulls_last(), ListaEspera.created_at.asc().nulls_last())
        .all()
    )
    # Cooperados disponíveis para adicionar à fila: quem não está na fila
    ids_em_fila = {it.cooperado_id for it in lista_espera if it.cooperado_id}
    cooperados_disponiveis = [c for c in cooperados if c.id not in ids_em_fila]

    return render_template(
        'admin.html',
        entregas=entregas, cooperados=cooperados,
        estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim,
        to_brasilia=to_brasilia, request=request, now=lambda: datetime.now(BRAZIL_TZ),
        feriado_hoje=feriado_hoje, tem_pendente=tem_pendente,
        lista_espera=lista_espera, cooperados_disponiveis=cooperados_disponiveis
    )

@app.route('/clonar_entrega/<int:id>', methods=['POST'])
def clonar_entrega(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    nova = Entrega(
        cliente=e.cliente,
        bairro=e.bairro,
        valor=e.valor,
        data_envio=datetime.utcnow(),   # agora
        data_atribuida=None,            # nao atribui
        cooperado_id=None,              # nao clona cooperado
        status='pendente',
        status_pagamento='Pendente',
        pagamento=e.pagamento,
        recebido_por=None
    )
    db.session.add(nova)
    db.session.commit()
    flash(f'Entrega #{e.id} clonada em #{nova.id}. Edite para atribuir um cooperado.')
    return redirect_back_to_admin()

@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('user_id') is None or session.get('is_admin'):
        return redirect(url_for('login'))
    user_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == user_id)

    if not inicio and not fim:
        hoje_brasil = datetime.now(BRAZIL_TZ).date()
        inicio_utc, fim_utc = local_date_window_to_utc_range(hoje_brasil)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)
    if inicio:
        di = datetime.strptime(inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if fim:
        df = datetime.strptime(fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)

    entregas = (
        query.options(joinedload(Entrega.cooperado))  # performance
        .order_by(Entrega.data_envio.desc())
        .all()
    )
    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago')
    total_pendente = total_geral - total_pago
    return render_template('painel_cooperado.html', entregas=entregas, total_geral=total_geral,
                           total_pago=total_pago, total_pendente=total_pendente, request=request,
                           to_brasilia=to_brasilia)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form.get('nome')
        senha = request.form.get('senha')
        if Cooperado.query.filter(func.lower(Cooperado.nome) == (nome or '').lower()).first():
            flash('Já existe um cooperado com esse nome.')
        else:
            c = Cooperado(nome=nome)
            c.set_senha(senha)
            db.session.add(c)
            db.session.commit()
            flash('Cooperado cadastrado!')
            return redirect_back_to_admin()
    return render_template('cadastrar_cooperado.html')

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    if request.method == 'POST':
        cliente = request.form.get('cliente')
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor'))
        cooperado_id = request.form.get('cooperado_id')
        pagamento = request.form.get('pagamento')

        entrega = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            data_envio=datetime.utcnow(),
            status_pagamento='Pendente',
            status='pendente',
            pagamento=pagamento
        )
        if cooperado_id:
            entrega.cooperado_id = int(cooperado_id)
            entrega.data_atribuida = datetime.utcnow()
        db.session.add(entrega)

        # Remove da fila se atribuiu cooperado
        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()
        flash('Entrega cadastrada!')
        return redirect_back_to_admin()
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/agendar_entrega', methods=['GET', 'POST'])
def agendar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    if request.method == 'POST':
        cliente = request.form.get('cliente')
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor'))
        data_str = request.form.get('data')  # 'YYYY-MM-DDTHH:MM'
        status_entrega = request.form.get('status_entrega')
        status_pagamento = request.form.get('status_pagamento')
        cooperado_id = request.form.get('cooperado_id')
        pagamento = request.form.get('pagamento')

        data_envio = parse_local_datetime_to_utc_naive(data_str)

        entrega = Entrega(
            cliente=cliente, bairro=bairro, valor=valor,
            data_envio=data_envio,
            cooperado_id=int(cooperado_id) if cooperado_id else None,
            status=status_entrega,
            status_pagamento=status_pagamento,
            pagamento=pagamento
        )
        db.session.add(entrega)

        # Remove da fila se atribuiu cooperado
        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()
        flash('Entrega agendada!')
        return redirect_back_to_admin()
    return render_template('agendar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    is_admin = session.get('is_admin')
    if not is_admin and entrega.cooperado_id != session['user_id']:
        flash("Acesso não permitido.")
        return redirect(url_for('painel_cooperado'))

    if request.method == 'POST':
        if is_admin:
            entrega.cliente = request.form.get('cliente')
            entrega.bairro = request.form.get('bairro')
            entrega.valor = float(request.form.get('valor'))
            novo_coop_id = request.form.get('cooperado_id')
            if novo_coop_id:
                novo_coop_id = int(novo_coop_id)
                if entrega.cooperado_id != novo_coop_id:
                    entrega.cooperado_id = novo_coop_id
                    entrega.data_atribuida = datetime.utcnow()
                    # Remove da fila quando atribuído/alterado
                    ListaEspera.query.filter_by(cooperado_id=novo_coop_id).delete()
            else:
                entrega.cooperado_id = None

            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.recebido_por = request.form.get('recebido_por')
            entrega.pagamento = request.form.get('pagamento')

            db.session.commit()
            flash('Entrega atualizada!')
            return redirect_back_to_admin()
        else:
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('painel_cooperado'))

    if is_admin:
        return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)
    else:
        return render_template('editar_entrega_cooperado.html', entrega=entrega)

@app.route('/excluir_entrega/<int:id>', methods=['POST'])
def excluir_entrega(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    db.session.delete(entrega)
    db.session.commit()
    flash('Entrega excluída.')
    return redirect_back_to_admin()

@app.route('/excluir_cooperado/<int:id>', methods=['POST'])
def excluir_cooperado(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    c = Cooperado.query.get_or_404(id)
    Entrega.query.filter_by(cooperado_id=c.id).delete()
    ListaEspera.query.filter_by(cooperado_id=c.id).delete()  # limpa fila
    db.session.delete(c)
    db.session.commit()
    flash('Cooperado excluído.')
    return redirect_back_to_admin()

# ========= NOVO: endpoints para clique nos status =========
@app.post('/entregas/<int:id>/marcar-pagamento')
def marcar_pagamento(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    e.status_pagamento = "pago"  # lowercase para combinar com checagens .lower()
    db.session.commit()
    return redirect_back_to_admin()

@app.post('/entregas/<int:id>/marcar-entregue')
def marcar_entregue(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    e.status = "entregue"
    db.session.commit()
    return redirect_back_to_admin()
# =========================================================

# ====== ESTATÍSTICAS ======
@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    status_pagamento = request.args.get('status_pagamento', 'todos')
    cliente = (request.args.get('cliente') or '').strip()

    query = Entrega.query
    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)
    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))
    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    entregas = (
        query.options(joinedload(Entrega.cooperado))   # performance
        .order_by(Entrega.data_envio.asc())
        .all()
    )

    total = len(entregas)
    pagas = len([e for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago'])
    pendentes = total - pagas
    total_valor = sum(e.valor for e in entregas)
    ticket_medio = (total_valor / total) if total > 0 else 0.0

    # Dia com mais entregas (Brasil)
    cont_dias = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_dias[dt_local.date()] += 1
    dia_top = {"data": None, "qtd": 0, "nome": "-"}
    if cont_dias:
        d, qtd = cont_dias.most_common(1)[0]
        dia_top = {"data": d.strftime('%Y-%m-%d'), "qtd": qtd, "nome": f"{d.strftime('%d/%m/%Y')} ({qtd})"}

    # Horários de pico (top1 + top3)
    cont_horas = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_horas[dt_local.strftime('%H:00')] += 1
    hora_pico = cont_horas.most_common(1)[0][0] if cont_horas else "-"
    horas_pico_top3 = [h for h, _ in cont_horas.most_common(3)] if cont_horas else []

    # Forma de pagamento mais usada
    cont_pgto = Counter([e.pagamento for e in entregas if e.pagamento])
    pgto_top = cont_pgto.most_common(1)[0][0] if cont_pgto else "-"

    # Ranking cooperados
    mapa_coop = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    total_geral_periodo = 0.0
    for e in entregas:
        nm = e.cooperado.nome if e.cooperado else "Sem Cooperado"
        mapa_coop[nm]["qtd"] += 1
        mapa_coop[nm]["total"] += float(e.valor or 0)
        total_geral_periodo += float(e.valor or 0)

    ranking_cooperados = []
    for nome, dct in mapa_coop.items():
        percent = (dct["total"] / total_geral_periodo * 100.0) if total_geral_periodo > 0 else 0.0
        ranking_cooperados.append({
            "nome": nome,
            "qtd": dct["qtd"],
            "total_valor": round(dct["total"], 2),
            "percent": percent
        })
    ranking_cooperados.sort(key=lambda x: x["total_valor"], reverse=True)

    # Ranking bairros
    cont_bairros = Counter([e.bairro for e in entregas if e.bairro])
    ranking_bairros = [{"bairro": b, "qtd": q} for b, q in cont_bairros.most_common()]

    # Ranking formas pgto
    ranking_pgto = [{"forma": f, "qtd": q} for f, q in cont_pgto.most_common()]

    # Ranking clientes (com valor total por cliente)
    soma_por_cliente = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for e in entregas:
        if e.cliente:
            soma_por_cliente[e.cliente]["qtd"] += 1
            soma_por_cliente[e.cliente]["total"] += float(e.valor or 0)
    ranking_clientes = [
        {"cliente": c, "qtd": d["qtd"], "total": round(d["total"], 2)}
        for c, d in sorted(soma_por_cliente.items(), key=lambda kv: kv[1]["total"], reverse=True)
    ]

    # Gráficos
    dias_ordenados = sorted(list(cont_dias.keys()))
    chart_entregas_labels = [d.strftime("%d/%m") for d in dias_ordenados]
    chart_entregas_values = [cont_dias[d] for d in dias_ordenados]

    chart_faturamento_labels = [r["nome"] for r in ranking_cooperados]
    chart_faturamento_values = [r["total_valor"] for r in ranking_cooperados]

    periodo_legivel = periodo_legivel_str(data_inicio, data_fim)

    estatisticas = {
        "total": total,
        "pagas": pagas,
        "pendentes": pendentes,
        "total_valor": total_valor,
        "ticket_medio": ticket_medio,
        "dia_top": dia_top,
        "hora_pico": hora_pico,
        "pgto_top": pgto_top
    }

    return render_template(
        'estatisticas_cooperado.html',
        cooperados=cooperados,
        cooperado_id=cooperado_id,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status_pagamento=status_pagamento,
        cliente=cliente,
        estatisticas=estatisticas,
        ranking_cooperados=ranking_cooperados,
        ranking_bairros=ranking_bairros,
        ranking_pgto=ranking_pgto,
        ranking_clientes=ranking_clientes,          # inclui total por cliente
        horas_pico_top3=horas_pico_top3,            # top3 horários
        chart_entregas_labels=chart_entregas_labels,
        chart_entregas_values=chart_entregas_values,
        chart_faturamento_labels=chart_faturamento_labels,
        chart_faturamento_values=chart_faturamento_values,
        periodo_legivel=periodo_legivel
    )

# ====== EXPORTAÇÃO detalhada ======
@app.route('/exportar_xlsx')
def exportar_xlsx():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    cooperado_id = request.args.get('cooperado_id', 'todos')
    cliente = (request.args.get('cliente') or '').strip()

    query = Entrega.query

    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)

    entregas = query.order_by(Entrega.data_envio.asc()).all()

    rows = []
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        rows.append({
            'Data': dt_local.strftime('%d/%m/%Y') if dt_local else '',
            'Hora': dt_local.strftime('%H:%M') if dt_local else '',
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Valor': e.valor,
            'Status Pagamento': e.status_pagamento,
            'Status Entrega': e.status,
            'Forma Pagamento': e.pagamento,
            'Cooperado': (e.cooperado.nome if e.cooperado else 'Sem Cooperado'),
            'Recebido Por': e.recebido_por or ''
        })

    df = pd.DataFrame(rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Entregas'
        df.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]
        col_widths = [10, 6, 28, 18, 10, 18, 16, 16, 22, 18]
        for i, w in enumerate(col_widths[:len(df.columns)]):
            ws.set_column(i, i, w)
    output.seek(0)
    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

# ====== EXPORTAÇÃO resumo Cooperado × Valor (título com período) ======
@app.route('/estatisticas_cooperado_exportar_xlsx')
def estatisticas_cooperado_exportar_xlsx():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    status_pagamento = request.args.get('status_pagamento', 'todos')
    cliente = (request.args.get('cliente') or '').strip()

    query = Entrega.query
    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)
    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))
    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    entregas = query.all()

    soma_por_coop = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    total_geral = 0.0
    for e in entregas:
        nm = e.cooperado.nome if e.cooperado else "Sem Cooperado"
        soma_por_coop[nm]["qtd"] += 1
        soma_por_coop[nm]["total"] += float(e.valor or 0)
        total_geral += float(e.valor or 0)

    linhas = []
    for nome, d in soma_por_coop.items():
        percent = (d["total"] / total_geral * 100.0) if total_geral > 0 else 0.0
        linhas.append({
            "Cooperado": nome,
            "Qtd Entregas": d["qtd"],
            "Valor Total (R$)": round(d["total"], 2),
            "% do Total": round(percent, 1)
        })
    linhas.sort(key=lambda r: r["Valor Total (R$)"], reverse=True)

    df = pd.DataFrame(linhas)

    # Título usando helper correta (sem acento no nome da função)
    titulo = f"Faturamento dos cooperados do período ({periodo_legivel_str(data_inicio, data_fim)})"

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Resumo'
        start_row = 1
        df.to_excel(writer, index=False, sheet_name=sheet, startrow=start_row)
        ws = writer.sheets[sheet]

        last_col = len(df.columns) - 1
        ws.merge_range(0, 0, 0, last_col, titulo, writer.book.add_format({
            'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
            'font_color': '#003399'
        }))

        widths = [28, 14, 18, 12]
        for i, w in enumerate(widths[:len(df.columns)]):
            ws.set_column(i, i, w)

        money_fmt = writer.book.add_format({'num_format': '#,##0.00'})
        pct_fmt = writer.book.add_format({'num_format': '0.0"%"'})
        cols = list(df.columns)
        if "Valor Total (R$)" in cols:
            idx = cols.index("Valor Total (R$)")
            ws.set_column(idx, idx, 18, money_fmt)
        if "% do Total" in cols:
            idx = cols.index("% do Total")
            ws.set_column(idx, idx, 12, pct_fmt)

    output.seek(0)
    return send_file(output, download_name="faturamento_cooperados.xlsx", as_attachment=True)

# ====== FILA DE ESPERA ======
@app.route('/lista_espera/add', methods=['POST'])
def lista_espera_add():
    """
    Adiciona à fila:
      - Preferencialmente por 'cooperado_id' (select do admin.html)
      - Mantém compatibilidade com 'nome' enviado pelo form (preenche automaticamente)
    Evita duplicados e define posição (pos) no final da fila.
    """
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperado_id = request.form.get('cooperado_id')
    nome_form = (request.form.get('nome') or '').strip()

    if not cooperado_id and not nome_form:
        flash('Selecione um cooperado ou informe um nome.')
        return redirect_back_to_admin()

    # Se veio cooperado_id, buscamos o nome
    if cooperado_id:
        coop = Cooperado.query.get(int(cooperado_id))
        if not coop:
            flash('Cooperado inválido.')
            return redirect_back_to_admin()

        # já está na fila?
        if ListaEspera.query.filter_by(cooperado_id=coop.id).first():
            flash('Este cooperado já está na fila de espera.')
            return redirect_back_to_admin()

        # Posição final
        max_pos = db.session.query(func.max(ListaEspera.pos)).scalar() or 0
        item = ListaEspera(
            cooperado_id=coop.id,
            nome=coop.nome,              # preenche p/ compat com schema antigo (NOT NULL)
            pos=max_pos + 1,
            created_at=datetime.utcnow()
        )
        db.session.add(item)
        db.session.commit()
        flash('Cooperado adicionado à lista de espera.')
        return redirect_back_to_admin()

    # Sem cooperado_id: usa apenas o nome (modo legado)
    if ListaEspera.query.filter(func.lower(ListaEspera.nome) == nome_form.lower()).first():
        flash('Este nome já está na fila de espera.')
        return redirect_back_to_admin()

    max_pos = db.session.query(func.max(ListaEspera.pos)).scalar() or 0
    item = ListaEspera(
        nome=nome_form,
        cooperado_id=None,
        pos=max_pos + 1,
        created_at=datetime.utcnow()
    )
    db.session.add(item)
    db.session.commit()
    flash('Nome adicionado à lista de espera.')
    return redirect_back_to_admin()

@app.route('/lista_espera/remove/<int:id>', methods=['POST'])
def lista_espera_remove(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    item = ListaEspera.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Removido da lista de espera.')
    return redirect_back_to_admin()

@app.route('/lista_espera/reordenar', methods=['POST'])
def lista_espera_reordenar():
    """
    Recebe JSON: {"ordem": ["3","1","2", ...]} com IDs na ordem nova.
    Atualiza 'pos' de cada item.
    """
    if not session.get('is_admin'):
        return ("", 403)
    data = request.get_json(silent=True) or {}
    ordem = data.get('ordem') or []
    try:
        for i, sid in enumerate(ordem, start=1):
            try:
                _id = int(sid)
            except:
                continue
            db.session.query(ListaEspera).filter_by(id=_id).update({"pos": i})
        db.session.commit()
        return ("", 204)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Reordenar fila falhou: {e}")
        return ("", 500)

# ====== BOOTSTRAP BANCO: criar tabelas, colunas faltantes e índices ======
def criar_bd():
    with app.app_context():
        db.create_all()

        # Tenta criar colunas novas da lista_espera caso seu banco antigo não tenha
        ddl_cmds = [
            # adiciona cooperado_id/pos/created_at se não existirem (Postgres)
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS cooperado_id INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS pos INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
            # cria FK (se possível, ignora erro se já existir ou se for SQLite)
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='lista_espera_cooperado_id_fkey') THEN "
            "ALTER TABLE lista_espera ADD CONSTRAINT lista_espera_cooperado_id_fkey FOREIGN KEY (cooperado_id) REFERENCES cooperado(id) ON DELETE SET NULL; "
            "END IF; "
            "END $$;"
        ]
        for s in ddl_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                # Provavelmente SQLite ou já existe; ignora
                pass

        # Índices p/ performance (se já existirem, ignora)
        idx_cmds = [
            "CREATE INDEX IF NOT EXISTS idx_entrega_data_envio ON entrega (data_envio DESC)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cooperado_id ON entrega (cooperado_id)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_status_pagamento_lower ON entrega ((lower(status_pagamento)))",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cliente_lower ON entrega ((lower(cliente)))",
            "CREATE INDEX IF NOT EXISTS idx_lista_espera_pos ON lista_espera (pos ASC)"
        ]
        for s in idx_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        db.session.commit()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
