from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)  # armazene hash da senha, nunca plain text
    tipo = db.Column(db.String(20), nullable=False)  # ex: 'cooperado', 'admin', 'master'

    def __repr__(self):
        return f"<Usuario {self.nome} ({self.tipo})>"

class Entrega(db.Model):
    __tablename__ = 'entrega'
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    bairro = db.Column(db.String(100))
    valor = db.Column(db.Float)
    status = db.Column(db.String(20))  # ex: 'pendente', 'recebido', 'cancelado'
    cooperado = db.Column(db.String(100), nullable=True)  # pode ser nome ou user_id, conforme preferir
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    hora_saida = db.Column(db.DateTime, nullable=True)
    hora_entrega = db.Column(db.DateTime, nullable=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)  # admin que cadastrou ou alterou

    def __repr__(self):
        return f"<Entrega {self.cliente} - {self.bairro} - {self.status}>"

class Desconto(db.Model):
    __tablename__ = 'desconto'
    id = db.Column(db.Integer, primary_key=True)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    motivo = db.Column(db.String(255))
    data = db.Column(db.DateTime, default=datetime.utcnow)
    admin_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)  # admin que aplicou

    def __repr__(self):
        return f"<Desconto {self.valor} para cooperado {self.cooperado_id}>"
