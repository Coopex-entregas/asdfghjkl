import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, time, date
import pandas as pd
import io
import holidays
import pytz
from collections import Counter, defaultdict

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

# FILA DE ESPERA — agora com coluna cooperado_id e ordenação (pos)
class ListaEspera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)  # pode existir legado sem preencher
    pos = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    cooperado = db.relationship('Cooperado')

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

# ====== util: garantir colunas da fila de espera no banco (sem Alembic) ======
def ensure_lista_espera_schema():
    try:
        db.session.execute(text("CREATE TABLE IF NOT EXISTS lista_espera (id SERIAL PRIMARY KEY)"))
        db.session.execute(text("ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS cooperado_id INTEGER"))
        db.session.execute(text("ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS pos INTEGER NOT NULL DEFAULT 0"))
        db.session.execute(text("ALTER TABLE lista_espera ADD COLUMN IF NOT EXISTS created_at TIMESTAMP"))
        # tenta vincular por nome legado (se existir coluna nome)
        try:
            db.session.execute(text("""
                UPDATE lista_espera le
                SET cooperado_id = c.id
                FROM cooperado c
                WHERE le.cooperado_id IS NULL
                  AND EXISTS (SELECT 1 FROM information_schema.columns
                              WHERE table_name='lista_espera' AND column_name='nome')
                  AND lower(c.nome) = lower(COALESCE((SELECT le.nome), ''))
            """))
        except Exception:
            pass
        # preenche pos
        db.session.execute(text("""
            WITH numbered AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY pos, created_at, id) AS rn
                FROM lista_espera
            )
            UPDATE lista_espera le
            SET pos = numbered.rn
            FROM numbered
            WHERE numbered.id = le.id AND (le.pos IS NULL OR le.pos = 0);
        """))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("ensure_lista_espera_schema error:", e)

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

    entregas_all = query.order_by(Entrega.data_envio.desc()).all()
    nao_atribuidos = [e for e in entregas_all if not e.cooperado_id]
    atribuidos = [e for e in entregas_all if e.cooperado_id]
    entregas = nao_atribuidos + atribuidos

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    hoje = datetime.now(BRAZIL_TZ).date()
    inicio_dia_utc, fim_dia_utc = local_date_window_to_utc_range(hoje)
    total_dia = Entrega.query.filter(Entrega.data_envio >= inicio_dia_utc, Entrega.data_envio <= fim_dia_utc).count()
    total_mes = Entrega.query.filter(func.extract('month', Entrega.data_envio) == hoje.month,
                                     func.extract('year', Entrega.data_envio) == hoje.year).count()
    total_ano = Entrega.query.filter(func.extract('year', Entrega.data_envio) == hoje.year).count()
    estatisticas = {"total_dia": total_dia, "total_mes": total_mes, "total_ano": total_ano}

    feriado_hoje = verifica_feriado(hoje)
    tem_pendente = Entrega.query.filter(
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc,
        (Entrega.status_pagamento == None) | (Entrega.status_pagamento.ilike('pendente'))
    ).count() > 0

    # FILA DE ESPERA: carrega ordenado
    ensure_lista_espera_schema()
    lista_raw = db.session.execute(text("""
        SELECT le.id, le.cooperado_id, le.pos, co.nome
        FROM lista_espera le
        JOIN cooperado co ON co.id = le.cooperado_id
        ORDER BY le.pos ASC, le.created_at ASC, le.id ASC
    """)).fetchall()
    lista_espera = [{"id": r.id, "cooperado_id": r.cooperado_id, "pos": r.pos, "nome": r.nome} for r in lista_raw]

    # cooperados disponiveis para adicionar na fila (não repetidos)
    ids_fila = {r["cooperado_id"] for r in lista_espera}
    cooperados_disponiveis = [c for c in cooperados if c.id not in ids_fila]

    return render_template('admin.html',
                           entregas=entregas, cooperados=cooperados,
                           estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim,
                           to_brasilia=to_brasilia, request=request, now=lambda: datetime.now(BRAZIL_TZ),
                           feriado_hoje=feriado_hoje, tem_pendente=tem_pendente,
                           lista_espera=lista_espera, cooperados_disponiveis=cooperados_disponiveis)

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
    return redirect(url_for('admin'))

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

    entregas = query.order_by(Entrega.data_envio.desc()).all()
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
        if Cooperado.query.filter(func.lower(Cooperado.nome) == nome.lower()).first():
            flash('Já existe um cooperado com esse nome.')
        else:
            c = Cooperado(nome=nome)
            c.set_senha(senha)
            db.session.add(c)
            db.session.commit()
            flash('Cooperado cadastrado!')
            return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

# ===== util: remover cooperado da fila (usado em criar/editar entrega) =====
def remover_da_fila_por_cooperado(cooperado_id: int):
    try:
        db.session.execute(text("DELETE FROM lista_espera WHERE cooperado_id = :cid"), {"cid": cooperado_id})
        db.session.commit()
    except Exception:
        db.session.rollback()

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
        db.session.commit()

        # se atribuiu cooperado, tira automaticamente da fila
        if cooperado_id:
            remover_da_fila_por_cooperado(int(cooperado_id))

        flash('Entrega cadastrada!')
        return redirect(url_for('admin'))
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
        db.session.commit()

        if cooperado_id:
            remover_da_fila_por_cooperado(int(cooperado_id))

        flash('Entrega agendada!')
        return redirect(url_for('admin'))
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
                    db.session.commit()
                    # remove da fila ao atribuir
                    remover_da_fila_por_cooperado(novo_coop_id)
                else:
                    db.session.commit()
            else:
                entrega.cooperado_id = None
                db.session.commit()

            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.recebido_por = request.form.get('recebido_por')
            entrega.pagamento = request.form.get('pagamento')

            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('admin'))
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
    return redirect(url_for('admin'))

@app.route('/excluir_cooperado/<int:id>', methods=['POST'])
def excluir_cooperado(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    c = Cooperado.query.get_or_404(id)
    Entrega.query.filter_by(cooperado_id=c.id).delete()
    # também limpa se estiver na fila
    remover_da_fila_por_cooperado(c.id)
    db.session.delete(c)
    db.session.commit()
    flash('Cooperado excluído.')
    return redirect(url_for('admin'))

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

    entregas = query.order_by(Entrega.data_envio.asc()).all()

    total = len(entregas)
    pagas = len([e for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago'])
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

    # Ranking clientes (AGORA com total e ticket médio)
    mapa_clientes = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for e in entregas:
        if e.cliente:
            mapa_clientes[e.cliente]["qtd"] += 1
            mapa_clientes[e.cliente]["total"] += float(e.valor or 0)
    ranking_clientes = []
    for cli, d in mapa_clientes.items():
        tkt = (d["total"] / d["qtd"]) if d["qtd"] else 0.0
        ranking_clientes.append({
            "cliente": cli,
            "qtd": d["qtd"],
            "total": round(d["total"], 2),
            "ticket": round(tkt, 2)
        })
    ranking_clientes.sort(key=lambda x: x["total"], reverse=True)

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
        ranking_clientes=ranking_clientes,          # com total/ticket
        horas_pico_top3=horas_pico_top3,
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
    status_pagamento = request.args.get('status_pagamento')

    query = Entrega.query

    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

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
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    ensure_lista_espera_schema()

    # aceita cooperado_id (preferencial) ou nome (legado)
    cooperado_id = request.form.get('cooperado_id')
    nome = (request.form.get('nome') or '').strip()

    if not cooperado_id:
        if not nome:
            flash('Informe um cooperado para adicionar na fila.')
            return redirect(url_for('admin'))
        c = Cooperado.query.filter(func.lower(Cooperado.nome) == nome.lower()).first()
        if not c:
            flash('Cooperado não encontrado.')
            return redirect(url_for('admin'))
        cooperado_id = c.id
    else:
        cooperado_id = int(cooperado_id)

    ja_existe = db.session.execute(
        text("SELECT 1 FROM lista_espera WHERE cooperado_id=:cid LIMIT 1"), {"cid": cooperado_id}
    ).fetchone()
    if ja_existe:
        flash('Este cooperado já está na fila.')
        return redirect(url_for('admin'))

    max_pos = db.session.execute(text("SELECT COALESCE(MAX(pos),0) FROM lista_espera")).scalar() or 0
    db.session.execute(
        text("INSERT INTO lista_espera (cooperado_id,pos,created_at) VALUES (:cid,:p,:dt)"),
        {"cid": cooperado_id, "p": max_pos + 1, "dt": datetime.utcnow()}
    )
    db.session.commit()
    flash('Cooperado adicionado à lista de espera.')
    return redirect(url_for('admin'))

@app.route('/lista_espera/remove/<int:id>', methods=['POST'])
def lista_espera_remove(id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    db.session.execute(text("DELETE FROM lista_espera WHERE id=:id"), {"id": id})
    db.session.commit()
    flash('Removido da lista de espera.')
    return redirect(url_for('admin'))

@app.route('/lista_espera/reordenar', methods=['POST'])
def lista_espera_reordenar():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    # aceita JSON {"ordem":[ids]} ou form ordem="1,3,2"
    ordem = request.json.get('ordem') if request.is_json else None
    if not ordem:
        raw = request.form.get('ordem') or ''
        ordem = [int(x) for x in raw.split(',') if x.strip().isdigit()]
    if not ordem:
        return jsonify({"ok": False, "msg": "ordem vazia"}), 400

    for idx, le_id in enumerate(ordem, start=1):
        db.session.execute(text("UPDATE lista_espera SET pos=:p WHERE id=:id"), {"p": idx, "id": le_id})
    db.session.commit()
    return jsonify({"ok": True})

def criar_bd():
    with app.app_context():
        db.create_all()
        ensure_lista_espera_schema()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
