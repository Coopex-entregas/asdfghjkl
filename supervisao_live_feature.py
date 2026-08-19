"""Atualização automática do painel Supervisão para múltiplos operadores.

O recurso evita depender de F5 manual. O navegador consulta um estado leve do
banco e recarrega a tela somente quando percebe mudança real em dados
operacionais, preservando a posição de rolagem e aguardando o fim de uma edição.
"""

import hashlib
import json
from datetime import datetime

from flask import jsonify, request, session, url_for


def install(app_module):
    app = app_module.app

    if app.extensions.get("supervisao_live_installed"):
        return
    app.extensions["supervisao_live_installed"] = True

    Entrega = app_module.Entrega
    ListaEspera = app_module.ListaEspera
    Cooperado = app_module.Cooperado
    SolicitacaoAlteracaoValor = app_module.SolicitacaoAlteracaoValor
    ConfigSistema = getattr(app_module, "ConfigSistema", None)

    @app.get("/api/admin/live-state")
    def api_admin_live_state():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 401

        try:
            partes = []

            entregas = (
                Entrega.query
                .order_by(Entrega.id.desc())
                .limit(600)
                .all()
            )
            for e in entregas:
                partes.append((
                    "e", e.id, e.cliente, e.bairro,
                    round(float(e.valor or 0), 2), e.cooperado_id,
                    e.status, e.status_pagamento, e.pagamento,
                    e.recebido_por, e.status_corrida,
                    e.data_envio.isoformat() if e.data_envio else "",
                    e.data_atribuida.isoformat() if e.data_atribuida else "",
                ))

            fila = (
                ListaEspera.query
                .order_by(
                    ListaEspera.pos.asc(),
                    ListaEspera.created_at.asc(),
                    ListaEspera.id.asc(),
                )
                .all()
            )
            for item in fila:
                partes.append((
                    "f", item.id, item.cooperado_id, item.nome, item.pos,
                    item.created_at.isoformat() if item.created_at else "",
                ))

            solicitacoes = (
                SolicitacaoAlteracaoValor.query
                .order_by(SolicitacaoAlteracaoValor.id.desc())
                .limit(300)
                .all()
            )
            for item in solicitacoes:
                partes.append((
                    "s", item.id, item.entrega_id, item.cooperado_id,
                    round(float(item.valor_original or 0), 2),
                    round(float(item.valor_solicitado or 0), 2),
                    item.status,
                    item.criado_em.isoformat() if item.criado_em else "",
                    item.analisado_em.isoformat() if item.analisado_em else "",
                ))

            cooperados = Cooperado.query.order_by(Cooperado.id.asc()).all()
            for c in cooperados:
                # Não entram GPS/ping/online aqui, pois esses campos mudam o tempo
                # todo e causariam recargas desnecessárias do painel.
                partes.append(("c", c.id, c.nome, bool(c.ativo)))

            if ConfigSistema is not None:
                configs = ConfigSistema.query.order_by(ConfigSistema.chave.asc()).all()
                for item in configs:
                    partes.append(("cfg", item.chave, item.valor))

            bruto = json.dumps(
                partes,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            assinatura = hashlib.blake2s(
                bruto.encode("utf-8"), digest_size=16
            ).hexdigest()

            return jsonify(
                ok=True,
                assinatura=assinatura,
                entregas=len(entregas),
                fila=len(fila),
                solicitacoes=len(solicitacoes),
                servidor=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            )
        except Exception as exc:
            app.logger.warning("Falha no Supervisão ao vivo: %s", exc)
            return jsonify(ok=False, error="live-state indisponível"), 503

    @app.after_request
    def supervisao_injetar_live(response):
        """Inclui o cliente de atualização apenas no painel /admin."""
        try:
            if request.path != "/admin":
                return response
            if response.status_code != 200 or response.mimetype != "text/html":
                return response

            html = response.get_data(as_text=True)

            # O admin.html atual já possui o cliente "ao vivo" inline.
            # Não injeta novamente e evita corromper scripts que montam HTML
            # contendo a sequência literal "</body>" (ex.: impressão de cupom).
            if (
                "supervisao_live.js" in html
                or 'id="supervisao-live-sync"' in html
            ):
                return response

            tag = (
                '<script src="'
                + url_for("static", filename="js/supervisao_live.js")
                + '"></script>'
            )

            # Se for necessário injetar, usa o ÚLTIMO </body> do documento.
            # Nunca o primeiro, pois pode existir "</body>" dentro de uma
            # string JavaScript e isso faria o navegador exibir código na tela.
            pos = html.rfind("</body>")
            if pos >= 0:
                html = html[:pos] + tag + html[pos:]
            else:
                html += tag

            response.set_data(html)
        except Exception as exc:
            app.logger.warning("Falha ao injetar Supervisão ao vivo: %s", exc)
        return response
