from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://usuario:senha@host:porta/banco'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default="admin")  # admin ou cooperado

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)  # Hora que foi atribuída ao cooperado
    data_recebido = db.Column(db.DateTime, nullable=True)   # Hora que entregou / recebeu
    status = db.Column(db.String(20), default="pendente")   # pendente ou recebido
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)

with app.app_context():
    db.create_all()

    # Usuário admin padrão
    if not Usuario.query.filter_by(nome='coopex').first():
        db.session.add(Usuario(nome='coopex', senha='05062721', tipo='admin'))
        db.session.commit()

# ROTAS

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['usuario']
        senha = request.form['senha']
        usuario = Usuario.query.filter_by(nome=nome).first()
        if usuario and usuario.senha == senha:
            session['usuario_id'] = usuario.id
            session['usuario_tipo'] = usuario.tipo
            session['user_nome'] = usuario.nome
            if usuario.tipo == "admin":
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha inválidos')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# PAINEL ADMIN

@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    
    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    query = Entrega.query

    if cooperado_id != 'todos':
        try:
            cooperado_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == cooperado_id_int)
        except:
            pass
    
    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.all()

    # Estatísticas básicas para filtro aplicado
    total_dia = sum(e.valor for e in entregas if e.data_envio.date() == datetime.utcnow().date())
    total_mes = sum(e.valor for e in entregas if e.data_envio.month == datetime.utcnow().month and e.data_envio.year == datetime.utcnow().year)
    total_ano = sum(e.valor for e in entregas if e.data_envio.year == datetime.utcnow().year)
    valores_dia = total_dia
    valores_mes = total_mes
    valores_ano = total_ano

    estatisticas = {
        'total_dia': total_dia,
        'total_mes': total_mes,
        'total_ano': total_ano,
        'valores_dia': valores_dia,
        'valores_mes': valores_mes,
        'valores_ano': valores_ano
    }

    return render_template('admin.html', entregas=entregas, cooperados=cooperados, estatisticas=estatisticas,
                           data_inicio=data_inicio, data_fim=data_fim)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        if Cooperado.query.filter_by(nome=nome).first():
            flash('Já existe um cooperado com esse nome!')
            return redirect(url_for('cadastrar_cooperado'))
        novo = Cooperado(nome=nome, senha=senha)
        db.session.add(novo)
        db.session.commit()
        flash('Cooperado cadastrado com sucesso!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = float(request.form['valor'])
        cooperado_id = request.form.get('cooperado_id')
        if cooperado_id == '':
            cooperado_id = None
        else:
            cooperado_id = int(cooperado_id)
        nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, data_envio=datetime.utcnow(), status='pendente', cooperado_id=cooperado_id, data_atribuida=datetime.utcnow() if cooperado_id else None)
        db.session.add(nova)
        db.session.commit()
        flash('Entrega cadastrada!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))

    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.all()
    user_tipo = session.get('usuario_tipo')

    if request.method == 'POST':
        if user_tipo == 'admin':
            entrega.cliente = request.form['cliente']
            entrega.bairro = request.form['bairro']
            entrega.valor = float(request.form['valor'])
            cooperado_id = request.form.get('cooperado_id')
            if cooperado_id == '' or cooperado_id is None:
                entrega.cooperado_id = None
                entrega.data_atribuida = None
            else:
                entrega.cooperado_id = int(cooperado_id)
                entrega.data_atribuida = datetime.utcnow()
            status = request.form.get('status')
            entrega.status = status
            if status == 'recebido' and not entrega.data_recebido:
                entrega.data_recebido = datetime.utcnow()
            elif status == 'pendente':
                entrega.data_recebido = None
        elif user_tipo == 'cooperado':
            # Cooperado só pode alterar status pagamento e status entrega
            status_pagamento = request.form.get('status_pagamento')
            status_entrega = request.form.get('status_entrega')
            if status_pagamento in ['pendente', 'pago']:
                entrega.status_pagamento = status_pagamento
            if status_entrega in ['pendente', 'em rota', 'entregue']:
                entrega.status_entrega = status_entrega
            if status_entrega == 'entregue' and not entrega.data_recebido:
                entrega.data_recebido = datetime.utcnow()
            elif status_entrega != 'entregue':
                entrega.data_recebido = None
        db.session.commit()
        flash('Entrega atualizada!')
        if user_tipo == 'admin':
            return redirect(url_for('admin'))
        else:
            return redirect(url_for('painel_cooperado'))
    return render_template('editar_entrega_admin.html' if user_tipo == 'admin' else 'editar_entrega_cooperado.html',
                           entrega=entrega, cooperados=cooperados, user_tipo=user_tipo)

@app.route('/painel_cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    usuario = Usuario.query.get(session['usuario_id'])
    cooperado = Cooperado.query.filter_by(nome=usuario.nome).first()
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')

    query = Entrega.query.filter(Entrega.cooperado_id == cooperado.id)

    if inicio:
        try:
            dt_inicio = datetime.strptime(inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if fim:
        try:
            dt_fim = datetime.strptime(fim, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.data_envio.desc()).all()

    # Estatísticas
    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if getattr(e, 'status_pagamento', 'pendente') == 'pago')
    total_pendente = total_geral - total_pago

    return render_template('painel_cooperado.html', entregas=entregas, total_geral=total_geral,
                           total_pago=total_pago, total_pendente=total_pendente)

@app.route('/exportar_xlsx')
def exportar_xlsx():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))

    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    query = Entrega.query

    if cooperado_id != 'todos':
        try:
            cooperado_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == cooperado_id_int)
        except:
            pass

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            query = query.filter(Entrega.data_envio <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.data_envio.desc()).all()
    cooperados = Cooperado.query.all()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if cooperado_id != 'todos':
            # Aba única para 1 cooperado
            data = []
            for e in entregas:
                tempo = (e.data_recebido - e.data_envio).total_seconds() / 60 if e.data_recebido else None
                data.append({
                    'Cliente': e.cliente,
                    'Bairro': e.bairro,
                    'Valor': e.valor,
                    'Data Envio': e.data_envio.strftime('%d/%m/%Y %H:%M'),
                    'Data Recebido': e.data_recebido.strftime('%d/%m/%Y %H:%M') if e.data_recebido else '',
                    'Status': e.status,
                    'Tempo (min)': round(tempo, 1) if tempo else '',
                })
            df = pd.DataFrame(data)
            nome_aba = next((c.nome for c in cooperados if c.id == cooperado_id_int), 'Cooperado')
            df.to_excel(writer, sheet_name=nome_aba[:31], index=False)
        else:
            # Aba por cooperado
            for cooperado in cooperados:
                entregas_coop = [e for e in entregas if e.cooperado_id == cooperado.id]
                data = []
                for e in entregas_coop:
                    tempo = (e.data_recebido - e.data_envio).total_seconds() / 60 if e.data_recebido else None
                    data.append({
                        'Cliente': e.cliente,
                        'Bairro': e.bairro,
                        'Valor': e.valor,
                        'Data Envio': e.data_envio.strftime('%d/%m/%Y %H:%M'),
                        'Data Recebido': e.data_recebido.strftime('%d/%m/%Y %H:%M') if e.data_recebido else '',
                        'Status': e.status,
                        'Tempo (min)': round(tempo, 1) if tempo else '',
                    })
                df = pd.DataFrame(data)
                df.to_excel(writer, sheet_name=cooperado.nome[:31], index=False)

    output.seek(0)
    return send_file(output, download_name="relatorio_entregas.xlsx", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
