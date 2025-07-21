import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import pandas as pd
import io

# Configuração
app = Flask(__name__)
app.secret_key = 'supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELS
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

    cooperado = db.relationship('Cooperado', backref='entregas')

# Função auxiliar para timezone Brasilia
def to_brasilia(dt):
    if not dt:
        return None
    return dt - timedelta(hours=3)  # UTC-3 fixo

# ROTAS

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    # NÃO limpar a sessão aqui!
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
    if cooperado_id and cooperado_id != 'todos':
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))
    if data_inicio:
        query = query.filter(Entrega.data_envio >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(Entrega.data_envio <= datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
    entregas = query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    # Estatísticas simples
    hoje = datetime.utcnow().date()
    total_dia = Entrega.query.filter(func.date(Entrega.data_envio) == hoje).count()
    total_mes = Entrega.query.filter(func.extract('month', Entrega.data_envio) == hoje.month,
                                     func.extract('year', Entrega.data_envio) == hoje.year).count()
    total_ano = Entrega.query.filter(func.extract('year', Entrega.data_envio) == hoje.year).count()

    estatisticas = {
        "total_dia": total_dia,
        "total_mes": total_mes,
        "total_ano": total_ano
    }
    return render_template('admin.html', entregas=entregas, cooperados=cooperados,
                           estatisticas=estatisticas, data_inicio=data_inicio, data_fim=data_fim, to_brasilia=to_brasilia)

@app.route('/painel_cooperado')
def painel_cooperado():
    if not session.get('user_id') or session.get('is_admin'):
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
        entrega = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            data_envio=datetime.utcnow(),
            status_pagamento='Pendente',
            status='pendente'
        )
        if cooperado_id:
            entrega.cooperado_id = int(cooperado_id)
            entrega.data_atribuida = datetime.utcnow()
        db.session.add(entrega)
        db.session.commit()
        flash('Entrega cadastrada!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    # Checagem de permissão: admin ou cooperado dono
    if not session.get('user_id'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin')
    if not is_admin and entrega.cooperado_id != session['user_id']:
        flash("Acesso não permitido.")
        return redirect(url_for('painel_cooperado'))

    if request.method == 'POST':
        # Admin pode alterar tudo
        if is_admin:
            entrega.cliente = request.form.get('cliente')
            entrega.bairro = request.form.get('bairro')
            entrega.valor = float(request.form.get('valor'))
            coop_id = request.form.get('cooperado_id')
            entrega.cooperado_id = int(coop_id) if coop_id else None
            if coop_id and not entrega.data_atribuida:
                entrega.data_atribuida = datetime.utcnow()
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('admin'))
        # Cooperado só pode marcar status/pagamento
        else:
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status_entrega') or entrega.status
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('painel_cooperado'))

    # Renderização correta: tela diferente se admin ou cooperado
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
    # Remove entregas antes
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

    # Organiza dados para Excel: uma aba por cooperado
    dados = {}
    for e in entregas:
        nome = cooperados.get(e.cooperado_id, 'Sem Cooperado')
        if nome not in dados:
            dados[nome] = []
        tempo = ""
        if e.data_envio and e.data_atribuida:
            tempo = str(e.data_atribuida - e.data_envio).split(".")[0]
        dados[nome].append({
            'ID': e.id,
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Valor': e.valor,
            'Data Pedido': to_brasilia(e.data_envio).strftime('%d/%m/%Y %H:%M') if e.data_envio else '',
            'Data Atribuída': to_brasilia(e.data_atribuida).strftime('%d/%m/%Y %H:%M') if e.data_atribuida else '',
            'Tempo até atribuição': tempo,
            'Status Pgto': e.status_pagamento,
            'Status Entrega': e.status
        })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for coop, entregas_list in dados.items():
            df = pd.DataFrame(entregas_list)
            df.to_excel(writer, index=False, sheet_name=str(coop)[:31])  # Sheet name max 31 chars
    output.seek(0)
    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

# CRIAÇÃO DE TABELAS (para Flask 2.3+)
def criar_bd():
    with app.app_context():
        db.create_all()

criar_bd()

# Para Render: vai rodar o gunicorn app:app normalmente, então use só app
if __name__ == '__main__':
    app.run(debug=True)
