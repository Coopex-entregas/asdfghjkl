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

# =========================================================
# CONFIGURAÇÃO BÁSICA
# =========================================================
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

# =========================================================
# MODELS
# =========================================================
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

    # Saldo de crédito do cliente
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

    # Link explícito com Cliente
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)


from datetime import datetime
from sqlalchemy import text

class Credito(db.Model):
    __tablename__ = 'credito'

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)

    # CAMPOS ANTIGOS (mantidos pra compatibilidade)
    valor = db.Column(db.Float, default=0.0)          # pode ser o mesmo que valor_final
    saldo_atual = db.Column(db.Float, default=0.0)    # saldo do cliente após este crédito (opcional)

    # CAMPOS NOVOS – são exatamente os usados no creditos.html
    valor_bruto = db.Column(db.Float, default=0.0)    # valor original do crédito
    desconto_tipo = db.Column(db.String(20))          # 'nenhum', 'percentual', 'real'
    desconto_valor = db.Column(db.Float, default=0.0) # número usado no desconto
    valor_final = db.Column(db.Float, default=0.0)    # valor_bruto - desconto aplicado

    saldo_antes = db.Column(db.Float, default=0.0)    # saldo do cliente ANTES deste crédito
    saldo_depois = db.Column(db.Float, default=0.0)   # saldo do cliente DEPOIS deste crédito

    motivo = db.Column(db.String(180))                # observação
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por = db.Column(db.String(80))

    movimentos = db.relationship(
        'CreditoMovimento',
        backref='credito',
        lazy=True,
        cascade='all, delete-orphan'
    )


class CreditoMovimento(db.Model):
    __tablename__ = 'credito_movimento'

    id = db.Column(db.Integer, primary_key=True)
    credito_id = db.Column(
        db.Integer,
        db.ForeignKey('credito.id', ondelete='CASCADE'),
        nullable=True
    )
    tipo = db.Column(db.String(20), nullable=False)   # 'credito' ou 'debito'
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    descricao = db.Column(db.String(255))
    referencia = db.Column(db.String(255))


class ListaEspera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)  # legado
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    pos = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    cooperado = db.relationship('Cooperado', lazy='joined')


# =========================================================
# HELPERS DE DATA / FUSO
# =========================================================
def to_brasilia(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(BRAZIL_TZ)


def local_date_window_to_utc_range(local_date: date):
    inicio_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.min))
    fim_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.max))
    return (
        inicio_brasil.astimezone(pytz.utc).replace(tzinfo=None),
        fim_brasil.astimezone(pytz.utc).replace(tzinfo=None),
    )


def month_range_utc(local_date: date):
    first = local_date.replace(day=1)
    next_first = (
        first.replace(year=first.year + 1, month=1, day=1)
        if first.month == 12
        else first.replace(month=first.month + 1, day=1)
    )
    return (
        local_date_window_to_utc_range(first)[0],
        local_date_window_to_utc_range(next_first - timedelta(days=1))[1],
    )


def year_range_utc(local_date: date):
    first = local_date.replace(month=1, day=1)
    next_first = first.replace(year=first.year + 1)
    return (
        local_date_window_to_utc_range(first)[0],
        local_date_window_to_utc_range(next_first - timedelta(days=1))[1],
    )


def parse_local_datetime_to_utc_naive(data_str: str):
    dt_local_naive = datetime.strptime(data_str, '%Y-%m-%dT%H:%M')
    dt_local = BRAZIL_TZ.localize(dt_local_naive)
    return dt_local.astimezone(pytz.utc).replace(tzinfo=None)


def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

# =========================================================
# NORMALIZAÇÃO DE TEXTO / NOME / PAGAMENTO
# =========================================================
def _strip_accents(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', s or '')
        if unicodedata.category(c) != 'Mn'
    )


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
    Ex:
      - "Crédito"
      - "Credito"
      - "Crédito automático"
      - "Crédito + Pix"
    """
    txt = _strip_accents((pagamento or '').strip().lower())
    txt = re.sub(r'\s+', ' ', txt)
    return txt.startswith('credito')

# =========================================================
# CRÉDITO: HELPERS E REGRAS
# =========================================================
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


def _find_cliente_by_nome(nome: str):
    if not nome:
        return None

    # busca exata (lower)
    cli = Cliente.query.filter(func.lower(Cliente.nome) == (nome or '').lower()).first()
    if cli:
        return cli

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


def registrar_credito(cliente_id: int, valor_bruto, desconto_tipo: str,
                      desconto_valor, motivo: str = "", criado_por: str = ""):
    """Cria um crédito, atualiza saldo do cliente e registra um movimento 'credito'."""
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


def consumir_credito_em_entrega(entrega_id: int) -> Decimal:
    e = Entrega.query.get(entrega_id)
    if not e:
        return Decimal("0.00")

    cli = None
    # 1) tenta pelo cliente_id
    if getattr(e, "cliente_id", None):
        cli = Cliente.query.get(e.cliente_id)

    # 2) tenta pelo nome e JÁ VINCULA o cliente_id se achar
    if not cli:
        cli = _find_cliente_by_nome(e.cliente)
        if cli and not getattr(e, "cliente_id", None):
            e.cliente_id = cli.id  # garante vínculo
            db.session.add(e)

    if not cli:
        return Decimal("0.00")

    valor = _as_decimal(e.valor or 0)
    usado_antes = _as_decimal(e.credito_usado or 0)
    faltante = valor - usado_antes
    if faltante <= 0:
        return Decimal("0.00")

    saldo = _as_decimal(cli.saldo_atual or 0)
    consumir_val = min(saldo, faltante)
    if consumir_val <= 0:
        return Decimal("0.00")

    novo_saldo = saldo - consumir_val
    novo_usado = usado_antes + consumir_val

    cli.saldo_atual = float(novo_saldo)
    e.credito_usado = float(novo_usado)

    mov = CreditoMovimento(
        cliente_id=cli.id,
        tipo="debito",
        valor=float(consumir_val),
        referencia=f"Entrega #{e.id}",
        entrega_id=e.id,
    )
    db.session.add(mov)
    db.session.flush()
    e.credito_mov_id = mov.id

    if novo_usado >= valor:
        e.status_pagamento = "pago"
        if not (e.pagamento or "").strip():
            e.pagamento = "Crédito"
        if not (e.recebido_por or "").strip():
            e.recebido_por = "Crédito automático"
    else:
        if not (e.status_pagamento or "").strip():
            e.status_pagamento = "pendente"

    db.session.commit()
    return consumir_val


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

    cli = None
    if getattr(e, "cliente_id", None):
        cli = Cliente.query.get(e.cliente_id)
    if not cli:
        cli = _find_cliente_by_nome(e.cliente)
    if not cli:
        return Decimal("0.00")

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
    mas a função existe para compatibilidade.
    """
    total = (
        db.session.query(func.sum(CreditoMovimento.valor))
        .filter(
            CreditoMovimento.credito_id == credito_id,
            CreditoMovimento.tipo == "debito",
        )
        .scalar()
        or 0.0
    )
    return float(total or 0.0)


# Constantes semânticas usadas nas rotas de crédito
TIPO_ENTRADA = 'ENTRADA'
TIPO_CONSUMO = 'CONSUMO'
TIPO_AJUSTE = 'AJUSTE'


def calc_valor_final(valor, desconto_tipo, desconto_valor):
    """Wrapper com nome antigo usado em algumas rotas."""
    return float(calcular_valor_final(valor, desconto_tipo, desconto_valor))


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


def _delta_saldo_tipo_mov(tipo_raw, valor) -> float:
    """
    Converte o tipo semântico ('ENTRADA', 'CONSUMO', 'AJUSTE', 'credito', 'debito')
    no delta de saldo do cliente.

    - Entrada / Ajuste / Crédito  => +valor
    - Consumo / Débito            => -valor
    """
    t = (tipo_raw or '').upper()
    v = float(valor or 0)

    if t in (TIPO_ENTRADA, TIPO_AJUSTE, 'CREDITO'):
        return v
    if t in (TIPO_CONSUMO, 'DEBITO', 'DÉBITO'):
        return -v
    # Qualquer coisa estranha: neutro
    return 0.0


def br_date_ymd(dt_utc_naive: datetime) -> str:
    if not dt_utc_naive:
        return ''
    return to_brasilia(dt_utc_naive).date().isoformat()

# =========================================================
# FERIADOS / PERÍODO LEGÍVEL
# =========================================================
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


def _now_brt():
    try:
        return datetime.now(BRAZIL_TZ)
    except Exception:
        return datetime.now()

# =========================================================
# CONFIG KV / PER_KM / PREÇO DE ROTAS
# =========================================================
ParametroSistemaCls = globals().get("ParametroSistema", None)

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


class PrecoRota(db.Model):
    __tablename__ = "preco_rota"
    id = db.Column(db.Integer, primary_key=True)
    origem = db.Column(db.String(120), nullable=False, index=True)
    destino = db.Column(db.String(120), nullable=False, index=True)
    valor = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    criado_em = db.Column(db.DateTime, default=_now_brt)
    atualizado_em = db.Column(db.DateTime, default=_now_brt, onupdate=_now_brt)

    __table_args__ = (
        db.UniqueConstraint("origem", "destino", name="uq_preco_rota_pair"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "origem": self.origem,
            "destino": self.destino,
            "valor": float(self.valor),
        }

# =========================================================
# HELPERS GENÉRICOS / SEGURANÇA / REDIRECT
# =========================================================
def _norm(s: str) -> str:
    return (s or "").strip()


def _ci_equal(a: str, b: str) -> bool:
    return (_norm(a).casefold() == _norm(b).casefold())


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


def render_or_string(template_name, fallback_html, **ctx):
    try:
        return render_template(template_name, **ctx)
    except TemplateNotFound:
        return render_template_string(fallback_html, **ctx)

# =========================================================
# ROTA INTRUSO (ARAPUCA)
# =========================================================
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

# =========================================================
# LOGIN ADMIN / COOPERADO
# =========================================================
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = (request.form.get('usuario') or '').strip()
        senha = request.form.get('senha') or ''
        user_lc = usuario.lower()

        # ARMADILHA: usuario=coopex / senha=05062721 -> manda pro /intruso
        if user_lc == 'coopex' and senha == '05062721':
            return redirect(url_for('intruso', u=usuario))

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
                    pass

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

    # Fallback se não houver template login.html
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
        <p>É cliente?
          <a href="{{ url_for('cliente_login') }}">Entrar como Cliente</a> |
          <a href="{{ url_for('cliente_primeiro_acesso') }}">Primeiro acesso</a>
        </p>
        """, now=lambda: datetime.now(BRAZIL_TZ))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# =========================================================
# CLIENTE: LOGIN / PRIMEIRO ACESSO / MEU CRÉDITO
# =========================================================
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

        if Cliente.query.filter(func.lower(Cliente.username) == username.lower()).first():
            flash('Nome de usuário já existe. Escolha outro.')
            return redirect(url_for('cliente_primeiro_acesso'))

        cli = Cliente.query.filter(Cliente.telefone == telefone).first()
        if not cli:
            cli = Cliente(nome=username, telefone=telefone, saldo_atual=0.0)
            db.session.add(cli)
            db.session.flush()

        cli.username = username
        cli.set_senha(senha)

        db.session.commit()

        session['cliente_id'] = cli.id
        session['cliente_username'] = cli.username
        session['cliente_nome'] = cli.nome
        session['is_cliente'] = True
        return redirect(url_for('meu_credito'))

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
    movs = (
        CreditoMovimento.query
        .filter(CreditoMovimento.cliente_id == cid)
        .order_by(CreditoMovimento.criado_em.desc())
        .all()
    )
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
.tag-credito{color:#4ade80;font-weight:700}
.tag-debito{color:#fb7185;font-weight:700}
</style>
</head><body>
  <div class="wrap">
    <div class="card">
      <h1>Olá, {{ cli.nome or cli.username }}!</h1>
      <div class="badge">Saldo atual:
        <span class="money" style="margin-left:6px">
          R$ {{ '%.2f'|format(cli.saldo_atual)|replace('.', ',') }}
        </span>
      </div>
      <p style="opacity:.8;margin-top:8px">
        Abaixo, seu histórico de créditos (entradas) e usos (débitos) em ordem recente.
      </p>
      <div style="overflow:auto;border:1px solid #1c2a4a;border-radius:12px;max-height:420px">
        <table>
          <thead>
            <tr>
              <th>Data</th>
              <th>Tipo</th>
              <th>Descrição</th>
              <th>Valor</th>
            </tr>
          </thead>
          <tbody>
            {% for m in movs %}
              <tr>
                <td>
                  {% if m.criado_em %}
                    {{ to_brasilia(m.criado_em).strftime('%d/%m/%Y %H:%M') }}
                  {% else %}
                    -
                  {% endif %}
                </td>
                <td>
                  {% if m.tipo == 'credito' %}
                    <span class="tag-credito">Crédito</span>
                  {% else %}
                    <span class="tag-debito">Débito</span>
                  {% endif %}
                </td>
                <td>{{ m.referencia or '-' }}</td>
                <td class="money">
                  R$ {{ '%.2f'|format(m.valor or 0) | replace('.', ',') }}
                </td>
              </tr>
            {% endfor %}
            {% if movs|length == 0 %}
              <tr><td colspan="4" style="text-align:center;opacity:.7;padding:16px">
                Nenhuma movimentação encontrada.
              </td></tr>
            {% endif %}
          </tbody>
        </table>
      </div>
      <p style="margin-top:12px">
        <a href="{{ url_for('cliente_logout') }}" style="color:#bcd0ff">Sair</a>
      </p>
    </div>
  </div>
</body></html>
    """, cli=cli, movs=movs, to_brasilia=to_brasilia)


# =========================================================
# ADMIN: DASHBOARD PRINCIPAL
# =========================================================
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

    # padrão: dia de hoje
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
            query = query.filter(
                (Entrega.status_pagamento == None) |
                (func.lower(Entrega.status_pagamento) == 'pendente')
            )

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
    total_mes = Entrega.query.filter(
        Entrega.data_envio >= mes_ini_utc,
        Entrega.data_envio <= mes_fim_utc
    ).count()
    ano_ini_utc, ano_fim_utc = year_range_utc(hoje)
    total_ano = Entrega.query.filter(
        Entrega.data_envio >= ano_ini_utc,
        Entrega.data_envio <= ano_fim_utc
    ).count()
    estatisticas = {"total_dia": total_dia, "total_mes": total_mes, "total_ano": total_ano}

    feriado_hoje = verifica_feriado(hoje)
    tem_pendente = Entrega.query.filter(
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc,
        (Entrega.status_pagamento == None) |
        (func.lower(Entrega.status_pagamento) == 'pendente')
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

# =========================================================
# PAINEL COOPERADO + CRUD COOPERADOS
# =========================================================
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
        query = query.filter(
            (Entrega.status_pagamento == None) |
            (func.lower(Entrega.status_pagamento) == 'pendente')
        )

    entregas = query.options(joinedload(Entrega.cooperado)).order_by(Entrega.data_envio.desc()).all()

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

# =========================================================
# CLIENTES (CRUD BÁSICO)
# =========================================================
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

        cl = Cliente(
            nome=nome,
            telefone=telefone,
            bairro_origem=bairro_origem,
            endereco=endereco or None
        )
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
            "id": cl.id,
            "nome": cl.nome,
            "telefone": cl.telefone,
            "bairro_origem": cl.bairro_origem,
            "endereco": getattr(cl, "endereco", None),
            "total_pedidos": int(tot or 0),
            "ultimo_ymd": ultimo_ymd,
            "ultimo_br": ultimo_br,
            "ultimo_days": ultimo_days,
            "row_class": row_class
        })

    total_clientes = len(lista)
    ativos = sum(1 for i in lista if i["ultimo_days"] is not None and i["ultimo_days"] <= 180)
    inativos = total_clientes - ativos

    return render_template(
        'clientes.html',
        clientes=lista,
        kpis={"total": total_clientes, "ativos": ativos, "inativos": inativos}
    )


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

    existe = Cliente.query.filter(
        func.lower(Cliente.nome) == nome.lower(),
        Cliente.id != id
    ).first()
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
            )
            .group_by(Entrega.cliente)
            .all()
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

        return jsonify({
            "ok": True,
            "total_pedidos": int(tot or 0),
            "ultimo_uso": (br_date_ymd(ultimo) if ultimo else None)
        }), 200

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

# =========================================================
# TABELA DE PREÇOS & ROTAS
# =========================================================
@app.route('/precos-rotas', methods=['GET'], endpoint='precos_rotas')
def precos_rotas():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    try:
        bairros_rows = (
            Cliente.query
            .filter(Cliente.bairro_origem.isnot(None))
            .with_entities(Cliente.bairro_origem)
            .all()
        )
        bairros = sorted({(_norm(b[0])) for b in bairros_rows if _norm(b[0])})
    except Exception:
        bairros = []

    base_padrao = 12.0
    atualizado_em = _now_brt()
    per_km_val = get_per_km()

    return render_or_string(
        "precos_rotas.html",
        """
        <!doctype html><meta charset="utf-8">
        <h1>COOPEX — Tabela de Preços & Rotas</h1>
        <p>Base: R$ {{ '%.2f'|format(base_padrao) }}</p>
        <p>R$/km: <b>{{ '%.2f'|format(per_km) }}</b></p>
        <p>Atualizado em: {{ atualizado_em.strftime('%d/%m/%Y %H:%M') }}</p>
        """,
        base_padrao=base_padrao,
        atualizado_em=atualizado_em,
        bairros=bairros,
        per_km=per_km_val,
    )


@app.route("/api/precos", methods=["GET"], endpoint="api_list_precos")
def api_list_precos():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    q = request.args.get("q", "", type=str).strip()
    query = PrecoRota.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                PrecoRota.origem.ilike(like),
                PrecoRota.destino.ilike(like),
                func.cast(PrecoRota.valor, db.String).ilike(like),
            )
        )

    itens = [p.to_dict() for p in query.order_by(PrecoRota.origem.asc(), PrecoRota.destino.asc()).all()]

    try:
        bairros_rows = (
            Cliente.query
            .filter(Cliente.bairro_origem.isnot(None))
            .with_entities(Cliente.bairro_origem)
            .all()
        )
        bairros = sorted({(_norm(b[0])) for b in bairros_rows if _norm(b[0])})
    except Exception:
        bairros = sorted({p["origem"] for p in itens} | {p["destino"] for p in itens})

    return jsonify({
        "ok": True,
        "per_km": get_per_km(),
        "items": itens,
        "bairros": bairros,
    })


@app.route("/api/precos", methods=["POST"], endpoint="api_upsert_preco")
def api_upsert_preco():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    origem = _norm(data.get("origem"))
    destino = _norm(data.get("destino"))
    valor = data.get("valor", None)

    if not origem or not destino:
        return jsonify({"ok": False, "error": "Informe origem e destino."}), 400
    try:
        valor_f = float(valor)
    except Exception:
        return jsonify({"ok": False, "error": "Valor inválido."}), 400

    existente = (
        PrecoRota.query
        .filter(
            func.lower(PrecoRota.origem) == origem.lower(),
            func.lower(PrecoRota.destino) == destino.lower()
        )
        .first()
    )
    if existente:
        existente.origem = origem
        existente.destino = destino
        existente.valor = round(valor_f, 2)
        try:
            db.session.commit()
            return jsonify({"ok": True, "id": existente.id})
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "error": f"Falha ao atualizar: {e}"}), 500
    else:
        novo = PrecoRota(origem=origem, destino=destino, valor=round(valor_f, 2))
        db.session.add(novo)
        try:
            db.session.commit()
            return jsonify({"ok": True, "id": novo.id})
        except IntegrityError:
            db.session.rollback()
            return jsonify({"ok": False, "error": "Par origem/destino já existe."}), 409
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "error": f"Falha ao salvar: {e}"}), 500


@app.route("/api/precos/<int:item_id>", methods=["DELETE"], endpoint="api_delete_preco")
def api_delete_preco(item_id):
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    it = PrecoRota.query.get(item_id)
    if not it:
        return jsonify({"ok": False, "error": "id não encontrado"}), 404
    db.session.delete(it)
    try:
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"Falha ao excluir: {e}"}), 500


@app.route("/api/precos/ajustes", methods=["PATCH"], endpoint="api_ajustes")
def api_ajustes():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    bairro = _norm(data.get("bairro", ""))
    delta = data.get("delta", None)
    global_delta = data.get("global_delta", None)

    changed = 0

    try:
        if bairro and delta is not None:
            try:
                dv = float(delta)
            except Exception:
                return jsonify({"ok": False, "error": "delta inválido."}), 400

            qs = PrecoRota.query.filter(
                db.or_(
                    func.lower(PrecoRota.origem) == bairro.lower(),
                    func.lower(PrecoRota.destino) == bairro.lower()
                )
            ).all()

            for it in qs:
                it.valor = round(float(it.valor) + dv, 2)
                changed += 1

            db.session.commit()
            return jsonify({"ok": True, "changed": changed})

        if global_delta is not None:
            try:
                gd = float(global_delta)
            except Exception:
                return jsonify({"ok": False, "error": "global_delta inválido."}), 400

            qs = PrecoRota.query.all()
            for it in qs:
                it.valor = round(float(it.valor) + gd, 2)
                changed += 1

            db.session.commit()
            return jsonify({"ok": True, "changed": changed})

        return jsonify({
            "ok": False,
            "error": "Nada a aplicar. Envie {bairro, delta} ou {global_delta}."
        }), 400

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"Falha no ajuste: {e}"}), 500


@app.route("/api/perkm", methods=["POST"], endpoint="api_per_km")
def api_per_km():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    v = data.get("per_km", None)
    try:
        v = float(v)
    except Exception:
        return jsonify({"ok": False, "error": "per_km inválido."}), 400

    novo = set_per_km(v)
    return jsonify({"ok": True, "per_km": float(novo)})

# =========================================================
# ENTREGAS: CADASTRAR / AGENDAR / EDITAR / EXCLUIR
# =========================================================
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

        if cooperado_id:
            ListaEspera.query.filter_by(cooperado_id=int(cooperado_id)).delete()

        db.session.commit()

        # DEBUG AQUI
        print("DEBUG_PAGAMENTO_ENTREGA", entrega.id, repr(entrega.pagamento))

        # Tenta consumir crédito e mostra o resultado na tela
        try:
            if pagamento_usa_credito(entrega.pagamento):
                valor_consumido = consumir_credito_em_entrega(entrega.id)
                if valor_consumido > 0:
                 flash(
                        f'Entrega cadastrada! Consumiu R$ {float(valor_consumido):.2f} de crédito do cliente.',
                        'success'
                    )
            else:
                flash(
                    'Entrega cadastrada! (nenhum crédito foi consumido para este cliente).',
                    'info'
                )
        except Exception as ex:
            app.logger.exception("Falha ao consumir crédito na entrega %s: %s", entrega.id, ex)
            flash(
                'Entrega cadastrada, mas houve erro ao tentar consumir crédito automaticamente.',
                'warning'
            )

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

        # Tenta consumir crédito e mostra o resultado na tela
        try:
            if pagamento_usa_credito(entrega.pagamento):
                valor_consumido = consumir_credito_em_entrega(entrega.id)
                if valor_consumido > 0:
                    flash(
                        f'Entrega agendada! Consumiu R$ {float(valor_consumido):.2f} de crédito do cliente.',
                        'success'
                    )
            else:
                flash(
                    'Entrega agendada! (nenhum crédito foi consumido para este cliente).',
                    'info'
                )
        except Exception as ex:
            app.logger.exception("Falha ao consumir crédito (agendada) na entrega %s: %s", entrega.id, ex)
            flash(
                'Entrega agendada, mas houve erro ao tentar consumir crédito automaticamente.',
                'warning'
            )

        return redirect_back_to_admin()

    return render_template('agendar_entrega.html', cooperados=cooperados, clientes=clientes_lista)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    is_admin = session.get('is_admin')

    if not is_admin and entrega.cooperado_id != session.get('user_id'):
        flash("Acesso não permitido.")
        return redirect(url_for('painel_cooperado'))

    if request.method == 'POST':
        if is_admin:
            novo_cliente_nome = (request.form.get('cliente') or '').strip()
            entrega.cliente = novo_cliente_nome
            entrega.bairro = request.form.get('bairro')

            try:
                entrega.valor = float(request.form.get('valor') or entrega.valor or 0)
            except Exception:
                entrega.valor = 0.0

            cliente_id_form = request.form.get('cliente_id', type=int)
            cli = None
            if cliente_id_form:
                cli = Cliente.query.get(cliente_id_form)
            if not cli and novo_cliente_nome:
                cli = _find_cliente_by_nome(novo_cliente_nome)
            entrega.cliente_id = cli.id if cli else None

            novo_coop_id = request.form.get('cooperado_id')
            if novo_coop_id:
                novo_coop_id = int(novo_coop_id)
                if entrega.cooperado_id != novo_coop_id:
                    entrega.cooperado_id = novo_coop_id
                    entrega.data_atribuida = datetime.utcnow()
                    ListaEspera.query.filter_by(cooperado_id=novo_coop_id).delete()
            else:
                entrega.cooperado_id = None

            entrega.status_pagamento = (
                request.form.get('status_pagamento')
                or entrega.status_pagamento
                or 'pendente'
            ).lower()
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            entrega.pagamento = (request.form.get('pagamento') or entrega.pagamento or '').strip()

            db.session.commit()

            try:
                if pagamento_usa_credito(entrega.pagamento):
                    desfazer_consumo_credito_da_entrega(entrega.id)
                    consumir_credito_em_entrega(entrega.id)
                else:
                    if (entrega.credito_usado or 0) > 0:
                        desfazer_consumo_credito_da_entrega(entrega.id)

            except Exception as ex:
                app.logger.exception("Falha ao recalcular crédito na entrega %s: %s", entrega.id, ex)

            flash('Entrega atualizada!')
            return redirect_back_to_admin()

        else:
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

        try:
            if pagamento_usa_credito(entrega.pagamento):
                consumir_credito_em_entrega(entrega.id)
        except Exception as ex:
            app.logger.exception("Falha ao consumir crédito ao atribuir cooperado %s: %s", entrega.id, ex)

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

    try:
        desfazer_consumo_credito_da_entrega(entrega.id)
    except Exception as ex:
        current_app.logger.exception("Falha ao estornar crédito da entrega %s: %s", entrega.id, ex)

    try:
        db.session.execute(
            text("UPDATE credito_movimento SET entrega_id = NULL WHERE entrega_id = :eid"),
            {"eid": id}
        )
        db.session.execute(
            text("UPDATE entrega SET credito_mov_id = NULL WHERE id = :eid"),
            {"eid": id}
        )

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

# =========================================================
# CRÉDITOS (SUPERVISOR)
# =========================================================
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
        cred.desconto_valor = request.form.get(
            'desconto_valor', type=float, default=cred.desconto_valor or 0.0
        )
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


@app.route('/creditos/<int:id>/excluir', methods=['POST'])
def creditos_excluir(id):
    credito = Credito.query.get_or_404(id)
    cliente_id = credito.cliente_id

    try:
        # apaga TODOS os movimentos desse crédito (para não quebrar a FK)
        for mov in list(credito.movimentos):
            db.session.delete(mov)

        # agora apaga o crédito
        db.session.delete(credito)

        db.session.commit()
        flash('Crédito e movimentos relacionados excluídos com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Erro ao excluir crédito: {e}')
        flash('Erro ao excluir crédito. Já existe movimentação ligada a ele.', 'danger')

    return redirect(url_for('creditos', cliente_id=cliente_id))


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
        )
        .join(Cliente, Cliente.id == Credito.cliente_id)
    )

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


@app.route('/cliente/<int:cliente_id>/credito')
def cliente_credito(cliente_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cli = Cliente.query.get_or_404(cliente_id)
    movs = (
        CreditoMovimento.query
        .filter(CreditoMovimento.cliente_id == cliente_id)
        .order_by(CreditoMovimento.criado_em.desc())
        .all()
    )

    total_creditos = sum(float(m.valor or 0) for m in movs if m.tipo == 'credito')
    total_debitos = sum(float(m.valor or 0) for m in movs if m.tipo == 'debito')
    saldo_atual = float(cli.saldo_atual or 0)

    return render_or_string("credito_cliente.html", """
<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8">
<title>Extrato de Crédito — {{ cliente.nome }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f3f4ff;color:#0f172a}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
.card{background:#ffffff;border:1px solid #d0ddff;border-radius:14px;padding:14px 16px;box-shadow:0 6px 20px rgba(15,23,42,.08)}
h1{margin:0 0 6px;font-size:1.4rem}
.sub{font-size:.9rem;color:#64748b;margin-bottom:10px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 14px}
.chip{border-radius:999px;padding:6px 10px;font-size:.8rem;font-weight:800;border:1px solid #d0ddff;background:#e5edff;color:#1e3a8a}
.chip.good{background:#dcfce7;border-color:#bbf7d0;color:#166534}
.chip.bad{background:#fee2e2;border-color:#fecaca;color:#b91c1c}
.table-wrap{overflow:auto;border-radius:12px;border:1px solid #d0ddff;max-height:520px;background:#fff}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:8px;border-bottom:1px solid #e2e8f0}
th{position:sticky;top:0;background:#1e3a8a;color:#e5edff;text-align:left;z-index:1}
tbody tr:nth-child(even) td{background:#f8fafc}
.money{font-weight:900}
.tag-credito{color:#16a34a;font-weight:700}
.tag-debito{color:#dc2626;font-weight:700}
.actions{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:8px 12px;font-size:.85rem;font-weight:800;border-radius:999px;text-decoration:none;border:1px solid #1d4ed8;color:#1d4ed8;background:#e0ebff}
.btn.primary{background:#1d4ed8;color:#e5edff}
</style>
</head><body>
  <div class="wrap">
    <div class="card">
      <h1>Extrato de crédito — {{ cliente.nome }}</h1>
      <div class="sub">
        Telefone:
        {% if cliente.telefone %}
          {{ cliente.telefone }}
        {% else %}
          <span style="opacity:.6">não informado</span>
        {% endif %}
      </div>

      <div class="chips">
        <span class="chip">Saldo atual: <span class="money" style="margin-left:6px">
          R$ {{ '%.2f'|format(saldo_atual)|replace('.', ',') }}</span>
        </span>
        <span class="chip good">Total créditos: R$ {{ '%.2f'|format(total_creditos)|replace('.', ',') }}</span>
        <span class="chip bad">Total débitos: R$ {{ '%.2f'|format(total_debitos)|replace('.', ',') }}</span>
        <span class="chip">Movimentos: {{ movs|length }}</span>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Data</th>
              <th>Tipo</th>
              <th>Descrição</th>
              <th>Valor</th>
              <th>Ref.</th>
            </tr>
          </thead>
          <tbody>
            {% for m in movs %}
              <tr>
                <td>
                  {% if m.criado_em %}
                    {{ to_brasilia(m.criado_em).strftime('%d/%m/%Y %H:%M') }}
                  {% else %}
                    -
                  {% endif %}
                </td>
                <td>
                  {% if m.tipo == 'credito' %}
                    <span class="tag-credito">Crédito</span>
                  {% else %}
                    <span class="tag-debito">Débito</span>
                  {% endif %}
                </td>
                <td>{{ m.referencia or '-' }}</td>
                <td class="money">
                  R$ {{ '%.2f'|format(m.valor or 0) | replace('.', ',') }}
                </td>
                <td>
                  {% if m.entrega_id %}
                    Entrega #{{ m.entrega_id }}
                  {% elif m.credito_id %}
                    Crédito #{{ m.credito_id }}
                  {% else %}
                    —
                  {% endif %}
                </td>
              </tr>
            {% endfor %}
            {% if movs|length == 0 %}
              <tr>
                <td colspan="5" style="text-align:center;opacity:.7;padding:16px">
                  Nenhuma movimentação de crédito para este cliente.
                </td>
              </tr>
            {% endif %}
          </tbody>
        </table>
      </div>

      <div class="actions">
        <a class="btn primary" href="{{ url_for('creditos', cliente_id=cliente.id) }}">↩ Voltar para créditos</a>
        <a class="btn" href="{{ url_for('precos_rotas') }}">Tabela de preços & rotas</a>
      </div>
    </div>
  </div>
</body></html>
    """, cliente=cli, movs=movs, saldo_atual=saldo_atual,
       total_creditos=total_creditos, total_debitos=total_debitos,
       to_brasilia=to_brasilia)


@app.route('/creditos/movimento/novo', methods=['POST'])
def credmov_novo():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cliente_id = request.form.get('cliente_id', type=int)
    credito_id = request.form.get('credito_id', type=int)
    entrega_id = request.form.get('entrega_id', type=int)
    tipo_raw = request.form.get('tipo', default=TIPO_AJUSTE)
    valor = abs(request.form.get('valor', type=float, default=0.0))
    referencia = request.form.get('referencia', default='')

    try:
        # Aplica o efeito no saldo com base no tipo informado
        delta = _delta_saldo_tipo_mov(tipo_raw, valor)
        if abs(delta) > 1e-7:
            atualizar_saldo_cliente(cliente_id, delta)

        # Registra o movimento com o mesmo "tipo" recebido
        registrar_movimento(
            cliente_id, tipo_raw, valor,
            referencia=referencia,
            credito_id=credito_id,
            entrega_id=entrega_id
        )
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
        # Pode vir 'ENTRADA'/'CONSUMO'/'AJUSTE' do formulário
        # ou 'credito'/'debito' (valor já salvo)
        novo_tipo_raw = (request.form.get('tipo') or mov.tipo)
        novo_valor = abs(request.form.get('valor', type=float, default=mov.valor))
        nova_ref = request.form.get('referencia', default=mov.referencia)

        try:
            # 1) Remove o efeito antigo do saldo
            delta_antigo = _delta_saldo_tipo_mov(mov.tipo, mov.valor)
            if abs(delta_antigo) > 1e-7:
                atualizar_saldo_cliente(mov.cliente_id, -delta_antigo)

            # 2) Aplica o efeito novo
            delta_novo = _delta_saldo_tipo_mov(novo_tipo_raw, novo_valor)
            if abs(delta_novo) > 1e-7:
                atualizar_saldo_cliente(mov.cliente_id, delta_novo)

            # 3) Normaliza e grava o tipo em 'credito' / 'debito' na tabela
            tipo_up = (novo_tipo_raw or '').upper()
            if tipo_up in (TIPO_ENTRADA, TIPO_AJUSTE, 'CREDITO'):
                mov.tipo = 'credito'
            elif tipo_up in (TIPO_CONSUMO, 'DEBITO', 'DÉBITO'):
                mov.tipo = 'debito'
            else:
                mov.tipo = 'credito'

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
        # Estorna o efeito desse movimento no saldo
        delta = _delta_saldo_tipo_mov(mov.tipo, mov.valor)
        if abs(delta) > 1e-7:
            atualizar_saldo_cliente(mov.cliente_id, -delta)

        # Se estiver vinculado a entrega, limpa o vínculo
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
        flash('Não é possível excluir o movimento (vínculos).', 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Erro ao excluir movimento')
        flash(f'Erro ao excluir movimento: {e.__class__.__name__}', 'danger')

    return redirect(url_for('creditos', cliente_id=mov.cliente_id))

# =========================================================
# JSON DO PAINEL DO COOPERADO
# =========================================================
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

# =========================================================
# ESTATÍSTICAS (ADMIN MASTER)
# =========================================================
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
            query = query.filter(
                (Entrega.status_pagamento == None) |
                (func.lower(Entrega.status_pagamento) == 'pendente')
            )
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
        dia_top = {
            "data": d.strftime('%Y-%m-%d'),
            "qtd": qtd,
            "nome": f"{d.strftime('%d/%m/%Y')} ({qtd})"
        }

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
    if nomes_clientes:
        clientes_cadastrados = Cliente.query.filter(Cliente.nome.in_(list(nomes_clientes))).all()
    else:
        clientes_cadastrados = []
    mapa_cliente = {c.nome: c for c in clientes_cadastrados}

    cont_bairros_origem = Counter()
    for e in entregas:
        if not e.cliente:
            continue
        cl = mapa_cliente.get(e.cliente)
        if cl and cl.bairro_origem:
            cont_bairros_origem[(cl.bairro_origem or '').strip()] += 1

    ranking_bairros_origem = [
        {"bairro": (b or 'Não informado'), "qtd": q}
        for b, q in cont_bairros_origem.most_common()
    ]

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

# =========================================================
# EXPORTAÇÃO ESTATÍSTICAS (MASTER)
# =========================================================
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
            query = query.filter(
                (Entrega.status_pagamento == None) |
                (func.lower(Entrega.status_pagamento) == 'pendente')
            )
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
        ws.merge_range(
            0, 0, 0, last_col, titulo,
            writer.book.add_format({
                'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
                'font_color': '#003399'
            })
        )

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

# =========================================================
# EXPORTAR / IMPORTAR CLIENTES
# =========================================================
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
                detalhes.append(
                    f"Linha {i+2}: telefone inválido '{tel}' (esperado 10 ou 11 dígitos)."
                )
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
        return jsonify(
            ok=True,
            adicionados=adicionados,
            atualizados=atualizados,
            erros=erros,
            detalhes=detalhes
        )
    else:
        flash(
            f'Importação concluída: {adicionados} adicionados, '
            f'{atualizados} atualizados, {erros} erros.'
        )
        return redirect(url_for('clientes'))

# =========================================================
# FILA DE ESPERA
# =========================================================
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
        item = ListaEspera(
            cooperado_id=coop.id,
            nome=coop.nome,
            pos=max_pos + 1,
            created_at=datetime.utcnow()
        )
        db.session.add(item)
        db.session.commit()
        flash('Cooperado adicionado à lista de espera.')
        return redirect_back_to_admin()

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

# =========================================================
# RELATÓRIO TÉRMICO
# =========================================================
@app.route('/relatorio_termico')
def relatorio_termico():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    status_pagamento = request.args.get('status_pagamento', 'todos')
    cliente = (request.args.get('cliente') or '').strip()

    if data_inicio:
        di_date = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di_date)
    else:
        hoje = datetime.now(BRAZIL_TZ).date()
        inicio_utc, _ = local_date_window_to_utc_range(hoje)

    if data_fim:
        df_date = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df_date)
    else:
        base = datetime.strptime(data_inicio, "%Y-%m-%d").date() if data_inicio else datetime.now(BRAZIL_TZ).date()
        _, fim_utc = local_date_window_to_utc_range(base)

    q = Entrega.query

    if cooperado_id and cooperado_id != 'todos':
        try:
            q = q.filter(Entrega.cooperado_id == int(cooperado_id))
        except Exception:
            pass

    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            q = q.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            q = q.filter(
                (Entrega.status_pagamento == None) |
                (func.lower(Entrega.status_pagamento) == 'pendente')
            )

    if cliente:
        like = f"%{cliente.lower()}%"
        q = q.filter(func.lower(Entrega.cliente).like(like))

    coalesce_dt = func.coalesce(Entrega.data_atribuida, Entrega.data_envio)
    q = q.filter(coalesce_dt >= inicio_utc, coalesce_dt <= fim_utc).order_by(
        coalesce_dt.asc(),
        Entrega.cliente.asc()
    )

    entregas = q.options(joinedload(Entrega.cooperado)).all()

    periodo_txt = periodo_legivel_str(data_inicio, data_fim)

    coop_nome = "Todos"
    if cooperado_id and cooperado_id != "todos":
        coop = Cooperado.query.get(int(cooperado_id))
        if coop:
            coop_nome = coop.nome

    total_relatorio = sum(float(e.valor or 0) for e in entregas)
    agora = datetime.now(BRAZIL_TZ)

    return render_template(
        'relatorio_termico.html',
        entregas=entregas,
        periodo_txt=periodo_txt,
        coop_nome=coop_nome,
        agora=agora,
        to_brasilia=to_brasilia,
        total_relatorio=total_relatorio
    )

# =========================================================
# BOOTSTRAP BANCO / DDL / ÍNDICES / BACKFILL
# =========================================================
def criar_bd():
    with app.app_context():
        db.create_all()

        try:
            db.session.execute(text("PRAGMA foreign_keys = ON"))
        except Exception:
            pass

        ddl_cmds = [
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS cooperado_id INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS pos INTEGER",
            "ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",

            "ALTER TABLE cliente ADD COLUMN IF NOT EXISTS endereco VARCHAR(255)",
            "ALTER TABLE cliente ADD COLUMN IF NOT EXISTS saldo_atual REAL DEFAULT 0",
            "ALTER TABLE cliente ADD COLUMN IF NOT EXISTS username VARCHAR(80)",
            "ALTER TABLE cliente ADD COLUMN IF NOT EXISTS senha_hash VARCHAR(128)",

            "ALTER TABLE entrega ADD COLUMN IF NOT EXISTS credito_usado REAL DEFAULT 0",
            "ALTER TABLE entrega ADD COLUMN IF NOT EXISTS credito_mov_id INTEGER",
            "ALTER TABLE entrega ADD COLUMN IF NOT EXISTS cliente_id INTEGER",

            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS desconto_tipo VARCHAR(20) DEFAULT 'nenhum'",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS desconto_valor REAL DEFAULT 0",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS valor_final REAL",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS motivo VARCHAR(180)",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS saldo_antes REAL DEFAULT 0",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS saldo_depois REAL DEFAULT 0",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS criado_por VARCHAR(80)",
            "ALTER TABLE credito ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP",

            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS tipo VARCHAR(10)",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS valor REAL",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS referencia VARCHAR(120)",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS credito_id INTEGER",
            "ALTER TABLE credito_movimento ADD COLUMN IF NOT EXISTS entrega_id INTEGER",
        ]
        for s in ddl_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        fk_cmds_create_if_missing = [
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='lista_espera_cooperado_id_fkey') THEN "
                "ALTER TABLE lista_espera ADD CONSTRAINT lista_espera_cooperado_id_fkey "
                "FOREIGN KEY (cooperado_id) REFERENCES cooperado(id) ON DELETE SET NULL; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='entrega_cooperado_id_fkey') THEN "
                "ALTER TABLE entrega ADD CONSTRAINT entrega_cooperado_id_fkey "
                "FOREIGN KEY (cooperado_id) REFERENCES cooperado(id) ON DELETE SET NULL; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='entrega_cliente_id_fkey') THEN "
                "ALTER TABLE entrega ADD CONSTRAINT entrega_cliente_id_fkey "
                "FOREIGN KEY (cliente_id) REFERENCES cliente(id) ON DELETE SET NULL; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='credito_cliente_id_fkey') THEN "
                "ALTER TABLE credito ADD CONSTRAINT credito_cliente_id_fkey "
                "FOREIGN KEY (cliente_id) REFERENCES cliente(id) ON DELETE CASCADE; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='credito_movimento_cliente_id_fkey') THEN "
                "ALTER TABLE credito_movimento ADD CONSTRAINT credito_movimento_cliente_id_fkey "
                "FOREIGN KEY (cliente_id) REFERENCES cliente(id) ON DELETE CASCADE; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='credito_movimento_credito_id_fkey') THEN "
                "ALTER TABLE credito_movimento ADD CONSTRAINT credito_movimento_credito_id_fkey "
                "FOREIGN KEY (credito_id) REFERENCES credito(id) ON DELETE SET NULL; "
                "END IF; END $$;"
            ),
            (
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name='credito_movimento_entrega_id_fkey') THEN "
                "ALTER TABLE credito_movimento ADD CONSTRAINT credito_movimento_entrega_id_fkey "
                "FOREIGN KEY (entrega_id) REFERENCES entrega(id) ON DELETE SET NULL; "
                "END IF; END $$;"
            ),
        ]
        for s in fk_cmds_create_if_missing:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        fix_fk_cmd = (
            "DO $$ DECLARE del CHAR; BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='credito_movimento_entrega_id_fkey') THEN "
            "  SELECT c.confdeltype INTO del FROM pg_constraint c WHERE c.conname='credito_movimento_entrega_id_fkey'; "
            "  IF del IS DISTINCT FROM 'n' THEN "
            "    ALTER TABLE credito_movimento DROP CONSTRAINT credito_movimento_entrega_id_fkey; "
            "    ALTER TABLE credito_movimento ADD CONSTRAINT credito_movimento_entrega_id_fkey "
            "    FOREIGN KEY (entrega_id) REFERENCES entrega(id) ON DELETE SET NULL; "
            "  END IF; "
            "END IF; "
            "END $$;"
        )
        try:
            db.session.execute(text(fix_fk_cmd))
        except Exception:
            pass

        idx_cmds = [
            "CREATE INDEX IF NOT EXISTS idx_entrega_data_envio ON entrega (data_envio DESC)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cooperado_id ON entrega (cooperado_id)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cliente_id ON entrega (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_entrega_status_pagamento_lower ON entrega ((lower(status_pagamento)))",
            "CREATE INDEX IF NOT EXISTS idx_entrega_cliente_lower ON entrega ((lower(cliente)))",

            "CREATE INDEX IF NOT EXISTS idx_lista_espera_pos ON lista_espera (pos ASC)",

            "CREATE INDEX IF NOT EXISTS idx_cliente_nome_lower ON cliente ((lower(nome)))",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cliente_username ON cliente (username)",

            "CREATE INDEX IF NOT EXISTS idx_credito_cliente_id ON credito (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_credito_criado_em ON credito (criado_em DESC)",

            "CREATE INDEX IF NOT EXISTS idx_credmov_cliente_id ON credito_movimento (cliente_id)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_entrega_id ON credito_movimento (entrega_id)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_criado_em ON credito_movimento (criado_em DESC)",
            "CREATE INDEX IF NOT EXISTS idx_credmov_tipo ON credito_movimento (tipo)",
        ]
        for s in idx_cmds:
            try:
                db.session.execute(text(s))
            except Exception:
                pass

        try:
            pend = (
                Entrega.query
                .filter((Entrega.cliente_id == None) | (Entrega.cliente_id.is_(None)))
                .limit(5000)
                .all()
            )
            if pend:
                nomes = {(e.cliente or '').strip().lower() for e in pend if (e.cliente or '').strip()}
                if nomes:
                    mapa = {
                        c.nome.strip().lower(): c.id
                        for c in Cliente.query.filter(
                            func.lower(Cliente.nome).in_(list(nomes))
                        ).all()
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


criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
