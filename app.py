import os
import io
import re
import unicodedata
from datetime import datetime, timedelta, time, date
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify, abort
)
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


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(30), nullable=True)
    bairro_origem = db.Column(db.String(50), nullable=True)
    # NOVO: endereço opcional
    endereco = db.Column(db.String(255), nullable=True)


class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(50), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    # guardado em UTC naive
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)  # "pago" / "pendente"
    status = db.Column(db.String(20), nullable=True)            # "recebido"/"pendente"/"entregue"
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
        next_first = first.replace(year=first.year + 1, month=1, day=1)
    else:
        next_first = first.replace(month=first.month + 1, day=1)
    return local_date_window_to_utc_range(first)[0], local_date_window_to_utc_range(next_first - timedelta(days=1))[1]

def year_range_utc(local_date: date):
    first = local_date.replace(month=1, day=1)
    next_first = first.replace(year=first.year + 1)
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

# ====== Normalização forte (clientes) ======
def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn')

def normalize_letters_key(s: str) -> str:
    """
    1) tira acentos
    2) lowercase
    3) remove números e pontuação (só letras e espaço)
    4) compacta espaços
    """
    s = _strip_accents(s).lower()
    s = re.sub(r'[^a-z\u00c0-\u024f\s]', ' ', s)   # letras latinas + espaço
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def normalize_first_token(s: str) -> str:
    k = normalize_letters_key(s)
    return (k.split(' ')[0] if k else '')

def br_date_ymd(dt_utc_naive: datetime) -> str:
    """Converte UTC naive para data local Brasil 'YYYY-MM-DD'."""
    if not dt_utc_naive:
        return ''
    loc = to_brasilia(dt_utc_naive)
    return loc.date().isoformat()

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

# ====== (NOVO) Helper de segurança para o painel do cooperado ======
def _assert_entrega_do_cooperado(entrega: Entrega):
    """Garante que o usuário logado é o dono da entrega."""
    uid = session.get('user_id')
    if uid is None or session.get('is_admin'):
        abort(403)
    if entrega.cooperado_id != uid:
        abort(403)

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
        df_ = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
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

    total_dia = Entrega.query.filter(
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc
    ).count()
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
        (Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente')
    ).count() > 0

    # Fila de espera (sem usar NULLS LAST para portabilidade)
    lista_espera = (
        ListaEspera.query
        .order_by(ListaEspera.pos.asc(), ListaEspera.created_at.asc())
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
        status_pagamento='pendente',
        pagamento=e.pagamento,
        recebido_por=None
    )
    db.session.add(nova)
    db.session.commit()
    flash(f'Entrega #{e.id} clonada em #{nova.id}. Edite para atribuir um cooperado.')
    return redirect_back_to_admin()

# ====== PAINEL COOPERADO ======
@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('user_id') is None or session.get('is_admin'):
        return redirect(url_for('login'))

    user_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    status_pgto = (request.args.get('status_pgto') or 'todas').lower()  # todas | pago | pendente

    query = Entrega.query.filter(Entrega.cooperado_id == user_id)

    # Filtro por data (default = hoje)
    hoje_brasil = datetime.now(BRAZIL_TZ).date()
    if not inicio and not fim:
        inicio_utc, fim_utc = local_date_window_to_utc_range(hoje_brasil)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)
    if inicio:
        di = datetime.strptime(inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if fim:
        df_ = datetime.strptime(fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
        query = query.filter(Entrega.data_envio <= fim_utc)

    # Filtro por status do pagamento
    if status_pgto == 'pago':
        query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
    elif status_pgto == 'pendente':
        query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    entregas = (
        query.options(joinedload(Entrega.cooperado))  # performance
        .order_by(Entrega.data_envio.desc())
        .all()
    )

    # Totais do conjunto filtrado
    total_geral = sum(float(e.valor or 0) for e in entregas)
    total_pago = sum(float(e.valor or 0) for e in entregas if (e.status_pagamento or '').lower() == 'pago')
    total_pendente = max(0.0, total_geral - total_pago)

    return render_template(
        'painel_cooperado.html',
        entregas=entregas,
        total_geral=total_geral,
        total_pago=total_pago,
        total_pendente=total_pendente,
        request=request,
        to_brasilia=to_brasilia,
        status_pgto=status_pgto
    )

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

# ====== CLIENTES (CRUD) ======
@app.route('/clientes', methods=['GET', 'POST'])
def clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    # POST = criar cliente
    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        telefone = (request.form.get('telefone') or '').strip()
        bairro_origem = (request.form.get('bairro_origem') or '').strip()
        endereco = (request.form.get('endereco') or '').strip()
        if not nome:
            flash('Informe o nome do cliente.')
            return redirect(url_for('clientes'))
        existe = Cliente.query.filter(func.lower(Cliente.nome) == nome.lower()).first()
        if existe:
            flash('Já existe um cliente com esse nome.')
            return redirect(url_for('clientes'))
        cl = Cliente(nome=nome, telefone=telefone, bairro_origem=bairro_origem, endereco=endereco or None)
        db.session.add(cl)
        db.session.commit()
        flash('Cliente cadastrado!')
        return redirect(url_for('clientes'))

    # ---------- GET: métricas de uso com normalização forte ----------
    aggs = (
        db.session.query(
            Entrega.cliente.label('cli'),
            func.count(Entrega.id).label('qtd'),
            func.max(Entrega.data_envio).label('ultimo')
        )
        .group_by(Entrega.cliente)
        .all()
    )

    stats_by_full = defaultdict(lambda: {"qtd": 0, "ultimo": None})
    stats_by_first = defaultdict(lambda: {"qtd": 0, "ultimo": None})
    for row in aggs:
        raw = (row.cli or '').strip()
        key_full = normalize_letters_key(raw)
        key_first = normalize_first_token(raw)

        s = stats_by_full[key_full]
        s["qtd"] += int(row.qtd or 0)
        if row.ultimo and (s["ultimo"] is None or row.ultimo > s["ultimo"]):
            s["ultimo"] = row.ultimo

        f = stats_by_first[key_first]
        f["qtd"] += int(row.qtd or 0)
        if row.ultimo and (f["ultimo"] is None or row.ultimo > f["ultimo"]):
            f["ultimo"] = row.ultimo

    hoje_local = datetime.now(BRAZIL_TZ).date()

    lista = []
    for cl in Cliente.query.order_by(Cliente.nome).all():
        k_full  = normalize_letters_key(cl.nome or '')
        k_first = normalize_first_token(cl.nome or '')

        tot, dt = 0, None
        if k_full in stats_by_full:
            tot = stats_by_full[k_full]["qtd"]
            dt  = stats_by_full[k_full]["ultimo"]
        elif k_first in stats_by_first:
            tot = stats_by_first[k_first]["qtd"]
            dt  = stats_by_first[k_first]["ultimo"]

        # Datas prontas para o HTML (sem depender de JS)
        ultimo_ymd, ultimo_br, ultimo_days, row_class = None, None, None, ""
        if dt:
            loc_date = to_brasilia(dt).date()
            ultimo_ymd  = loc_date.isoformat()              # 'YYYY-MM-DD'
            ultimo_br   = loc_date.strftime('%d/%m/%Y')     # 'DD/MM/YYYY'
            ultimo_days = (hoje_local - loc_date).days

            if   ultimo_days > 60: row_class = "st-gt60"    # vermelho
            elif ultimo_days > 30: row_class = "st-gt30"    # amarelo
            else:                  row_class = "st-lt30"    # verde

        lista.append({
            "id": cl.id,
            "nome": cl.nome,
            "telefone": cl.telefone,
            "bairro_origem": cl.bairro_origem,
            "endereco": getattr(cl, "endereco", None),
            "total_pedidos": int(tot or 0),
            "ultimo_ymd": ultimo_ymd,       # para filtros/sort
            "ultimo_br": ultimo_br,         # já formatado para exibir
            "ultimo_days": ultimo_days,     # número de dias desde o último uso
            "row_class": row_class
        })

    # KPIs: Ativos (<=6 meses) / Inativos (>=6 meses ou nunca)
    total_clientes = len(lista)
    ativos   = sum(1 for i in lista if i["ultimo_days"] is not None and i["ultimo_days"] <= 180)
    inativos = total_clientes - ativos

    return render_template('clientes.html',
                           clientes=lista,
                           kpis={"total": total_clientes, "ativos": ativos, "inativos": inativos})

@app.route('/clientes/<int:id>/editar', methods=['POST'])
def editar_cliente(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cl = Cliente.query.get_or_404(id)
    nome = (request.form.get('nome') or '').strip()
    telefone = (request.form.get('telefone') or '').strip()
    bairro_origem = (request.form.get('bairro_origem') or '').strip()
    endereco = (request.form.get('endereco') or '').strip()
    if not nome:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error='Informe o nome do cliente.'), 400
        flash('Informe o nome do cliente.')
        return redirect(url_for('clientes'))
    existe = Cliente.query.filter(func.lower(Cliente.nome) == nome.lower(), Cliente.id != id).first()
    if existe:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error='Já existe outro cliente com esse nome.'), 400
        flash('Já existe outro cliente com esse nome.')
        return redirect(url_for('clientes'))

    cl.nome = nome
    cl.telefone = telefone
    cl.bairro_origem = bairro_origem
    cl.endereco = endereco or None
    db.session.commit()

    # Se foi AJAX (fetch), devolve JSON com métricas atualizadas para esta linha
    if request.headers.get('X-Requested-With') == 'fetch':
        aggs = (
            db.session.query(
                Entrega.cliente.label('cli'),
                func.count(Entrega.id).label('qtd'),
                func.max(Entrega.data_envio).label('ultimo')
            ).group_by(Entrega.cliente).all()
        )
        k_full = normalize_letters_key(cl.nome or '')
        k_first = normalize_first_token(cl.nome or '')

        tot, ultimo = 0, None
        for row in aggs:
            raw = (row.cli or '')
            if normalize_letters_key(raw) == k_full or normalize_first_token(raw) == k_first:
                tot += int(row.qtd or 0)
                if row.ultimo and (ultimo is None or row.ultimo > ultimo):
                    ultimo = row.ultimo

        return jsonify({"ok": True, "total_pedidos": int(tot or 0), "ultimo_uso": (br_date_ymd(ultimo) if ultimo else None)}), 200

    flash('Cliente atualizado!')
    return redirect(url_for('clientes'))

@app.route('/clientes/<int:id>/excluir', methods=['POST'])
def excluir_cliente(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cl = Cliente.query.get_or_404(id)
    db.session.delete(cl)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch':
        return ("", 204)
    flash('Cliente excluído.')
    return redirect(url_for('clientes'))

# ====== ENTREGAS ======
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    clientes_lista = Cliente.query.order_by(Cliente.nome).all()
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
            status_pagamento='pendente',
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
    return render_template('cadastrar_entrega.html', cooperados=cooperados, clientes=clientes_lista)

@app.route('/agendar_entrega', methods=['GET', 'POST'])
def agendar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    clientes_lista = Cliente.query.order_by(Cliente.nome).all()
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
            status=(status_entrega or 'pendente'),
            status_pagamento=(status_pagamento or 'pendente').lower(),
            pagamento=pagamento
        )
        db.session.add(entrega)

        # Remove da fila se atribuiu cooperado
        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()
        flash('Entrega agendada!')
        return redirect_back_to_admin()
    return render_template('agendar_entrega.html', cooperados=cooperados, clientes=clientes_lista)

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

            entrega.status_pagamento = (request.form.get('status_pagamento') or entrega.status_pagamento or 'pendente').lower()
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            entrega.pagamento = request.form.get('pagamento') or entrega.pagamento

            db.session.commit()
            flash('Entrega atualizada!')
            return redirect_back_to_admin()
        else:
            entrega.status_pagamento = (request.form.get('status_pagamento') or entrega.status_pagamento or 'pendente').lower()
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

# ========= BOTÕES RÁPIDOS (ADMIN) =========
@app.post('/entregas/<int:id>/marcar-pagamento')
def marcar_pagamento(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    e.status_pagamento = "pago"
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

# ========= JSON do PAINEL DO COOPERADO =========
@app.post('/cooperado/toggle_pagamento/<int:id>')
def toggle_pagamento(id):
    """Alterna Pago/Pendente na própria entrega do cooperado."""
    e = Entrega.query.get_or_404(id)
    _assert_entrega_do_cooperado(e)
    atual = (e.status_pagamento or 'pendente').lower()
    novo = 'pago' if atual != 'pago' else 'pendente'
    e.status_pagamento = novo
    db.session.commit()
    return jsonify(ok=True, status_pagamento=novo)

@app.post('/cooperado/marcar_entregue/<int:id>')
def cooperado_marcar_entregue(id):
    """Marca como entregue + salva recebido_por (campo obrigatório)."""
    e = Entrega.query.get_or_404(id)
    _assert_entrega_do_cooperado(e)
    payload = request.get_json(silent=True) or {}
    recebido_por = (payload.get('recebido_por') or '').strip()
    if not recebido_por:
        return jsonify(ok=False, error='Campo "recebido_por" é obrigatório.'), 400
    # compatibilidade: alguns lugares usam status 'recebido'
    e.status = 'recebido'
    e.recebido_por = recebido_por
    db.session.commit()
    return jsonify(ok=True)

# ====== ESTATÍSTICAS (ADMIN) ======
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
        df_ = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
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
    pagas = len([e for e in entregas if (e.status_pagamento or '').lower() == 'pago'])
    pendentes = total - pagas
    total_valor = sum(float(e.valor or 0) for e in entregas)
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

    # Horários de pico (Top 3)
    cont_horas = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_horas[dt_local.strftime('%H:00')] += 1
    hora_pico = cont_horas.most_common(1)[0][0] if cont_horas else "-"
    horas_pico_top3 = [f"{h} ({q})" for h, q in cont_horas.most_common(3)]

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

    # Ranking bairros (destino)
    cont_bairros = Counter([e.bairro for e in entregas if e.bairro])
    ranking_bairros = [{"bairro": b, "qtd": q} for b, q in cont_bairros.most_common()]

    # Ranking de bairros de origem (clientes)
    nomes_clientes = {e.cliente for e in entregas if e.cliente}
    clientes_cadastrados = Cliente.query.filter(Cliente.nome.in_(list(nomes_clientes))).all() if nomes_clientes else []
    mapa_cliente = {c.nome: c for c in clientes_cadastrados}

    cont_bairros_origem = Counter()
    for e in entregas:
        if not e.cliente:
            continue
        cl = mapa_cliente.get(e.cliente)
        if cl and cl.bairro_origem:
            cont_bairros_origem[(cl.bairro_origem or '').strip()] += 1

    ranking_bairros_origem = [{"bairro": (b or 'Não informado'), "qtd": q}
                              for b, q in cont_bairros_origem.most_common()]

    # Ranking formas pgto
    ranking_pgto = [{"forma": f, "qtd": q} for f, q in cont_pgto.most_common()]

    # Ranking clientes (valor total por cliente)
    soma_por_cliente = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for e in entregas:
        if e.cliente:
            soma_por_cliente[e.cliente]["qtd"] += 1
            soma_por_cliente[e.cliente]["total"] += float(e.valor or 0)
    ranking_clientes = [
        {"cliente": c, "qtd": d["qtd"], "total": round(d["total"], 2)}
        for c, d in sorted(soma_por_cliente.items(), key=lambda kv: kv[1]["total"], reverse=True)
    ]

    # Gráficos por dia
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

    # ====== Séries anuais (2025+) – faturamento, quantidade, ticket médio ======
    por_ano_total = defaultdict(float)
    por_ano_qtd = defaultdict(int)

    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if not dt_local:
            continue
        ano_local = dt_local.year
        if ano_local < 2025:
            continue
        por_ano_qtd[ano_local] += 1
        por_ano_total[ano_local] += float(e.valor or 0)

    if por_ano_total:
        ultimo_ano = max(set(por_ano_total.keys()) | set(por_ano_qtd.keys()))
    else:
        ultimo_ano = max(2025, datetime.now(BRAZIL_TZ).year)

    chart_ano_labels = list(range(2025, ultimo_ano + 1))
    chart_ano_totais = []
    chart_ano_qtd = []
    chart_ano_ticket = []

    for y in chart_ano_labels:
        tot = float(por_ano_total.get(y, 0.0))
        qtd = int(por_ano_qtd.get(y, 0))
        tkt = (tot / qtd) if qtd else 0.0
        chart_ano_totais.append(round(tot, 2))
        chart_ano_qtd.append(qtd)
        chart_ano_ticket.append(round(tkt, 2))

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
        ranking_bairros_origem=ranking_bairros_origem,
        ranking_pgto=ranking_pgto,
        ranking_clientes=ranking_clientes,
        horas_pico_top3=horas_pico_top3,
        chart_entregas_labels=chart_entregas_labels,
        chart_entregas_values=chart_entregas_values,
        chart_faturamento_labels=chart_faturamento_labels,
        chart_faturamento_values=chart_faturamento_values,
        periodo_legivel=periodo_legivel,
        chart_ano_labels=chart_ano_labels,
        chart_ano_totais=chart_ano_totais,
        chart_ano_qtd=chart_ano_qtd,
        chart_ano_ticket=chart_ano_ticket,
    )

# ====== EXPORTAÇÃO detalhada (sem HORA; só Data) ======
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
        df_ = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
        query = query.filter(Entrega.data_envio <= fim_utc)

    entregas = query.order_by(Entrega.data_envio.asc()).all()

    rows = []
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        rows.append({
            'Data': dt_local.strftime('%d/%m/%Y') if dt_local else '',
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Valor': e.valor,
            'Status Pagamento': e.status_pagamento,
            'Status Entrega': e.status,
            'Forma Pagamento': e.pagamento,
            'Cooperado': (e.cooperado.nome if e.cooperado else 'Sem Cooperado'),
            'Recebido Por': e.recebido_por or ''
        })

    df_out = pd.DataFrame(rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Entregas'
        df_out.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]
        col_widths = [12, 28, 18, 10, 18, 16, 16, 22, 18]
        for i, w in enumerate(col_widths[:len(df_out.columns)]):
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
        df_ = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
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

    df_out = pd.DataFrame(linhas)

    titulo = f"Faturamento dos cooperados do período ({periodo_legivel_str(data_inicio, data_fim)})"

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Resumo'
        start_row = 1
        df_out.to_excel(writer, index=False, sheet_name=sheet, startrow=start_row)
        ws = writer.sheets[sheet]

        last_col = len(df_out.columns) - 1
        ws.merge_range(0, 0, 0, last_col, titulo, writer.book.add_format({
            'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
            'font_color': '#003399'
        }))

        widths = [28, 14, 18, 12]
        for i, w in enumerate(widths[:len(df_out.columns)]):
            ws.set_column(i, i, w)

        money_fmt = writer.book.add_format({'num_format': '#,##0.00'})
        pct_fmt = writer.book.add_format({'num_format': '0.0"%"'})
        cols = list(df_out.columns)
        if "Valor Total (R$)" in cols:
            idx = cols.index("Valor Total (R$)")
            ws.set_column(idx, idx, 18, money_fmt)
        if "% do Total" in cols:
            idx = cols.index("% do Total")
            ws.set_column(idx, idx, 12, pct_fmt)

    output.seek(0)
    return send_file(output, download_name="faturamento_cooperados.xlsx", as_attachment=True)

# ====== IMPORTAR / EXPORTAR CLIENTES (NOVO) ======
@app.route('/clientes/exportar')
def exportar_clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    # Agrega entregas por nome original e consolida por chave normalizada
    aggs = (
        db.session.query(
            Entrega.cliente.label('cli'),
            func.count(Entrega.id).label('qtd'),
            func.max(Entrega.data_envio).label('ultimo')
        )
        .group_by(Entrega.cliente)
        .all()
    )
    stats = defaultdict(lambda: {"qtd": 0, "ultimo": None})
    for row in aggs:
        key = normalize_letters_key(row.cli or '')
        s = stats[key]
        s["qtd"] += int(row.qtd or 0)
        if row.ultimo and (s["ultimo"] is None or row.ultimo > s["ultimo"]):
            s["ultimo"] = row.ultimo

    rows = []
    for c in Cliente.query.order_by(Cliente.nome).all():
        key = normalize_letters_key(c.nome or '')
        s = stats.get(key, {})
        rows.append([
            c.id, c.nome, c.telefone, c.bairro_origem, c.endereco,
            br_date_ymd(s.get("ultimo")) if s else "", int((s or {}).get("qtd") or 0)
        ])

    df_out = pd.DataFrame(rows, columns=[
        "ID", "Nome", "Telefone", "Bairro", "Endereco", "UltimoUso", "TotalPedidos"
    ])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Clientes'
        df_out.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]
        widths = [8, 28, 18, 18, 32, 12, 14]
        for i, w in enumerate(widths[:len(df_out.columns)]):
            ws.set_column(i, i, w)
    output.seek(0)
    return send_file(output, download_name="clientes.xlsx", as_attachment=True)

# ---------- ROTA IMPORTAÇÃO ROBUSTA ----------
@app.route('/clientes/importar', methods=['POST'])
def importar_clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    f = request.files.get('arquivo')
    if not f or not f.filename:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error="Envie um arquivo (.xlsx ou .csv)."), 400
        flash("Envie um arquivo (.xlsx ou .csv).")
        return redirect(url_for('clientes'))

    filename = f.filename.lower()

    # Lê o upload em memória
    try:
        raw = f.read()
        if not raw:
            raise ValueError("Arquivo vazio.")
    except Exception as e:
        msg = f"Falha ao ler upload: {e}"
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error=msg), 400
        flash(msg)
        return redirect(url_for('clientes'))

    df_in = None
    load_errors = []

    # Preferência: XLSX com pandas + openpyxl
    if filename.endswith('.xlsx'):
        try:
            df_in = pd.read_excel(io.BytesIO(raw), engine='openpyxl', dtype=str)
        except Exception as e:
            load_errors.append(f"Pandas/openpyxl: {e}")

        # Fallback: openpyxl puro
        if df_in is None:
            try:
                from openpyxl import load_workbook
                wb = load_workbook(io.BytesIO(raw), data_only=True)
                ws = wb['Sheet1'] if 'Sheet1' in wb.sheetnames else wb.active
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    raise ValueError("Planilha vazia.")
                header = [str(x).strip() if x is not None else '' for x in rows[0]]
                data = [[("" if c is None else str(c)) for c in r] for r in rows[1:]]
                df_in = pd.DataFrame(data, columns=header)
            except Exception as e:
                load_errors.append(f"openpyxl: {e}")

    # CSV (ou txt)
    if df_in is None and (filename.endswith('.csv') or filename.endswith('.txt')):
        try:
            df_in = pd.read_csv(io.BytesIO(raw), sep=None, engine='python', dtype=str, encoding='utf-8')
        except Exception:
            try:
                df_in = pd.read_csv(io.BytesIO(raw), sep=None, engine='python', dtype=str, encoding='latin-1')
            except Exception as e:
                load_errors.append(f"CSV: {e}")

    if df_in is None:
        msg = "Não consegui ler o arquivo. " + (" | ".join(load_errors) if load_errors else "")
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error=msg), 400
        flash(msg)
        return redirect(url_for('clientes'))

    # --- mapeamento de colunas ---
    cols_map = {str(c).lower().strip(): c for c in df_in.columns}

    def colget(*ops, opt=False):
        for k in ops:
            if k in cols_map:
                return cols_map[k]
        return None if opt else None

    col_id     = colget('id')
    col_nome   = colget('nome', 'name')
    col_tel    = colget('telefone', 'phone', 'numero', 'número', 'mobile', 'celular')
    col_bairro = colget('bairro', 'bairro_origem')
    col_end    = colget('endereco', 'endereço', 'address')

    missing = []
    if not col_nome:
        missing.append("Nome")
    if not col_tel:
        missing.append("Telefone/Número")
    if missing:
        msg = f"Cabeçalho ausente: {', '.join(missing)}. Colunas recebidas: {list(df_in.columns)}"
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error=msg), 400
        flash(msg)
        return redirect(url_for('clientes'))

    # --- normalização de telefone ---
    def norm_phone(s: str) -> str:
        if s is None:
            return ""
        digits = re.sub(r'\D+', '', str(s))
        if digits.startswith('55'):
            digits = digits[2:]
        if len(digits) > 11:
            digits = digits[-11:]
        return digits

    adicionados = 0
    atualizados = 0
    erros = 0
    detalhes = []

    for i, row in df_in.iterrows():
        try:
            rid = None
            if col_id and not pd.isna(row.get(col_id)):
                try:
                    rid = int(str(row[col_id]).strip())
                except Exception:
                    rid = None

            nome = str(row.get(col_nome) or '').strip()
            tel  = norm_phone(row.get(col_tel))
            bairro = str(row.get(col_bairro) or '').strip() if col_bairro else None
            ender  = str(row.get(col_end) or '').strip() if col_end else None

            if not nome and not tel:
                continue

            if tel and len(tel) not in (10, 11):
                erros += 1
                detalhes.append(f"Linha {i+2}: telefone inválido '{tel}' (esperado 10 ou 11 dígitos).")
                continue

            if rid:
                cl = Cliente.query.get(rid)
                if not cl:
                    # Atualiza por nome se ID não encontrado
                    cl = Cliente.query.filter(func.lower(Cliente.nome) == nome.lower()).first()
            else:
                cl = Cliente.query.filter(func.lower(Cliente.nome) == nome.lower()).first()

            if cl:
                cl.nome = nome
                cl.telefone = tel or None
                cl.bairro_origem = bairro or None
                cl.endereco = ender or None
                atualizados += 1
            else:
                novo = Cliente(
                    nome=nome,
                    telefone=tel or None,
                    bairro_origem=bairro or None,
                    endereco=ender or None
                )
                db.session.add(novo)
                adicionados += 1

        except Exception as e:
            erros += 1
            detalhes.append(f"Linha {i+2}: erro inesperado ({e}).")

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Erro ao salvar no banco durante importação")
        msg = "Erro ao salvar no banco."
        if app.debug:
            msg += f" Detalhes: {e}"
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error=msg), 500
        flash(msg)
        return redirect(url_for('clientes'))

    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify(ok=True, adicionados=adicionados, atualizados=atualizados, erros=erros, detalhes=detalhes)
    else:
        flash(f'Importação concluída: {adicionados} adicionados, {atualizados} atualizados, {erros} erros.')
        return redirect(url_for('clientes'))

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
            nome=coop.nome,              # compat com schema antigo (NOT NULL)
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
            except Exception:
                continue
            db.session.query(ListaEspera).filter_by(id=_id).update({"pos": i})
        db.session.commit()
        return ("", 204)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Reordenar fila falhou: {e}")
        return ("", 500)

# ====== >>> NOVA ROTA: RELATÓRIO 80 mm (Epson TM-T20) <<< ======
@app.route('/relatorio_termico')
def relatorio_termico():
    """
    Relatório térmico 80mm (logo em static/logo_coopex.png)
    Campos: Cliente, Valor, Data/Hora da ATRIBUIÇÃO (fallback: envio).
    Respeita filtros: cooperado_id, data_inicio, data_fim, status_pagamento, cliente.
    O período usa COALESCE(data_atribuida, data_envio).
    """
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    status_pagamento = request.args.get('status_pagamento', 'todos')
    cliente = (request.args.get('cliente') or '').strip()

    # Janela de tempo (default = hoje local)
    if data_inicio:
        di_date = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _tmp = local_date_window_to_utc_range(di_date)
    else:
        hoje = datetime.now(BRAZIL_TZ).date()
        inicio_utc, _tmp = local_date_window_to_utc_range(hoje)

    if data_fim:
        df_date = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _tmp2, fim_utc = local_date_window_to_utc_range(df_date)
    else:
        # se não informar fim, usa mesmo dia do início
        base = datetime.strptime(data_inicio, "%Y-%m-%d").date() if data_inicio else datetime.now(BRAZIL_TZ).date()
        _tmp2, fim_utc = local_date_window_to_utc_range(base)

    q = Entrega.query

    # Filtros iguais aos do admin
    if cooperado_id and cooperado_id != 'todos':
        try:
            q = q.filter(Entrega.cooperado_id == int(cooperado_id))
        except Exception:
            pass

    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            q = q.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            q = q.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    if cliente:
        like = f"%{cliente.lower()}%"
        q = q.filter(func.lower(Entrega.cliente).like(like))

    # Usa COALESCE para janela por data atribuída (ou envio se não houver)
    coalesce_dt = func.coalesce(Entrega.data_atribuida, Entrega.data_envio)
    q = q.filter(coalesce_dt >= inicio_utc, coalesce_dt <= fim_utc).order_by(coalesce_dt.asc(), Entrega.cliente.asc())

    entregas = q.options(joinedload(Entrega.cooperado)).all()

    # Texto do período pronto
    periodo_txt = periodo_legivel_str(data_inicio, data_fim)

    # Nome do cooperado (ou "Todos")
    coop_nome = "Todos"
    if cooperado_id and cooperado_id != "todos":
        coop = Cooperado.query.get(int(cooperado_id))
        if coop:
            coop_nome = coop.nome

    # TOTAL do relatório (calculado no backend)
    total_relatorio = sum(float(e.valor or 0) for e in entregas)

    agora = datetime.now(BRAZIL_TZ)

    return render_template(
        'relatorio_termico.html',   # salve seu HTML com este nome
        entregas=entregas,
        periodo_txt=periodo_txt,
        coop_nome=coop_nome,
        agora=agora,
        to_brasilia=to_brasilia,
        total_relatorio=total_relatorio
    )

# ====== BOOTSTRAP BANCO: criar tabelas, colunas faltantes e índices ======
def criar_bd():
    with app.app_context():
        db.create_all()

        # Tenta criar colunas novas (Postgres; em SQLite será ignorado via except)
        ddl_cmds = [
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS cooperado_id INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS pos INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
            "ALTER TABLE cliente ADD COLUMN IF NOT EXISTS endereco VARCHAR(255)",
            # cria FK em Postgres (ignorado em SQLite)
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='lista_espera_cooperado_id_fkey') THEN "
                "ALTER TABLE lista_espera ADD CONSTRAINT lista_espera_cooperado_id_fkey "
                "FOREIGN KEY (cooperado_id) REFERENCES cooperado(id) ON DELETE SET NULL; "
                "END IF; "
                "END $$;"
            ),
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
            "CREATE INDEX IF NOT EXISTS idx_lista_espera_pos ON lista_espera (pos ASC)",
            "CREATE INDEX IF NOT EXISTS idx_cliente_nome_lower ON cliente ((lower(nome)))"
        ]
        for s in idx_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        db.session.commit()

criar_bd()

if __name__ == '__main__':
    # Em dev, isto habilita mensagens detalhadas no JSON de erro do import.
    app.run(debug=True)
