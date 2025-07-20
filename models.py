from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), unique=True, nullable=False)  # login
    senha = db.Column(db.String(128), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default='admin')  # 'admin' ou 'cooperado'

class Cooperado(db.Model):
    __tablename__ = 'cooperado'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    telefone = db.Column(db.String(30), nullable=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

class Entrega(db.Model):
    __tablename__ = 'entrega'
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pendente')
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'))
    cooperado = db.relationship('Cooperado', backref='entregas')
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_recebido = db.Column(db.DateTime, nullable=True)

    def tempo_entre_coleta_e_entrega(self):
        if self.data_recebido:
            return self.data_recebido - self.data_criacao
        return None
