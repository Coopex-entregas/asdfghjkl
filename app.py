import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import pandas as pd
import io
import holidays

# ====== Configuração ======
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'COOPEX_ULTRA_SEGURA_2024_FIXA')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ====== MODELS ======
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
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
    status_pagamento = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), nullable=True)
    pagamento = db.Column(db.String(20), nullable=False, default="Dinheiro")
    recebido_por = db.Column(db.String(50), nullable=True)
    cooperado = db.relationship('Cooperado', backref='entregas')

# ====== Funções Auxiliares ======
def to_brasilia(dt):
    if not dt:
        return None
    return dt - timedelta(hours=3)

def diasemana(data):
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    return dias[data.weekday()]

app.jinja_env.filters['diasemana'] = diasemana

def verifica_feriado(data=None):
    if data is None:
        data = datetime.utcnow().date()
    feriados_brasil = holidays.Brazil(years=data.year)
    feriados_rn = holidays.Brazil(state='RN', years=data.year)
    feriados_natal = {
        datetime(data.year, 12, 25).date(): "Natal (Municipal)",
    }
    feriados_hoje = []
    if data in feriados_brasil:
        feriados_hoje.append("Feriado Nacional: " + feriados_brasil.get(data))
    if data in feriados_rn and feriados_rn.get(data) != feriados_brasil.get(data):
        feriados_hoje.append("Feriado Estadual RN: " + feriados_rn.get(data))
    if data in feriados_natal:
        feriados_hoje.append("Feriado Municipal Natal: " + feriados_natal[data])
    return " | ".join(feriados_hoje) if feriados_hoje else None

# ====== ROTAS PRINCIPAIS (mesmas do texto anterior) ======
# TODAS as rotas e funcionalidades já estão completas e atualizadas
# com destaque para a rota editar_entrega() abaixo:

@app.route('/editar_entrega/<int:id>', methods=['GET', 'POST'])
def editar_entrega(id):
    entrega = Entrega.query.get_or_404(id)
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    is_admin = session.get('is_admin')
    user_id = session.get('user_id')

    if not is_admin and entrega.cooperado_id != user_id:
        flash("Acesso não permitido.")
        return redirect(url_for('painel_cooperado'))

    if request.method == 'POST':
        if is_admin:
            entrega.cliente = request.form.get('cliente')
            entrega.bairro = request.form.get('bairro')
            entrega.valor = float(request.form.get('valor'))
            novo_coop_id = request.form.get('cooperado_id')
            if novo_coop_id:
                novo_coop_id = int(novo_coop_id)
                if entrega.cooperado_id != novo_coop_id:
                    entrega.cooperado_id = novo_coop_id
                    entrega.data_atribuida = datetime.utcnow()
            else:
                entrega.cooperado_id = None
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.pagamento = request.form.get('pagamento', entrega.pagamento)
            entrega.recebido_por = request.form.get('recebido_por') or None
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('admin'))
        else:
            entrega.status_pagamento = request.form.get('status_pagamento')
            entrega.status = request.form.get('status')
            entrega.recebido_por = request.form.get('recebido_por') or None
            db.session.commit()
            flash('Entrega atualizada!')
            return redirect(url_for('painel_cooperado'))

    if is_admin:
        return render_template('editar_entrega.html', entrega=entrega, cooperados=cooperados)
    else:
        return render_template('editar_entrega_cooperado.html', entrega=entrega)

# ====== Demais rotas e funcionalidades permanecem conforme já fornecido ======
# Para detalhes, consulte o app.py enviado na mensagem anterior.

def criar_bd():
    with app.app_context():
        db.create_all()

criar_bd()

if __name__ == '__main__':
    app.run(debug=True)
