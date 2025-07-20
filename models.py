from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(100), nullable=False)

class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)

class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))
    cooperado = db.Column(db.String(100), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_entrega = db.Column(db.DateTime, nullable=True)
