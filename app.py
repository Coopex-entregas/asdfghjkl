import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
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
    # Armazenamos em UTC "naive" (sem tzinfo) por consistência com datetime.utcnow()
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)  # "Pago" ou "Pendente"
    status = db.Column(db.String(20), nullable=True)            # "recebido"/"pendente"
    pagamento = db.Column(db.String(50), nullable=False)        # Forma de pagamento obrigatória
    recebido_por = db.Column(db.String(100), nullable=True)     # Usado no editar por cooperado

    cooperado = db.relationship('Cooperado', backref='entregas')

# ====== NOVO MODEL PARA FILA DE ESPERA ======
class ListaEspera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)

# ====== Funções auxiliares de data/hora ======
def to_brasilia(dt):
    """Converte um datetime salvo como UTC naive para America/Sao_Paulo para exibição."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(BRAZIL_TZ)

def local_date_window_to_utc_range(local_date: date):
    """Para uma data no fuso Brasil, devolve (inicio_utc_naive, fim_utc_naive) em UTC naive."""
    inicio_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.min))
    fim_brasil = BRAZIL_TZ.localize(datetime.combine(local_date, time.max))
    inicio_utc = inicio_brasil.astimezone(pytz.utc).replace(tzinfo=None)
    fim_utc = fim_brasil.astimezone(pytz.utc).replace(tzinfo=None)
    return inicio_utc, fim_utc

def parse_local_datetime_to_utc_naive(data_str: str):
    """Converte 'YYYY-MM-DDTHH:MM' (datetime-local) assumindo Brasil para UTC naive."""
    dt_local_naive = datetime.strptime(data_str, '%Y-%m-%dT%H:%M')
    dt_local = BRAZIL_TZ.localize(dt_local_naive)
    dt_utc = dt_local.astimezone(pytz.utc)
    return dt_utc.replace(tzinfo=None)

# ====== Filtro Jinja para dia da semana ======
def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

# ====== Feriados (Nacional + RN + Municipal Natal) ======
MUNICIPAIS_NATAL = {
    # 21/11 – Padroeira de Natal
    (11, 21): "Nossa Senhora da Apresentação (Municipal - Natal/RN)",
}

def verifica_feriado(data_ref=None):
    """Retorna string com nomes de feriados aplicáveis para Natal/RN, ou None se não houver."""
    if data_ref is None:
        data_ref = datetime.now(BRAZIL_TZ).date()

    feriados_nac = holidays.Brazil(years=data_ref.year)                     # Nacional
    feriados_est = holidays.Brazil(state='RN', years=data_ref.year)         # Estadual RN

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
    """Formata o período para título/cabeçalho."""
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

    # Filtro por dia atual (Brasil) se não há filtros de data
    if not data_inicio and not data_fim:
        hoje_brasil = datetime.now(BRAZIL_TZ).date()
        inicio_utc, fim_utc = local_date_window_to_utc_range(hoje_brasil)
        query = query.filter(Entrega.data_envio >= inicio_utc, Entrega.data_envio <= fim_utc)

    # Cooperado
    if cooperado_id and cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    # Datas (Brasil → UTC naive)
    if data_inicio:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        inicio_utc, _ = local_date_window_to_utc_range(di)
        query = query.filter(Entrega.data_envio >= inicio_utc)
    if data_fim:
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
        _, fim_utc = local_date_window_to_utc_range(df)
        query = query.filter(Entrega.data_envio <= fim_utc)

    # Status pagamento
    if status_pagamento and status_pagamento != 'todos':
        if status_pagamento == 'pago':
            query = query.filter(func.lower(Entrega.status_pagamento) == 'pago')
        elif status_pagamento == 'pendente':
            query = query.filter((Entrega.status_pagamento == None) | (func.lower(Entrega.status_pagamento) == 'pendente'))

    # Cliente (parcial, case-insensitive)
    if cliente:
        like = f"%{cliente.lower()}%"
        query = query.filter(func.lower(Entrega.cliente).like(like))

    entregas_all = query.order_by(Entrega.data_envio.desc()).all()
    nao_atribuidos = [e for e in entregas_all if not e.cooperado_id]
    atribuidos = [e for e in entregas_all if e.cooperado_id]
    entregas = nao_atribuidos + atribuidos

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    # Estatísticas do dia Brasil
    hoje = datetime.now(BRAZIL_TZ).date()
    inicio_dia_utc, fim_dia_utc = local_date_window_to_utc_range(hoje)
    total_dia = Entrega.query.filter(
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc
    ).count()
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
        Entrega.data_envio >= inicio_dia_utc,
        Entrega.data_envio <= fim_dia_utc,
        (Entrega.status_pagamento == None) | (Entrega.status_pagamento.ilike('pendente'))
    ).count() > 0

    # Lista de espera
    lista_espera = ListaEspera.query.order_by(ListaEspera.id).all()

    return render_template('admin.html', entregas=entregas, cooperados=cooperados,
                           estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim,
                           to_brasilia=to_brasilia, request=request, now=lambda: datetime.now(BRAZIL_TZ),
                           feriado_hoje=feriado_hoje, tem_pendente=tem_pendente,
                           lista_espera=lista_espera)

@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('user_id') is None or session.get('is_admin'):
        return redirect(url_for('login'))
    user_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == user_id)

    # Sempre mostra o dia Brasil (salvo filtros)
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
            data_envio=datetime.utcnow(),  # UTC naive
            status_pagamento='Pendente',
            status='pendente',
            pagamento=pagamento
        )
        if cooperado_id:
            entrega.cooperado_id = int(cooperado_id)
            entrega.data_atribuida = datetime.utcnow()  # UTC naive
        db.session.add(entrega)
        db.session.commit()
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

        # Converte hora local Brasil do input para UTC naive
        data_envio = parse_local_datetime_to_utc_naive(data_str)

        entrega = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            data_envio=data_envio,
            cooperado_id=int(cooperado_id) if cooperado_id else None,
            status=status_entrega,
            status_pagamento=status_pagamento,
            pagamento=pagamento
        )
        db.session.add(entrega)
        db.session.commit()
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
                    entrega.data_atribuida = datetime.utcnow()  # UTC naive
            else:
                entrega.cooperado_id = None

            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.recebido_por = request.form.get('recebido_por')
            # >>> Atualiza a forma de pagamento
            entrega.pagamento = request.form.get('pagamento')

            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('admin'))
        else:
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status') or entrega.status
            entrega.recebido_por = request.form.get('recebido_por')
            # Se quiser permitir que o cooperado altere a forma de pagamento, descomente:
            # entrega.pagamento = request.form.get('pagamento') or entrega.pagamento
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
    db.session.delete(c)
    db.session.commit()
    flash('Cooperado excluído.')
    return redirect(url_for('admin'))

# ====== ESTATÍSTICAS (AGORA COMPLETAS – GRÁFICOS/RANKINGS) ======
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

    # KPIs básicos
    total = len(entregas)
    pagas = len([e for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago'])
    pendentes = total - pagas
    total_valor = sum(e.valor for e in entregas)

    # Ticket médio
    ticket_medio = (total_valor / total) if total > 0 else 0.0

    # Dia com mais entregas (em data local Brasil)
    cont_dias = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_dias[dt_local.date()] += 1
    dia_top = None
    if cont_dias:
        d, qtd = cont_dias.most_common(1)[0]
        nome = f"{d.strftime('%d/%m/%Y')} ({qtd})"
        dia_top = {"data": d.strftime('%Y-%m-%d'), "qtd": qtd, "nome": nome}
    else:
        dia_top = {"data": None, "qtd": 0, "nome": "-"}

    # Horário de pico (hora)
    cont_horas = Counter()
    for e in entregas:
        dt_local = to_brasilia(e.data_envio)
        if dt_local:
            cont_horas[dt_local.strftime('%H:00')] += 1
    hora_pico = cont_horas.most_common(1)[0][0] if cont_horas else "-"

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

    # Ranking bairros (por quantidade)
    cont_bairros = Counter([e.bairro for e in entregas if e.bairro])
    ranking_bairros = [{"bairro": b, "qtd": q} for b, q in cont_bairros.most_common()]

    # Ranking formas pgto (por quantidade)
    ranking_pgto = [{"forma": f, "qtd": q} for f, q in cont_pgto.most_common()]

    # Séries para gráficos
    # Entregas por dia
    # Ordena por data local
    dias_ordenados = sorted(list(cont_dias.keys()))
    chart_entregas_labels = [d.strftime("%d/%m") for d in dias_ordenados]
    chart_entregas_values = [cont_dias[d] for d in dias_ordenados]

    # Faturamento por cooperado (usa ranking_cooperados)
    chart_faturamento_labels = [r["nome"] for r in ranking_cooperados]
    chart_faturamento_values = [r["total_valor"] for r in ranking_cooperados]

    # Período legível para títulos/export
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
        chart_entregas_labels=chart_entregas_labels,
        chart_entregas_values=chart_entregas_values,
        chart_faturamento_labels=chart_faturamento_labels,
        chart_faturamento_values=chart_faturamento_values,
        periodo_legivel=periodo_legivel
    )

# ====== EXPORTAÇÃO (uma única aba + Cooperado + demais dados detalhados) ======
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

    # Uma ÚNICA aba com Cooperado + dados detalhados
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

# ====== EXPORTAÇÃO RESUMO COOPERADO × VALOR (título com período) ======
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

    # agrega por cooperado
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
    # Ordena por valor
    linhas.sort(key=lambda r: r["Valor Total (R$)"], reverse=True)

    df = pd.DataFrame(linhas)

    titulo = f"Faturamento dos cooperados do período ({periodo_legivel_str(data_inicio, data_fim)})"

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        sheet = 'Resumo'
        # Reservar linha 1 para o título
        start_row = 1
        df.to_excel(writer, index=False, sheet_name=sheet, startrow=start_row)
        ws = writer.sheets[sheet]

        # Título mesclado sobre as colunas do df
        last_col = len(df.columns) - 1
        ws.merge_range(0, 0, 0, last_col, titulo, writer.book.add_format({
            'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
            'font_color': '#003399'
        }))

        # Larguras amigáveis
        widths = [28, 14, 18, 12]
        for i, w in enumerate(widths[:len(df.columns)]):
            ws.set_column(i, i, w)

        # Formatação numérica
        money_fmt = writer.book.add_format({'num_format': '#,##0.00'})
        pct_fmt = writer.book.add_format({'num_format': '0.0"%"'})
        # Localiza colunas por nome
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
    nome = request.form.get('nome')
    if not nome or nome.strip() == '':
        flash('Nome para fila de espera é obrigatório.')
        return redirect(url_for('admin'))
    novo = ListaEspera(nome=nome.strip())
    db.session.add(novo)
    db.session.commit()
    flash('Nome adicionado à lista de espera.')
    return redirect(url_for('admin'))

@app.route('/lista_espera/remove/<int:id>', methods=['POST'])
def lista_espera_remove(id):
    item = ListaEspera.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Nome removido da lista de espera.')
    return redirect(url_for('admin'))

def criar_bd():
    with app.app_context():
        db.create_all()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
