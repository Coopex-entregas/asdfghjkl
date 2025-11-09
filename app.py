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
from sqlalchemy.exc import IntegrityError
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

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Dados gerais
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(30), nullable=True)
    bairro_origem = db.Column(db.String(50), nullable=True)
    endereco = db.Column(db.String(255), nullable=True)
    saldo_atual = db.Column(db.Float, nullable=False, default=0.0)
    # Login do cliente
    username = db.Column(db.String(80), unique=True, index=True)
    senha_hash = db.Column(db.String(128), nullable=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        if not self.senha_hash:
            return False
        return check_password_hash(self.senha_hash, senha)


class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
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

    # Controle de crédito usado nesta entrega
    credito_usado = db.Column(db.Float, nullable=False, default=0.0)
    credito_mov_id = db.Column(db.Integer, nullable=True)

    # Link para cliente "cadastrado"
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)


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

    credito_id = db.Column(db.Integer, db.ForeignKey("credito.id"), nullable=True)
    entrega_id = db.Column(db.Integer, db.ForeignKey("entrega.id"), nullable=True)


class ListaEspera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)  # legado
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
    return (inicio_brasil.astimezone(pytz.utc).replace(tzinfo=None),
            fim_brasil.astimezone(pytz.utc).replace(tzinfo=None))


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


def pagamento_usa_credito(pagamento: str) -> bool:
    """
    True se a forma de pagamento usar crédito.
    Aceita, por ex:
      - "Crédito"
      - "Credito"
      - "Crédito automático"
      - "Crédito + Pix", etc.
    """
    txt = _strip_accents((pagamento or '').strip().lower())
    txt = re.sub(r'\s+', ' ', txt)
    return txt.startswith('credito')


# ====== CRÉDITO: helpers e regras ======
def _as_decimal(x) -> Decimal:
    if x is None:
        return Decimal("0.00")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x)).quantize(Decimal("0.01"))


def calcular_valor_final(valor_bruto, desconto_tipo, desconto_valor) -> Decimal:
    bruto = _as_decimal(valor_bruto)
    d = _as_decimal(desconto_valor)
    if desconto_tipo == "percentual":
        desc = (bruto * d) / Decimal("100")
    elif desconto_tipo == "real":
        desc = d
    else:
        desc = Decimal("0.00")
    if desc > bruto:
        desc = bruto
    return (bruto - desc).quantize(Decimal("0.01"))


def calc_valor_final(valor, desconto_tipo, desconto_valor):
    return float(calcular_valor_final(valor, desconto_tipo, desconto_valor))


def _find_cliente_by_nome(nome: str):
    if not nome:
        return None
    cli = Cliente.query.filter(func.lower(Cliente.nome) == (nome or '').lower()).first()
    if cli:
        return cli

    target = normalize_letters_key(nome or '')
    for c in Cliente.query.all():
        if normalize_letters_key(c.nome or '') == target:
            return c

    tok = normalize_first_token(nome or '')
    for c in Cliente.query.all():
        if normalize_first_token(c.nome or '') == tok:
            return c
    return None


def consumo_total_do_credito(credito_id: int) -> float:
    """
    Soma quanto já foi CONSUMIDO (tipo='debito') vinculado a este crédito.
    No fluxo normal os débitos automáticos não apontam para credito_id,
    então normalmente será 0 a não ser que tenha ajuste manual ligado ao crédito.
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


# Constantes "semânticas" (para movimentos manuais)
TIPO_ENTRADA = 'ENTRADA'
TIPO_CONSUMO = 'CONSUMO'
TIPO_AJUSTE = 'AJUSTE'


def atualizar_saldo_cliente(cliente_id, delta):
    cli = Cliente.query.get(cliente_id)
    if not cli:
        return
    cli.saldo_atual = float(_as_decimal(cli.saldo_atual) + _as_decimal(delta))
    db.session.add(cli)


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


def br_date_ymd(dt_utc_naive: datetime) -> str:
    if not dt_utc_naive:
        return ''
    return to_brasilia(dt_utc_naive).date().isoformat()


# ====== feriados ======
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
    if next_url:
        return redirect(next_url)
    from_ref = _build_admin_url_from_referrer()
    if from_ref:
        return redirect(from_ref)
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
    # Admin / Cooperado
    if request.method == 'POST':
        usuario = (request.form.get('usuario') or '').strip()
        senha = request.form.get('senha') or ''
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
    if s is None:
        return ""
    digits = re.sub(r'\D+', '', str(s))
    if digits.startswith('55'):
        digits = digits[2:]
    if len(digits) > 11:
        digits = digits[-11:]
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
    for k in ['cliente_id', 'cliente_username', 'cliente_nome', 'is_cliente']:
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
        cliente=e.cliente, bairro=e.bairro, valor=e.valor,
        data_envio=datetime.utcnow(),
        data_atribuida=None, cooperado_id=None,
        status='pendente', status_pagamento='pendente',
        pagamento=e.pagamento, recebido_por=None
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
    status_pgto = (request.args.get('status_pgto') or 'todas').lower()

    query = Entrega.query.filter(Entrega.cooperado_id == user_id)

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

    if status_pgto == 'pago':
        query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
    elif status_pgto == 'pendente':
        query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    entregas = query.options(joinedload(Entrega.cooperado)).order_by(Entrega.data_envio.desc()).all()

    total_geral = sum(float(e.valor or 0) for e in entregas)
    total_pago = sum(float(e.valor or 0) for e in entregas if (e.status_pagamento or '').lower() == 'pago')
    total_pendente = max(0.0, total_geral - total_pago)

    return render_template('painel_cooperado.html',
                           entregas=entregas,
                           total_geral=total_geral,
                           total_pago=total_pago,
                           total_pendente=total_pendente,
                           request=request,
                           to_brasilia=to_brasilia,
                           status_pgto=status_pgto)


# ====== COOPERADOS (CRUD) ======
@app.route('/cooperados/cadastrar', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form.get('nome')
        senha = request.form.get('senha')
        if nome and senha:
            if Cooperado.query.filter_by(nome=nome).first():
                flash('Já existe um cooperado com esse nome!')
            else:
                novo = Cooperado(nome=nome)
                novo.set_senha(senha)
                db.session.add(novo)
                db.session.commit()
                flash('Cooperado cadastrado com sucesso!')
        else:
            flash('Preencha todos os campos.')
        return redirect(url_for('cadastrar_cooperado'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    return render_template('cadastrar_cooperado.html', cooperados=cooperados)


@app.route('/cooperados/<int:coop_id>/atualizar', methods=['POST'])
def atualizar_cooperado(coop_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(coop_id)
    novo_nome = request.form.get('novo_nome')
    nova_senha = request.form.get('nova_senha')
    if novo_nome and novo_nome != cooperado.nome:
        existe = Cooperado.query.filter_by(nome=novo_nome).first()
        if existe and existe.id != cooperado.id:
            flash('Já existe um cooperado com esse nome!')
            return redirect(url_for('cadastrar_cooperado'))
        cooperado.nome = novo_nome
    if nova_senha:
        cooperado.set_senha(nova_senha)
    db.session.commit()
    flash('Dados do cooperado atualizados!')
    return redirect(url_for('cadastrar_cooperado'))


@app.route('/cooperados/<int:coop_id>/excluir', methods=['POST'])
def excluir_cooperado(coop_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(coop_id)
    db.session.delete(cooperado)
    db.session.commit()
    flash('Cooperado excluído com sucesso!')
    return redirect(url_for('cadastrar_cooperado'))


@app.route('/cooperados/<int:coop_id>/status', methods=['POST'])
def mudar_status_cooperado(coop_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    novo_status = request.form.get('novo_status')
    cooperado = Cooperado.query.get_or_404(coop_id)
    cooperado.ativo = (novo_status == "1")
    db.session.commit()
    flash(f"Status de {cooperado.nome} alterado para {'Ativo' if cooperado.ativo else 'Inativo'}!")
    return redirect(url_for('cadastrar_cooperado'))


# ====== CLIENTES (CRUD) ======
@app.route('/clientes', methods=['GET', 'POST'])
def clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        telefone = _norm_phone(request.form.get('telefone') or '')
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

    # Métricas
    aggs = (
        db.session.query(
            Entrega.cliente.label('cli'),
            func.count(Entrega.id).label('qtd'),
            func.max(Entrega.data_envio).label('ultimo')
        ).group_by(Entrega.cliente).all()
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
        k_full = normalize_letters_key(cl.nome or '')
        k_first = normalize_first_token(cl.nome or '')

        tot, dt = 0, None
        if k_full in stats_by_full:
            tot = stats_by_full[k_full]["qtd"]
            dt = stats_by_full[k_full]["ultimo"]
        elif k_first in stats_by_first:
            tot = stats_by_first[k_first]["qtd"]
            dt = stats_by_first[k_first]["ultimo"]

        ultimo_ymd, ultimo_br, ultimo_days, row_class = None, None, None, ""
        if dt:
            loc_date = to_brasilia(dt).date()
            ultimo_ymd = loc_date.isoformat()
            ultimo_br = loc_date.strftime('%d/%m/%Y')
            ultimo_days = (hoje_local - loc_date).days
            if ultimo_days > 60:
                row_class = "st-gt60"
            elif ultimo_days > 30:
                row_class = "st-gt30"
            else:
                row_class = "st-lt30"

        lista.append({
            "id": cl.id, "nome": cl.nome, "telefone": cl.telefone,
            "bairro_origem": cl.bairro_origem, "endereco": getattr(cl, "endereco", None),
            "total_pedidos": int(tot or 0),
            "ultimo_ymd": ultimo_ymd, "ultimo_br": ultimo_br, "ultimo_days": ultimo_days,
            "row_class": row_class
        })

    total_clientes = len(lista)
    ativos = sum(1 for i in lista if i["ultimo_days"] is not None and i["ultimo_days"] <= 180)
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
    telefone = _norm_phone(request.form.get('telefone') or '')
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


# ====== ENTREGAS (CADASTRAR / AGENDAR / EDITAR) ======
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    clientes_lista = Cliente.query.order_by(Cliente.nome).all()

    if request.method == 'POST':
        cliente_nome = (request.form.get('cliente') or '').strip()
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor') or 0)
        cooperado_id = request.form.get('cooperado_id')
        pagamento = (request.form.get('pagamento') or '').strip()

        # tenta linkar com cliente_id (hidden) ou pelo nome
        cliente_id_form = request.form.get('cliente_id', type=int)
        cli = None
        if cliente_id_form:
            cli = Cliente.query.get(cliente_id_form)
        if not cli and cliente_nome:
            cli = _find_cliente_by_nome(cliente_nome)

        entrega = Entrega(
            cliente=cliente_nome,
            bairro=bairro,
            valor=valor,
            data_envio=datetime.utcnow(),
            status_pagamento='pendente',
            status='pendente',
            pagamento=pagamento
        )
        if cli:
            entrega.cliente_id = cli.id

        if cooperado_id:
            entrega.cooperado_id = int(cooperado_id)
            entrega.data_atribuida = datetime.utcnow()

        db.session.add(entrega)

        # Se atribuiu cooperado, remove da fila
        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()  # garante entrega.id

        # Consumo automático de crédito do cliente
        try:
            consumir_credito_em_entrega(entrega.id)
        except Exception as ex:
            current_app.logger.exception("Falha ao consumir crédito na entrega %s: %s", entrega.id, ex)

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
        cliente_nome = (request.form.get('cliente') or '').strip()
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor') or 0)
        data_str = request.form.get('data')  # 'YYYY-MM-DDTHH:MM'
        status_entrega = request.form.get('status_entrega')
        status_pagamento = request.form.get('status_pagamento')
        cooperado_id = request.form.get('cooperado_id')
        pagamento = (request.form.get('pagamento') or '').strip()

        data_envio = parse_local_datetime_to_utc_naive(data_str)

        # tenta linkar com cliente
        cliente_id_form = request.form.get('cliente_id', type=int)
        cli = None
        if cliente_id_form:
            cli = Cliente.query.get(cliente_id_form)
        if not cli and cliente_nome:
            cli = _find_cliente_by_nome(cliente_nome)

        entrega = Entrega(
            cliente=cliente_nome,
            bairro=bairro,
            valor=valor,
            data_envio=data_envio,
            cooperado_id=int(cooperado_id) if cooperado_id else None,
            status=(status_entrega or 'pendente'),
            status_pagamento=(status_pagamento or 'pendente').lower(),
            pagamento=pagamento
        )

        if cli:
            entrega.cliente_id = cli.id

        db.session.add(entrega)

        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()

        # Consumo automático de crédito do cliente (agendada)
        try:
            consumir_credito_em_entrega(entrega.id)
        except Exception as ex:
            current_app.logger.exception("Falha ao consumir crédito (agendada) na entrega %s: %s", entrega.id, ex)

        flash('Entrega agendada!')
        return redirect_back_to_admin()

    return render_template('agendar_entrega.html', cooperados=cooperados, clientes=clientes_lista)


@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    is_admin = session.get('is_admin')

    # Cooperado só pode mexer nas próprias entregas
    if not is_admin and entrega.cooperado_id != session['user_id']:
        flash("Acesso não permitido.")
        return redirect(url_for('painel_cooperado'))

    if request.method == 'POST':
        if is_admin:
            # ===== ADMIN EDITA A ENTREGA =====
            novo_cliente_nome = (request.form.get('cliente') or '').strip()
            entrega.cliente = novo_cliente_nome
            entrega.bairro = request.form.get('bairro')

            try:
                entrega.valor = float(request.form.get('valor') or entrega.valor or 0)
            except Exception:
                entrega.valor = 0.0

            # Cliente
            cliente_id_form = request.form.get('cliente_id', type=int)
            cli = None
            if cliente_id_form:
                cli = Cliente.query.get(cliente_id_form)
            if not cli and novo_cliente_nome:
                cli = _find_cliente_by_nome(novo_cliente_nome)
            entrega.cliente_id = cli.id if cli else None

            # Cooperado
            novo_coop_id = request.form.get('cooperado_id')
            if novo_coop_id:
                novo_coop_id = int(novo_coop_id)
                if entrega.cooperado_id != novo_coop_id:
                    entrega.cooperado_id = novo_coop_id
                    entrega.data_atribuida = datetime.utcnow()
                    ListaEspera.query.filter_by(cooperado_id=novo_coop_id).delete()
            else:
                entrega.cooperado_id = None

            # Status / pagamento / recebido_por
            entrega.status_pagamento = (
                request.form.get('status_pagamento')
                or entrega.status_pagamento
                or 'pendente'
            ).lower()
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            entrega.pagamento = (request.form.get('pagamento') or entrega.pagamento or '').strip()

            db.session.commit()

            # ===== REGRAS DE CRÉDITO =====
            try:
                if pagamento_usa_credito(entrega.pagamento):
                    # Usa crédito: estorna tudo e consome de novo com o novo valor
                    desfazer_consumo_credito_da_entrega(entrega.id)
                    consumir_credito_em_entrega(entrega.id)
                else:
                    # Pagamento NÃO é crédito → estorna se tiver algo consumido
                    if (entrega.credito_usado or 0) > 0:
                        desfazer_consumo_credito_da_entrega(entrega.id)
            except Exception as ex:
                current_app.logger.exception("Falha ao recalcular crédito na entrega %s: %s", entrega.id, ex)

            flash('Entrega atualizada!')
            return redirect_back_to_admin()

        else:
            # ===== COOPERADO EDITA APENAS STATUS/RECEBIDO_POR =====
            entrega.status_pagamento = (
                request.form.get('status_pagamento')
                or entrega.status_pagamento
                or 'pendente'
            ).lower()
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('painel_cooperado'))

    # GET
    if is_admin:
        return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)
    else:
        return render_template('editar_entrega_cooperado.html', entrega=entrega)


@app.post('/atribuir_cooperado/<int:id>')
def atribuir_cooperado(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    entrega = Entrega.query.get_or_404(id)
    coop_id = (request.form.get('cooperado_id') or '').strip()

    try:
        if coop_id:
            coop = Cooperado.query.get_or_404(int(coop_id))
            entrega.cooperado_id = coop.id
            entrega.data_atribuida = datetime.utcnow()
            ListaEspera.query.filter_by(cooperado_id=coop.id).delete()
        else:
            entrega.cooperado_id = None

        db.session.commit()

        # Se forma de pagamento usa crédito, tenta consumir (ou recalcular)
        try:
            if pagamento_usa_credito(entrega.pagamento):
                consumir_credito_em_entrega(entrega.id)
        except Exception as ex:
            current_app.logger.exception("Falha ao consumir crédito ao atribuir cooperado %s: %s", entrega.id, ex)

        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=True, cooperado_id=entrega.cooperado_id), 200

        flash('Cooperado atualizado na entrega.')
        return redirect_back_to_admin()

    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error=str(e)), 400
        flash('Não foi possível atualizar o cooperado.')
        return redirect_back_to_admin()


@app.route('/excluir_entrega/<int:id>', methods=['POST'])
def excluir_entrega(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    entrega = Entrega.query.get_or_404(id)

    # 1) Estorna eventual crédito
    try:
        desfazer_consumo_credito_da_entrega(entrega.id)
    except Exception as ex:
        current_app.logger.exception("Falha ao estornar crédito da entrega %s: %s", entrega.id, ex)

    try:
        # 2) Remove vínculos com movimentos
        db.session.execute(
            text("UPDATE credito_movimento SET entrega_id = NULL WHERE entrega_id = :eid"),
            {"eid": id}
        )
        db.session.execute(
            text("UPDATE entrega SET credito_mov_id = NULL WHERE id = :eid"),
            {"eid": id}
        )

        # 3) Exclui a entrega
        db.session.delete(entrega)
        db.session.commit()
        flash('Entrega excluída com sucesso.', 'success')

    except IntegrityError:
        db.session.rollback()
        flash('Não foi possível excluir: há vínculos de crédito ativos.', 'danger')
        current_app.logger.exception("IntegrityError ao excluir entrega %s", id)

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir entrega: {e.__class__.__name__}', 'danger')
        current_app.logger.exception("Erro ao excluir entrega %s", id)

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


# ====== CRÉDITO: função registrar_credito (entrada principal) ======
def registrar_credito(cliente_id: int, valor_bruto, desconto_tipo: str,
                      desconto_valor, motivo: str = "", criado_por: str = ""):
    cli = Cliente.query.get(cliente_id)
    if not cli:
        raise ValueError("Cliente não encontrado")

    valor_final = calcular_valor_final(valor_bruto, desconto_tipo, desconto_valor)
    saldo_antes = _as_decimal(cli.saldo_atual)
    cli.saldo_atual = float(saldo_antes + valor_final)

    c = Credito(
        cliente_id=cli.id,
        valor_bruto=float(_as_decimal(valor_bruto)),
        desconto_tipo=desconto_tipo or "nenhum",
        desconto_valor=float(_as_decimal(desconto_valor or 0)),
        valor_final=float(valor_final),
        motivo=motivo or "",
        saldo_antes=float(saldo_antes),
        saldo_depois=float(_as_decimal(cli.saldo_atual)),
        criado_por=criado_por or "Supervisor"
    )
    db.session.add(c)
    db.session.flush()

    mov = CreditoMovimento(
        cliente_id=cli.id,
        tipo="credito",
        valor=float(valor_final),
        referencia=f"Crédito #{c.id}",
        credito_id=c.id
    )
    db.session.add(mov)
    db.session.commit()
    return c


# ====== CRÉDITOS (Supervisor) ======
@app.route('/creditos')
def creditos():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cliente_id = request.args.get('cliente_id', type=int)

    q = Credito.query
    if cliente_id:
        q = q.filter(Credito.cliente_id == cliente_id)

    lista = q.order_by(Credito.id.desc()).limit(500).all()
    clientes = Cliente.query.order_by(Cliente.nome).all()

    return render_template(
        'creditos.html',
        creditos=lista,
        cliente_id=cliente_id,
        clientes=clientes,
        request=request
    )


@app.route('/creditos/novo', methods=['GET', 'POST'])
def creditos_novo():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id', type=int)
        valor_bruto = request.form.get('valor', type=float)
        desconto_tipo = request.form.get('desconto_tipo', default='nenhum')
        desconto_valor = request.form.get('desconto_valor', type=float, default=0.0)
        motivo = request.form.get('motivo', default='')
        criado_por = session.get('user_nome', 'Supervisor')

        try:
            registrar_credito(cliente_id, valor_bruto, desconto_tipo, desconto_valor, motivo, criado_por)
            flash('Crédito criado com sucesso.', 'success')
            return redirect(url_for('creditos', cliente_id=cliente_id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Erro ao criar crédito')
            flash(f'Erro ao criar crédito: {e.__class__.__name__}', 'danger')

    return render_template('credito_form.html')


@app.route('/creditos/<int:credito_id>/editar', methods=['GET', 'POST'])
def creditos_editar(credito_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cred = Credito.query.get_or_404(credito_id)

    if request.method == 'POST':
        old_final = float(cred.valor_final or 0.0)

        novo_valor_bruto = request.form.get('valor', type=float, default=cred.valor_bruto)
        cred.valor_bruto = novo_valor_bruto
        cred.desconto_tipo = request.form.get('desconto_tipo', default=cred.desconto_tipo or 'nenhum')
        cred.desconto_valor = request.form.get('desconto_valor', type=float, default=cred.desconto_valor or 0.0)
        cred.motivo = request.form.get('motivo', default=cred.motivo or '')

        new_final = float(calcular_valor_final(cred.valor_bruto, cred.desconto_tipo, cred.desconto_valor))
        cred.valor_final = new_final
        delta = new_final - old_final

        try:
            if abs(delta) > 1e-7:
                atualizar_saldo_cliente(cred.cliente_id, delta)
                ref = f'Ajuste do crédito #{cred.id}'
                registrar_movimento(
                    cred.cliente_id,
                    (TIPO_ENTRADA if delta > 0 else TIPO_CONSUMO),
                    abs(delta),
                    referencia=ref,
                    credito_id=cred.id
                )
            db.session.commit()
            flash('Crédito atualizado.', 'success')
            return redirect(url_for('creditos', cliente_id=cred.cliente_id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Erro ao editar crédito')
            flash(f'Erro ao editar crédito: {e.__class__.__name__}', 'danger')

    return render_template('credito_form.html', credito=cred)


@app.route('/creditos/<int:credito_id>/excluir', methods=['POST'])
def creditos_excluir(credito_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cred = Credito.query.get_or_404(credito_id)

    total_consumo = consumo_total_do_credito(cred.id)
    if total_consumo > 0.0:
        flash('Não é possível excluir: este crédito possui consumo vinculado. Estorne/exclua os consumos primeiro.', 'warning')
        return redirect(url_for('creditos', cliente_id=cred.cliente_id))

    try:
        delta = -float(cred.valor_final or 0.0)
        if abs(delta) > 1e-7:
            atualizar_saldo_cliente(cred.cliente_id, delta)
            registrar_movimento(
                cred.cliente_id,
                TIPO_CONSUMO,
                abs(delta),
                referencia=f'Exclusão do crédito #{cred.id}',
                credito_id=cred.id
            )

        db.session.execute(text("DELETE FROM credito_movimento WHERE credito_id = :cid"), {"cid": cred.id})

        db.session.delete(cred)
        db.session.commit()
        flash('Crédito excluído.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Erro ao excluir crédito')
        flash(f'Erro ao excluir crédito: {e.__class__.__name__}', 'danger')

    return redirect(url_for('creditos', cliente_id=cred.cliente_id))


@app.route('/creditos/exportar')
def creditos_exportar():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cliente_id = request.args.get('cliente_id', type=int)
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    q = (db.session.query(
        Credito.id.label('id'),
        Cliente.nome.label('cliente'),
        Credito.valor_bruto.label('valor_bruto'),
        Credito.desconto_tipo.label('desconto_tipo'),
        Credito.desconto_valor.label('desconto_valor'),
        Credito.valor_final.label('valor_final'),
        Credito.motivo.label('motivo'),
        Credito.saldo_antes.label('saldo_antes'),
        Credito.saldo_depois.label('saldo_depois'),
        Credito.criado_por.label('criado_por'),
        Credito.criado_em.label('criado_em'),
    ).join(Cliente, Cliente.id == Credito.cliente_id))

    if cliente_id:
        q = q.filter(Credito.cliente_id == cliente_id)
    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d")
        q = q.filter(Credito.criado_em >= di)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
        q = q.filter(Credito.criado_em <= df)

    q = q.order_by(Credito.criado_em.asc())

    rows = []
    for r in q.all():
        dt_local = to_brasilia(r.criado_em)
        rows.append({
            'Data': dt_local.strftime('%d/%m/%Y %H:%M') if dt_local else '',
            'Cliente': r.cliente,
            'Valor Bruto': float(r.valor_bruto or 0),
            'Desconto Tipo': r.desconto_tipo or 'nenhum',
            'Desconto Valor': float(r.desconto_valor or 0),
            'Valor Final': float(r.valor_final or 0),
            'Motivo': r.motivo or '',
            'Saldo Antes': float(r.saldo_antes or 0),
            'Saldo Depois': float(r.saldo_depois or 0),
            'Criado Por': r.criado_por or '',
            'ID Crédito': int(r.id),
        })

    df_out = pd.DataFrame(rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Créditos'
        df_out.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]

        widths = [20, 28, 14, 16, 16, 14, 30, 14, 14, 16, 12]
        for i, w in enumerate(widths[:len(df_out.columns)]):
            ws.set_column(i, i, w)

        money_fmt = writer.book.add_format({'num_format': '#,##0.00'})
        for col_name in ['Valor Bruto', 'Desconto Valor', 'Valor Final', 'Saldo Antes', 'Saldo Depois']:
            if col_name in df_out.columns:
                idx = list(df_out.columns).index(col_name)
                ws.set_column(idx, idx, None, money_fmt)

    output.seek(0)
    return send_file(output, download_name='creditos.xlsx', as_attachment=True)


@app.route('/creditos/cadastrar', methods=['POST'])
def creditos_cadastrar():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cliente_id = int(request.form['cliente_id'])
    valor_bruto = request.form['valor_bruto']
    desconto_tipo = request.form.get('desconto_tipo', 'nenhum')
    desconto_valor = request.form.get('desconto_valor', 0)
    motivo = request.form.get('motivo', '')
    criado_por = session.get('user_nome', 'Supervisor')

    registrar_credito(cliente_id, valor_bruto, desconto_tipo, desconto_valor, motivo, criado_por)
    flash('Crédito registrado com sucesso!')
    return redirect(url_for('creditos'))


# ====== VISÃO DO CLIENTE (ADMIN vê um cliente específico) ======
@app.route('/cliente/<int:cliente_id>/credito')
def cliente_credito(cliente_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cli = Cliente.query.get_or_404(cliente_id)
    movs = (CreditoMovimento.query
            .filter(CreditoMovimento.cliente_id == cliente_id)
            .order_by(CreditoMovimento.criado_em.desc()).all())

    movimentos = []
    for m in movs:
        movimentos.append({
            "tipo": m.tipo,
            "titulo": "Crédito" if m.tipo == "credito" else "Atribuição/Débito",
            "descricao": m.referencia or "",
            "valor": float(m.valor or 0),
            "data": m.criado_em,
            "referencia": m.referencia or ""
        })

    return render_template('credito_cliente.html',
                           cliente=cli,
                           movimentos=movimentos,
                           to_brasilia=to_brasilia,
                           now=lambda: datetime.now(BRAZIL_TZ))


# ====== MOVIMENTO MANUAL DE CRÉDITO ======
@app.route('/creditos/movimento/novo', methods=['POST'])
def credmov_novo():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cliente_id = request.form.get('cliente_id', type=int)
    credito_id = request.form.get('credito_id', type=int)
    entrega_id = request.form.get('entrega_id', type=int)
    tipo = (request.form.get('tipo', default=TIPO_AJUSTE) or TIPO_AJUSTE).upper()
    valor = abs(request.form.get('valor', type=float, default=0.0))
    referencia = request.form.get('referencia', default='')

    try:
        # efeito no saldo (ENTRADA / CONSUMO / AJUSTE)
        if tipo == TIPO_ENTRADA:
            atualizar_saldo_cliente(cliente_id, +valor)
        elif tipo == TIPO_CONSUMO:
            atualizar_saldo_cliente(cliente_id, -valor)
        elif tipo == TIPO_AJUSTE:
            atualizar_saldo_cliente(cliente_id, +valor)

        registrar_movimento(cliente_id, tipo, valor,
                            referencia=referencia,
                            credito_id=credito_id,
                            entrega_id=entrega_id)
        db.session.commit()
        flash('Movimento registrado.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Erro ao criar movimento')
        flash(f'Erro ao criar movimento: {e.__class__.__name__}', 'danger')

    return redirect(url_for('creditos', cliente_id=cliente_id))


@app.route('/creditos/movimento/<int:mov_id>/editar', methods=['GET', 'POST'])
def credmov_editar(mov_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    mov = CreditoMovimento.query.get_or_404(mov_id)

    if request.method == 'POST':
        # tipo vindo do form (ENTRADA / CONSUMO / AJUSTE)
        novo_tipo = (request.form.get('tipo') or '').upper().strip()
        if novo_tipo not in (TIPO_ENTRADA, TIPO_CONSUMO, TIPO_AJUSTE):
            # se não vier nada, mapeia a partir do tipo atual ('credito'/'debito')
            if mov.tipo == 'credito':
                novo_tipo = TIPO_ENTRADA
            else:
                novo_tipo = TIPO_CONSUMO

        novo_valor = abs(request.form.get('valor', type=float, default=mov.valor))
        nova_ref = request.form.get('referencia', default=mov.referencia)

        try:
            # Reverte efeito antigo do saldo (mov.tipo é 'credito' ou 'debito')
            if mov.tipo == 'credito':
                atualizar_saldo_cliente(mov.cliente_id, -float(mov.valor or 0))
            elif mov.tipo == 'debito':
                atualizar_saldo_cliente(mov.cliente_id, +float(mov.valor or 0))

            # Aplica efeito novo no saldo conforme novo_tipo
            if novo_tipo in (TIPO_ENTRADA, TIPO_AJUSTE):
                atualizar_saldo_cliente(mov.cliente_id, +float(novo_valor or 0))
                novo_tipo_salvo = 'credito'
            elif novo_tipo == TIPO_CONSUMO:
                atualizar_saldo_cliente(mov.cliente_id, -float(novo_valor or 0))
                novo_tipo_salvo = 'debito'
            else:
                novo_tipo_salvo = mov.tipo  # fallback

            mov.tipo = novo_tipo_salvo
            mov.valor = novo_valor
            mov.referencia = (nova_ref or '')[:120]

            db.session.add(mov)
            db.session.commit()
            flash('Movimento atualizado.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Erro ao editar movimento')
            flash(f'Erro ao editar movimento: {e.__class__.__name__}', 'danger')

        return redirect(url_for('creditos', cliente_id=mov.cliente_id))

    return render_template('credmov_form.html', movimento=mov)


@app.route('/creditos/movimento/<int:mov_id>/excluir', methods=['POST'])
def credmov_excluir(mov_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    mov = CreditoMovimento.query.get_or_404(mov_id)
    try:
        # Reverte efeito no saldo (mov.tipo é 'credito' ou 'debito')
        if mov.tipo == 'credito':
            atualizar_saldo_cliente(mov.cliente_id, -float(mov.valor or 0))
        elif mov.tipo == 'debito':
            atualizar_saldo_cliente(mov.cliente_id, +float(mov.valor or 0))

        # Desacopla entrega, se tinha
        if mov.entrega_id:
            try:
                db.session.execute(
                    text("UPDATE entrega SET credito_mov_id = NULL WHERE id = :eid"),
                    {"eid": mov.entrega_id}
                )
            except Exception:
                pass

        db.session.delete(mov)
        db.session.commit()
        flash('Movimento excluído.', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('Não foi possível excluir o movimento (vínculos).', 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Erro ao excluir movimento')
        flash(f'Erro ao excluir movimento: {e.__class__.__name__}', 'danger')

    return redirect(url_for('creditos', cliente_id=mov.cliente_id))


# ========= JSON do PAINEL DO COOPERADO =========
@app.post('/cooperado/toggle_pagamento/<int:id>')
def toggle_pagamento(id):
    e = Entrega.query.get_or_404(id)
    _assert_entrega_do_cooperado(e)
    atual = (e.status_pagamento or 'pendente').lower()
    novo = 'pago' if atual != 'pago' else 'pendente'
    e.status_pagamento = novo
    db.session.commit()
    return jsonify(ok=True, status_pagamento=novo)


@app.post('/cooperado/marcar_entregue/<int:id>')
def cooperado_marcar_entregue(id):
    e = Entrega.query.get_or_404(id)
    _assert_entrega_do_cooperado(e)
    payload = request.get_json(silent=True) or {}
    recebido_por = (payload.get('recebido_por') or '').strip()
    if not recebido_por:
        return jsonify(ok=False, error='Campo "recebido_por" é obrigatório.'), 400
    e.status = 'recebido'
    e.recebido_por = recebido_por
    db.session.commit()
    return jsonify(ok=True)


# ====== ESTATÍSTICAS (ADMIN MASTER) ======
@app.route('/estatisticas_cooperado')
@master_required
def estatisticas_cooperado():
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

    entregas = query.options(joinedload(Entrega.cooperado)).order_by(Entrega.data_envio.asc()).all()

    total = len(entregas)
    pagas = len([e for e in entregas if (e.status_pagamento or '').lower() == 'pago'])
    pendentes = total - pagas
    total_valor = sum(float(e.valor or 0) for e in entregas)
    ticket_medio = (total_valor / total) if total > 0 else 0.0

    cont_dias = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_dias[dt_local.date()] += 1
    dia_top = {"data": None, "qtd": 0, "nome": "-"}
    if cont_dias:
        d, qtd = cont_dias.most_common(1)[0]
        dia_top = {"data": d.strftime('%Y-%m-%d'), "qtd": qtd, "nome": f"{d.strftime('%d/%m/%Y')} ({qtd})"}

    cont_horas = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_horas[dt_local.strftime('%H:00')] += 1
    hora_pico = cont_horas.most_common(1)[0][0] if cont_horas else "-"
    horas_pico_top3 = [f"{h} ({q})" for h, q in cont_horas.most_common(3)]

    cont_pgto = Counter([e.pagamento for e in entregas if e.pagamento])
    pgto_top = cont_pgto.most_common(1)[0][0] if cont_pgto else "-"

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

    cont_bairros = Counter([e.bairro for e in entregas if e.bairro])
    ranking_bairros = [{"bairro": b, "qtd": q} for b, q in cont_bairros.most_common()]

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

    ranking_pgto = [{"forma": f, "qtd": q} for f, q in cont_pgto.most_common()]

    soma_por_cliente = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for e in entregas:
        if e.cliente:
            soma_por_cliente[e.cliente]["qtd"] += 1
            soma_por_cliente[e.cliente]["total"] += float(e.valor or 0)
    ranking_clientes = [
        {"cliente": c, "qtd": d["qtd"], "total": round(d["total"], 2)}
        for c, d in sorted(soma_por_cliente.items(), key=lambda kv: kv[1]["total"], reverse=True)
    ]

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

    # Séries anuais (2025+)
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


# ====== EXPORTAÇÃO (MASTER) ======
@app.route('/estatisticas_cooperado_exportar_xlsx')
@master_required
def estatisticas_cooperado_exportar_xlsx():
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


# ====== EXPORTAÇÃO detalhada de entregas ======
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


# ====== IMPORTAR / EXPORTAR CLIENTES ======
@app.route('/clientes/exportar')
def exportar_clientes():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

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

    if filename.endswith('.xlsx'):
        try:
            df_in = pd.read_excel(io.BytesIO(raw), engine='openpyxl', dtype=str)
        except Exception as e:
            load_errors.append(f"Pandas/openpyxl: {e}")

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

    cols_map = {str(c).lower().strip(): c for c in df_in.columns}

    def colget(*ops, opt=False):
        for k in ops:
            if k in cols_map:
                return cols_map[k]
        return None if opt else None

    col_id = colget('id')
    col_nome = colget('nome', 'name')
    col_tel = colget('telefone', 'phone', 'numero', 'número', 'mobile', 'celular')
    col_bairro = colget('bairro', 'bairro_origem')
    col_end = colget('endereco', 'endereço', 'address')

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
            tel = norm_phone(row.get(col_tel))
            bairro = str(row.get(col_bairro) or '').strip() if col_bairro else None
            ender = str(row.get(col_end) or '').strip() if col_end else None

            if not nome and not tel:
                continue

            if tel and len(tel) not in (10, 11):
                erros += 1
                detalhes.append(f"Linha {i + 2}: telefone inválido '{tel}' (esperado 10 ou 11 dígitos).")
                continue

            if rid:
                cl = Cliente.query.get(rid)
                if not cl:
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
                novo = Cliente(nome=nome, telefone=tel or None, bairro_origem=bairro or None, endereco=ender or None)
                db.session.add(novo)
                adicionados += 1

        except Exception as e:
            erros += 1
            detalhes.append(f"Linha {i + 2}: erro inesperado ({e}).")

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao salvar no banco durante importação")
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
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperado_id = request.form.get('cooperado_id')
    nome_form = (request.form.get('nome') or '').strip()

    if not cooperado_id and not nome_form:
        flash('Selecione um cooperado ou informe um nome.')
        return redirect_back_to_admin()

    if cooperado_id:
        coop = Cooperado.query.get(int(cooperado_id))
        if not coop:
            flash('Cooperado inválido.')
            return redirect_back_to_admin()

                if ListaEspera.query.filter_by(cooperado_id=coop.id).first():
            flash('Este cooperado já está na fila de espera.')
            return redirect_back_to_admin()

        max_pos = db.session.query(func.max(ListaEspera.pos)).scalar() or 0
        novo = ListaEspera(
            cooperado_id=coop.id,
            nome=coop.nome,
            pos=max_pos + 1
        )
        db.session.add(novo)
        db.session.commit()
        flash('Cooperado adicionado à fila de espera.')
        return redirect_back_to_admin()

    # Caso não venha cooperado_id, usa só o nome (modo legado)
    max_pos = db.session.query(func.max(ListaEspera.pos)).scalar() or 0
    novo = ListaEspera(
        cooperado_id=None,
        nome=nome_form,
        pos=max_pos + 1
    )
    db.session.add(novo)
    db.session.commit()
    flash('Nome adicionado à fila de espera.')
    return redirect_back_to_admin()


@app.route('/lista_espera/<int:item_id>/remover', methods=['POST'])
def lista_espera_remover(item_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    item = ListaEspera.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()

    # Reorganiza posições para ficar 1,2,3...
    itens = ListaEspera.query.order_by(ListaEspera.pos.asc(), ListaEspera.created_at.asc()).all()
    for idx, it in enumerate(itens, start=1):
        it.pos = idx
    db.session.commit()

    flash('Removido da fila de espera.')
    return redirect_back_to_admin()


@app.route('/lista_espera/<int:item_id>/mover', methods=['POST'])
def lista_espera_mover(item_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    direction = (request.form.get('direction') or '').lower().strip()  # 'up' ou 'down'
    item = ListaEspera.query.get_or_404(item_id)

    if direction not in ('up', 'down'):
        flash('Direção inválida para mover na fila.', 'warning')
        return redirect_back_to_admin()

    if item.pos is None:
        # força reordenação antes de mexer
        itens = ListaEspera.query.order_by(ListaEspera.pos.asc(), ListaEspera.created_at.asc()).all()
        for idx, it in enumerate(itens, start=1):
            it.pos = idx
        db.session.commit()

    if direction == 'up':
        alvo = (ListaEspera.query
                .filter(ListaEspera.pos < item.pos)
                .order_by(ListaEspera.pos.desc())
                .first())
    else:  # down
        alvo = (ListaEspera.query
                .filter(ListaEspera.pos > item.pos)
                .order_by(ListaEspera.pos.asc())
                .first())

    if not alvo:
        # Já é o primeiro ou o último
        return redirect_back_to_admin()

    item.pos, alvo.pos = alvo.pos, item.pos
    db.session.commit()
    return redirect_back_to_admin()


# ====== HEALTHCHECK SIMPLES ======
@app.route('/health')
def health():
    return 'OK', 200


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    debug_flag = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_flag)

