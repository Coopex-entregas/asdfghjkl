"""COOPEX PWA – registro seguro sem modificar regras do Flask.

Serve o worker na raiz e injeta o registro nas páginas HTML existentes.
Nunca modifica endpoints de escrita, sessões ou respostas JSON.
"""
from flask import request


def install(app_module):
    app = app_module.app

    @app.get("/coopex-sw.js")
    def coopex_pwa_root_worker():
        response = app.send_static_file("coopex-sw.js")
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache, max-age=0"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.after_request
    def coopex_pwa_html_registration(response):
        if (request.method != "GET" or response.status_code != 200
                or response.mimetype != "text/html"
                or response.direct_passthrough):
            return response
        try:
            body = response.get_data(as_text=True)
            if 'id="coopex-pwa-script"' in body or "</body>" not in body.lower():
                return response
            manifest = '<link rel="manifest" href="/static/coopex.webmanifest">'
            script = '<script id="coopex-pwa-script" defer src="/static/js/coopex-pwa-init.js"></script>'
            head_pos = body.lower().find("</head>")
            if head_pos >= 0:
                body = body[:head_pos] + manifest + "\n" + body[head_pos:]
            body_pos = body.lower().rfind("</body>")
            body = body[:body_pos] + script + "\n" + body[body_pos:]
            response.set_data(body)
        except Exception:
            app.logger.exception("Falha ao incluir registro PWA COOPEX")
        return response
