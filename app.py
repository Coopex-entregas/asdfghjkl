from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from models import db, Usuario, Cooperado, Entrega
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import pandas as pd
from io import BytesIO

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///app.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'supersegredo123'

db.init_app(app)

# CRIAR BANCO E ADMIN PADRÃO
with app.app_context():
    db.create_all()
    if not Usuario.query.filter_by(nome='coopex').first():
        admin = Usuario(nome='coopex', senha=generate_password_hash('05062721'), tipo='admin')
        db.session.add(admin)
        db.session.commit()

# --- Login ---
@app.route("/", methods=['GET', 'POST'])
@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        nome = request.form['nome']
        senha = request.form['senha']
        user = Usuario.query.filter_by(nome=nome).first()
        if user and check_password_hash(user.senha, senha):
            session['user_id'] = user.id
            session['user_nome'] = user.nome
            session['user_tipo'] = user.tipo
            if user.tipo == "admin":
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('cooperado'))
        else:
            flash("Usuário ou senha inválidos!", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- Painel ADMIN ---
@app.route("/admin")
def admin():
    if not session.get('user_tipo') == 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    cooperados = Cooperado.query.all()
    return render_template("admin.html", entregas=entregas, cooperados=cooperados)

# --- Painel COOPERADO ---
@app.route("/cooperado")
def cooperado():
    if not session.get('user_tipo') == 'cooperado':
        return redirect(url_for('login'))
    user = Usuario.query.get(session['user_id'])
    coop = Cooperado.query.filter_by(nome=user.nome).first()
    entregas = Entrega.query.filter_by(cooperado=coop).order_by(Entrega.data_criacao.desc()).all() if coop else []
    return render_template("cooperado.html", entregas=entregas, cooperado=coop)

# --- CADASTRAR NOVO COOPERADO ---
@app.route("/cadastrar_cooperado", methods=["GET", "POST"])
def cadastrar_cooperado():
    if not session.get('user_tipo') == 'admin':
        return redirect(url_for('login'))
    if request.method == "POST":
        nome = request.form['nome']
        if not Cooperado.query.filter_by(nome=nome).first():
            novo = Cooperado(nome=nome)
            db.session.add(novo)
            db.session.add(Usuario(nome=nome, senha=generate_password_hash('123456'), tipo='cooperado'))
            db.session.commit()
            flash("Cooperado cadastrado com sucesso!", "success")
        else:
            flash("Nome já existe!", "danger")
        return redirect(url_for('admin'))
    return render_template("cadastrar_cooperado.html")

# --- CADASTRAR NOVA ENTREGA ---
@app.route("/cadastrar_entrega", methods=["GET", "POST"])
def cadastrar_entrega():
    if not session.get('user_tipo') == 'admin':
        return redirect(url_for('login'))
    cooperados = Cooperado.query.all()
    if request.method == "POST":
        cliente = request.form['cliente']
        bairro = request.form['bairro']
        valor = float(request.form['valor'])
        status = request.form['status']
        cooperado_id = request.form.get('cooperado_id')
        cooperado = Cooperado.query.get(cooperado_id) if cooperado_id else None
        nova = Entrega(cliente=cliente, bairro=bairro, valor=valor, status=status, cooperado=cooperado)
        db.session.add(nova)
        db.session.commit()
        flash("Entrega cadastrada!", "success")
        return redirect(url_for('admin'))
    return render_template("cadastrar_entrega.html", cooperados=cooperados)

# --- EDITAR ENTREGA ---
@app.route("/editar_entrega/<int:entrega_id>", methods=["GET", "POST"])
def editar_entrega(entrega_id):
    if not session.get('user_tipo') == 'admin':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    cooperados = Cooperado.query.all()
    if request.method == "POST":
        entrega.cliente = request.form['cliente']
        entrega.bairro = request.form['bairro']
        entrega.valor = float(request.form['valor'])
        entrega.status = request.form['status']
        entrega.cooperado_id = request.form.get('cooperado_id')
        db.session.commit()
        flash("Entrega atualizada!", "success")
        return redirect(url_for('admin'))
    return render_template("editar_entrega.html", entrega=entrega, cooperados=cooperados)

# --- MARCAR ENTREGA COMO RECEBIDA (COOPERADO) ---
@app.route("/receber_entrega/<int:entrega_id>")
def receber_entrega(entrega_id):
    if not session.get('user_tipo') == 'cooperado':
        return redirect(url_for('login'))
    entrega = Entrega.query.get_or_404(entrega_id)
    entrega.status = "Recebido"
    entrega.data_recebimento = datetime.utcnow()
    db.session.commit()
    flash("Entrega marcada como recebida!", "success")
    return redirect(url_for('cooperado'))

# --- EXPORTAR DADOS PARA EXCEL XLSX ---
@app.route('/exportar')
def exportar():
    if not session.get('user_tipo') == 'admin':
        return redirect(url_for('login'))
    entregas = Entrega.query.order_by(Entrega.data_criacao.desc()).all()
    cooperados = Cooperado.query.all()
    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    for coop in cooperados:
        co_entregas = [e for e in entregas if e.cooperado == coop]
        data = []
        for e in co_entregas:
            tempo = (e.data_recebimento - e.data_criacao).total_seconds()/60 if (e.data_recebimento and e.data_criacao) else ''
            data.append({
                "ID": e.id,
                "Cliente": e.cliente,
                "Bairro": e.bairro,
                "Valor": e.valor,
                "Status": e.status,
                "Data Criação": e.data_criacao.strftime("%d/%m/%Y %H:%M") if e.data_criacao else '',
                "Data Recebimento": e.data_recebimento.strftime("%d/%m/%Y %H:%M") if e.data_recebimento else '',
                "Tempo (min)": tempo
            })
        df = pd.DataFrame(data)
        df.to_excel(writer, index=False, sheet_name=coop.nome if coop else "Sem Cooperado")
    writer.close()
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='entregas.xlsx')

# --- ESTATÍSTICAS (Exemplo de página extra) ---
@app.route('/estatisticas')
def estatisticas():
    if not session.get('user_tipo'):
        return redirect(url_for('login'))
    # Faça aqui um resumo se quiser
    return render_template("estatisticas.html")

if __name__ == "__main__":
    app.run(debug=True)
