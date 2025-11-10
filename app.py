import os
import io
import re
import unicodedata
from datetime import datetime, timedelta, time, date
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs
from functools import wraps
from decimal import Decimal

from flask import (
    Flask, render_template, render_template_string, request, redirect, url_for,
    flash, session, send_file, jsonify, abort, current_app
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import text
from werkzeug.security import generate_password_hash, check_password_hash

import pandas as pd
import holidays
import pytz
from jinja2 import TemplateNotFound

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')

# --- Admins fixos (usuario: coopex, 2 senhas) ---
ADMIN_CREDENTIALS = {
    'coopex': {
        os.environ.get('ADMIN_PWD_COOPEX_MASTER', 'coopex05289'): {'is_master': True},
        os.environ.get('ADMIN_PWD_COOPEX',        '05062721'):     {'is_master': False},
    }
}

# Banco
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": 5,
    "max_overflow": 10,
}

db = SQLAlchemy(app)

# Fuso Brasil
BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

# ====== MODELS ======
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    def set_senha(self, senha): self.senha_hash = generate_password_hash(senha)
    def check_senha(self, senha): return check_password_hash(self.senha_hash, senha)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Dados gerais
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(30), nullable=True)
    bairro_origem = db.Column(db.String(50), nullable=True)
    endereco = db.Column(db.String(255), nullable=True)
    saldo_atual = db.Column(db.Float, nullable=False, default=0.0)
    # Login do cliente
    username = db.Column(db.String(80), unique=True, index=True)  # primeiro acesso cria
    senha_hash = db.Column(db.String(128), nullable=True)

    def set_senha(self, senha): self.senha_hash = generate_password_hash(senha)
    def check_senha(self, senha): 
        if not self.senha_hash: 
            return False
        return check_password_hash(self.senha_hash, senha)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False) # <--- ÚNICA OCORRÊNCIA MANTIDA
    bairro = db.Column(db.String(50), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # UTC naive
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), nullable=True)
    pagamento = db.Column(db.String(50), nullable=False)
    recebido_por = db.Column(db.String(100), nullable=True)
    cooperado = db.relationship('Cooperado', backref='entregas')

    # NOVOS: controle de crédito usado nesta entrega
    credito_usado = db.Column(db.Float, nullable=False, default=0.0)
    credito_mov_id = db.Column(db.Integer, nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)  # << NOVO

class Credito(db.Model):
    __tablename__ = "credito"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("cliente.id"), nullable=False)

    valor_bruto = db.Column(db.Float, nullable=False)
    desconto_tipo = db.Column(db.String(20), nullable=False, default="nenhum")  # 'nenhum'|'percentual'|'real'
    desconto_valor = db.Column(db.Float, nullable=False, default=0.0)
    valor_final = db.Column(db.Float, nullable=False)

    motivo = db.Column(db.String(180))
    saldo_antes = db.Column(db.Float, nullable=False, default=0.0)
    saldo_depois = db.Column(db.Float, nullable=False, default=0.0)

    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    criado_por = db.Column(db.String(80))

class CreditoMovimento(db.Model):
    """
    tipo='credito'  (entrada: quando a supervisão concede crédito)
    tipo='debito'   (saída: quando o crédito é usado numa entrega)
    """
    __tablename__ = "credito_movimento"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("cliente.id"), nullable=False)

    tipo = db.Column(db.String(10), nullable=False)  # 'credito' | 'debito'
    valor = db.Column(db.Float, nullable=False)
    referencia = db.Column(db.String(120))           # ex.: "Crédito #10" ou "Entrega #123"
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    credito_id = db.Column(db.Integer, db.ForeignKey("credito.id"), nullable=True)  # (quando tipo='credito')
    entrega_id = db.Column(db.Integer, db.ForeignKey("entrega.id"), nullable=True)  # (quando tipo='debito')

class ListaEspera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)  # legado
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    pos = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    cooperado = db.relationship('Cooperado', lazy='joined')

# ====== helpers datas ======
def to_brasilia(dt):
    if not dt: return None
    if dt.tzinfo is None: dt = pytz.utc.localize(dt)
    return dt.astimezone(BRAZIL_TZ)

def local_date_window_to_utc_range(local_date: date):
    inicio_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.min))
    fim_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.max))
    return (inicio_brasil.astimezone(pytz.utc).replace(tzinfo=None),
            fim_brasil.astimezone(pytz.utc).replace(tzinfo=None))

def month_range_utc(local_date: date):
    first = local_date.replace(day=1)
    next_first = (first.replace(year=first.year + 1, month=1, day=1)
                  if first.month == 12 else first.replace(month=first.month + 1, day=1))
    return local_date_window_to_utc_range(first)[0], local_date_window_to_utc_range(next_first - timedelta(days=1))[1]

def year_range_utc(local_date: date):
    first = local_date.replace(month=1, day=1)
    next_first = first.replace(year=first.year + 1)
    return local_date_window_to_utc_range(first)[0], local_date_window_to_utc_range(next_first - timedelta(days=1))[1]

def parse_local_datetime_to_utc_naive(data_str: str):
    dt_local_naive = datetime.strptime(data_str, '%Y-%m-%dT%H:%M')
    dt_local = BRAZIL_TZ.localize(dt_local_naive)
    return dt_local.astimezone(pytz.utc).replace(tzinfo=None)

def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

# ====== Normalização forte (clientes) ======
def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn')

def normalize_letters_key(s: str) -> str:
    s = _strip_accents(s).lower()
    s = re.sub(r'[^a-z\u00c0-\u024f\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def normalize_first_token(s: str) -> str:
    k = normalize_letters_key(s)
    return (k.split(' ')[0] if k else '')

# ====== CRÉDITO: helpers e regras ======
def _as_decimal(x) -> Decimal:
    if x is None: return Decimal("0.00")
    if isinstance(x, Decimal): return x
    return Decimal(str(x)).quantize(Decimal("0.01"))

def calcular_valor_final(valor_bruto, desconto_tipo, desconto_valor) -> Decimal:
    bruto = _as_decimal(valor_bruto)
    d     = _as_decimal(desconto_valor)
    if desconto_tipo == "percentual":
        desc = (bruto * d) / Decimal("100")
    elif desconto_tipo == "real":
        desc = d
    else:
        desc = Decimal("0.00")
    if desc > bruto: desc = bruto
    return (bruto - desc).quantize(Decimal("0.01"))

def _find_cliente_by_nome(nome: str):
    if not nome: return None
    cli = Cliente.query.filter(func.lower(Cliente.nome) == (nome or '').lower()).first()
    if cli: return cli
    # fallback por normalização forte
    target = normalize_letters_key(nome or '')
    for c in Cliente.query.all():
        if normalize_letters_key(c.nome or '') == target:
            return c
    # último recurso: 1º token
    tok = normalize_first_token(nome or '')
    for c in Cliente.query.all():
        if normalize_first_token(c.nome or '') == tok:
            return c
    return None

def consumir_credito_em_entrega(entrega_id: int) -> Decimal:
    """
    Consome crédito do cliente SEM depender da forma de pagamento.
    Sempre que houver saldo, usa para abater o valor da entrega.

    - Debita do saldo_atual do cliente
    - Atualiza entrega.credito_usado
    - Cria um CreditoMovimento(tipo='debito')
    - Se cobrir 100% do valor, marca status_pagamento='pago'
    """
    e = Entrega.query.get(entrega_id)
    if not e:
        return Decimal("0.00")

    # Acha o cliente (cliente_id tem prioridade)
    cli = None
    if getattr(e, "cliente_id", None):
        cli = Cliente.query.get(e.cliente_id)
    if not cli:
        cli = _find_cliente_by_nome(e.cliente)
    if not cli:
        return Decimal("0.00")

    valor = _as_decimal(e.valor or 0)
    usado_antes = _as_decimal(e.credito_usado or 0)
    faltante = valor - usado_antes
    if faltante <= 0:
        # já totalmente coberto
        return Decimal("0.00")

    saldo = _as_decimal(cli.saldo_atual or 0)
    consumir = min(saldo, faltante)
    if consumir <= 0:
        # não há saldo
        return Decimal("0.00")

    novo_saldo = saldo - consumir
    novo_usado = usado_antes + consumir

    cli.saldo_atual = float(novo_saldo)
    e.credito_usado = float(novo_usado)

    mov = CreditoMovimento(
        cliente_id=cli.id,
        tipo="debito",
        valor=float(consumir),
        referencia=f"Entrega #{e.id}",
        entrega_id=e.id,
    )
    db.session.add(mov)
    db.session.flush()
    e.credito_mov_id = mov.id

    # Se o crédito cobriu tudo → marca pago
    if novo_usado >= valor:
        e.status_pagamento = "pago"

        # Só força "Crédito" se estiver vazio
        if not (e.pagamento or "").strip():
            e.pagamento = "Crédito"

        if not (e.recebido_por or "").strip():
            e.recebido_por = "Crédito automático"
    else:
        # crédito parcial → mantém status se já tiver, senão "pendente"
        if not (e.status_pagamento or "").strip():
            e.status_pagamento = "pendente"

    db.session.commit()
    return consumir


def desfazer_consumo_credito_da_entrega(entrega_id: int) -> Decimal:
    """
    Estorna TODO crédito usado nesta entrega, devolvendo para o saldo do cliente
    e zerando entrega.credito_usado / entrega.credito_mov_id.
    NÃO mexe em pagamento/status_pagamento.
    """
    e = Entrega.query.get(entrega_id)
    if not e:
        return Decimal("0.00")

    usado = _as_decimal(e.credito_usado or 0)
    if usado <= 0:
        return Decimal("0.00")

    # Acha o cliente
    cli = None
    if getattr(e, "cliente_id", None):
        cli = Cliente.query.get(e.cliente_id)
    if not cli:
        cli = _find_cliente_by_nome(e.cliente)
    if not cli:
        return Decimal("0.00")

    # devolve ao saldo
    cli.saldo_atual = float(_as_decimal(cli.saldo_atual) + usado)

    mov_estorno = CreditoMovimento(
        cliente_id=cli.id,
        tipo="credito",
        valor=float(usado),
        referencia=f"Estorno Entrega #{e.id}",
    )
    db.session.add(mov_estorno)

    e.credito_usado = 0.0
    e.credito_mov_id = None

    db.session.commit()
    return usado

def consumo_total_do_credito(credito_id: int) -> float:
    """
    Soma quanto já foi CONSUMIDO (tipo='debito') vinculado a este crédito.
    No modelo atual quase sempre será 0, porque os débitos não usam credito_id,
    mas a função existe para manter compatibilidade com creditos_excluir.
    """
    total = (
        db.session.query(func.sum(CreditoMovimento.valor))
        .filter(
            CreditoMovimento.credito_id == credito_id,
            CreditoMovimento.tipo == "debito"
        )
        .scalar()
        or 0.0
    )
    return float(total or 0.0)

# ---- Constantes "semânticas" para compatibilidade com código antigo
TIPO_ENTRADA = 'ENTRADA'
TIPO_CONSUMO = 'CONSUMO'
TIPO_AJUSTE  = 'AJUSTE'

# ---- Compat: wrapper com o nome antigo usado em algumas rotas
def calc_valor_final(valor, desconto_tipo, desconto_valor):
    return float(calcular_valor_final(valor, desconto_tipo, desconto_valor))

# ---- Atualiza saldo do cliente (sem dar commit; quem chama decide)
def atualizar_saldo_cliente(cliente_id, delta):
    cli = Cliente.query.get(cliente_id)
    if not cli:
        return
    cli.saldo_atual = float(_as_decimal(cli.saldo_atual) + _as_decimal(delta))
    db.session.add(cli)

# ---- Registrar movimento (mapeia para CreditoMovimento.tipo = 'credito'/'debito')
def registrar_movimento(cliente_id, tipo, valor, referencia='', credito_id=None, entrega_id=None):
    tipo_up = (tipo or '').upper()
    if tipo_up in (TIPO_ENTRADA, TIPO_AJUSTE, 'CREDITO'):
        tm = 'credito'
    elif tipo_up in (TIPO_CONSUMO, 'DEBITO', 'DÉBITO'):
        tm = 'debito'
    else:
        tm = 'credito'
    mov = CreditoMovimento(
        cliente_id=cliente_id,
        tipo=tm,
        valor=float(_as_decimal(valor)),
        referencia=(referencia or '')[:120],
        credito_id=credito_id,
        entrega_id=entrega_id
    )
    db.session.add(mov)
    return mov


def br_date_ymd(dt_utc_naive: datetime) -> str:
    if not dt_utc_naive:
        return ''
    return to_brasilia(dt_utc_naive).date().isoformat()

# ====== feriados ======
MUNICIPAIS_NATAL = {(11, 21): "Nossa Senhora da Apresentação (Municipal - Natal/RN)"}
def verifica_feriado(data_ref=None):
    if data_ref is None: data_ref = datetime.now(BRAZIL_TZ).date()
    feriados_nac = holidays.Brazil(years=data_ref.year)
    feriados_est = holidays.Brazil(state='RN', years=data_ref.year)
    nomes = []
    if data_ref in feriados_nac: nomes.append(f"Feriado Nacional – {feriados_nac.get(data_ref)}")
    if data_ref in feriados_est and feriados_est.get(data_ref) != feriados_nac.get(data_ref):
        nomes.append(f"Feriado Estadual (RN) – {feriados_est.get(data_ref)}")
    if (data_ref.month, data_ref.day) in MUNICIPAIS_NATAL:
        nomes.append(f"Feriado Municipal (Natal/RN) – {MUNICIPAIS_NATAL[(data_ref.month, data_ref.day)]}")
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
    if next_url: return redirect(next_url)
    from_ref = _build_admin_url_from_referrer()
    if from_ref: return redirect(from_ref)
    params = session.get("last_filters") or {}
    return redirect(url_for("admin", **params))

# ====== Helpers de segurança ======
def _assert_entrega_do_cooperado(entrega: 'Entrega'):
    uid = session.get('user_id')
    if uid is None or session.get('is_admin'):
        abort(403)
    if entrega.cooperado_id != uid:
        abort(403)

def master_required(view_func):
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        if not session.get('is_master'):
            flash('Acesso restrito ao admin master.')
            return redirect(url_for('admin'))
        return view_func(*args, **kwargs)
    return _wrapped

# ====== RENDER SAFE (fallback inline) ======
def render_or_string(template_name, fallback_html, **ctx):
    try:
        return render_template(template_name, **ctx)
    except TemplateNotFound:
        return render_template_string(fallback_html, **ctx)

# =========================
# ====== LOGIN ADMIN ======
# =========================
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Admin / Cooperado (mantido)
    if request.method == 'POST':
        usuario = (request.form.get('usuario') or '').strip()
        senha   = request.form.get('senha') or ''
        user_lc = usuario.lower()

        # Admin fixo
        if user_lc in ADMIN_CREDENTIALS:
            cred_map = ADMIN_CREDENTIALS[user_lc]
            if senha in cred_map:
                session['user_id'] = 0
                session['user_nome'] = usuario
                session['is_admin'] = True
                session['is_master'] = bool(cred_map[senha].get('is_master'))
                return redirect(url_for('admin'))
            else:
                flash('Usuário ou senha incorretos.')
                try:
                    return render_template('login.html', now=lambda: datetime.now(BRAZIL_TZ))
                except TemplateNotFound:
                    pass  # cai no fallback abaixo

        # Cooperado
        cooperado = Cooperado.query.filter(func.lower(Cooperado.nome) == user_lc).first()
        if cooperado and cooperado.check_senha(senha):
            if not getattr(cooperado, 'ativo', True):
                flash('Usuário inativo. Fale com o administrador.')
                try:
                    return render_template('login.html', now=lambda: datetime.now(BRAZIL_TZ))
                except TemplateNotFound:
                    pass
            session['user_id'] = cooperado.id
            session['user_nome'] = cooperado.nome
            session['is_admin'] = False
            session['is_master'] = False
            return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha incorretos.')

    # Se não houver template de login, mostra um mínimo
    try:
        return render_template('login.html', now=lambda: datetime.now(BRAZIL_TZ))
    except TemplateNotFound:
        return render_template_string("""
        <h2>Login (Admin/Cooperado)</h2>
        <form method="post">
          <div><label>Usuário</label><input name="usuario"></div>
          <div><label>Senha</label><input name="senha" type="password"></div>
          <button type="submit">Entrar</button>
        </form>
        <hr>
        <p>É cliente? <a href="{{ url_for('cliente_login') }}">Entrar como Cliente</a> | 
        <a href="{{ url_for('cliente_primeiro_acesso') }}">Primeiro acesso</a></p>
        """, now=lambda: datetime.now(BRAZIL_TZ))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================================
# ====== CLIENTE: LOGIN & SIGNUP ===
# ==================================
def _norm_phone(s: str) -> str:
    if s is None: return ""
    digits = re.sub(r'\D+', '', str(s))
    if digits.startswith('55'): digits = digits[2:]
    if len(digits) > 11: digits = digits[-11:]
    return digits

@app.route('/cliente/primeiro_acesso', methods=['GET', 'POST'])
def cliente_primeiro_acesso():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        telefone = _norm_phone(request.form.get('telefone') or '')
        senha = request.form.get('senha') or ''
        if not username or not telefone or not senha:
            flash('Informe usuário, telefone e senha.')
            return redirect(url_for('cliente_primeiro_acesso'))

        # username único
        if Cliente.query.filter(func.lower(Cliente.username) == username.lower()).first():
            flash('Nome de usuário já existe. Escolha outro.')
            return redirect(url_for('cliente_primeiro_acesso'))

        # se já existe cliente com mesmo telefone, atualiza para associar login
        cli = Cliente.query.filter(Cliente.telefone == telefone).first()
        if not cli:
            # cria cliente novo; "nome" obrigatório — usamos o username no nome por padrão
            cli = Cliente(nome=username, telefone=telefone, saldo_atual=0.0)
            db.session.add(cli)
            db.session.flush()
        cli.username = username
        cli.set_senha(senha)

        db.session.commit()

        # loga e vai direto para Meu Crédito
        session['cliente_id'] = cli.id
        session['cliente_username'] = cli.username
        session['cliente_nome'] = cli.nome
        session['is_cliente'] = True
        return redirect(url_for('meu_credito'))

    # Fallback mínimo se não houver template
    return render_or_string("cliente_primeiro_acesso.html", """
    <h2>Primeiro Acesso do Cliente</h2>
    <form method="post">
      <div><label>Nome de usuário</label><input name="username" required></div>
      <div><label>Telefone</label><input name="telefone" required></div>
      <div><label>Senha</label><input type="password" name="senha" required></div>
      <button type="submit">Cadastrar e entrar</button>
    </form>
    <p>Já tem cadastro? <a href="{{ url_for('cliente_login') }}">Entrar como Cliente</a></p>
    """)

@app.route('/cliente/login', methods=['GET', 'POST'])
def cliente_login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        senha = request.form.get('senha') or ''
        if not username or not senha:
            flash('Informe usuário e senha.')
            return redirect(url_for('cliente_login'))

        cli = Cliente.query.filter(func.lower(Cliente.username) == username.lower()).first()
        if not cli or not cli.check_senha(senha):
            flash('Usuário ou senha inválidos.')
            return redirect(url_for('cliente_login'))

        session['cliente_id'] = cli.id
        session['cliente_username'] = cli.username
        session['cliente_nome'] = cli.nome
        session['is_cliente'] = True
        return redirect(url_for('meu_credito'))

    return render_or_string("cliente_login.html", """
    <h2>Login do Cliente</h2>
    <form method="post">
      <div><label>Usuário</label><input name="username" required></div>
      <div><label>Senha</label><input type="password" name="senha" required></div>
      <button type="submit">Entrar</button>
    </form>
    <p>Novo por aqui? <a href="{{ url_for('cliente_primeiro_acesso') }}">Primeiro acesso</a></p>
    """)

@app.route('/cliente/logout')
def cliente_logout():
    for k in ['cliente_id','cliente_username','cliente_nome','is_cliente']:
        session.pop(k, None)
    flash('Você saiu da área do cliente.')
    return redirect(url_for('cliente_login'))

def cliente_required(view_func):
    @wraps(view_func)
    def _wrap(*a, **kw):
        if not session.get('is_cliente') or not session.get('cliente_id'):
            return redirect(url_for('cliente_login'))
        return view_func(*a, **kw)
    return _wrap

@app.route('/meu-credito')
@cliente_required
def meu_credito():
    cid = session['cliente_id']
    cli = Cliente.query.get_or_404(cid)
    movs = (CreditoMovimento.query
            .filter(CreditoMovimento.cliente_id == cid)
            .order_by(CreditoMovimento.criado_em.desc()).all())
    # Render com fallback
    return render_or_string("meu_credito.html", """
<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8">
<title>Meu Crédito</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0b1220;color:#e6efff}
.wrap{max-width:960px;margin:0 auto;padding:24px}
.card{background:#0f1629;border:1px solid #1c2a4a;border-radius:16px;padding:16px}
h1{margin:0 0 6px}
.badge{display:inline-block;font-weight:800;border:1px solid #3557d6;border-radius:999px;padding:4px 10px;background:#0d1b3d;color:#bcd0ff}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}
th,td{padding:8px;border-bottom:1px solid #1c2a4a}
th{background:#102053;position:sticky;top:0}
.money{font-weight:900}
</style>
</head><body>
  <div class="wrap">
    <div class="card">
      <h1>Olá, {{ cli.nome or cli.username }}!</h1>
      <div class="badge">Saldo atual: <span class="money" style="margin-left:6px">R$ {{ '%.2f'|format(cli.saldo_atual)|replace('.', ',') }}</span></div>
      <p style="opacity:.8;margin-top:8px">Abaixo, seu histórico de créditos (entradas) e usos (débitos).</p>
      <div style="overflow:auto;border:1px solid #1c2a4a;border-radius:12px">
        <table>
          <thead><tr><th>Data</th><th>Tipo</th><th>Descrição</th><th>Valor</th></tr></thead>
          <tbody>
            {% for m in movs %}
              <tr>
                <td>{{ to_brasilia(m.criado_em).strftime('%d/%m/%Y %H:%M') }}</td>
                <td>{{ 'Crédito' if m.tipo=='credito' else 'Débito' }}</td>
                <td>{{ m.referencia or '-' }}</td>
                <td class="money">R$ {{ '%.2f'|format(m.valor) | replace('.', ',') }}</td>
              </tr>
            {% endfor %}
            {% if movs|length == 0 %}
              <tr><td colspan="4" style="text-align:center;opacity:.7;padding:16px">Nenhuma movimentação.</td></tr>
            {% endif %}
          </tbody>
        </table>
      </div>
      <p style="margin-top:12px"><a href="{{ url_for('cliente_logout') }}" style="color:#bcd0ff">Sair</a></p>
    </div>
  </div>
</body></html>
    """, cli=cli, movs=movs, to_brasilia=to_brasilia)


# ===========================
# ====== ROTAS EXISTENTES ===
# ===========================
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
        query.options(joinedload(Entrega.cooperado))
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

    lista_espera = ListaEspera.query.order_by(ListaEspera.pos.asc(), ListaEspera.created_at.asc()).all()
    ids_em_fila = {it.cooperado_id for it in lista_espera if it.cooperado_id}
    cooperados_disponiveis = [c for c in cooperados if c.id not in ids_em_fila]

    return render_template(
        'admin.html',
        entregas=entregas,
        cooperados=cooperados,
        estatisticas=estatisticas,
        data_inicio=data_inicio,
        data_fim=data_fim,
        to_brasilia=to_brasilia,
        request=request,
        now=lambda: datetime.now(BRAZIL_TZ),
        feriado_hoje=feriado_hoje,
        tem_pendente=tem_pendente,
        lista_espera=lista_espera,
        cooperados_disponiveis=cooperados_disponiveis
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
        data_envio=datetime.utcnow(),
        data_atribuida=None,
        cooperado_id=None,
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
    cooperado_nome = session['user_nome']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    status_pgto = (request.args.get('status_pgto') or 'todas').lower()

    query = Entrega.query.filter(Entrega.cooperado_id == user_id)
    hoje_brasil = datetime.now(BRAZIL_TZ).date()

    if not inicio and not fim:
        # Padrão: entregas do dia atual
        inicio_utc, fim_utc = local_date_window_to_utc_range(hoje_brasil)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)
        periodo_str = "hoje"
    else:
        # Filtro por data
        if inicio:
            di = datetime.strptime(inicio, "%Y-%m-%d").date()
            inicio_utc, _ = local_date_window_to_utc_range(di)
            query = query.filter(Entrega.data_envio >= inicio_utc)
        if fim:
            df_ = datetime.strptime(fim, "%Y-%m-%d").date()
            _, fim_utc = local_date_window_to_utc_range(df_)
            query = query.filter(Entrega.data_envio <= fim_utc)
        periodo_str = periodo_legivel_str(inicio, fim)

    if status_pgto != 'todas':
        if status_pgto == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pgto == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    entregas = query.order_by(Entrega.data_envio.desc()).all()

    # Estatísticas do período
    total_entregas = len(entregas)
    valor_total = sum(e.valor or 0 for e in entregas)
    pago = sum(e.valor or 0 for e in entregas if (e.status_pagamento or '').lower() == 'pago')
    pendente = valor_total - pago

    estatisticas = {
        "total_entregas": total_entregas,
        "valor_total": valor_total,
        "pago": pago,
        "pendente": pendente,
        "periodo_str": periodo_str
    }

    return render_template(
        'painel_cooperado.html',
        entregas=entregas,
        cooperado_nome=cooperado_nome,
        estatisticas=estatisticas,
        to_brasilia=to_brasilia,
        inicio=inicio,
        fim=fim,
        status_pgto=status_pgto
    )

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    if request.method == 'POST':
        # Desfaz consumo de crédito antes de qualquer alteração de valor/status
        desfazer_consumo_credito_da_entrega(e.id)

        e.cliente = request.form['cliente']
        e.bairro = request.form['bairro']
        e.valor = float(request.form['valor'])
        
        # A data é local (BR), precisa converter para UTC naive antes de salvar
        e.data_envio = parse_local_datetime_to_utc_naive(request.form['data_envio'])
        
        cooperado_id = request.form.get('cooperado_id')
        e.cooperado_id = int(cooperado_id) if cooperado_id else None
        
        e.status_pagamento = request.form.get('status_pagamento') or 'pendente'
        e.status = request.form.get('status') or 'pendente'
        e.pagamento = request.form.get('pagamento') or ''
        e.recebido_por = request.form.get('recebido_por') or None
        
        # Se foi atribuído agora ou já estava atribuído, marca data_atribuida
        if e.cooperado_id and e.data_atribuida is None:
            e.data_atribuida = datetime.utcnow()

        # Tenta re-consumir crédito após alteração
        if e.cliente_id or e.cliente:
            consumir_credito_em_entrega(e.id)
            
        db.session.commit()
        flash(f'Entrega #{e.id} atualizada com sucesso!')
        return redirect_back_to_admin()

    # Para exibir no formulário: data em formato local BR
    data_local = to_brasilia(e.data_envio)
    data_envio_formatada = data_local.strftime('%Y-%m-%dT%H:%M') if data_local else ''

    return render_template(
        'editar_entrega.html',
        entrega=e,
        cooperados=cooperados,
        data_envio_formatada=data_envio_formatada
    )

@app.route('/excluir_entrega/<int:id>', methods=['POST'])
def excluir_entrega(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    desfazer_consumo_credito_da_entrega(e.id) # Estorna antes de excluir
    
    # Remove movimentos de débito ligados a esta entrega, se existirem
    CreditoMovimento.query.filter_by(entrega_id=e.id).delete()
    
    db.session.delete(e)
    db.session.commit()
    flash(f'Entrega #{id} excluída com sucesso.')
    return redirect_back_to_admin()

@app.route('/atribuir_cooperado/<int:entrega_id>/<int:cooperado_id>', methods=['POST'])
def atribuir_cooperado(entrega_id, cooperado_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(entrega_id)
    c = Cooperado.query.get_or_404(cooperado_id)
    e.cooperado_id = c.id
    if e.data_atribuida is None:
        e.data_atribuida = datetime.utcnow()
    db.session.commit()
    flash(f'Entrega #{e.id} atribuída a {c.nome}.')
    return redirect_back_to_admin()

@app.route('/marcar_pago/<int:id>', methods=['POST'])
def marcar_pago(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    e = Entrega.query.get_or_404(id)
    e.status_pagamento = 'pago'
    if not e.status:
        e.status = 'pendente' # Mantém pendente se não foi entregue
    db.session.commit()
    flash(f'Entrega #{e.id} marcada como PAGA.')
    return redirect_back_to_admin()

@app.route('/cooperado_marcar_entregue/<int:id>', methods=['POST'])
def cooperado_marcar_entregue(id):
    e = Entrega.query.get_or_404(id)
    _assert_entrega_do_cooperado(e)
    
    e.status = 'entregue'
    
    pagamento_recebido = request.form.get('pagamento_recebido')
    recebido_por = request.form.get('recebido_por')

    if pagamento_recebido:
        e.status_pagamento = 'pago'
        e.pagamento = pagamento_recebido
        e.recebido_por = recebido_por
    
    db.session.commit()
    flash(f'Entrega #{e.id} marcada como ENTREGUE.')
    return redirect(url_for('painel_cooperado'))

@app.route('/pagamento_usa_credito/<int:entrega_id>', methods=['POST'])
def pagamento_usa_credito(entrega_id):
    e = Entrega.query.get_or_404(entrega_id)
    _assert_entrega_do_cooperado(e)
    
    consumido = consumir_credito_em_entrega(e.id)
    
    if consumido > 0:
        flash(f'R$ {consumido:.2f} de crédito utilizado na entrega #{e.id}.')
    else:
        flash('Não foi possível usar crédito: saldo insuficiente ou entrega já coberta.')
        
    return redirect(url_for('painel_cooperado'))


# =================================
# ====== Rotas de Crédito =========
# =================================
@app.route('/creditos')
def creditos():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    cliente_id = request.args.get('cliente_id', 'todos')

    query = db.session.query(Credito, Cliente).join(Cliente).order_by(Credito.criado_em.desc())
    
    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Credito.criado_em >= inicio_utc)
    if data_fim:
        df_ = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
        query = query.filter(Credito.criado_em <= fim_utc)
        
    if cliente_id and cliente_id != 'todos':
        query = query.filter(Credito.cliente_id == int(cliente_id))

    creditos_data = query.all()
    clientes = Cliente.query.order_by(Cliente.nome).all()

    return render_template(
        'creditos.html',
        creditos_data=creditos_data,
        clientes=clientes,
        to_brasilia=to_brasilia,
        request=request
    )

@app.route('/creditos/novo', methods=['GET', 'POST'])
def creditos_novo():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    clientes = Cliente.query.order_by(Cliente.nome).all()

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        valor_bruto = request.form.get('valor_bruto', 0.0)
        desconto_tipo = request.form.get('desconto_tipo', 'nenhum')
        desconto_valor = request.form.get('desconto_valor', 0.0)
        motivo = request.form.get('motivo')
        
        try:
            cli = Cliente.query.get(int(cliente_id))
            if not cli:
                flash("Cliente não encontrado.", 'error')
                return redirect(url_for('creditos_novo'))
            
            valor_bruto = float(valor_bruto)
            desconto_valor = float(desconto_valor)
            valor_final_dec = calcular_valor_final(valor_bruto, desconto_tipo, desconto_valor)
            valor_final = float(valor_final_dec)
            
            saldo_antes = cli.saldo_atual
            saldo_depois = float(_as_decimal(saldo_antes) + valor_final_dec)
            
            credito = Credito(
                cliente_id=cli.id,
                valor_bruto=valor_bruto,
                desconto_tipo=desconto_tipo,
                desconto_valor=desconto_valor,
                valor_final=valor_final,
                motivo=motivo,
                saldo_antes=saldo_antes,
                saldo_depois=saldo_depois,
                criado_por=session.get('user_nome')
            )
            db.session.add(credito)
            db.session.flush() # Para obter o ID

            # Atualiza saldo do cliente
            atualizar_saldo_cliente(cli.id, valor_final)
            
            # Registra movimento
            registrar_movimento(
                cliente_id=cli.id,
                tipo=TIPO_ENTRADA, 
                valor=valor_final, 
                referencia=f"Crédito #{credito.id}",
                credito_id=credito.id
            )

            db.session.commit()
            flash(f'Crédito #{credito.id} de R$ {valor_final:.2f} adicionado para {cli.nome}.')
            return redirect(url_for('creditos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao adicionar crédito: {str(e)}", 'error')
            app.logger.error(f"Erro em creditos_novo: {e}")

    return render_template('creditos_novo.html', clientes=clientes)

@app.route('/creditos/editar/<int:id>', methods=['GET', 'POST'])
def creditos_editar(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    credito = Credito.query.get_or_404(id)
    cli = Cliente.query.get(credito.cliente_id)
    
    if request.method == 'POST':
        # Valor original
        valor_original = _as_decimal(credito.valor_final)

        # Novos valores
        valor_bruto = float(request.form.get('valor_bruto', 0.0))
        desconto_tipo = request.form.get('desconto_tipo', 'nenhum')
        desconto_valor = float(request.form.get('desconto_valor', 0.0))
        motivo = request.form.get('motivo')
        
        try:
            valor_final_dec = calcular_valor_final(valor_bruto, desconto_tipo, desconto_valor)
            valor_final = float(valor_final_dec)
            
            delta_valor = valor_final_dec - valor_original
            
            # 1. Atualiza saldo do cliente
            atualizar_saldo_cliente(cli.id, delta_valor)
            
            # 2. Atualiza o registro de Crédito
            credito.valor_bruto = valor_bruto
            credito.desconto_tipo = desconto_tipo
            credito.desconto_valor = desconto_valor
            credito.valor_final = valor_final
            credito.motivo = motivo
            
            # O saldo_antes/depois é de quando foi criado, não atualiza aqui
            # cli.saldo_atual já foi atualizado por atualizar_saldo_cliente
            
            # 3. Atualiza movimento de entrada correspondente (se existir)
            mov = CreditoMovimento.query.filter_by(credito_id=credito.id, tipo='credito').first()
            if mov:
                mov.valor = valor_final
            else:
                # Se não houver, registra o delta como um ajuste
                if delta_valor != Decimal('0.00'):
                    registrar_movimento(
                        cliente_id=cli.id,
                        tipo=TIPO_AJUSTE, 
                        valor=float(delta_valor), 
                        referencia=f"Ajuste Crédito #{credito.id}",
                        credito_id=credito.id # Mantém a referência original
                    )
            
            db.session.commit()
            flash(f'Crédito #{credito.id} de {cli.nome} atualizado.')
            return redirect(url_for('creditos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao editar crédito: {str(e)}", 'error')
            app.logger.error(f"Erro em creditos_editar: {e}")

    return render_template('creditos_editar.html', credito=credito, cliente=cli)

@app.route('/creditos/excluir/<int:id>', methods=['POST'])
def creditos_excluir(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    credito = Credito.query.get_or_404(id)
    cli = Cliente.query.get(credito.cliente_id)
    
    # 1. Verifica consumo
    consumido = consumo_total_do_credito(credito.id)
    if consumido > 0.0001: # tolerância
        flash(f'Erro: Não é possível excluir. R$ {consumido:.2f} deste crédito já foi consumido.', 'error')
        return redirect(url_for('creditos'))

    valor_final = _as_decimal(credito.valor_final)
    
    try:
        # 2. Reverte no saldo
        atualizar_saldo_cliente(cli.id, -valor_final)
        
        # 3. Remove movimentos
        CreditoMovimento.query.filter_by(credito_id=credito.id).delete()
        
        # 4. Remove o crédito
        db.session.delete(credito)
        
        db.session.commit()
        flash(f'Crédito #{id} de R$ {valor_final:.2f} removido do saldo de {cli.nome}.')
        return redirect(url_for('creditos'))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir crédito: {str(e)}", 'error')
        app.logger.error(f"Erro em creditos_excluir: {e}")
        return redirect(url_for('creditos'))

@app.route('/creditos/exportar')
def creditos_exportar():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    query = db.session.query(Credito, Cliente).join(Cliente).order_by(Credito.criado_em.desc())
    creditos_data = query.all()

    data = [{
        'ID Crédito': c.id,
        'Cliente': cli.nome,
        'Data Criação (BR)': to_brasilia(c.criado_em).strftime('%d/%m/%Y %H:%M'),
        'Valor Bruto': c.valor_bruto,
        'Desconto Tipo': c.desconto_tipo,
        'Desconto Valor': c.desconto_valor,
        'Valor Final': c.valor_final,
        'Motivo': c.motivo,
        'Saldo Antes': c.saldo_antes,
        'Saldo Depois': c.saldo_depois,
        'Criado Por': c.criado_por,
    } for c, cli in creditos_data]
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='Creditos')
    writer.close()
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name='creditos_coopex.xlsx',
        as_attachment=True
    )

# =================================
# ====== Movimentos de Crédito ====
# =================================

@app.route('/credmov/novo', methods=['GET', 'POST'])
def credmov_novo():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    clientes = Cliente.query.order_by(Cliente.nome).all()

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        tipo = request.form.get('tipo') # 'credito' ou 'debito'
        valor = request.form.get('valor', 0.0)
        referencia = request.form.get('referencia')

        try:
            cli = Cliente.query.get(int(cliente_id))
            if not cli:
                flash("Cliente não encontrado.", 'error')
                return redirect(url_for('credmov_novo'))
            
            valor = float(valor)
            if valor <= 0:
                flash("Valor deve ser positivo.", 'error')
                return redirect(url_for('credmov_novo'))

            valor_dec = _as_decimal(valor)
            delta = valor_dec if tipo == 'credito' else -valor_dec
            
            # 1. Atualiza saldo
            atualizar_saldo_cliente(cli.id, delta)
            
            # 2. Registra Movimento (como AJUSTE)
            registrar_movimento(
                cliente_id=cli.id,
                tipo=tipo, 
                valor=valor, 
                referencia=referencia or f"Ajuste Manual {tipo.capitalize()}"
            )

            db.session.commit()
            flash(f'Movimento ({tipo}) de R$ {valor:.2f} registrado para {cli.nome}.')
            return redirect(url_for('creditos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar movimento: {str(e)}", 'error')
            app.logger.error(f"Erro em credmov_novo: {e}")

    return render_template('credmov_novo.html', clientes=clientes)

@app.route('/credmov/editar/<int:id>', methods=['GET', 'POST'])
def credmov_editar(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    mov = CreditoMovimento.query.get_or_404(id)
    cli = Cliente.query.get(mov.cliente_id)
    
    # Bloqueia edição de movimentos automáticos (ligados a Crédito ou Entrega)
    if mov.credito_id or mov.entrega_id:
        flash("Movimento automático não pode ser editado. Edite o Crédito ou a Entrega de origem.", 'error')
        return redirect(url_for('creditos'))

    if request.method == 'POST':
        # Valor original
        valor_original = _as_decimal(mov.valor)
        delta_original = valor_original if mov.tipo == 'credito' else -valor_original

        # Novos valores
        valor_novo = float(request.form.get('valor', 0.0))
        tipo_novo = request.form.get('tipo')
        referencia_nova = request.form.get('referencia')
        
        try:
            valor_novo_dec = _as_decimal(valor_novo)
            if valor_novo_dec <= 0:
                flash("Valor deve ser positivo.", 'error')
                return redirect(url_for('credmov_editar', id=id))
                
            delta_novo = valor_novo_dec if tipo_novo == 'credito' else -valor_novo_dec
            
            # 1. Reverte o delta original
            atualizar_saldo_cliente(cli.id, -delta_original)
            
            # 2. Aplica o novo delta
            atualizar_saldo_cliente(cli.id, delta_novo)

            # 3. Atualiza o Movimento
            mov.valor = valor_novo
            mov.tipo = tipo_novo
            mov.referencia = referencia_nova
            
            db.session.commit()
            flash(f'Movimento #{mov.id} de {cli.nome} atualizado.')
            return redirect(url_for('creditos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao editar movimento: {str(e)}", 'error')
            app.logger.error(f"Erro em credmov_editar: {e}")

    return render_template('credmov_editar.html', mov=mov, cliente=cli)

@app.route('/credmov/excluir/<int:id>', methods=['POST'])
def credmov_excluir(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    mov = CreditoMovimento.query.get_or_404(id)
    cli = Cliente.query.get(mov.cliente_id)
    
    # Bloqueia exclusão de movimentos automáticos
    if mov.credito_id or mov.entrega_id:
        flash("Movimento automático não pode ser excluído. Exclua o Crédito ou a Entrega de origem.", 'error')
        return redirect(url_for('creditos'))

    valor = _as_decimal(mov.valor)
    delta = valor if mov.tipo == 'credito' else -valor
    
    try:
        # 1. Reverte no saldo
        atualizar_saldo_cliente(cli.id, -delta)
        
        # 2. Remove o movimento
        db.session.delete(mov)
        
        db.session.commit()
        flash(f'Movimento #{id} removido e saldo de {cli.nome} ajustado.')
        return redirect(url_for('creditos'))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir movimento: {str(e)}", 'error')
        app.logger.error(f"Erro em credmov_excluir: {e}")
        return redirect(url_for('creditos'))


# =================================
# ====== Estatísticas =============
# =================================

@app.route('/estatisticas/cooperado')
def estatisticas_cooperado():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    
    cooperados = Cooperado.query.filter_by(ativo=True).order_by(Cooperado.nome).all()
    stats = []

    # Período
    hoje = datetime.now(BRAZIL_TZ).date()
    # Padrão: Mês atual
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    if data_inicio_str and data_fim_str:
        di = datetime.strptime(data_inicio_str, "%Y-%m-%d").date()
        df = datetime.strptime(data_fim_str, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        _, fim_utc = local_date_window_to_utc_range(df)
        periodo_str = periodo_legivel_str(data_inicio_str, data_fim_str)
    else:
        # Mês atual
        di = hoje.replace(day=1)
        df = (di + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        inicio_utc, fim_utc = month_range_utc(hoje)
        periodo_str = f"{di.strftime('%d/%m/%Y')} a {df.strftime('%d/%m/%Y')}"
        data_inicio_str = di.isoformat()
        data_fim_str = df.isoformat()

    for coop in cooperados:
        entregas = Entrega.query.filter(
            Entrega.cooperado_id == coop.id,
            Entrega.data_envio >= inicio_utc,
            Entrega.data_envio <= fim_utc
        ).all()
        
        total_entregas = len(entregas)
        valor_total = sum(e.valor or 0 for e in entregas)
        pago = sum(e.valor or 0 for e in entregas if (e.status_pagamento or '').lower() == 'pago')
        pendente = valor_total - pago
        
        stats.append({
            'nome': coop.nome,
            'total_entregas': total_entregas,
            'valor_total': valor_total,
            'pago': pago,
            'pendente': pendente
        })
        
    return render_template(
        'estatisticas_cooperado.html',
        stats=stats,
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        periodo_str=periodo_str
    )

@app.route('/estatisticas/clientes')
def estatisticas_clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    
    # Busca os 20 clientes com maior saldo (credito positivo)
    top_clientes_saldo = (
        Cliente.query
        .filter(Cliente.saldo_atual > 0)
        .order_by(Cliente.saldo_atual.desc())
        .limit(20)
        .all()
    )

    # Busca os 20 clientes com mais entregas (contagem)
    top_clientes_entregas_data = (
        db.session.query(Entrega.cliente_id, Cliente.nome, func.count(Entrega.id).label('total_entregas'))
        .join(Cliente, Entrega.cliente_id == Cliente.id)
        .group_by(Entrega.cliente_id, Cliente.nome)
        .order_by(text('total_entregas DESC'))
        .limit(20)
        .all()
    )
    
    top_clientes_entregas = [{
        'nome': nome,
        'total_entregas': total_entregas,
        'saldo': Cliente.query.get(cliente_id).saldo_atual if cliente_id else 0.0
    } for cliente_id, nome, total_entregas in top_clientes_entregas_data if cliente_id]
    
    # Clientes sem login
    clientes_sem_login = Cliente.query.filter((Cliente.username == None) | (Cliente.username == '')).count()

    return render_template(
        'estatisticas_clientes.html',
        top_clientes_saldo=top_clientes_saldo,
        top_clientes_entregas=top_clientes_entregas,
        clientes_sem_login=clientes_sem_login
    )


# =================================
# ====== Exportação CSV/XLSX ======
# =================================

@app.route('/exportar/faturamento-cooperado', methods=['GET'])
def exportar_faturamento_coop():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')

    if not data_inicio_str or not data_fim_str:
        flash('Informe o período (Data Início e Data Fim) para exportar o faturamento por cooperado.', 'error')
        return redirect(url_for('estatisticas_cooperado'))

    di = datetime.strptime(data_inicio_str, "%Y-%m-%d").date()
    df = datetime.strptime(data_fim_str, "%Y-%m-%d").date()
    inicio_utc, _ = local_date_window_to_utc_range(di)
    _, fim_utc = local_date_window_to_utc_range(df)

    cooperados = Cooperado.query.filter_by(ativo=True).order_by(Cooperado.nome).all()
    data = []

    for coop in cooperados:
        entregas = Entrega.query.filter(
            Entrega.cooperado_id == coop.id,
            Entrega.data_envio >= inicio_utc,
            Entrega.data_envio <= fim_utc
        ).all()
        
        total_entregas = len(entregas)
        valor_total = sum(e.valor or 0 for e in entregas)
        pago = sum(e.valor or 0 for e in entregas if (e.status_pagamento or '').lower() == 'pago')
        pendente = valor_total - pago

        data.append({
            'Cooperado': coop.nome,
            'Total de Entregas': total_entregas,
            'Valor Total': valor_total,
            'Valor Pago': pago,
            'Valor Pendente': pendente,
        })
        
    df_export = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    
    sheet_name = f'Faturamento {di.strftime("%d%m%y")} a {df.strftime("%d%m%y")}'
    df_export.to_excel(writer, index=False, sheet_name=sheet_name)
    
    writer.close()
    output.seek(0)
    
    filename = f'faturamento_cooperado_{di.isoformat()}_a_{df.isoformat()}.xlsx'
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name=filename,
        as_attachment=True
    )

@app.route('/exportar/entregas', methods=['GET'])
def exportar_entregas():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    cooperado_id = request.args.get('cooperado_id')

    query = Entrega.query.options(joinedload(Entrega.cooperado))
    filename = 'entregas_completo.xlsx'
    
    if data_inicio_str:
        di = datetime.strptime(data_inicio_str, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
        filename = f'entregas_{di.isoformat()}.xlsx'
        
    if data_fim_str:
        df_ = datetime.strptime(data_fim_str, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_)
        query = query.filter(Entrega.data_envio <= fim_utc)
        filename = f'entregas_{data_inicio_str}_a_{data_fim_str}.xlsx'

    if cooperado_id:
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    entregas = query.order_by(Entrega.data_envio.desc()).all()
    
    data = [{
        'ID': e.id,
        'Cliente': e.cliente,
        'Bairro': e.bairro,
        'Valor': e.valor,
        'Data Envio (BR)': to_brasilia(e.data_envio).strftime('%d/%m/%Y %H:%M'),
        'Data Atribuída (BR)': to_brasilia(e.data_atribuida).strftime('%d/%m/%Y %H:%M') if e.data_atribuida else '',
        'Cooperado ID': e.cooperado_id,
        'Cooperado Nome': e.cooperado.nome if e.cooperado else '',
        'Status Pagamento': e.status_pagamento,
        'Status Entrega': e.status,
        'Pagamento Tipo': e.pagamento,
        'Recebido Por': e.recebido_por,
        'Crédito Usado': e.credito_usado or 0.0,
        'Cliente ID (Novo)': e.cliente_id
    } for e in entregas]
    
    df_export = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df_export.to_excel(writer, index=False, sheet_name='Entregas')
    writer.close()
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name=filename,
        as_attachment=True
    )

@app.route('/exportar/clientes', methods=['GET'])
def exportar_clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    clientes = Cliente.query.order_by(Cliente.nome).all()
    
    data = [{
        'ID': c.id,
        'Nome': c.nome,
        'Telefone': c.telefone,
        'Bairro Origem': c.bairro_origem,
        'Endereco': c.endereco,
        'Saldo Atual': c.saldo_atual,
        'Username Login': c.username or '',
    } for c in clientes]
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='Clientes')
    writer.close()
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name='clientes_coopex.xlsx',
        as_attachment=True
    )

@app.route('/importar/clientes', methods=['GET', 'POST'])
@master_required
def importar_clientes():
    if request.method == 'POST':
        if 'arquivo' not in request.files:
            flash('Nenhum arquivo enviado.', 'error')
            return redirect(url_for('importar_clientes'))

        file = request.files['arquivo']
        if file.filename == '' or not file.filename.endswith(('.xlsx', '.xls')):
            flash('Arquivo inválido. Use um arquivo Excel (.xlsx ou .xls).', 'error')
            return redirect(url_for('importar_clientes'))

        try:
            df = pd.read_excel(file)
            colunas_obrigatorias = ['Nome', 'Telefone', 'Bairro Origem', 'Endereco', 'Saldo Atual']
            
            for col in colunas_obrigatorias:
                if col not in df.columns:
                    flash(f"Erro: Coluna '{col}' obrigatória não encontrada no arquivo.", 'error')
                    return redirect(url_for('importar_clientes'))

            cont_novo = 0
            cont_atualizado = 0
            
            for index, row in df.iterrows():
                nome = str(row['Nome']).strip()
                telefone = _norm_phone(row['Telefone'])
                
                if not nome or not telefone:
                    continue
                
                # Procura por telefone normalizado
                cli = Cliente.query.filter_by(telefone=telefone).first()
                
                if cli:
                    # Atualiza
                    cli.nome = nome
                    cli.bairro_origem = str(row['Bairro Origem']).strip()
                    cli.endereco = str(row['Endereco']).strip()
                    cli.saldo_atual = float(row['Saldo Atual'])
                    cont_atualizado += 1
                else:
                    # Novo
                    cli = Cliente(
                        nome=nome,
                        telefone=telefone,
                        bairro_origem=str(row['Bairro Origem']).strip(),
                        endereco=str(row['Endereco']).strip(),
                        saldo_atual=float(row['Saldo Atual']),
                    )
                    db.session.add(cli)
                    cont_novo += 1
            
            db.session.commit()
            flash(f'Importação concluída. {cont_novo} clientes novos e {cont_atualizado} atualizados.')
            return redirect(url_for('clientes'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro na leitura ou processamento do arquivo: {str(e)}", 'error')
            app.logger.error(f"Erro em importar_clientes: {e}")
            return redirect(url_for('importar_clientes'))

    return render_template('importar_clientes.html')


# =================================
# ====== Lista de Espera ==========
# =================================

@app.route('/lista_espera/add/<int:cooperado_id>', methods=['POST'])
@master_required
def lista_espera_add(cooperado_id):
    c = Cooperado.query.get_or_404(cooperado_id)
    
    if ListaEspera.query.filter_by(cooperado_id=c.id).first():
        flash(f'{c.nome} já está na fila.', 'warning')
        return redirect(url_for('admin'))
        
    last_pos = db.session.query(func.max(ListaEspera.pos)).scalar() or 0
    
    item = ListaEspera(
        cooperado_id=c.id,
        nome=c.nome,
        pos=last_pos + 1
    )
    db.session.add(item)
    db.session.commit()
    flash(f'{c.nome} adicionado à lista de espera.')
    return redirect(url_for('admin'))

@app.route('/lista_espera/remove/<int:id>', methods=['POST'])
@master_required
def lista_espera_remove(id):
    item = ListaEspera.query.get_or_404(id)
    nome = item.nome
    db.session.delete(item)
    
    # Reorganiza a fila (opcional, mas bom)
    db.session.execute(text("""
        UPDATE lista_espera 
        SET pos = sub.new_pos
        FROM (
            SELECT id, ROW_NUMBER() OVER(ORDER BY pos ASC, created_at ASC) as new_pos
            FROM lista_espera
        ) AS sub
        WHERE lista_espera.id = sub.id;
    """))
    
    db.session.commit()
    flash(f'{nome} removido da lista de espera.')
    return redirect(url_for('admin'))

@app.route('/lista_espera/clear', methods=['POST'])
@master_required
def lista_espera_clear():
    ListaEspera.query.delete()
    db.session.commit()
    flash('Lista de espera limpa.', 'warning')
    return redirect(url_for('admin'))


# =================================
# ====== Gerenciar Cooperados =====
# =================================

@app.route('/cooperados')
def cooperados():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    return render_template('cooperados.html', cooperados=cooperados)

@app.route('/cooperados/cadastrar', methods=['GET', 'POST'])
@master_required
def cadastrar_cooperado():
    if request.method == 'POST':
        nome = (request.form['nome'] or '').strip()
        senha = request.form['senha']
        
        if not nome or not senha:
            flash('Nome e senha são obrigatórios.', 'error')
            return redirect(url_for('cadastrar_cooperado'))

        if Cooperado.query.filter(func.lower(Cooperado.nome) == nome.lower()).first():
            flash('Nome de cooperado já existe.', 'error')
            return redirect(url_for('cadastrar_cooperado'))
            
        c = Cooperado(nome=nome)
        c.set_senha(senha)
        db.session.add(c)
        db.session.commit()
        flash(f'Cooperado {nome} cadastrado com sucesso.')
        return redirect(url_for('cooperados'))

    return render_template('cadastrar_cooperado.html')

@app.route('/cooperados/atualizar/<int:id>', methods=['GET', 'POST'])
@master_required
def atualizar_cooperado(id):
    c = Cooperado.query.get_or_404(id)
    if request.method == 'POST':
        nome_novo = (request.form['nome'] or '').strip()
        senha_nova = request.form.get('senha')
        
        if not nome_novo:
            flash('Nome é obrigatório.', 'error')
            return redirect(url_for('atualizar_cooperado', id=id))
            
        # Verifica se o nome já existe (ignorando o cooperado atual)
        if Cooperado.query.filter(func.lower(Cooperado.nome) == nome_novo.lower(), Cooperado.id != id).first():
            flash('Nome de cooperado já existe.', 'error')
            return redirect(url_for('atualizar_cooperado', id=id))

        c.nome = nome_novo
        if senha_nova:
            c.set_senha(senha_nova)
            
        db.session.commit()
        flash(f'Cooperado {c.nome} atualizado com sucesso.')
        return redirect(url_for('cooperados'))
        
    return render_template('atualizar_cooperado.html', cooperado=c)

@app.route('/cooperados/excluir/<int:id>', methods=['POST'])
@master_required
def excluir_cooperado(id):
    c = Cooperado.query.get_or_404(id)
    
    # Verifica se há entregas ativas ou pendentes
    entregas_ativas = Entrega.query.filter_by(cooperado_id=id).count()
    if entregas_ativas > 0:
        flash(f'Não é possível excluir o cooperado {c.nome}: ele(a) possui {entregas_ativas} entregas vinculadas.', 'error')
        return redirect(url_for('cooperados'))
        
    # Remove da lista de espera
    ListaEspera.query.filter_by(cooperado_id=id).delete()
    
    db.session.delete(c)
    db.session.commit()
    flash(f'Cooperado {c.nome} excluído com sucesso.')
    return redirect(url_for('cooperados'))

@app.route('/cooperados/status/<int:id>', methods=['POST'])
@master_required
def mudar_status_cooperado(id):
    c = Cooperado.query.get_or_404(id)
    status_novo = request.form.get('status')
    
    if status_novo == 'ativo':
        c.ativo = True
    elif status_novo == 'inativo':
        c.ativo = False
        # Remove da lista de espera se inativar
        ListaEspera.query.filter_by(cooperado_id=id).delete()
        
    db.session.commit()
    flash(f'Status do cooperado {c.nome} alterado para {"ATIVO" if c.ativo else "INATIVO"}.')
    return redirect(url_for('cooperados'))


# =================================
# ====== Gerenciar Clientes =======
# =================================

@app.route('/clientes')
def clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    
    search_term = request.args.get('search', '').strip()
    query = Cliente.query
    
    if search_term:
        like = f"%{search_term.lower()}%"
        query = query.filter(
            (func.lower(Cliente.nome).like(like)) |
            (func.lower(Cliente.telefone).like(like)) |
            (func.lower(Cliente.username).like(like))
        )
        
    clientes = query.order_by(Cliente.nome).all()
    return render_template('clientes.html', clientes=clientes, search_term=search_term)

@app.route('/clientes/editar/<int:id>', methods=['GET', 'POST'])
@master_required
def editar_cliente(id):
    c = Cliente.query.get_or_404(id)
    if request.method == 'POST':
        nome = (request.form['nome'] or '').strip()
        telefone = _norm_phone(request.form.get('telefone') or '')
        bairro_origem = (request.form.get('bairro_origem') or '').strip()
        endereco = (request.form.get('endereco') or '').strip()
        saldo_atual = float(request.form.get('saldo_atual', 0.0))
        username = (request.form.get('username') or '').strip()
        senha_nova = request.form.get('senha_nova')
        
        if not nome or not telefone:
            flash('Nome e telefone são obrigatórios.', 'error')
            return redirect(url_for('editar_cliente', id=id))

        # Verifica unicidade do username (ignorando o cliente atual)
        if username and Cliente.query.filter(func.lower(Cliente.username) == username.lower(), Cliente.id != id).first():
            flash('Nome de usuário já existe para outro cliente.', 'error')
            return redirect(url_for('editar_cliente', id=id))
            
        c.nome = nome
        c.telefone = telefone
        c.bairro_origem = bairro_origem
        c.endereco = endereco
        # O saldo é atualizado apenas manualmente aqui; movimentos devem ser feitos na tela de Crédito
        c.saldo_atual = saldo_atual 
        c.username = username or None
        
        if senha_nova:
            c.set_senha(senha_nova)
            
        db.session.commit()
        flash(f'Cliente {c.nome} atualizado com sucesso.')
        return redirect(url_for('clientes'))
        
    return render_template('editar_cliente.html', cliente=c)

@app.route('/clientes/excluir/<int:id>', methods=['POST'])
@master_required
def excluir_cliente(id):
    c = Cliente.query.get_or_404(id)
    
    # Verifica entregas e movimentos
    entregas_count = Entrega.query.filter_by(cliente_id=id).count()
    if entregas_count > 0:
        flash(f'Não é possível excluir o cliente {c.nome}: ele(a) possui {entregas_count} entregas vinculadas.', 'error')
        return redirect(url_for('clientes'))

    Credito.query.filter_by(cliente_id=id).delete()
    CreditoMovimento.query.filter_by(cliente_id=id).delete()
    
    db.session.delete(c)
    db.session.commit()
    flash(f'Cliente {c.nome} e todo o seu histórico de crédito excluído com sucesso.')
    return redirect(url_for('clientes'))


# =================================
# ====== SETUP BD E RUN ===========
# =================================

def criar_bd():
    with app.app_context():
        # Cria as tabelas
        db.create_all()

        # Cria índices para otimização
        idx_cmds = [
            "CREATE INDEX IF NOT EXISTS idx_entrega_cliente_id ON entrega (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cliente ON entrega (cliente)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cooperado ON entrega (cooperado_id)",
            "CREATE INDEX IF NOT EXISTS idx_cliente_telefone ON cliente (telefone)",
            "CREATE INDEX IF NOT EXISTS idx_cliente_username ON cliente (username)",
            "CREATE INDEX IF NOT EXISTS idx_credito_cliente ON credito (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_cliente ON credito_movimento (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_data ON credito_movimento (criado_em DESC)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_tipo ON credito_movimento (tipo)",
        ]
        for s in idx_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        # ---------- Backfill simples: entrega.cliente_id a partir do nome (igual exato) ----------
        try:
            # Tenta preencher cliente_id em entregas sem ele
            pend = (Entrega.query
                    .filter((Entrega.cliente_id == None) | (Entrega.cliente_id.is_(None)))
                    .limit(5000).all())
            if pend:
                nomes = {(e.cliente or '').strip().lower() for e in pend if (e.cliente or '').strip()}
                if nomes:
                    # Cria um mapa de nome normalizado do cliente -> ID
                    mapa = {
                        c.nome.strip().lower(): c.id
                        for c in Cliente.query.filter(func.lower(Cliente.nome).in_(list(nomes))).all()
                        if (c.nome or '').strip()
                    }
                    mudou = 0
                    for e in pend:
                        cid = mapa.get((e.cliente or '').strip().lower())
                        if cid:
                            e.cliente_id = cid
                            mudou += 1
                    if mudou:
                        db.session.commit()
        except Exception:
            db.session.rollback()

        db.session.commit()


if __name__ == '__main__':
    criar_bd()
    app.run(debug=True)
