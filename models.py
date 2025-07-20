from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)  # admin, cooperado, master

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    complemento_inss = db.Column(db.String(50), nullable=True)  # Adicionado conforme sua solicitação

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pendente')  # recebido/pendente
    cooperado = db.Column(db.String(100), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    hora_coleta = db.Column(db.DateTime, nullable=True)  
    hora_entrega = db.Column(db.DateTime, nullable=True)

    @property
    def tempo_coleta_entrega(self):
        if self.hora_coleta and self.hora_entrega:
            return (self.hora_entrega - self.hora_coleta).total_seconds() / 60
        return None
