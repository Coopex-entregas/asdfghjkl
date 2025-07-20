from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pandas as pd
import io
from models import db, Usuario, Cooperado, Entrega

app = Flask(__name__)
app.config['SECRET_KEY'] = 'coopexsecret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coopex.db'  # Troque para PostgreSQL no Render
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Criação do banco e usuário admin na primeira execução
with app.app_context():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        admin = Usuario(nome='coopex', senha=generate_password_hash('05062721'), tipo='admin')
        db.session.add(admin)
        db.session.commit()

# ROTAS LOGIN/LOGOUT

@app.route('/', methods=['GET', 'POST'])
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
                return redirect(url_for('cooperado_painel'))
        else:
            flash('Usuário ou senha inválidos.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# PAINEL ADMIN

@app.route('/admin')
def admin():
    if not session.get('user_id') or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

# CADASTRAR COOPERADO

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if not session.get('user_id') or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form.get('email')
        telefone = request.form.get('telefone')
        novo = Cooperado(nome=nome, email=email, telefone=telefone)
        db.session.add(novo)
        db.session.commit()
        flash('Cooperado cadastrado com sucesso.')
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

# CADASTRAR ENTREGA

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if not session.get('user_id') or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = float(request.form['valor'])
        cooperado_id = request.form.get('cooperado_id')
        nova = Entrega(
            cliente=cliente, bairro=bairro, valor=valor,
            cooperado_id=cooperado_id if cooperado_id else None,
            data_criacao=datetime.utcnow(), status='pendente'
        )
        db.session.add(nova)
        db.session.commit()
        flash('Entrega cadastrada.')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

# EDITAR ENTREGA

@app.route('/editar_entrega/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    if not session.get('user_id') or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.cooperado_id = request.form.get('cooperado_id')
        entrega.status = request.form.get('status', 'pendente')
        if request.form.get('data_recebido'):
            entrega.data_recebido = datetime.strptime(request.form['data_recebido'], '%Y-%m-%dT%H:%M')
        db.session.commit()
        flash('Entrega editada.')
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

# PAINEL COOPERADO

@app.route('/cooperado')
def cooperado_painel():
    if not session.get('user_id') or session.get('user_tipo') != 'cooperado':
        return redirect(url_for('login'))
    user = Usuario.query.get(session['user_id'])
    cooperado = Cooperado.query.filter_by(nome=user.nome).first()
    entregas = Entrega.query.filter_by(cooperado_id=cooperado.id).order_by(Entrega.data_criacao.desc()).all() if cooperado else []
    return render_template('cooperado.html', entregas=entregas, cooperado=cooperado)

# EXPORTAÇÃO EXCEL

@app.route('/exportar_excel')
def exportar_excel():
    if not session.get('user_id') or session.get('user_tipo') != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for cooperado in Cooperado.query.all():
            data = [
                {
                    'Cliente': e.cliente,
                    'Bairro': e.bairro,
                    'Valor': e.valor,
                    'Status': e.status,
                    'Cooperado': cooperado.nome,
                    'Data Criação': e.data_criacao.strftime('%d/%m/%Y %H:%M'),
                    'Data Recebido': e.data_recebido.strftime('%d/%m/%Y %H:%M') if e.data_recebido else '',
                    'Tempo (envio-entrega)': str(e.tempo_entre_coleta_e_entrega()) if e.tempo_entre_coleta_e_entrega() else ''
                }
                for e in entregas if e.cooperado_id == cooperado.id
            ]
            if data:
                df = pd.DataFrame(data)
                df.to_excel(writer, index=False, sheet_name=cooperado.nome[:31])
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='entregas.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == "__main__":
    app.run(debug=True)
