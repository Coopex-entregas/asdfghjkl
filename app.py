from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://banco_de_dados_umjo_user:RhyjcVd65ByuboYnBhTR5O4za6CkQbWZ@dpg-d1ukc36mcj7s73ek6v00-a.oregon-postgres.render.com/banco_de_dados_umjo'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default="admin")  # ou "cooperado"

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    bairro = db.Column(db.String(100), nullable=True)
    valor = db.Column(db.Float, nullable=True)
    hora_pedido = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    status_pagamento = db.Column(db.String(20), default="pendente")  # pendente ou pago
    status_entrega = db.Column(db.String(20), default="pendente")    # pendente, em rota, entregue
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)

with app.app_context():
    db.create_all()

    # Corrige tabela para incluir colunas que possam estar faltando (opcional)
    def corrigir_tabela_entrega():
        with db.engine.connect() as con:
            colunas = con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='entrega';"))
            colunas_existentes = {row[0] for row in colunas}
            alteracoes = []
            if 'descricao' not in colunas_existentes:
                alteracoes.append("ADD COLUMN descricao VARCHAR(200) NOT NULL DEFAULT ''")
            if 'bairro' not in colunas_existentes:
                alteracoes.append("ADD COLUMN bairro VARCHAR(100)")
            if 'valor' not in colunas_existentes:
                alteracoes.append("ADD COLUMN valor FLOAT")
            if 'hora_pedido' not in colunas_existentes:
                alteracoes.append("ADD COLUMN hora_pedido TIMESTAMP NOT NULL DEFAULT now()")
            if 'hora_atribuida' not in colunas_existentes:
                alteracoes.append("ADD COLUMN hora_atribuida TIMESTAMP")
            if 'status_pagamento' not in colunas_existentes:
                alteracoes.append("ADD COLUMN status_pagamento VARCHAR(20) DEFAULT 'pendente'")
            if 'status_entrega' not in colunas_existentes:
                alteracoes.append("ADD COLUMN status_entrega VARCHAR(20) DEFAULT 'pendente'")
            if 'cooperado_id' not in colunas_existentes:
                alteracoes.append("ADD COLUMN cooperado_id INTEGER")
            if alteracoes:
                con.execute(text(f'ALTER TABLE entrega {", ".join(alteracoes)};'))
    corrigir_tabela_entrega()

    # Cria usuário admin padrão, se não existir
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

@app.route('/admin')
def admin():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))

    # Filtros
    cooperado_id = request.args.get('cooperado_id', 'todos')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    query = Entrega.query

    if cooperado_id != 'todos':
        try:
            coop_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == coop_id_int)
        except:
            pass

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.hora_pedido >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            # Para pegar até o final do dia:
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.hora_pedido <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Cooperado.query.all()

    # Estatísticas
    total_dia = sum(e.valor or 0 for e in entregas if e.hora_pedido.date() == datetime.utcnow().date())
    total_mes = sum(e.valor or 0 for e in entregas if e.hora_pedido.month == datetime.utcnow().month and e.hora_pedido.year == datetime.utcnow().year)
    total_ano = sum(e.valor or 0 for e in entregas if e.hora_pedido.year == datetime.utcnow().year)

    valores_dia = total_dia
    valores_mes = total_mes
    valores_ano = total_ano

    estatisticas = {
        'total_dia': len([e for e in entregas if e.hora_pedido.date() == datetime.utcnow().date()]),
        'total_mes': len([e for e in entregas if e.hora_pedido.month == datetime.utcnow().month and e.hora_pedido.year == datetime.utcnow().year]),
        'total_ano': len([e for e in entregas if e.hora_pedido.year == datetime.utcnow().year]),
        'valores_dia': f"{valores_dia:.2f}",
        'valores_mes': f"{valores_mes:.2f}",
        'valores_ano': f"{valores_ano:.2f}",
    }

    return render_template('admin.html', entregas=entregas, cooperados=cooperados, estatisticas=estatisticas)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        if Usuario.query.filter_by(nome=nome).first() or Cooperado.query.filter_by(nome=nome).first():
            flash('Já existe um usuário ou cooperado com esse nome!')
            return redirect(url_for('cadastrar_cooperado'))
        novo_usuario = Usuario(nome=nome, senha=senha, tipo='cooperado')
        novo_cooperado = Cooperado(nome=nome)
        db.session.add(novo_usuario)
        db.session.add(novo_cooperado)
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
        descricao = request.form['descricao']
        bairro = request.form.get('bairro')
        valor = request.form.get('valor')
        valor_float = float(valor) if valor else 0.0
        hora_pedido_str = request.form.get('hora_pedido')
        hora_pedido = datetime.strptime(hora_pedido_str, '%Y-%m-%dT%H:%M') if hora_pedido_str else datetime.utcnow()
        cooperado_id = request.form.get('cooperado_id')
        nova_entrega = Entrega(
            descricao=descricao,
            bairro=bairro,
            valor=valor_float,
            hora_pedido=hora_pedido,
            status_pagamento='pendente',
            status_entrega='pendente',
            cooperado_id=int(cooperado_id) if cooperado_id else None
        )
        db.session.add(nova_entrega)
        db.session.commit()
        flash('Entrega cadastrada com sucesso!')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    usuario = Usuario.query.get(session['usuario_id'])
    cooperados = Cooperado.query.all()

    if usuario.tipo == 'admin':
        if request.method == 'POST':
            entrega.descricao = request.form['descricao']
            entrega.bairro = request.form.get('bairro', entrega.bairro)
            valor = request.form.get('valor')
            entrega.valor = float(valor) if valor else entrega.valor
            cooperado_id = request.form.get('cooperado_id')
            entrega.cooperado_id = int(cooperado_id) if cooperado_id else None
            entrega.status_pagamento = request.form.get('status_pagamento', entrega.status_pagamento)
            entrega.status_entrega = request.form.get('status_entrega', entrega.status_entrega)
            db.session.commit()
            flash('Entrega atualizada com sucesso!')
            return redirect(url_for('admin'))

        return render_template('editar_entrega_admin.html', entrega=entrega, cooperados=cooperados, user_tipo='admin')

    elif usuario.tipo == 'cooperado':
        cooperado = Cooperado.query.filter_by(nome=usuario.nome).first()
        if not cooperado or entrega.cooperado_id != cooperado.id:
            abort(403)  # Acesso negado

        if request.method == 'POST':
            entrega.status_pagamento = request.form.get('status_pagamento', entrega.status_pagamento)
            entrega.status_entrega = request.form.get('status_entrega', entrega.status_entrega)
            db.session.commit()
            flash('Status da entrega atualizado!')
            return redirect(url_for('painel_cooperado'))

        return render_template('editar_entrega_cooperado.html', entrega=entrega, user_tipo='cooperado')

    else:
        abort(403)

@app.route('/painel_cooperado')
def painel_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'cooperado':
        return redirect(url_for('login'))
    usuario = Usuario.query.get(session['usuario_id'])
    cooperado = Cooperado.query.filter_by(nome=usuario.nome).first()

    # Filtros
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter(Entrega.cooperado_id == cooperado.id)

    if inicio:
        try:
            dt_inicio = datetime.strptime(inicio, '%Y-%m-%d')
            query = query.filter(Entrega.hora_pedido >= dt_inicio)
        except:
            pass
    if fim:
        try:
            dt_fim = datetime.strptime(fim, '%Y-%m-%d')
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.hora_pedido <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()

    # Estatísticas para o cooperado (pagas e pendentes no período)
    total_geral = sum(e.valor or 0 for e in entregas)
    total_pago = sum(e.valor or 0 for e in entregas if e.status_pagamento == 'pago')
    total_pendente = sum(e.valor or 0 for e in entregas if e.status_pagamento == 'pendente')

    return render_template('cooperado.html',
                           entregas=entregas,
                           total_geral=total_geral,
                           total_pago=total_pago,
                           total_pendente=total_pendente)

@app.route('/marcar_recebido/<int:id>')
def marcar_recebido(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status_entrega = 'entregue'
    entrega.status_pagamento = 'pago'
    db.session.commit()
    flash('Entrega marcada como recebida!')
    if session.get('usuario_tipo') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('painel_cooperado'))

@app.route('/marcar_pendente/<int:id>')
def marcar_pendente(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status_entrega = 'pendente'
    entrega.status_pagamento = 'pendente'
    db.session.commit()
    flash('Entrega marcada como pendente!')
    if session.get('usuario_tipo') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('painel_cooperado'))

@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    if 'usuario_id' not in session or session.get('usuario_tipo') != 'admin':
        return redirect(url_for('login'))

    cooperados = Cooperado.query.all()
    dados = []
    for c in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=c.id).all()
        total = sum(e.valor or 0 for e in entregas)
        total_pago = sum(e.valor or 0 for e in entregas if e.status_pagamento == 'pago')
        total_pendente = sum(e.valor or 0 for e in entregas if e.status_pagamento == 'pendente')
        dados.append({
            'cooperado': c.nome,
            'total': total,
            'total_pago': total_pago,
            'total_pendente': total_pendente,
            'quantidade': len(entregas)
        })
    return render_template('estatisticas_cooperado.html', dados=dados)

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
            coop_id_int = int(cooperado_id)
            query = query.filter(Entrega.cooperado_id == coop_id_int)
        except:
            pass

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            query = query.filter(Entrega.hora_pedido >= dt_inicio)
        except:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            dt_fim = dt_fim.replace(hour=23, minute=59, second=59)
            query = query.filter(Entrega.hora_pedido <= dt_fim)
        except:
            pass

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()
    cooperados = Cooperado.query.all()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if cooperado_id == 'todos':
            # Aba para cada cooperado
            for cooperado in cooperados:
                entregas_coop = [e for e in entregas if e.cooperado_id == cooperado.id]
                data = []
                for e in entregas_coop:
                    tempo = (e.hora_atribuida - e.hora_pedido).total_seconds() / 60 if e.hora_atribuida else None
                    data.append({
                        'Descrição': e.descricao,
                        'Bairro': e.bairro,
                        'Valor': e.valor,
                        'Hora Pedido': e.hora_pedido.strftime('%d/%m/%Y %H:%M'),
                        'Hora Atribuída': e.hora_atribuida.strftime('%d/%m/%Y %H:%M') if e.hora_atribuida else '',
                        'Status Pagamento': e.status_pagamento,
                        'Status Entrega': e.status_entrega,
                        'Tempo entre pedido e atribuição (min)': round(tempo, 1) if tempo else '',
                    })
                df = pd.DataFrame(data)
                df.to_excel(writer, sheet_name=cooperado.nome[:31], index=False)
        else:
            # Aba única para cooperado específico
            data = []
            for e in entregas:
                tempo = (e.hora_atribuida - e.hora_pedido).total_seconds() / 60 if e.hora_atribuida else None
                data.append({
                    'Descrição': e.descricao,
                    'Bairro': e.bairro,
                    'Valor': e.valor,
                    'Hora Pedido': e.hora_pedido.strftime('%d/%m/%Y %H:%M'),
                    'Hora Atribuída': e.hora_atribuida.strftime('%d/%m/%Y %H:%M') if e.hora_atribuida else '',
                    'Status Pagamento': e.status_pagamento,
                    'Status Entrega': e.status_entrega,
                    'Tempo entre pedido e atribuição (min)': round(tempo, 1) if tempo else '',
                })
            df = pd.DataFrame(data)
            nome_aba = cooperados[0].nome[:31] if cooperados else 'Entregas'
            df.to_excel(writer, sheet_name=nome_aba, index=False)

    output.seek(0)
    return send_file(output, download_name="relatorio_entregas.xlsx", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
