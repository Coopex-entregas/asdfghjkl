from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)  # login
    senha = db.Column(db.String(200), nullable=False)  # hash da senha
    tipo = db.Column(db.String(20), nullable=False)  # 'admin', 'cooperado'

class Cooperado(db.Model):
    __tablename__ = 'cooperado'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    inss_complemento = db.Column(db.Float, nullable=True)

class Entrega(db.Model):
    __tablename__ = 'entrega'
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))  # Ex: 'pendente', 'recebido'
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    hora_envio = db.Column(db.DateTime, nullable=True)  # quando coleta foi enviada
    hora_entrega = db.Column(db.DateTime, nullable=True)  # quando entrega foi feita

    cooperado = db.relationship('Cooperado', backref=db.backref('entregas', lazy=True))
