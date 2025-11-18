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
        os.environ.get('ADMIN_PWD_COOPEX',        '84253700'):     {'is_master': False},
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


@app.route('/intruso')
def intruso():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'Desconhecido')
    username = request.args.get('u')

    agora_brasil = datetime.now(BRAZIL_TZ)
    acesso_data = agora_brasil.strftime('%d/%m/%Y %H:%M:%S')
    registro_id = agora_brasil.strftime('%Y%m%d%H%M%S')

    return render_template(
        'intruso.html',
        ip=ip,
        user_agent=user_agent,
        username=username,
        acesso_data=acesso_data,
        registro_id=registro_id
    )

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

    # NOVOS: controle de crédito usado nesta entrega
    credito_usado = db.Column(db.Float, nullable=False, default=0.0)
    credito_mov_id = db.Column(db.Integer, nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)  # << mantém só isso de novo


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

def _now_brt():
    try:
        return datetime.now(BRAZIL_TZ)  # se você já definiu BRAZIL_TZ
    except Exception:
        return datetime.now()

# Detecta se já existe ParametroSistema no projeto
ParametroSistemaCls = globals().get("ParametroSistema", None)

# Se não existir ParametroSistema, criamos uma KV simples
if ParametroSistemaCls is None:
    class ConfigKV(db.Model):
        __tablename__ = "config_kv"
        id = db.Column(db.Integer, primary_key=True)
        chave = db.Column(db.String(80), unique=True, nullable=False, index=True)
        valor = db.Column(db.String(255), nullable=True)

        def __repr__(self):
            return f"<ConfigKV {self.chave}={self.valor}>"

    def _get_param(chave: str, default=None):
        row = ConfigKV.query.filter_by(chave=chave).first()
        return row.valor if row and row.valor is not None else default

    def _set_param(chave: str, valor: str):
        row = ConfigKV.query.filter_by(chave=chave).first()
        if not row:
            row = ConfigKV(chave=chave, valor=valor)
            db.session.add(row)
        else:
            row.valor = valor
        db.session.commit()

else:
    # Usa o ParametroSistema do seu projeto
    def _get_param(chave: str, default=None):
        row = ParametroSistema.query.filter_by(chave=chave).first()
        return row.valor if row and row.valor is not None else default

    def _set_param(chave: str, valor: str):
        row = ParametroSistema.query.filter_by(chave=chave).first()
        if not row:
            row = ParametroSistema(chave=chave, valor=str(valor))
            db.session.add(row)
        else:
            row.valor = str(valor)
        db.session.commit()

def get_per_km():
    # ordem: DB -> ENV -> 3.00
    v = _get_param("per_km", None)
    if v is not None:
        try:
            return float(v)
        except Exception:
            pass
    try:
        return float(os.getenv("PER_KM", "3.00"))
    except Exception:
        return 3.00

def set_per_km(novo_valor: float):
    _set_param("per_km", f"{float(novo_valor):.2f}")
    return get_per_km()

# Modelo no banco para a tabela de preços
class PrecoRota(db.Model):
    __tablename__ = "preco_rota"
    id       = db.Column(db.Integer, primary_key=True)
    origem   = db.Column(db.String(120), nullable=False, index=True)
    destino  = db.Column(db.String(120), nullable=False, index=True)
    valor    = db.Column(db.Numeric(10,2), nullable=False, default=0)
    criado_em  = db.Column(db.DateTime, default=_now_brt)
    atualizado_em = db.Column(db.DateTime, default=_now_brt, onupdate=_now_brt)

    __table_args__ = (
        db.UniqueConstraint("origem","destino", name="uq_preco_rota_pair"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "origem": self.origem,
            "destino": self.destino,
            "valor": float(self.valor),
        }

# Garante criação das tabelas novas (não mexe nas existentes)
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        # não derruba a app se o create_all falhar em produção
        app.logger.warning(f"create_all falhou (ignorado): {e}")

# Normalizadores
def _norm(s: str) -> str:
    return (s or "").strip()

def _ci_equal(a: str, b: str) -> bool:
    return (_norm(a).casefold() == _norm(b).casefold())

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
    if request.method == 'POST':
        usuario  = (request.form.get('usuario') or '').strip()
        senha_raw = request.form.get('senha') or ''
        senha     = senha_raw.strip()   # <- tira espaço, quebra de linha, etc
        user_lc   = usuario.lower()

        # === ARMADILHA: login "secreto" pra jogar na tela de segurança ===
        # usuario = coopex / senha = 05062721
        if user_lc == 'coopex' and senha == '05062721':
            return redirect(url_for('intruso', u=usuario))

        # ===== Admin fixo (mantido) =====
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
                    pass  # cai no fallback lá embaixo

        # ===== Cooperado (mantido) =====
        cooperado = Cooperado.query.filter(func.lower(Cooperado.nome) == user_lc).first()
        if cooperado and cooperado.check_senha(senha):
            if not getattr(cooperado, 'ativo', True):
                flash('Usuário inativo. Fale com o administrador.')
                try:
                    return render_template('login.html', now=lambda: datetime.now(BRAZIL_TZ))
                except TemplateNotFound:
                    pass
            session['user_id']   = cooperado.id
            session['user_nome'] = cooperado.nome
            session['is_admin']  = False
            session['is_master'] = False
            return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha incorretos.')

    # ===== Fallback se não achar login.html (mantido) =====
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

@app.route('/cooperados/cadastrar', methods=['GET', 'POST'])
def cadastrar_cooperado():
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
    cooperado = Cooperado.query.get_or_404(coop_id)
    db.session.delete(cooperado)
    db.session.commit()
    flash('Cooperado excluído com sucesso!')
    return redirect(url_for('cadastrar_cooperado'))

@app.route('/cooperados/<int:coop_id>/status', methods=['POST'])
def mudar_status_cooperado(coop_id):
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

    # Monta estatísticas por cliente (tentando casar pelo nome, com normalização forte)
    clientes_db = Cliente.query.order_by(Cliente.nome).all()
    clientes_info = []
    for c in clientes_db:
        key_full = normalize_letters_key(c.nome or '')
        key_first = normalize_first_token(c.nome or '')

        st = stats_by_full.get(key_full) or stats_by_first.get(key_first) or {"qtd": 0, "ultimo": None}
        qtd = int(st.get("qtd") or 0)
        ultimo = st.get("ultimo")

        ultimo_local = to_brasilia(ultimo) if ultimo else None

        clientes_info.append({
            "obj": c,
            "qtd_entregas": qtd,
            "ultimo_pedido": ultimo_local,
        })

    return render_template(
        'clientes.html',
        clientes=[ci["obj"] for ci in clientes_info],
        clientes_info=clientes_info,
        hoje=hoje_local,
        to_brasilia=to_brasilia
    )


# ============================
# ====== CRÉDITO (ADMIN) =====
# ============================
@app.route('/admin/creditos', methods=['GET', 'POST'])
def admin_creditos():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        valor_bruto = request.form.get('valor_bruto') or '0'
        desconto_tipo = (request.form.get('desconto_tipo') or 'nenhum').lower()
        desconto_valor = request.form.get('desconto_valor') or '0'
        motivo = (request.form.get('motivo') or '').strip()

        try:
            cliente_id = int(cliente_id)
        except Exception:
            flash('Selecione um cliente válido.')
            return redirect(url_for('admin_creditos'))

        cli = Cliente.query.get(cliente_id)
        if not cli:
            flash('Cliente não encontrado.')
            return redirect(url_for('admin_creditos'))

        try:
            v_bruto = _as_decimal(valor_bruto)
            v_desc = _as_decimal(desconto_valor)
        except Exception:
            flash('Valores inválidos.')
            return redirect(url_for('admin_creditos'))

        if v_bruto <= 0:
            flash('O valor bruto deve ser maior que zero.')
            return redirect(url_for('admin_creditos'))

        v_final = calcular_valor_final(v_bruto, desconto_tipo, v_desc)
        saldo_antes = _as_decimal(cli.saldo_atual or 0)
        saldo_depois = saldo_antes + v_final

        cred = Credito(
            cliente_id=cli.id,
            valor_bruto=float(v_bruto),
            desconto_tipo=desconto_tipo,
            desconto_valor=float(v_desc),
            valor_final=float(v_final),
            motivo=motivo[:180] if motivo else None,
            saldo_antes=float(saldo_antes),
            saldo_depois=float(saldo_depois),
            criado_por=session.get('user_nome') or 'admin'
        )
        db.session.add(cred)
        db.session.flush()  # para pegar cred.id

        # Atualiza saldo do cliente + registra movimento
        atualizar_saldo_cliente(cli.id, float(v_final))
        registrar_movimento(
            cliente_id=cli.id,
            tipo=TIPO_ENTRADA,
            valor=float(v_final),
            referencia=f"Crédito #{cred.id}",
            credito_id=cred.id
        )

        db.session.commit()
        flash(f'Crédito de R$ {v_final:.2f} lançado para {cli.nome}.')
        return redirect(url_for('admin_creditos'))

    # GET: lista créditos + clientes
    creditos = Credito.query.order_by(Credito.criado_em.desc()).all()
    clientes = {c.id: c for c in Cliente.query.order_by(Cliente.nome).all()}

    return render_or_string("admin_creditos.html", """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Créditos de Clientes</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#020617;color:#e5e7eb}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
.card{background:#020617;border:1px solid #1e293b;border-radius:16px;padding:20px;margin-bottom:18px}
h1{margin:0 0 16px;font-size:22px}
label{font-size:14px;display:block;margin-bottom:4px}
input,select,textarea{width:100%;padding:8px 10px;border-radius:8px;border:1px solid #1e293b;background:#020617;color:#e5e7eb;font-size:14px}
textarea{min-height:60px;resize:vertical}
button{border:0;border-radius:999px;padding:8px 18px;font-size:14px;font-weight:600;cursor:pointer;background:#3b82f6;color:#0b1120}
button:hover{background:#2563eb}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
th,td{padding:8px;border-bottom:1px solid #1e293b;text-align:left}
th{background:#020617;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.badge{display:inline-flex;align-items:center;border-radius:999px;font-size:11px;padding:2px 8px;border:1px solid #1e293b;background:#020617}
.money{font-variant-numeric:tabular-nums;font-weight:700}
.flash{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:8px 12px;margin-bottom:10px;font-size:13px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>Lançar Crédito para Cliente</h1>
    {% with msgs = get_flashed_messages() %}
      {% if msgs %}
        {% for m in msgs %}
          <div class="flash">{{ m }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
      <div style="grid-column:1/-1">
        <label>Cliente</label>
        <select name="cliente_id" required>
          <option value="">Selecione...</option>
          {% for c in clientes.values()|sort(attribute='nome') %}
            <option value="{{ c.id }}">{{ c.nome }}{% if c.saldo_atual %} — saldo: R$ {{ '%.2f'|format(c.saldo_atual)|replace('.',',') }}{% endif %}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label>Valor bruto (R$)</label>
        <input type="number" step="0.01" min="0" name="valor_bruto" required>
      </div>
      <div>
        <label>Tipo de desconto</label>
        <select name="desconto_tipo">
          <option value="nenhum">Nenhum</option>
          <option value="percentual">% (sobre o valor)</option>
          <option value="real">Valor fixo (R$)</option>
        </select>
      </div>
      <div>
        <label>Desconto</label>
        <input type="number" step="0.01" min="0" name="desconto_valor" value="0">
      </div>
      <div style="grid-column:1/-1">
        <label>Motivo / Observação</label>
        <textarea name="motivo" maxlength="180" placeholder="Ex: Recarga de crédito do plano mensal, ajuste, cortesia, etc."></textarea>
      </div>
      <div style="grid-column:1/-1;text-align:right;margin-top:4px">
        <button type="submit">Lançar crédito</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2 style="margin:0 0 10px;font-size:18px">Histórico de Créditos</h2>
    <div style="overflow:auto;border:1px solid #1e293b;border-radius:12px">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Data</th>
            <th>Cliente</th>
            <th>Motivo</th>
            <th>Bruto</th>
            <th>Desconto</th>
            <th>Final</th>
            <th>Saldo antes</th>
            <th>Saldo depois</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for c in creditos %}
            <tr>
              <td>#{{ c.id }}</td>
              <td>{{ to_brasilia(c.criado_em).strftime('%d/%m/%Y %H:%M') if c.criado_em else '-' }}</td>
              <td>{{ clientes.get(c.cliente_id).nome if clientes.get(c.cliente_id) else 'ID ' ~ c.cliente_id }}</td>
              <td>{{ c.motivo or '-' }}</td>
              <td class="money">R$ {{ '%.2f'|format(c.valor_bruto)|replace('.',',') }}</td>
              <td>
                {% if c.desconto_tipo != 'nenhum' and c.desconto_valor %}
                  {% if c.desconto_tipo == 'percentual' %}
                    {{ '%.2f'|format(c.desconto_valor)|replace('.',',') }}%
                  {% else %}
                    R$ {{ '%.2f'|format(c.desconto_valor)|replace('.',',') }}
                  {% endif %}
                {% else %}
                  -
                {% endif %}
              </td>
              <td class="money">R$ {{ '%.2f'|format(c.valor_final)|replace('.',',') }}</td>
              <td>R$ {{ '%.2f'|format(c.saldo_antes)|replace('.',',') }}</td>
              <td>R$ {{ '%.2f'|format(c.saldo_depois)|replace('.',',') }}</td>
              <td>
                <form method="post" action="{{ url_for('admin_creditos_excluir', cred_id=c.id) }}" onsubmit="return confirm('Excluir este crédito? Só é possível se ele não tiver sido consumido.');">
                  <button type="submit" style="background:#ef4444;color:#0b1120">Excluir</button>
                </form>
              </td>
            </tr>
          {% endfor %}
          {% if creditos|length == 0 %}
            <tr><td colspan="10" style="text-align:center;padding:12px;opacity:.7">Nenhum crédito lançado.</td></tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>

  <p><a href="{{ url_for('admin') }}" style="color:#93c5fd;text-decoration:none">&larr; Voltar ao painel admin</a></p>
</div>
</body>
</html>
    """, creditos=creditos, clientes=clientes, to_brasilia=to_brasilia)


@app.route('/admin/creditos/<int:cred_id>/excluir', methods=['POST'])
def admin_creditos_excluir(cred_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cred = Credito.query.get_or_404(cred_id)
    cli = Cliente.query.get(cred.cliente_id)

    # Verifica se há consumo vinculado a este crédito
    consumido = consumo_total_do_credito(cred.id)
    if consumido > 0:
        flash('Não é possível excluir: esse crédito já foi parcialmente consumido.')
        return redirect(url_for('admin_creditos'))

    # Estorna o valor final do crédito do saldo do cliente
    if cli:
        cli.saldo_atual = float(_as_decimal(cli.saldo_atual) - _as_decimal(cred.valor_final))

    # Apaga movimentos de crédito associados
    CreditoMovimento.query.filter(
        CreditoMovimento.credito_id == cred.id,
        CreditoMovimento.tipo == 'credito'
    ).delete(synchronize_session=False)

    db.session.delete(cred)
    db.session.commit()
    flash('Crédito excluído e saldo ajustado.')
    return redirect(url_for('admin_creditos'))


# =======================================
# ====== TABELA DE PREÇOS POR ROTA ======
# =======================================
@app.route('/tabela_precos', methods=['GET', 'POST'])
def tabela_precos():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        acao = request.form.get('acao')

        # Atualizar per_km
        if acao == 'per_km':
            novo = request.form.get('per_km') or '0'
            try:
                nv = float(novo.replace(',', '.'))
                if nv <= 0:
                    raise ValueError
            except Exception:
                flash('Valor inválido para preço por km.')
            else:
                set_per_km(nv)
                flash(f'Valor por km atualizado para R$ {nv:.2f}.')
            return redirect(url_for('tabela_precos'))

        # Criar/atualizar rota
        if acao == 'rota':
            origem = (request.form.get('origem') or '').strip()
            destino = (request.form.get('destino') or '').strip()
            valor = (request.form.get('valor') or '').strip()

            if not origem or not destino or not valor:
                flash('Preencha origem, destino e valor.')
                return redirect(url_for('tabela_precos'))

            try:
                v = float(valor.replace(',', '.'))
                if v <= 0:
                    raise ValueError
            except Exception:
                flash('Valor inválido para a rota.')
                return redirect(url_for('tabela_precos'))

            origem_norm = origem.strip()
            destino_norm = destino.strip()

            rota = PrecoRota.query.filter_by(origem=origem_norm, destino=destino_norm).first()
            if rota:
                rota.valor = v
                flash('Rota atualizada com sucesso.')
            else:
                rota = PrecoRota(origem=origem_norm, destino=destino_norm, valor=v)
                db.session.add(rota)
                flash('Rota cadastrada com sucesso.')

            db.session.commit()
            return redirect(url_for('tabela_precos'))

    per_km_val = get_per_km()
    rotas = PrecoRota.query.order_by(PrecoRota.origem, PrecoRota.destino).all()

    return render_or_string("tabela_precos.html", """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Tabela de Preços por Rota</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#020617;color:#e5e7eb}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
.card{background:#020617;border:1px solid #1e293b;border-radius:16px;padding:20px;margin-bottom:18px}
h1,h2{margin:0 0 12px}
label{font-size:14px;display:block;margin-bottom:4px}
input{width:100%;padding:8px 10px;border-radius:8px;border:1px solid #1e293b;background:#020617;color:#e5e7eb;font-size:14px}
button{border:0;border-radius:999px;padding:8px 18px;font-size:14px;font-weight:600;cursor:pointer;background:#3b82f6;color:#0b1120}
button:hover{background:#2563eb}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
th,td{padding:8px;border-bottom:1px solid #1e293b;text-align:left}
th{background:#020617;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.flash{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:8px 12px;margin-bottom:10px;font-size:13px}
.money{font-variant-numeric:tabular-nums;font-weight:700}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>Tabela de Preços</h1>
    {% with msgs = get_flashed_messages() %}
      {% if msgs %}
        {% for m in msgs %}
          <div class="flash">{{ m }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <p style="opacity:.8;font-size:13px">
      Aqui você define o valor base por km e pode cadastrar rotas específicas (origem x destino) com preço fixo.
      Essas rotas podem ser usadas pelo painel de cálculo de corridas.
    </p>
  </div>

  <div class="card">
    <h2>Valor Padrão por km</h2>
    <form method="post" style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end">
      <input type="hidden" name="acao" value="per_km">
      <div style="flex:0 0 160px">
        <label>Valor por km (R$)</label>
        <input type="number" step="0.01" min="0" name="per_km" value="{{ '%.2f'|format(per_km_val) }}">
      </div>
      <div>
        <button type="submit">Atualizar</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Cadastrar / Atualizar Rota</h2>
    <form method="post" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
      <input type="hidden" name="acao" value="rota">
      <div>
        <label>Origem</label>
        <input name="origem" placeholder="Ex: Tirol">
      </div>
      <div>
        <label>Destino</label>
        <input name="destino" placeholder="Ex: Candelária">
      </div>
      <div>
        <label>Valor (R$)</label>
        <input type="number" step="0.01" min="0" name="valor">
      </div>
      <div style="grid-column:1/-1;text-align:right">
        <button type="submit">Salvar rota</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Rotas Cadastradas</h2>
    <div style="overflow:auto;border:1px solid #1e293b;border-radius:12px">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Origem</th>
            <th>Destino</th>
            <th>Valor</th>
            <th>Atualizado em</th>
          </tr>
        </thead>
        <tbody>
          {% for r in rotas %}
            <tr>
              <td>#{{ r.id }}</td>
              <td>{{ r.origem }}</td>
              <td>{{ r.destino }}</td>
              <td class="money">R$ {{ '%.2f'|format(r.valor)|replace('.',',') }}</td>
              <td>
                {% set dt = r.atualizado_em or r.criado_em %}
                {{ to_brasilia(dt).strftime('%d/%m/%Y %H:%M') if dt else '-' }}
              </td>
            </tr>
          {% endfor %}
          {% if rotas|length == 0 %}
            <tr><td colspan="5" style="text-align:center;padding:12px;opacity:.7">Nenhuma rota cadastrada.</td></tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>

  <p><a href="{{ url_for('admin') }}" style="color:#93c5fd;text-decoration:none">&larr; Voltar ao painel admin</a></p>
</div>
</body>
</html>
    """, per_km_val=per_km_val, rotas=rotas, to_brasilia=to_brasilia)


@app.route('/api/precos_rota')
def api_precos_rota():
    rotas = PrecoRota.query.all()
    return jsonify([r.to_dict() for r in rotas])


@app.route('/api/per_km')
def api_per_km():
    return jsonify({"per_km": get_per_km()})


if __name__ == "__main__":
    # Útil para rodar localmente; em produção (Render) o gunicorn usa apenas "app"
    app.run(debug=True)

