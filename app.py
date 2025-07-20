from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd
import io

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://banco_de_dados_qus2_user:o9OsVK4SDOxYahEyNI8DvrkTyji0nLLo@dpg-d1per5c9c44c738iiqr0-a.oregon-postgres.render.com/banco_de_dados_qus2'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

### MODELS ###
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), default='admin')  # admin ou cooperado

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    telefone = db.Column(db.String(30), nullable=True)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))
    cooperado_nome = db.Column(db.String(100), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_recebimento = db.Column(db.DateTime, nullable=True)

### RESETAR BANCO E CRIAR ADMIN (REMOVA DEPOIS DE CRIAR) ###
with app.app_context():
    db.drop_all()
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        admin = Usuario(nome='coopex', senha='05062721', tipo='admin')
        db.session.add(admin)
        db.session.commit()

### ROTAS ###

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['usuario']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome, senha=senha).first()
        if user:
            session['usuario'] = user.nome
            session['tipo'] = user.tipo
            if user.tipo == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('painel_cooperado'))
        else:
            flash('Usuário ou senha inválidos', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if not session.get('usuario'):
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cadastrar_cooperado', methods=['GET', 'POST'])
def cadastrar_cooperado():
    if not session.get('usuario'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        bairro = request.form.get('bairro', '')
        email = request.form.get('email', '')
        telefone = request.form.get('telefone', '')
        novo = Cooperado(nome=nome, bairro=bairro, email=email, telefone=telefone)
        db.session.add(novo)
        db.session.commit()
        flash('Cooperado cadastrado com sucesso!', 'success')
        return redirect(url_for('admin'))
    return render_template('cadastrar_cooperado.html')

@app.route('/cadastrar_entrega', methods=['GET', 'POST'])
def cadastrar_entrega():
    if not session.get('usuario'):
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        cliente = request.form['cliente']
        bairro = request.form.get('bairro', '')
        valor = float(request.form.get('valor', 0))
        status = request.form.get('status', 'pendente')
        cooperado_nome = request.form.get('cooperado_nome', None)
        nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, status=status, cooperado_nome=cooperado_nome)
        db.session.add(nova)
        db.session.commit()
        flash('Entrega cadastrada com sucesso!', 'success')
        return redirect(url_for('admin'))
    return render_template('cadastrar_entrega.html', cooperados=cooperados)

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    if not session.get('usuario'):
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.all()
    if request.method == 'POST':
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form.get('bairro', '')
        entrega.valor = float(request.form.get('valor', 0))
        entrega.status = request.form.get('status', entrega.status)
        entrega.cooperado_nome = request.form.get('cooperado_nome', entrega.cooperado_nome)
        if request.form.get('status') == 'recebido':
            entrega.data_recebimento = datetime.utcnow()
        db.session.commit()
        flash('Entrega editada com sucesso!', 'success')
        return redirect(url_for('admin'))
    return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)

@app.route('/painel_cooperado')
def painel_cooperado():
    if not session.get('usuario'):
        return redirect(url_for('login'))
    nome = session['usuario']
    entregas = Entrega.query.filter_by(cooperado_nome=nome).order_by(Entrega.data_criacao.desc()).all()
    return render_template('painel_cooperado.html', entregas=entregas)

@app.route('/marcar_recebido/<int:id>')
def marcar_recebido(id):
    if not session.get('usuario'):
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(id)
    entrega.status = 'recebido'
    entrega.data_recebimento = datetime.utcnow()
    db.session.commit()
    flash('Entrega marcada como recebida!', 'success')
    return redirect(url_for('admin'))

@app.route('/exportar', methods=['GET', 'POST'])
def exportar():
    if not session.get('usuario'):
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    dados = []
    for e in entregas:
        tempo = ""
        if e.data_recebimento and e.data_criacao:
            duracao = e.data_recebimento - e.data_criacao
            tempo = str(duracao)
        dados.append({
            'ID': e.id,
            'Cliente': e.cliente,
            'Bairro': e.bairro,
            'Valor': e.valor,
            'Status': e.status,
            'Cooperado': e.cooperado_nome,
            'Coleta (Envio)': e.data_criacao.strftime('%d/%m/%Y %H:%M') if e.data_criacao else "",
            'Entrega (Recebido)': e.data_recebimento.strftime('%d/%m/%Y %H:%M') if e.data_recebimento else "",
            'Tempo de Duração': tempo,
        })
    df = pd.DataFrame(dados)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not df.empty:
            motoboys = df['Cooperado'].unique()
            for motoboy in motoboys:
                if pd.isna(motoboy):
                    sheet_name = 'Sem Cooperado'
                else:
                    sheet_name = str(motoboy)[:31]  # Excel max 31 chars
                df_motoboy = df[df['Cooperado'] == motoboy]
                df_motoboy.to_excel(writer, sheet_name=sheet_name, index=False)
        else:
            df.to_excel(writer, sheet_name='Entregas', index=False)
    output.seek(0)
    return send_file(output, download_name="entregas.xlsx", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
