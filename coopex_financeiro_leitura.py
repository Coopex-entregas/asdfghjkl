"""API de leitura segura para tela do Financeiro sem compartilhar senha PostgreSQL.

Somente o servico antigo da Supervisao acessa seu banco; o Financeiro
solicita dados em HTTPS usando assinatura HMAC com expiracao curta.
"""
import json
import os
from datetime import datetime,timedelta
from flask import jsonify,request
from sqlalchemy import text
from coopex_financeiro_bridge import _chave,_verificar_assinatura


def instalar(app,db):
    @app.post("/api/interno/coopex-financeiro/consulta")
    def consulta_financeiro():
        if os.getenv("COOPEX_SUPERVISAO_BRIDGE_LEITURA_ATIVA")!="1":
            return jsonify(ok=False,erro="Consulta desabilitada"),404
        if request.content_length is None or request.content_length>1024:
            return jsonify(ok=False,erro="Solicitacao invalida"),413
        raw=request.get_data(cache=False)
        if not _verificar_assinatura(
            raw,request.headers.get("X-COOPEX-Timestamp"),
            request.headers.get("X-COOPEX-Signature"),_chave()):
            return jsonify(ok=False,erro="Nao autorizado"),403
        try:
            payload=json.loads(raw)
            if not isinstance(payload,dict) or set(payload)!={"inicio","fim"}:
                raise ValueError()
            inicio=datetime.fromisoformat(payload["inicio"])
            fim=datetime.fromisoformat(payload["fim"])
            if (inicio.tzinfo is not None or fim.tzinfo is not None
                    or inicio>=fim or fim-inicio>timedelta(days=2)
                    or inicio<datetime.utcnow()-timedelta(days=367)
                    or fim>datetime.utcnow()+timedelta(days=3)):
                raise ValueError()
        except (ValueError,TypeError,KeyError):
            return jsonify(ok=False,erro="Periodo invalido"),400
        try:
            with db.engine.connect() as con:
                con.execute(text("SET TRANSACTION READ ONLY"))
                itens=con.execute(text("""
                    SELECT e.id,e.cliente,e.bairro,e.valor,e.pagamento,
                           e.cooperado_id,c.nome AS cooperado_nome,
                           e.status_corrida,e.status,e.status_pagamento,e.data_envio
                    FROM public.entrega e
                    LEFT JOIN public.cooperado c ON c.id=e.cooperado_id
                    WHERE e.data_envio>=:inicio AND e.data_envio<:fim
                    ORDER BY (e.cooperado_id IS NOT NULL),e.data_envio DESC,e.id DESC
                    LIMIT 100
                """),{"inicio":inicio,"fim":fim}).mappings().all()
                resumo=con.execute(text("""
                    SELECT coalesce(e.status_corrida,'(sem status)') situacao,
                           count(*) quantidade
                    FROM public.entrega e
                    WHERE e.data_envio>=:inicio AND e.data_envio<:fim
                    GROUP BY coalesce(e.status_corrida,'(sem status)')
                """),{"inicio":inicio,"fim":fim}).mappings().all()
        except Exception:
            app.logger.exception("Falha na consulta interna da Supervisao")
            return jsonify(ok=False,erro="Consulta indisponivel"),503
        entregas=[{
            "id":int(e["id"]),"origem_entrega_id":int(e["id"]),
            "cliente":e["cliente"],"bairro":e["bairro"],
            "valor":float(e["valor"]),"pagamento":e["pagamento"],
            "cooperado_id":None,"cooperado_id_legado":e["cooperado_id"],
            "cooperado_nome":e["cooperado_nome"],
            "status_corrida":e["status_corrida"],
            "status_entrega":e["status"],
            "status_pagamento":e["status_pagamento"],
            "data_envio":e["data_envio"].isoformat() if e["data_envio"] else None,
            "somente_leitura":True,
        } for e in itens]
        situacoes={r["situacao"]:int(r["quantidade"]) for r in resumo}
        resposta=jsonify(ok=True,itens=entregas,limitado=len(entregas)==100,
                         resumo={"total":sum(situacoes.values()),
                                 "situacoes":situacoes},
                         origem="supervisao",somente_leitura=True,
                         financeiro_alterado=False)
        resposta.headers["Cache-Control"]="private, no-store"
        return resposta
