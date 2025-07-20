from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from datetime import datetime
from io import BytesIO
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///db.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from models import db, Usuario, Cooperado, Entrega

db.init_app(app)

# ---- PRIMEIRO USUÁRIO FIXO
@app.before_first_request
def criar_admin():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        user = Usuario(nome='coopex', senha=generate_password_hash('05062721'), tipo='admin')
        db.session.add(user)
        db.session.commit()

# ---- LOGIN ----
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome).first()
        if user and check_password_hash(user.senha, senha):
            session['user_id'] = user.id
            session['user_tipo'] = user.tipo
            if user.tipo == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('cooperado', cooperado_id=user.id))
        else:
            flash("Usuário ou senha inválidos!", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---- PAINEL ADMIN ----
@app.route('/')
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_id' not in session or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))

    filtro_motoboy = request.args.get('cooperado_id')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    entregas_query = Entrega.query
    if filtro_motoboy and filtro_motoboy != 'todos':
        entregas_query = entregas_query.filter_by(cooperado_id=int(filtro_motoboy))
    if data_inicio:
        entregas_query = entregas_query.filter(Entrega.data_criacao >= data_inicio)
    if data_fim:
        entregas_query = entregas_query.filter(Entrega.data_criacao <= data_fim)
    entregas = entregas_query.order_by(Entrega.data_criacao.desc()).all()

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

# ---- CADASTRAR COOPERADO ----
@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'user_id' not in session or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form.get('email')
        telefone = request.form.get('telefone')
        cooperado = Cooperado(nome=nome, email=email, telefone=telefone)
        db.session.add(cooperado)
        db.session.commit()
        flash('Cooperado cadastrado com sucesso!', 'success')
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

# ---- CADASTRAR ENTREGA ----
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if 'user_id' not in session or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form.get('bairro')
        valor = float(request.form.get('valor', 0))
        cooperado_id = request.form.get('cooperado_id')
        cooperado_nome = None
        if cooperado_id and cooperado_id != 'nenhum':
            cooperado = Cooperado.query.get(int(cooperado_id))
            cooperado_nome = cooperado.nome
        entrega = Entrega(
            cliente=cliente, bairro=bairro, valor=valor,
            cooperado_id=cooperado_id if cooperado_id != 'nenhum' else None,
            cooperado_nome=cooperado_nome,
            data_criacao=datetime.utcnow()
        )
        db.session.add(entrega)
        db.session.commit()
        flash('Entrega cadastrada!', 'success')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

# ---- EDITAR ENTREGA ----
@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    if 'user_id' not in session or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form.get('bairro')
        entrega.valor = float(request.form.get('valor', 0))
        entrega.status = request.form.get('status', 'Pendente')
        cooperado_id = request.form.get('cooperado_id')
        entrega.cooperado_id = cooperado_id if cooperado_id != 'nenhum' else None
        entrega.cooperado_nome = Cooperado.query.get(int(cooperado_id)).nome if cooperado_id and cooperado_id != 'nenhum' else None
        db.session.commit()
        flash('Entrega editada!', 'success')
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

# ---- COOPERADO PAINEL ----
@app.route('/cooperado/<int:cooperado_id>')
def cooperado(cooperado_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(cooperado_id)
    entregas = Entrega.query.filter_by(cooperado_id=cooperado_id).order_by(Entrega.data_criacao.desc()).all()
    return render_template('cooperado.html', cooperado=cooperado, entregas=entregas)

# ---- MARCAR ENTREGA COMO RECEBIDA ----
@app.route('/receber_entrega/<int:entrega_id>')
def receber_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)
    entrega.status = 'Recebido'
    entrega.data_entrega = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('cooperado', cooperado_id=entrega.cooperado_id))

# ---- EXPORTAÇÃO XLSX (motoboy por aba) ----
@app.route('/exportar_xlsx')
def exportar_xlsx():
    entregas = Entrega.query.order_by(Entrega.cooperado_nome, Entrega.data_criacao).all()
    dados = []
    for e in entregas:
        tempo = None
        if e.data_entrega:
            tempo = (e.data_entrega - e.data_criacao).total_seconds() / 60  # minutos
        dados.append({
            "ID": e.id,
            "Cliente": e.cliente,
            "Bairro": e.bairro,
            "Valor": e.valor,
            "Status": e.status,
            "Motoboy": e.cooperado_nome,
            "Data Coleta": e.data_criacao,
            "Data Entrega": e.data_entrega,
            "Tempo Entrega (min)": tempo
        })

    df = pd.DataFrame(dados)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for nome, grupo in df.groupby("Motoboy"):
            grupo.to_excel(writer, sheet_name=str(nome or 'Sem Motoboy'), index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="entregas.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---- ESTATÍSTICAS POR COOPERADO ----
@app.route('/estatisticas_cooperado')
def estatisticas_cooperado():
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    estatisticas = []
    for c in cooperados:
        entregas = Entrega.query.filter_by(cooperado_id=c.id).all()
        total = sum(e.valor for e in entregas if e.valor)
        estatisticas.append({
            'cooperado': c.nome,
            'total': total,
            'qtd_entregas': len(entregas)
        })
    return render_template('estatisticas.html', estatisticas=estatisticas)

if __name__ == '__main__':
    app.run(debug=True)
