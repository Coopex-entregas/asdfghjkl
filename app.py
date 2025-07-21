from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

# String de conexão com o banco (ajuste conforme necessário)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://banco_de_dados_umjo_user:RhyjcVd65ByuboYnBhTR5O4za6CkQbWZ@dpg-d1ukc36mcj7s73ek6v00-a.oregon-postgres.render.com:5432/banco_de_dados_umjo'
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
    data_atribuida = db.Column(db.DateTime, nullable=True)
    data_recebido = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="pendente")
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    # NOVA COLUNA:
    status_pagamento = db.Column(db.String(20), nullable=True, default="Pendente")

# MIGRAÇÃO AUTOMÁTICA: Adiciona coluna no banco se não existir (só para SQLite/Postgres comum)
def checar_e_adicionar_coluna_status_pagamento():
    with app.app_context():
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        colunas = [c['name'] for c in insp.get_columns('entrega')]
        if 'status_pagamento' not in colunas:
            # Adiciona coluna
            try:
                db.session.execute(text('ALTER TABLE entrega ADD COLUMN status_pagamento VARCHAR(20) DEFAULT \'Pendente\';'))
                db.session.commit()
            except Exception as e:
                db.session.rollback()

def inicializar_banco():
    with app.app_context():
        db.create_all()
        checar_e_adicionar_coluna_status_pagamento()
        if not Usuario.query.filter_by(nome='coopex').first():
            db.session.add(Usuario(nome='coopex', senha='05062721', tipo='admin'))
            db.session.commit()

# GARANTE QUE O BANCO E AS COLUNAS EXISTAM SEMPRE QUE O APP SUBIR!
inicializar_banco()

# ======= LOGIN AJUSTADO AQUI =======
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

        cooperado = Cooperado.query.filter_by(nome=nome).first()
        if cooperado and cooperado.senha == senha:
            session['usuario_id'] = cooperado.id
            session['usuario_tipo'] = "cooperado"
            session['user_nome'] = cooperado.nome
            return redirect(url_for('painel_cooperado'))

        flash('Usuário ou senha inválidos')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

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

    total_dia = sum(e.valor for e in entregas if e.data_envio.date() == datetime.utcnow().date())
    total_mes = sum(e.valor for e in entregas if e.data_envio.month == datetime.utcnow().month and e.data_envio.year == datetime.utcnow().year)
    total_ano = sum(e.valor for e in entregas if e.data_envio.year == datetime.utcnow().year)

    estatisticas = {
        'total_dia': total_dia,
        'total_mes': total_mes,
        'total_ano': total_ano
    }

    return render_template('admin.html', entregas=entregas, cooperados=cooperados, estatisticas=estatisticas,
                           data_inicio=data_inicio, data_fim=data_fim)

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
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

    entregas = query.all()
    cooperados = Cooperado.query.all()

    total = len(entregas)
    pagas = len([e for e in entregas if e.status == 'recebido'])
    pendentes = total - pagas
    total_valor = sum(e.valor for e in entregas)

    estatisticas = {
        'total': total,
        'pagas': pagas,
        'pendentes': pendentes,
        'total_valor': total_valor
    }

    return render_template('estatisticas_cooperado.html', cooperados=cooperados, estatisticas=estatisticas,
                           cooperado_id=cooperado_id, data_inicio=data_inicio, data_fim=data_fim)

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

@app.route('/excluir_cooperado/<int:id>', methods=['POST'])
def excluir_cooperado(id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(id)
    db.session.delete(cooperado)
    db.session.commit()
    flash('Cooperado excluído com sucesso!')
    return redirect(url_for('admin'))

@app.route('/excluir_entrega/<int:id>', methods=['POST'])
def excluir_entrega(id):
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    db.session.delete(entrega)
    db.session.commit()
    flash('Entrega excluída com sucesso!')
    return redirect(url_for('admin'))

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
        nova = Entrega(
            cliente=cliente,
            bairro=bairro,
            valor=valor,
            data_envio=datetime.utcnow(),
            status='pendente',
            status_pagamento='Pendente',
            cooperado_id=cooperado_id,
            data_atribuida=datetime.utcnow() if cooperado_id else None
        )
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
            status_pagamento = request.form.get('status_pagamento')
            entrega.status_pagamento = status_pagamento if status_pagamento else 'Pendente'
            if status == 'recebido' and not entrega.data_recebido:
                entrega.data_recebido = datetime.utcnow()
            elif status == 'pendente':
                entrega.data_recebido = None
        else:
            status = request.form.get('status')
            if status in ['pendente', 'recebido']:
                entrega.status = status
                if status == 'recebido' and not entrega.data_recebido:
                    entrega.data_recebido = datetime.utcnow()
                elif status == 'pendente':
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
    cooperado = Cooperado.query.get(session['usuario_id'])
    if not cooperado:
        flash('Cooperado não encontrado!')
        return redirect(url_for('login'))
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

    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if getattr(e, 'status', 'pendente') == 'recebido')
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
            data = []
            for e in entregas:
                data.append({
                    'Data do Pedido': e.data_envio.strftime('%d/%m/%Y') if e.data_envio else '',
                    'Hora do Pedido': e.data_envio.strftime('%H:%M') if e.data_envio else '',
                    'Cliente': e.cliente,
                    'Bairro': e.bairro,
                    'Hora Atribuída': e.data_atribuida.strftime('%H:%M') if e.data_atribuida else '',
                    'Valor': '%.2f' % e.valor if e.valor else '',
                    'Status Pagamento': e.status_pagamento if e.status_pagamento else 'Pendente',
                    'Status da Entrega': e.status
                })
            df = pd.DataFrame(data)
            nome_aba = next((c.nome for c in cooperados if c.id == cooperado_id_int), 'Cooperado')
            df.to_excel(writer, sheet_name=nome_aba[:31], index=False)
        else:
            for cooperado in cooperados:
                entregas_coop = [e for e in entregas if e.cooperado_id == cooperado.id]
                data = []
                for e in entregas_coop:
                    data.append({
                        'Data do Pedido': e.data_envio.strftime('%d/%m/%Y') if e.data_envio else '',
                        'Hora do Pedido': e.data_envio.strftime('%H:%M') if e.data_envio else '',
                        'Cliente': e.cliente,
                        'Bairro': e.bairro,
                        'Hora Atribuída': e.data_atribuida.strftime('%H:%M') if e.data_atribuida else '',
                        'Valor': '%.2f' % e.valor if e.valor else '',
                        'Status Pagamento': e.status_pagamento if e.status_pagamento else 'Pendente',
                        'Status da Entrega': e.status
                    })
                df = pd.DataFrame(data)
                df.to_excel(writer, sheet_name=cooperado.nome[:31], index=False)

    output.seek(0)
    return send_file(output, download_name="relatorio_entregas.xlsx", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
