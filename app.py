from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Usuario, Cooperado, Entrega
from datetime import datetime
import pandas as pd
import io

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# Configurações do banco (exemplo PostgreSQL)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://usuario:senha@host:porta/banco'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Rota setup - cria admin inicial e tabelas
@app.route('/setup')
def setup():
    db.create_all()
    admin = Usuario.query.filter_by(nome='coopex').first()
    if not admin:
        senha_hash = generate_password_hash('05062721')
        novo_admin = Usuario(nome='coopex', senha=senha_hash, tipo='admin')
        db.session.add(novo_admin)
        db.session.commit()
        return 'Setup concluído, admin criado.'
    return 'Admin já existe.'

# Login
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome).first()
        if user and check_password_hash(user.senha, senha):
            session['user_id'] = user.id
            session['user_tipo'] = user.tipo
            session['user_nome'] = user.nome
            return redirect(url_for('admin') if user.tipo == 'admin' else url_for('cooperado'))
        else:
            flash('Usuário ou senha inválidos.')
    return render_template('login.html')

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Painel admin
@app.route('/admin')
def admin():
    if 'user_tipo' not in session or session['user_tipo'] != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

# Painel cooperado
@app.route('/cooperado')
def cooperado():
    if 'user_tipo' not in session or session['user_tipo'] != 'cooperado':
        return redirect(url_for('login'))
    cooperado = Cooperado.query.filter_by(nome=session['user_nome']).first()
    entregas = Entrega.query.filter_by(cooperado=cooperado).all() if cooperado else []
    return render_template('cooperado.html', entregas=entregas)

# Cadastrar entrega
@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if 'user_tipo' not in session or session['user_tipo'] != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = float(request.form['valor'])
        cooperado_id = int(request.form['cooperado']) if request.form['cooperado'] else None
        entrega = Entrega(cliente=cliente, bairro=bairro, valor=valor, status='pendente', cooperado_id=cooperado_id, data_criacao=datetime.utcnow())
        db.session.add(entrega)
        db.session.commit()
        return redirect(url_for('admin'))
    cooperados = Cooperado.query.all()
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

# Cadastrar cooperado
@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if 'user_tipo' not in session or session['user_tipo'] != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        inss_complemento = float(request.form['inss_complemento']) if request.form['inss_complemento'] else None
        cooperado = Cooperado(nome=nome, inss_complemento=inss_complemento)
        db.session.add(cooperado)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

# Editar entrega
@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    if 'user_tipo' not in session or session['user_tipo'] != 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.status = request.form['status']
        entrega.cooperado_id = int(request.form['cooperado']) if request.form['cooperado'] else None
        db.session.commit()
        return redirect(url_for('admin'))
    cooperados = Cooperado.query.all()
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

# Trocar senha
@app.route('/trocar_senha', methods=['GET', 'POST'])
def trocar_senha():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        nova_senha = request.form['nova_senha']
        usuario = Usuario.query.get(session['user_id'])
        if usuario and check_password_hash(usuario.senha, senha_atual):
            usuario.senha = generate_password_hash(nova_senha)
            db.session.commit()
            flash('Senha alterada com sucesso!')
            return redirect(url_for('login'))
        else:
            flash('Senha atual incorreta.')
    return render_template('trocar_senha.html')

# Exportar entregas para Excel
@app.route('/exportar')
def exportar():
    if 'user_tipo' not in session or session['user_tipo'] != 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.all()
    data = []
    for e in entregas:
        tempo_envio = e.hora_entrega - e.hora_envio if e.hora_envio and e.hora_entrega else None
        data.append({
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Valor': e.valor,
            'Status': e.status,
            'Cooperado': e.cooperado.nome if e.cooperado else '',
            'Data Criação': e.data_criacao,
            'Hora Envio': e.hora_envio,
            'Hora Entrega': e.hora_entrega,
            'Tempo Envio-Entrega': tempo_envio,
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name='entregas.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
