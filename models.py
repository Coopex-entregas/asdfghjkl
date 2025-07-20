from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy(app)

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    senha = db.Column(db.String(120), nullable=False)
    tipo = db.Column(db.String(20), default='admin')  # pode ser 'admin' ou 'cooperado'

class Cooperado(db.Model):
    __tablename__ = 'cooperado'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    # Adicione outros campos conforme necessário

class Entrega(db.Model):
    __tablename__ = 'entrega'
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(120), nullable=False)
    bairro = db.Column(db.String(80), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_envio = db.Column(db.DateTime, default=datetime.utcnow)
    data_atribuida = db.Column(db.DateTime, nullable=True)  # Adicionado aqui
    data_recebido = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pendente')
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
    cooperado = db.relationship('Cooperado', backref=db.backref('entregas', lazy=True))
