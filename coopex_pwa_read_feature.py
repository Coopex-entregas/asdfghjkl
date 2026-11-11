"""COOPEX PWA: registro de worker e cache de consultas no HTML original."""
from flask import request, session
from sqlalchemy import text

def install(app_module):
    app=app_module.app
    # Registro idempotente: a chave local e a entrega nascem no mesmo COMMIT.
    # Cria uma única vez; os envios repetidos reencontram o ID oficial.
    with app.app_context():
        with app_module.db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS coopex_offline_entrega_ids (
                    chave VARCHAR(36) PRIMARY KEY,
                    entrega_id INTEGER NOT NULL,
                    payload_hash VARCHAR(64) NOT NULL,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
    @app.get('/coopex-sw.js')
    def coopex_pwa_worker_v2():
        response=app.send_static_file('coopex-sw.js')
        response.headers['Content-Type']='application/javascript; charset=utf-8'
        response.headers['Cache-Control']='no-cache'
        response.headers['Service-Worker-Allowed']='/'
        return response

    @app.after_request
    def coopex_pwa_inject_v2(response):
        if request.method!='GET' or response.status_code!=200 or response.mimetype!='text/html' or response.direct_passthrough:
            return response
        try:
            html=response.get_data(as_text=True)
            if 'id="coopex-pwa-script"' in html or '</body>' not in html.lower():
                return response
            manifest='<link rel="manifest" href="/static/coopex.webmanifest">'
            scripts=(
                '<script defer src="/static/js/coopex-read-cache.js"></script>'
                '<script defer src="/static/js/coopex-local-read.js"></script>'
                '<script id="coopex-pwa-script" defer src="/static/js/coopex-pwa-init.js"></script>'
            )
            if session.get('is_admin') and request.path in ('/admin','/cadastrar_entrega'):
                scripts += '<script defer src="/static/js/coopex-offline-entregas.js"></script>'
            head=html.lower().find('</head>')
            if head>=0:html=html[:head]+manifest+'\n'+html[head:]
            body=html.lower().rfind('</body>')
            response.set_data(html[:body]+scripts+'\n'+html[body:])
        except Exception:
            app.logger.exception('COOPEX PWA: falha de registro')
        return response
