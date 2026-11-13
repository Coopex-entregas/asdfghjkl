"""Ponte interna autenticada para atribuicao de entregas a partir do Financeiro.

Executa a funcao oficial da Supervisao (inclui fila e emissoes Socket.IO).
Nunca recebe credenciais de cooperados, valores financeiros ou dados bancarios.
Desabilitada por padrao. Nenhuma chamada externa a esta rota sem HMAC valido.
"""
import hashlib
import hmac
import json
import os
import time

from flask import jsonify, request


def _chave():
    secret=os.getenv("COOPEX_SUPERVISAO_BRIDGE_SECRET","")
    if len(secret)<32:
        return None
    return secret.encode("utf-8")


def _verificar_assinatura(raw,ts_str,signature,secret,now=None):
    if (not secret or len(raw)>2048 or not ts_str or not signature
            or len(signature)!=64):
        return False
    try:
        instante=int(ts_str)
    except (TypeError,ValueError):
        return False
    agora=time.time() if now is None else now
    if abs(agora-instante)>45:
        return False
    base=ts_str.encode("ascii")+b"\n"+raw
    calculada=hmac.new(secret,base,hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculada,signature)


def _validar_payload(raw):
    try:
        p=json.loads(raw)
    except (TypeError,ValueError) as exc:
        raise ValueError("JSON invalido") from exc
    if not isinstance(p,dict) or set(p)!={
        "entrega_id","cooperado_id_legado","esperado_cooperado_id_legado",
        "esperado_status_corrida"
    }:
        raise ValueError("Campos invalidos")
    for key in ("entrega_id","cooperado_id_legado"):
        if type(p[key]) is not int or p[key]<=0:
            raise ValueError("ID invalido")
    atual=p["esperado_cooperado_id_legado"]
    if atual is not None and (type(atual) is not int or atual<=0):
        raise ValueError("ID atual invalido")
    status=p["esperado_status_corrida"]
    if status is not None and (not isinstance(status,str) or len(status)>50):
        raise ValueError("Estado atual invalido")
    return p


def instalar(app,db,Entrega,Cooperado,atribuir_core):
    @app.post("/api/interno/coopex-financeiro/atribuir")
    def atribuicao_pelo_financeiro():
        if os.getenv("COOPEX_SUPERVISAO_BRIDGE_ATIVO")!="1":
            return jsonify(ok=False,erro="Ponte desabilitada"),404
        secret=_chave()
        raw=request.get_data(cache=False)
        if not _verificar_assinatura(
            raw,request.headers.get("X-COOPEX-Timestamp"),
            request.headers.get("X-COOPEX-Signature"),secret):
            return jsonify(ok=False,erro="Nao autorizado"),403
        try:
            p=_validar_payload(raw)
        except ValueError as e:
            return jsonify(ok=False,erro=str(e)),400
        try:
            entrega=(db.session.query(Entrega)
                     .filter(Entrega.id==p["entrega_id"])
                     .with_for_update().one_or_none())
            if entrega is None:
                return jsonify(ok=False,erro="Entrega nao encontrada"),404
            if (entrega.cooperado_id !=p["esperado_cooperado_id_legado"]
                    or entrega.status_corrida!=p["esperado_status_corrida"]):
                return jsonify(ok=False,erro="Entrega modificada; atualize a fila"),409
            status=(entrega.status or "").strip().lower()
            if (entrega.status_corrida=="finalizada"
                    or status in ("recebido","entregue","concluido","concluida",
                                  "finalizada","cancelado","cancelada")):
                return jsonify(ok=False,erro="Entrega ja encerrada"),409
            if entrega.cooperado_id is not None:
                return jsonify(ok=False,erro="Entrega ja atribuida"),409
            coop=(db.session.query(Cooperado)
                  .filter(Cooperado.id==p["cooperado_id_legado"]).first())
            if coop is None or not coop.ativo:
                return jsonify(ok=False,erro="Cooperado da origem inativo ou ausente"),422
            # Reusa o mesmo caminho da Supervisao, incluindo fila e avisos.
            atribuir_core(entrega,coop.id,commit=True)
            return jsonify(ok=True,entrega_id=entrega.id,
                           cooperado_id_legado=coop.id,
                           status_corrida=entrega.status_corrida)
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha na atribuicao autenticada pelo Financeiro")
            return jsonify(ok=False,erro="Falha ao atribuir entrega"),503
