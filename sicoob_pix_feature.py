"""Integração Pix Sicoob - A1. Nunca registrar certificados ou tokens em logs."""
import os, re, io, secrets
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from flask import request, session, abort, jsonify, redirect, url_for, render_template_string
from sqlalchemy import text

API = "https://api.sicoob.com.br/pix/api/v2"
TOKEN = "https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"
SCOPES = "cob.read cob.write pix.read webhook.read webhook.write"

def install(host):
    app, db, Entrega = host.app, host.db, host.Entrega
    from cryptography.fernet import Fernet
    from requests_pkcs12 import Pkcs12Adapter
    import requests

    def cipher():
        key = os.getenv("SICOOB_CONFIG_FERNET_KEY", "").encode()
        if not key:
            raise RuntimeError("Configure SICOOB_CONFIG_FERNET_KEY no ambiente do servidor")
        return Fernet(key)

    with app.app_context():
        binary_type = "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"
        db.session.execute(text(f"""CREATE TABLE IF NOT EXISTS sicoob_pix_config (
            id INTEGER PRIMARY KEY, client_id TEXT NOT NULL, chave TEXT NOT NULL,
            certificado {binary_type} NOT NULL, senha {binary_type} NOT NULL
        )"""))
        db.session.execute(text("""CREATE TABLE IF NOT EXISTS sicoob_pix_cobranca (
            entrega_id INTEGER PRIMARY KEY, txid VARCHAR(35) UNIQUE NOT NULL,
            valor VARCHAR(20) NOT NULL, brcode TEXT,
            e2eid VARCHAR(60) UNIQUE, pago_em VARCHAR(40),
            status VARCHAR(30) NOT NULL DEFAULT 'PENDENTE'
        )"""))
        db.session.commit()

    def admin():
        if not session.get("is_admin"):
            abort(403)

    def master():
        admin()
        if not session.get("is_master") or session.get("user_nome") != "coopex":
            abort(403)

    def csrf():
        if not session.get("sicoob_csrf"):
            session["sicoob_csrf"] = secrets.token_urlsafe(32)
        return session["sicoob_csrf"]

    def check_csrf():
        if not secrets.compare_digest(str(request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""), csrf()):
            abort(403)

    def conf():
        row = db.session.execute(text("SELECT client_id,chave,certificado,senha FROM sicoob_pix_config WHERE id=1")).mappings().first()
        if not row:
            raise ValueError("Configure o Sicoob em Pix Sicoob")
        c = cipher()
        return row["client_id"], row["chave"], c.decrypt(bytes(row["certificado"])), c.decrypt(bytes(row["senha"])).decode()

    def bank():
        client_id, chave, pfx, password = conf()
        s = requests.Session()
        s.mount("https://", Pkcs12Adapter(pkcs12_data=pfx, pkcs12_password=password))
        s.headers.update({"client_id":client_id,"Accept":"application/json"})
        response = s.post(TOKEN, data={"grant_type":"client_credentials","client_id":client_id,"scope":SCOPES},timeout=20)
        response.raise_for_status()
        access = response.json()["access_token"]
        s.headers["Authorization"] = "Bearer " + access
        return s, chave

    def bank_request(method, endpoint, payload=None):
        s, chave = bank()
        try:
            r = s.request(method, API + endpoint, json=payload, timeout=25)
            r.raise_for_status()
            return (r.json() if r.content else {}), chave
        finally:
            s.close()

    def money(value):
        try:
            result=Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if not result.is_finite() or result <= 0:
                raise ValueError()
            return result
        except (InvalidOperation, ValueError):
            raise ValueError("Valor inválido")

    PAGE = """<!doctype html><html lang="pt-br"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Pix Sicoob · COOPEX</title>
<style>body{font:15px system-ui;background:#f3f6fc;color:#15233d;margin:0}.wrap{max-width:790px;margin:36px auto;padding:22px}h1{color:#1452bc}section{background:white;border:1px solid #dde4f0;border-radius:16px;padding:24px;margin:16px 0}label{display:block;margin:12px 0 5px;font-weight:650}input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #bdcbe0;border-radius:9px}button{background:#1452bc;color:white;border:0;border-radius:9px;padding:12px 18px;cursor:pointer}a{color:#1452bc}.hint{font-size:13px;color:#58677e}.warn{background:#fff8ed;padding:12px;border-radius:8px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<div class="wrap"><a href="/admin">← Voltar ao painel</a><h1>Pix Sicoob · COOPEX</h1><p>Configuração exclusiva do administrador master.</p>
<section><h2>Certificado digital A1</h2><p class="hint">Certificado .pfx/.p12 e senha criptografados no banco de dados. A chave de criptografia deve estar somente nas variáveis protegidas do servidor.</p>
<p><strong>Situação:</strong> {{ 'Configurado' if configured else 'Ainda não configurado' }}</p>
<form action="/admin/pix-sicoob/config" method="post" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{{ csrf }}">
<label>Client ID Sicoob</label><input name="client_id" required autocomplete="off">
<label>Chave Pix</label><input name="chave" required autocomplete="off">
<label>Certificado A1 (.pfx ou .p12)</label><input type="file" name="cert" accept=".pfx,.p12" required>
<label>Senha do A1</label><input name="senha" type="password" required autocomplete="new-password">
<p><button type="submit">Salvar configuração protegida</button></p>
</form></section>
<section><h2>Gerar Pix Copia e Cola</h2><p class="hint">Usa o valor já cadastrado na entrega. Não altera a forma de pagamento existente até a confirmação pelo banco.</p>
<form id="pixForm"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Número da entrega</label><input name="entrega_id" type="number" min="1" required><p><button>Gerar/consultar cobrança</button></p></form>
<label>Código Pix Copia e Cola</label><pre id="brcode" class="warn">Nenhum código gerado.</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.querySelector('#brcode').textContent)">Copiar Pix</button>
<p id="situacao"></p><button type="button" onclick="consultar()">Verificar pagamento</button></section>
<script>
let id = null; const token = {{ csrf|tojson }};
document.querySelector('#pixForm').onsubmit=async e=>{
 e.preventDefault();id=e.target.entrega_id.value;
 await chamar('/admin/pix-sicoob/cobrar/'+id,'POST');
};
async function consultar(){if(id)await chamar('/admin/pix-sicoob/verificar/'+id,'POST');}
async function chamar(path,method){
 const el=document.querySelector('#situacao'); el.textContent='Consultando o Sicoob...';
 try{const r=await fetch(path,{method,headers:{'X-CSRF-Token':token}});
 const d=await r.json();if(!r.ok)throw Error(d.erro||'Erro');
 if(d.brcode) document.querySelector('#brcode').textContent=d.brcode;
 el.textContent='Entrega '+d.entrega_id+' · R$ '+d.valor+' · '+d.status;
 }catch(e){el.textContent='Não foi possível concluir: '+e.message}
}
</script></div></html>"""

    @app.get("/admin/pix-sicoob")
    def sicoob_painel():
        master()
        registered = bool(db.session.execute(text("SELECT id FROM sicoob_pix_config WHERE id=1")).first())
        response = app.make_response(render_template_string(PAGE, configured=registered, csrf=csrf()))
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        return response

    @app.post("/admin/pix-sicoob/config")
    def sicoob_config():
        master(); check_csrf()
        blob = request.files.get("cert")
        if not blob or not blob.filename.lower().endswith((".pfx",".p12")):
            return jsonify(erro="Selecione um certificado A1 .pfx ou .p12"),400
        cert = blob.stream.read(65537)
        if len(cert)>65536:
            return jsonify(erro="Certificado muito grande"),400
        senha = request.form.get("senha","")
        client_id = request.form.get("client_id","").strip()
        chave = request.form.get("chave","").strip()
        if not senha or not client_id or not chave:
            return jsonify(erro="Preencha todos os campos"),400
        # Valida a senha e o conteúdo do certificado antes de armazenar.
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        try:
            private, cert_obj, _ = load_key_and_certificates(cert,senha.encode())
            if not private or not cert_obj:
                raise ValueError()
        except Exception:
            return jsonify(erro="Certificado inválido ou senha incorreta"),400
        c = cipher()
        db.session.execute(text("DELETE FROM sicoob_pix_config WHERE id=1"))
        db.session.execute(text("INSERT INTO sicoob_pix_config(id,client_id,chave,certificado,senha) VALUES(1,:client,:chave,:cert,:pwd)"),
                           {"client":client_id,"chave":chave,"cert":c.encrypt(cert),"pwd":c.encrypt(senha.encode())})
        db.session.commit()
        return redirect("/admin/pix-sicoob",303)

    def get_record(entrega_id):
        return db.session.execute(text("SELECT entrega_id,txid,valor,brcode,status,e2eid FROM sicoob_pix_cobranca WHERE entrega_id=:id"),{"id":entrega_id}).mappings().first()

    def result(entrega_id,record):
        return jsonify(entrega_id=entrega_id,txid=record["txid"],valor=record["valor"],brcode=record["brcode"],status=record["status"])

    def _sicoob_bank_paid(record):
        data,_=bank_request("GET","/cob/"+record["txid"])
        pix=data.get("pix") or []
        valid=[p for p in pix if p.get("endToEndId") and p.get("txid",record["txid"])==record["txid"]]
        received=sum((Decimal(str(p.get("valor","0"))) for p in valid),Decimal("0"))
        return data.get("status")=="CONCLUIDA" and bool(valid) and received==Decimal(record["valor"])

    @app.post("/admin/pix-sicoob/cobrar/<int:entrega_id>")
    def sicoob_cobrar(entrega_id):
        admin();check_csrf()
        entrega=db.session.get(Entrega,entrega_id)
        if not entrega:
            return jsonify(erro="Entrega não encontrada"),404
        if (entrega.pagamento or "").strip().casefold() not in ("pix (cooperativa)", "pix cooperativa", "pix da cooperativa"):
            return jsonify(erro="Apenas entregas com pagamento Pix Cooperativa podem gerar cobrança Sicoob"),409
        record=get_record(entrega_id)
        # Nao entregar uma chave de uma cobranca ja paga (manualmente ou no banco).
        if (entrega.status_pagamento or "").lower()=="pago":
            return jsonify(erro="Esta entrega ja esta paga; nao pode gerar nem copiar outro Pix."),409
        if record:
            if record["status"]=="PAGO":
                return jsonify(erro="Pix ja pago. Nao gere ou copie novamente."),409
            try:
                # Consulta o banco antes de reutilizar a cobranca pendente.
                return sicoob_verificar(entrega_id) if _sicoob_bank_paid(record) else result(entrega_id,record)
            except Exception:
                return jsonify(erro="Nao foi possivel conferir o Pix existente no Sicoob. Tente consultar antes de copiar."),502
        if (entrega.status_pagamento or "").lower()=="pago":
            return jsonify(erro="Esta entrega já está marcada como paga"),409
        amount=money(entrega.valor)
        txid="COOPEX"+str(entrega_id).zfill(10)+secrets.token_hex(9).upper()
        _, chave = conf()[0:2]
        payload={"calendario":{"expiracao":3600},"valor":{"original":str(amount)},"chave":chave,"solicitacaoPagador":"Entrega COOPEX "+str(entrega_id)}
        try:
            data,_ = bank_request("PUT","/cob/"+txid,payload)
        except Exception:
            app.logger.exception("Falha na criação da cobrança Sicoob")
            return jsonify(erro="Falha na comunicação com o Sicoob. Consulte o administrador."),502
        brcode=data.get("brcode") or (data.get("loc") or {}).get("brcode")
        if not brcode:
            # Não cria outra cobrança automaticamente; deve haver reconciliação manual.
            return jsonify(erro="Sicoob não retornou brcode; consulte a cobrança pelo txid antes de tentar novamente"),502
        db.session.execute(text("INSERT INTO sicoob_pix_cobranca(entrega_id,txid,valor,brcode,status) VALUES(:id,:tx,:v,:bc,'PENDENTE')"),
                           {"id":entrega_id,"tx":txid,"v":str(amount),"bc":brcode})
        db.session.commit()
        return result(entrega_id,get_record(entrega_id))

    @app.post("/admin/pix-sicoob/verificar/<int:entrega_id>")
    def sicoob_verificar(entrega_id):
        admin();check_csrf()
        record=get_record(entrega_id)
        if not record:
            return jsonify(erro="Entrega sem cobrança Pix"),404
        try:
            data,_=bank_request("GET","/cob/"+record["txid"])
        except Exception:
            app.logger.exception("Falha na consulta da cobrança Sicoob")
            return jsonify(erro="Não foi possível consultar o banco"),502
        pix=data.get("pix") or []
        expected=Decimal(record["valor"])
        matches=[p for p in pix if p.get("txid",record["txid"])==record["txid"] and p.get("endToEndId")]
        received=sum((Decimal(str(p.get("valor","0"))) for p in matches),Decimal("0"))
        if data.get("status")=="CONCLUIDA" and received==expected and matches:
            e=db.session.get(Entrega,entrega_id)
            # Recusa baixa se houver intervenção manual com valor/formato divergente.
            if e and money(e.valor)==expected:
                e.status_pagamento="pago"
                db.session.execute(text("UPDATE sicoob_pix_cobranca SET status='PAGO',e2eid=:e,pago_em=:d WHERE entrega_id=:id"),
                                   {"id":entrega_id,"e":matches[0]["endToEndId"],"d":datetime.utcnow().isoformat(),"id":entrega_id})
                db.session.commit()
        return result(entrega_id,get_record(entrega_id))

    # Painel de diagnóstico: APENAS leitura e sem expor token/certificado.
    TEST_PAGE = """<!doctype html><html lang="pt-br"><meta charset="utf-8">
<title>COOPEX | Diagnóstico Sicoob</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:15px system-ui;background:#f4f7fd;color:#172b4d;margin:0}.main{max-width:760px;margin:32px auto;padding:18px}.box{background:white;border:1px solid #dce5f4;padding:24px;border-radius:14px;margin:16px 0}h1{color:#154db4}button{background:#1452bc;color:white;border:0;border-radius:8px;padding:12px 20px;cursor:pointer;margin:6px}code{word-break:break-all}#result{white-space:pre-wrap;background:#f3f7fc;padding:15px;border-radius:8px}a{color:#154db4}</style>
<div class="main"><a href="/admin/pix-sicoob">← Configurações Pix</a><h1>Diagnóstico da API Sicoob</h1>
<div class="box"><h3>Testes seguros e somente de leitura</h3>
<p>1. Verificar se o certificado A1 está cadastrado.</p>
<p>2. Solicitar token OAuth usando o certificado mTLS.</p>
<p>3. Consultar a API Pix Recebimentos (webhooks cadastrados).</p>
<p>Nenhum teste cria cobranças, altera webhooks ou movimenta dinheiro.</p>
<button onclick="run('config')">Testar cadastro A1</button>
<button onclick="run('token')">Testar autenticação</button>
<button onclick="run('pix')">Testar acesso à API Pix</button>
<h3>Resultado</h3><div id="result">Aguardando teste.</div></div></div>
<script>async function run(step){
let el=document.getElementById('result');el.textContent='Testando...';
try{let r=await fetch('/admin/pix-sicoob/diagnostico/'+step,{method:'POST',headers:{'X-CSRF-Token':{{ csrf|tojson }}}});
let d=await r.json();el.textContent=JSON.stringify(d,null,2);}catch(e){el.textContent='Falha de comunicação com o servidor.'}
}</script></html>"""

    @app.get("/admin/pix-sicoob/diagnostico")
    def sicoob_diagnostico_pagina():
        master()
        response=app.make_response(render_template_string(TEST_PAGE,csrf=csrf()))
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Frame-Options"]="DENY"
        return response

    @app.post("/admin/pix-sicoob/diagnostico/<step>")
    def sicoob_diagnostico_acao(step):
        master(); check_csrf()
        if step not in ("config","token","pix"):
            abort(404)
        try:
            client_id,chave,pfx,password=conf()
            from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
            private,certificate,_=load_key_and_certificates(pfx,password.encode())
            if not private or not certificate:
                return jsonify(sucesso=False,etapa="certificado",mensagem="Arquivo PFX inválido ou sem chave privada"),422
            if step=="config":
                return jsonify(sucesso=True,etapa="certificado",mensagem="PFX e chave privada carregados. Client ID e chave Pix cadastrados.")
        except Exception as e:
            # Não vazar detalhes de descriptografia, chaves ou senhas.
            return jsonify(sucesso=False,etapa="configuração",mensagem="Falha na configuração protegida ou certificado A1. Verifique os dados e a chave de criptografia."),422
        import requests
        from requests_pkcs12 import Pkcs12Adapter
        with requests.Session() as s:
            try:
                s.mount("https://",Pkcs12Adapter(pkcs12_data=pfx,pkcs12_password=password))
                resp=s.post(TOKEN,data={"grant_type":"client_credentials","client_id":client_id,"scope":SCOPES},
                    headers={"Content-Type":"application/x-www-form-urlencoded"},timeout=20)
                if resp.status_code!=200:
                    return jsonify(sucesso=False,etapa="OAuth mTLS",http=resp.status_code,
                        mensagem="Sicoob recusou a emissão do token. Verifique aprovação, certificado vinculado, permissões e credenciais."),200
                token=resp.json().get("access_token")
                if not token:
                    return jsonify(sucesso=False,etapa="OAuth mTLS",mensagem="Resposta do Sicoob sem access_token."),200
                if step=="token":
                    return jsonify(sucesso=True,etapa="OAuth mTLS",mensagem="Token emitido com sucesso. Token não é exibido."),200
                # GET é somente leitura; nunca devolva valores recebidos do banco ao navegador.
                r=s.get(API+"/webhook",headers={"Authorization":"Bearer "+token,"client_id":client_id,"Accept":"application/json"},timeout=20)
                return jsonify(sucesso=(r.status_code==200),etapa="Pix Recebimentos GET /webhook",http=r.status_code,
                    mensagem=("Consulta autorizada." if r.status_code==200 else "Resposta do Sicoob. Verifique permissão webhook.read ou autorização da aplicação.")),200
            except requests.exceptions.SSLError:
                return jsonify(sucesso=False,etapa="TLS",mensagem="Falha na validação TLS/mTLS. Confira o certificado e a cadeia de confiança."),200
            except requests.exceptions.Timeout:
                return jsonify(sucesso=False,etapa="rede",mensagem="Tempo de conexão com o Sicoob excedido."),200
            except requests.exceptions.RequestException:
                return jsonify(sucesso=False,etapa="rede",mensagem="Falha de conexão HTTPS. Consulte logs protegidos do Render."),200
            except (ValueError,KeyError):
                return jsonify(sucesso=False,etapa="OAuth",mensagem="Resposta de autenticação não reconhecida."),200

    # Cobranças avulsas não dependem de entrega. Valor imutável após criação.
    with app.app_context():
        db.session.execute(text("""CREATE TABLE IF NOT EXISTS sicoob_pix_avulso (
            id VARCHAR(36) PRIMARY KEY, txid VARCHAR(35) UNIQUE NOT NULL,
            valor VARCHAR(20) NOT NULL, brcode TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
            e2eid VARCHAR(60), criado_em VARCHAR(40) NOT NULL, pago_em VARCHAR(40)
        )"""))
        db.session.commit()
        from sqlalchemy import inspect
        cols = {col["name"] for col in inspect(db.engine).get_columns("sicoob_pix_avulso")}
        if "cliente_nome" not in cols:
            db.session.execute(text("ALTER TABLE sicoob_pix_avulso ADD COLUMN cliente_nome VARCHAR(160)"))
            db.session.commit()

    @app.post("/admin/pix-sicoob/avulso")
    def sicoob_avulso_criar():
        admin();check_csrf()
        payload=request.get_json(silent=True) or {}
        try:
            amount=money(str(payload.get("valor") or "").replace(",","."))
        except ValueError:
            return jsonify(erro="Informe um valor positivo, com duas casas decimais."),400
        if amount > Decimal("100000.00"):
            return jsonify(erro="Valor fora do limite de segurança."),400
        nome_cliente=str(payload.get("cliente_nome") or "").strip()
        if not nome_cliente or len(nome_cliente)>160:
            return jsonify(erro="Informe o nome do cliente (até 160 caracteres)."),400
        import uuid
        uid=str(uuid.uuid4())
        txid="COOPEXAV"+uuid.uuid4().hex[:26].upper()
        try:
            _,chave=conf()[0:2]
            dados,_=bank_request("PUT","/cob/"+txid,
                {"calendario":{"expiracao":3600},"valor":{"original":str(amount)},
                 "chave":chave,"solicitacaoPagador":"COOPEX Pagamento antecipado"})
            brcode=dados.get("brcode") or dados.get("pixCopiaECola")
            if not brcode:
                # Consulte o mesmo txid; nunca gere cobrança substituta automaticamente.
                dados,_=bank_request("GET","/cob/"+txid)
                brcode=dados.get("brcode") or dados.get("pixCopiaECola")
            if not brcode:
                return jsonify(erro="Cobrança enviada ao banco, mas código Pix não disponível. Não tente gerar novamente antes de conciliar."),502
            db.session.execute(text("""INSERT INTO sicoob_pix_avulso(id,txid,valor,brcode,status,criado_em,cliente_nome)
                VALUES(:id,:tx,:valor,:br,'PENDENTE',:criado,:cliente_nome)"""),
                {"id":uid,"tx":txid,"valor":str(amount),"br":brcode,"criado":datetime.utcnow().isoformat(),"cliente_nome":nome_cliente})
            db.session.commit()
            return jsonify(id=uid,cliente_nome=nome_cliente,valor=str(amount),brcode=brcode,status="PENDENTE")
        except Exception:
            app.logger.exception("Falha ao criar cobrança Pix avulsa")
            return jsonify(erro="Não foi possível confirmar a geração no Sicoob. Verifique antes de repetir."),502

    @app.get("/admin/pix-sicoob/avulsos")
    def sicoob_avulsos_listar():
        admin()
        rows=db.session.execute(text("""SELECT id,cliente_nome,valor,status,criado_em,pago_em,brcode
            FROM sicoob_pix_avulso ORDER BY criado_em DESC LIMIT 100""")).mappings().all()
        return jsonify(cobrancas=[dict(row) for row in rows])

    @app.post("/admin/pix-sicoob/avulso/<uid>/verificar")
    def sicoob_avulso_verificar(uid):
        admin();check_csrf()
        row=db.session.execute(text("SELECT * FROM sicoob_pix_avulso WHERE id=:id"),{"id":uid}).mappings().first()
        if not row:
            abort(404)
        if row["status"]=="PAGO":
            return jsonify(id=uid,status="PAGO")
        try:
            dados,_=bank_request("GET","/cob/"+row["txid"])
            pix=dados.get("pix") or []
            recebidos=[p for p in pix if p.get("endToEndId") and p.get("txid",row["txid"])==row["txid"]]
            soma=sum((Decimal(str(p.get("valor","0"))) for p in recebidos),Decimal("0"))
            if dados.get("status")=="CONCLUIDA" and soma==Decimal(row["valor"]) and recebidos:
                db.session.execute(text("""UPDATE sicoob_pix_avulso
                    SET status='PAGO', e2eid=:e, pago_em=:dt WHERE id=:id AND status='PENDENTE'"""),
                    {"e":recebidos[0]["endToEndId"],"dt":datetime.utcnow().isoformat(),"id":uid})
                db.session.commit()
            current=db.session.execute(text("SELECT status FROM sicoob_pix_avulso WHERE id=:id"),{"id":uid}).scalar()
            return jsonify(id=uid,status=current)
        except Exception:
            app.logger.exception("Falha na consulta do Pix avulso")
            return jsonify(erro="Falha na consulta ao Sicoob."),502

    @app.get("/admin/pix-sicoob/csrf")
    def sicoob_ui_csrf():
        admin()
        response=jsonify(csrf=csrf())
        response.headers["Cache-Control"]="no-store"
        return response
