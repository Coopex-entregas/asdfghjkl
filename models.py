class Entrega(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    bairro = db.Column(db.String(100), nullable=True)  # se quiser manter bairro
    valor = db.Column(db.Float, nullable=True)
    hora_pedido = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    hora_atribuida = db.Column(db.DateTime, nullable=True)
    status_pagamento = db.Column(db.String(20), default="pendente")  # novo
    status_entrega = db.Column(db.String(20), default="pendente")    # novo
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=True)
