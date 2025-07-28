import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import pandas as pd
import io
import holidays

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    status_pagamento = db.Column(db.String(20), nullable=True)  # "Pago" ou "Pendente"
    status = db.Column(db.String(20), nullable=True)            # "recebido"/"pendente"
    pagamento = db.Column(db.String(20), default="Dinheiro")    # Coluna pagamento adicionada
    cooperado = db.relationship('Cooperado', backref='entregas')

# ====== Função auxiliar ======
def to_brasilia(dt):
    if not dt:
        return None
    return dt - timedelta(hours=3)  # UTC-3 fixo

# ====== Filtro Jinja para dia da semana ======
def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

# ====== Função para verificar feriados ======
def verifica_feriado(data=None):
    if data is None:
        data = datetime.utcnow().date()
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
    if feriados_hoje:
        return " | ".join(feriados_hoje)
    else:
        return None

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
    query = Entrega.query
    if not data_inicio and not data_fim:
        hoje = datetime.utcnow().date()
        query = query.filter(func.date(Entrega.data_envio) == hoje)
    if cooperado_id and cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        query = query.filter(Entrega.data_envio >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(Entrega.data_envio <= datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
    entregas = query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    hoje = datetime.utcnow().date()
    total_dia = Entrega.query.filter(func.date(Entrega.data_envio) == hoje).count()
    total_mes = Entrega.query.filter(func.extract('month', Entrega.data_envio) == hoje.month,
                                     func.extract('year', Entrega.data_envio) == hoje.year).count()
    total_ano = Entrega.query.filter(func.extract('year', Entrega.data_envio) == hoje.year).count()
    estatisticas = {"total_dia": total_dia, "total_mes": total_mes, "total_ano": total_ano}
    feriado_hoje = verifica_feriado(hoje)
    tem_pendente = Entrega.query.filter(
        func.date(Entrega.data_envio) == hoje,
        (Entrega.status_pagamento == None) | (Entrega.status_pagamento.ilike('pendente'))
    ).count() > 0
    return render_template('admin.html', entregas=entregas, cooperados=cooperados,
                           estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim,
                           to_brasilia=to_brasilia, request=request, now=datetime.utcnow,
                           feriado_hoje=feriado_hoje, tem_pendente=tem_pendente)

@app.route('/painel_cooperado')
def painel_cooperado():
    if session.get('user_id') is None or session.get('is_admin'):
        return redirect(url_for('login'))
    user_id = session['user_id']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == user_id)
    if inicio:
        query = query.filter(Entrega.data_envio >= datetime.strptime(inicio, "%Y-%m-%d"))
    if fim:
        query = query.filter(Entrega.data_envio <= datetime.strptime(fim, "%Y-%m-%d") + timedelta(days=1))
    entregas = query.order_by(Entrega.data_envio.desc()).all()
    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago')
    total_pendente = total_geral - total_pago
    return render_template('painel_cooperado.html', entregas=entregas, total_geral=total_geral,
                           total_pago=total_pago, total_pendente=total_pendente, request=request)

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
        pagamento = request.form.get('pagamento', 'Dinheiro')
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
        flash('Entrega cadastrada!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/agendar_entrega', methods=['GET', 'POST'])
def agendar_entrega():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        cliente = request.form.get('cliente')
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor'))
        data_str = request.form.get('data')
        status_entrega = request.form.get('status_entrega')
        status_pagamento = request.form.get('status_pagamento')
        pagamento = request.form.get('pagamento', 'Dinheiro')
        data_envio = datetime.strptime(data_str, '%Y-%m-%dT%H:%M')
        entrega = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            data_envio=data_envio,
            cooperado_id=None,
            status=status_entrega,
            status_pagamento=status_pagamento,
            pagamento=pagamento
        )
        db.session.add(entrega)
        db.session.commit()
        flash('Entrega agendada!')
        return redirect(url_for('admin'))
    return render_template('agendar_entrega.html')

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
            else:
                entrega.cooperado_id = None
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.pagamento = request.form.get('pagamento', entrega.pagamento)
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('admin'))
        else:
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status_entrega') or entrega.status
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

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    query = Entrega.query
    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        query = query.filter(Entrega.data_envio >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(Entrega.data_envio <= datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
    entregas = query.all()
    estatisticas = {
        "total": len(entregas),
        "pagas": len([e for e in entregas if e.status_pagamento and e.status_pagamento.lower() == 'pago']),
        "pendentes": len([e for e in entregas if not e.status_pagamento or e.status_pagamento.lower() != 'pago']),
        "total_valor": sum(e.valor for e in entregas)
    }
    return render_template('estatisticas_cooperado.html', cooperados=cooperados,
                           cooperado_id=cooperado_id, data_inicio=data_inicio, data_fim=data_fim,
                           estatisticas=estatisticas)

@app.route('/exportar_xlsx')
def exportar_xlsx():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    cooperado_id = request.args.get('cooperado_id', 'todos')
    query = Entrega.query
    if cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        query = query.filter(Entrega.data_envio >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(Entrega.data_envio <= datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
    entregas = query.all()
    cooperados = {c.id: c.nome for c in Cooperado.query.all()}

    dados = {}
    for e in entregas:
        nome = cooperados.get(e.cooperado_id, 'Sem Cooperado')
        if nome not in dados:
            dados[nome] = []
        dados[nome].append({
            'Data do Pedido': to_brasilia(e.data_envio).strftime('%d/%m/%Y') if e.data_envio else '',
            'Hora do Pedido': to_brasilia(e.data_envio).strftime('%H:%M') if e.data_envio else '',
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Hora Atribuída': to_brasilia(e.data_atribuida).strftime('%H:%M') if e.data_atribuida else '',
            'Valor': e.valor,
            'Status Pagamento': e.status_pagamento,
            'Status da Entrega': e.status,
            'Pagamento': e.pagamento
        })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for coop, entregas_list in dados.items():
            df = pd.DataFrame(entregas_list)
            df.to_excel(writer, index=False, sheet_name=str(coop)[:31])
    output.seek(0)
    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

def criar_bd():
    with app.app_context():
        db.create_all()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
