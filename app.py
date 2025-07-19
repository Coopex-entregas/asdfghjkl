from flask import Flask, render_template, request, redirect, session, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Substitua aqui com sua URI do Render se quiser usar PostgreSQL:
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coopex.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_cliente = db.Column(db.String(120))
    nome_cooperado = db.Column(db.String(120))
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime)
    status_pagamento = db.Column(db.String(50), default="Pendente")
    status_entrega = db.Column(db.String(50), default="Pendente")

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    nome = request.form.get('nome')
    senha = request.form.get('senha')

    if nome == 'coopex' and senha == '05062721':
        session['admin'] = True
        return redirect('/admin')
    else:
        session['user_nome'] = nome
        session['user_tipo'] = 'cooperado'
        return redirect('/dashboard')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    return render_template('admin.html', entregas=entregas)

@app.route('/cadastrar', methods=['POST'])
def cadastrar():
    nome_cliente = request.form['nome_cliente']
    nome_cooperado = request.form['nome_cooperado']
    bairro = request.form['bairro']
    valor = float(request.form['valor'])
    nova = Entrega(
        nome_cliente=nome_cliente,
        nome_cooperado=nome_cooperado,
        bairro=bairro,
        valor=valor,
        hora_pedido=datetime.utcnow(),
        hora_atribuida=datetime.utcnow()
    )
    db.session.add(nova)
    db.session.commit()
    return redirect('/admin')

@app.route('/dashboard')
def dashboard():
    if 'user_nome' not in session:
        return redirect('/')
    
    nome = session['user_nome']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter_by(nome_cooperado=nome)

    if inicio:
        query = query.filter(Entrega.hora_pedido >= inicio)
    if fim:
        query = query.filter(Entrega.hora_pedido <= fim)

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()

    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if e.status_pagamento == 'Pago')
    total_pendente = total_geral - total_pago

    return render_template('dashboard_cooperado.html',
                           entregas=entregas,
                           total_geral=total_geral,
                           total_pago=total_pago,
                           total_pendente=total_pendente)

@app.route('/editar/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)

    if request.method == 'POST':
        entrega.status_pagamento = request.form.get('status_pagamento')
        entrega.status_entrega = request.form.get('status_entrega')
        db.session.commit()
        if session.get('admin'):


Perfeito. Você tem toda razão. Com base em **todos os seus arquivos enviados** (login, admin, dashboard do cooperado e editar entrega) e **as regras que combinamos**, agora vou te mandar o **código completo e correto do `app.py`**, pronto para funcionar com os seus HTMLs.

---

### ✅ `app.py` COMPLETO e compatível com **tudo que você me mandou**:

```python
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pandas as pd

app = Flask(__name__)
app.secret_key = 'chave_secreta_coopex'

# ✅ Conexão com banco PostgreSQL (substitua pela URI do Render se preferir)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coopex.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ✅ Modelo de Entrega
class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_cliente = db.Column(db.String(120))
    nome_cooperado = db.Column(db.String(120))
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    hora_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime)
    status_pagamento = db.Column(db.String(50), default="Pendente")
    status_entrega = db.Column(db.String(50), default="Pendente")

# ✅ Página de login
@app.route('/')
def index():
    return render_template('login.html')

# ✅ Verificação de login
@app.route('/login', methods=['POST'])
def login():
    nome = request.form.get('nome')
    senha = request.form.get('senha')

    if nome == 'coopex' and senha == '05062721':
        session['admin'] = True
        session['user_nome'] = nome
        session['user_tipo'] = 'adm'
        return redirect('/admin')
    else:
        session['user_nome'] = nome
        session['user_tipo'] = 'coop'
        return redirect('/dashboard')

# ✅ Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ✅ Painel do administrador
@app.route('/admin')
def admin():
    if not session.get('admin'):
        return redirect('/')
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    return render_template('admin.html', entregas=entregas)

# ✅ Cadastrar nova entrega
@app.route('/cadastrar', methods=['POST'])
def cadastrar():
    nome_cliente = request.form['nome_cliente']
    nome_cooperado = request.form['nome_cooperado']
    bairro = request.form['bairro']
    valor = float(request.form['valor'])
    nova = Entrega(
        nome_cliente=nome_cliente,
        nome_cooperado=nome_cooperado,
        bairro=bairro,
        valor=valor,
        hora_pedido=datetime.utcnow(),
        hora_atribuida=datetime.utcnow()
    )
    db.session.add(nova)
    db.session.commit()
    return redirect('/admin')

# ✅ Dashboard do cooperado
@app.route('/dashboard')
def dashboard():
    if not session.get('user_nome'):
        return redirect('/')

    nome = session['user_nome']
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    query = Entrega.query.filter_by(nome_cooperado=nome)

    if inicio:
        query = query.filter(Entrega.hora_pedido >= inicio)
    if fim:
        query = query.filter(Entrega.hora_pedido <= fim)

    entregas = query.order_by(Entrega.hora_pedido.desc()).all()
    total_geral = sum(e.valor for e in entregas)
    total_pago = sum(e.valor for e in entregas if e.status_pagamento.lower() == 'pago')
    total_pendente = total_geral - total_pago

    return render_template('dashboard_cooperado.html',
                           entregas=entregas,
                           total_geral=total_geral,
                           total_pago=total_pago,
                           total_pendente=total_pendente)

# ✅ Editar entrega (acesso do cooperado e admin)
@app.route('/editar/<int:entrega_id>', methods=['GET', 'POST'])
def editar_entrega(entrega_id):
    entrega = Entrega.query.get_or_404(entrega_id)

    if request.method == 'POST':
        entrega.status_pagamento = request.form['status_pagamento']
        entrega.status_entrega = request.form['status_entrega']
        db.session.commit()
        if session.get('admin'):
            return redirect('/admin')
        return redirect('/dashboard')

    return render_template('editar_entrega.html', entrega=entrega, user_tipo=session.get('user_tipo'))

# ✅ Exportar para Excel
@app.route('/exportar')
def exportar():
    entregas = Entrega.query.order_by(Entrega.hora_pedido.desc()).all()
    data = [{
        'Data': e.hora_pedido.strftime('%Y-%m-%d'),
        'Nome do Cliente': e.nome_cliente,
        'Hora do Pedido': e.hora_pedido.strftime('%H:%M:%S'),
        'Hora Atribuída': e.hora_atribuida.strftime('%H:%M:%S') if e.hora_atribuida else '',
        'Nome do Cooperado': e.nome_cooperado,
        'Status de Pagamento': e.status_pagamento,
        'Status da Entrega': e.status_entrega,
    } for e in entregas]
    df = pd.DataFrame(data)
    df.to_excel('entregas_exportadas.xlsx', index=False)
    return 'Exportado com sucesso!'

# ✅ Criar tabelas
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
