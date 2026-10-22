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
        from sqlalchemy import inspect
        cols={v["name"] for v in inspect(db.engine).get_columns("sicoob_pix_cobranca")}
        for col in ("criado_em", "expira_em"):
            if col not in cols:
                db.session.execute(text("ALTER TABLE sicoob_pix_cobranca ADD COLUMN "+col+" VARCHAR(40)"))
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
                if _sicoob_bank_paid(record):
                    sicoob_verificar(entrega_id)
                    return jsonify(erro="O Sicoob confirmou que este Pix ja foi pago. Nao copie novamente."),409
                return result(entrega_id,record)
            except Exception:
                return jsonify(erro="Nao foi possivel conferir o Pix existente no Sicoob. Tente consultar antes de copiar."),502
        if (entrega.status_pagamento or "").lower()=="pago":
            return jsonify(erro="Esta entrega já está marcada como paga"),409
        amount=money(entrega.valor)
        txid="COOPEX"+str(entrega_id).zfill(10)+secrets.token_hex(9).upper()
        _, chave = conf()[0:2]
        payload={"calendario":{"expiracao":86400},"valor":{"original":str(amount)},"chave":chave,"solicitacaoPagador":"Entrega COOPEX "+str(entrega_id)}
        try:
            data,_ = bank_request("PUT","/cob/"+txid,payload)
        except Exception:
            app.logger.exception("Falha na criação da cobrança Sicoob")
            return jsonify(erro="Falha na comunicação com o Sicoob. Consulte o administrador."),502
        brcode=data.get("brcode") or (data.get("loc") or {}).get("brcode")
        if not brcode:
            # Não cria outra cobrança automaticamente; deve haver reconciliação manual.
            return jsonify(erro="Sicoob não retornou brcode; consulte a cobrança pelo txid antes de tentar novamente"),502
        db.session.execute(text("INSERT INTO sicoob_pix_cobranca(entrega_id,txid,valor,brcode,status,criado_em,expira_em) VALUES(:id,:tx,:v,:bc,'PENDENTE',:dt,:exp)"),
                           {"id":entrega_id,"tx":txid,"v":str(amount),"bc":brcode,"dt":datetime.utcnow().isoformat(),"exp":(datetime.utcnow()+__import__("datetime").timedelta(hours=24)).isoformat()})
        db.session.commit()
        return result(entrega_id,get_record(entrega_id))

    @app.post("/admin/pix-sicoob/verificar/<int:entrega_id>")
    def sicoob_verificar(entrega_id):
        admin();check_csrf()
        record=get_record(entrega_id)
        if not record:
            return jsonify(erro="Entrega sem cobrança Pix"),404
        # A baixa bancária já confirmada é definitiva, inclusive se algum
        # formulário antigo tiver alterado o status local indevidamente.
        if record["status"]=="PAGO" or record["e2eid"]:
            entrega=db.session.get(Entrega,entrega_id)
            if entrega and (entrega.status_pagamento or "").lower()!="pago":
                entrega.status_pagamento="pago"
                db.session.commit()
            return result(entrega_id,get_record(entrega_id))
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

    @app.post("/admin/pix-sicoob/entrega/<int:entrega_id>/modificar")
    def sicoob_entrega_modificar(entrega_id):
        admin();check_csrf()
        import uuid
        from datetime import timedelta
        try:
            suffix=" FOR UPDATE" if db.engine.dialect.name=="postgresql" else ""
            row=db.session.execute(text("SELECT * FROM sicoob_pix_cobranca WHERE entrega_id=:id"+suffix),{"id":entrega_id}).mappings().first()
            if not row:
                db.session.rollback();return jsonify(erro="Pix de entrega não encontrado."),404
            entrega=db.session.get(Entrega,entrega_id)
            if row["status"]!="PENDENTE" or (entrega and str(entrega.status_pagamento).lower()=="pago"):
                db.session.rollback();return jsonify(erro="Esta entrega já foi paga ou não está pendente."),409
            original,_=bank_request("GET","/cob/"+row["txid"])
            pix=original.get("pix") or []
            if original.get("status")=="CONCLUIDA" or pix:
                db.session.rollback()
                return jsonify(erro="Sicoob informa pagamento ou movimentação. Verifique antes de substituir."),409
            if original.get("status")=="ATIVA":
                bank_request("PATCH","/cob/"+row["txid"],{"status":"REMOVIDA_PELO_USUARIO_RECEBEDOR"})
            confirmed,_=bank_request("GET","/cob/"+row["txid"])
            if confirmed.get("status")!="REMOVIDA_PELO_USUARIO_RECEBEDOR" or confirmed.get("pix"):
                db.session.rollback()
                return jsonify(erro="O Sicoob não confirmou o cancelamento do Pix anterior."),409
            newtx="COOPEX"+str(entrega_id).zfill(10)+__import__("secrets").token_hex(9).upper()
            _,chave=conf()[0:2]
            novo,_=bank_request("PUT","/cob/"+newtx,{
                "calendario":{"expiracao":86400},
                "valor":{"original":str(row["valor"])},
                "chave":chave,
                "solicitacaoPagador":"Entrega COOPEX "+str(entrega_id)})
            codigo=novo.get("brcode") or novo.get("pixCopiaECola")
            if not codigo:
                novo,_=bank_request("GET","/cob/"+newtx)
                codigo=novo.get("brcode") or novo.get("pixCopiaECola")
            if not codigo:
                db.session.rollback()
                return jsonify(erro="O Sicoob criou a cobrança, mas não informou o código. Não repita a operação antes de conciliar."),502
            now=datetime.utcnow()
            db.session.execute(text("""CREATE TABLE IF NOT EXISTS sicoob_pix_entrega_renovacoes
                (id VARCHAR(36) PRIMARY KEY, entrega_id INTEGER NOT NULL, txid_antigo VARCHAR(35) NOT NULL,
                 txid_novo VARCHAR(35) NOT NULL, criado_em VARCHAR(40) NOT NULL)"""))
            db.session.execute(text("""INSERT INTO sicoob_pix_entrega_renovacoes(id,entrega_id,txid_antigo,txid_novo,criado_em)
                VALUES(:id,:eid,:antigo,:novo,:dt)"""),{
                "id":str(uuid.uuid4()),"eid":entrega_id,"antigo":row["txid"],
                "novo":newtx,"dt":now.isoformat()})
            db.session.execute(text("""UPDATE sicoob_pix_cobranca SET txid=:tx,brcode=:br,
                criado_em=:dt,expira_em=:exp WHERE entrega_id=:eid AND status='PENDENTE'"""),{
                "tx":newtx,"br":codigo,"dt":now.isoformat(),
                "exp":(now+timedelta(hours=24)).isoformat(),"eid":entrega_id})
            db.session.commit()
            return jsonify(brcode=codigo,valor=row["valor"],status="PENDENTE",expira_em=(now+timedelta(hours=24)).isoformat())
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha ao modificar Pix de entrega")
            return jsonify(erro="Não foi possível confirmar a substituição do Pix no banco."),502

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

        if "expira_em" not in cols:
            db.session.execute(text("ALTER TABLE sicoob_pix_avulso ADD COLUMN expira_em VARCHAR(40)"))
            db.session.commit()
        if "origem_renovacao" not in cols:
            db.session.execute(text("ALTER TABLE sicoob_pix_avulso ADD COLUMN origem_renovacao VARCHAR(36)"))
            db.session.commit()
        db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pix_avulso_origem ON sicoob_pix_avulso(origem_renovacao)"))
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
                {"calendario":{"expiracao":86400},"valor":{"original":str(amount)},
                 "chave":chave,"solicitacaoPagador":"COOPEX Pagamento antecipado"})
            brcode=dados.get("brcode") or dados.get("pixCopiaECola")
            if not brcode:
                # Consulte o mesmo txid; nunca gere cobrança substituta automaticamente.
                dados,_=bank_request("GET","/cob/"+txid)
                brcode=dados.get("brcode") or dados.get("pixCopiaECola")
            if not brcode:
                return jsonify(erro="Cobrança enviada ao banco, mas código Pix não disponível. Não tente gerar novamente antes de conciliar."),502
            db.session.execute(text("""INSERT INTO sicoob_pix_avulso(id,txid,valor,brcode,status,criado_em,cliente_nome,expira_em)
                VALUES(:id,:tx,:valor,:br,'PENDENTE',:criado,:cliente_nome,:exp)"""),
                {"id":uid,"tx":txid,"valor":str(amount),"br":brcode,"criado":datetime.utcnow().isoformat(),"cliente_nome":nome_cliente,"exp":(datetime.utcnow()+__import__("datetime").timedelta(hours=24)).isoformat()})
            db.session.commit()
            return jsonify(id=uid,cliente_nome=nome_cliente,valor=str(amount),brcode=brcode,status="PENDENTE")
        except Exception:
            app.logger.exception("Falha ao criar cobrança Pix avulsa")
            return jsonify(erro="Não foi possível confirmar a geração no Sicoob. Verifique antes de repetir."),502

    HISTORY_PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>COOPEX | Histórico Pix Sicoob</title><style>body{margin:0;background:#f3f6fc;color:#19345e;font:15px system-ui}header{background:#1751b4;color:white;padding:17px 22px;display:flex;justify-content:space-between}header a{color:white}main{width:100%;max-width:none;box-sizing:border-box;margin:12px auto;padding:0 12px}section{background:white;border-radius:12px;padding:16px;width:100%;box-sizing:border-box}.tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:15px}button,select,input{padding:9px;border:1px solid #c8d5e9;border-radius:9px;background:white;color:#183d80}button{cursor:pointer;background:#eaf1ff}table{width:100%;min-width:850px;border-collapse:collapse}th,td{text-align:left;padding:11px;border-bottom:1px solid #e2e8f2}th{background:#ecf2fc}.table{overflow:auto}.paid{color:#067c4a;font-weight:700}.pending{color:#a16a00;font-weight:700}.cancel{color:#9e3333;font-weight:700}</style><style id="coopex-pix-history-redesign">
*{box-sizing:border-box}
body{background:#f3f7ff;color:#183563;font:14px Inter,system-ui,-apple-system,Segoe UI,sans-serif}
header{min-height:60px;padding:12px 24px;background:linear-gradient(105deg,#103d95,#1154bf 56%,#10419b);align-items:center;box-shadow:0 2px 10px #16488a22}
header strong{display:flex;align-items:center;gap:15px;font-size:16px;letter-spacing:.05px}
header strong:before{content:'◆';font-size:27px;line-height:1;color:#5de0b5;text-shadow:0 0 3px #2acb9b}
header a{font-size:13px;font-weight:650;color:#fff}
main{padding:18px 19px;margin:0 auto}
section{border:1px solid #e0eaf9;border-radius:12px;box-shadow:0 5px 18px #153d7409;padding:20px;margin-bottom:18px}
.tools{align-items:end;gap:12px;row-gap:15px;margin:0 0 24px}
.tools:before{content:'◈  Filtros';font-size:16px;font-weight:800;color:#173d84;flex-basis:100%;padding:0 0 4px}
.tools input,.tools select{border:1px solid #cbdaf0;border-radius:8px;min-height:39px;background:#fff;color:#223e75;padding:8px 12px;font-size:13px}
.tools label{color:#1c3768;font-size:13px;font-weight:600}
.tools label input[type="checkbox"]{min-height:auto;accent-color:#1657d9}
.tools #busca{min-width:240px}
.tools #refresh{background:linear-gradient(180deg,#1561e9,#0a49c9);border:1px solid #225ad6;color:white;font-weight:800;min-height:40px;padding:9px 17px;border-radius:8px}
#updated{font-weight:700;font-size:13px;align-self:center}
#summary{font-weight:700;color:#254578;padding-bottom:14px}
table{border-collapse:separate;border-spacing:0;width:100%}
th{background:#edf3fc;color:#203c72;font-weight:750;font-size:13px;padding:14px 12px}
th:first-child{border-radius:8px 0 0 8px}
th:last-child{border-radius:0 8px 8px 0}
td{padding:13px 12px;color:#27406d;border-bottom:1px solid #e3ebf7;font-size:13px}
td button{border:1px solid #c9dafc;border-radius:9px;background:#edf4ff;color:#0751d2;font-weight:650;padding:9px 10px;margin:2px 2px 2px 0;font-size:12px;white-space:nowrap}
td button:hover{background:#ddeafb}
td a{font-weight:750;color:#064ac2}
td.paid,td.pending,td.cancel{font-weight:800}
td.paid{color:#058553}td.pending{color:#bb7300}td.cancel{color:#d12b52}
.table{overflow-x:auto}
</style><style id="coopex-pix-history-reference-exact">
body{background:#f3f7ff;font-family:Inter,system-ui,'Segoe UI',sans-serif!important;font-size:14px!important;color:#213e70}
header{height:60px;background:linear-gradient(105deg,#174399 0%,#1359c5 58%,#164cae 100%);padding:0 32px;display:flex;align-items:center;box-shadow:0 2px 8px #143f8115}
header strong{font-size:16px;display:flex;align-items:center;gap:17px;font-weight:800}
header strong:before{content:none!important;display:none!important}
.sicoob-brand-icon{flex:none;display:block;filter:drop-shadow(0 1px 2px #011b3433)}
header a{font-size:13px;display:inline-block;text-decoration:none}
main{margin:0!important;padding:19px!important;max-width:none!important}
section{background:white!important;border:1px solid #dfebfa!important;border-radius:11px!important;box-shadow:0 7px 21px #31558f0c!important;padding:19px!important;margin:0 0 18px!important}
.history-filter-heading{display:flex;gap:14px;align-items:center;margin-bottom:20px}
.history-filter-icon{height:40px;width:40px;border-radius:11px;background:#edf3fe;display:flex;align-items:center;justify-content:center;color:#1555d3;font-size:29px}
.history-filter-heading b{font-size:16px;color:#213e75;display:block}
.history-filter-heading small{display:block;font-size:12px;margin-top:3px;color:#8093b5}
.tools{display:flex;align-items:end;flex-wrap:wrap;gap:12px;margin:0!important}
.tools:before{display:none!important;content:none!important}
.tools select,.tools input{height:41px!important;background:white!important;border:1px solid #cbdaf1!important;border-radius:8px!important;padding:8px 12px!important;font-size:13px!important}
.tools select:first-child{width:230px}
.tools select:nth-child(2){width:160px}
.tools label{font-size:13px;color:#23416f;font-weight:700}
.tools label input[type=date]{width:150px}
.tools label:has(input[type=checkbox]){display:flex;align-items:center;gap:5px;white-space:nowrap;margin-bottom:10px}
.tools input[type=checkbox]{height:auto!important}
.tools #busca{width:245px;min-width:150px!important}
.tools #refresh{background:linear-gradient(180deg,#1765ef,#064acb)!important;color:#fff!important;border-radius:8px!important;height:42px!important;font-size:13px!important;padding:0 18px!important}
#updated{color:#54719d;font-size:12px;font-weight:500}
.history-results{padding:20px 19px 23px!important}
#summary{font-size:13px;color:#294a85;font-weight:700;margin:0;padding:0 0 22px!important}
table{border-spacing:0!important;min-width:1000px}
th{background:#edf3fb!important;color:#20396a!important;padding:14px 12px!important;font-size:13px!important;text-align:left}
td{padding:11px 12px!important;font-size:13px!important;line-height:1.5!important;color:#2e4676!important}
td button{padding:8px 11px!important;border-radius:8px!important;font-weight:700!important;font-size:12px!important;background:#eef4ff!important;color:#1155ce!important;border:1px solid #cbdbfc!important}
td.paid{color:#058953!important}td.pending{color:#bb7908!important}td.cancel{color:#d52d5b!important}
</style><style id="coopex-pix-exact-reference-v2">
body{background:#f2f6ff!important}
header{min-height:64px!important;background:linear-gradient(110deg,#073681,#145bbb 61%,#094393)!important;padding:0 35px!important}
header strong{font-size:19px!important;font-weight:800!important;gap:21px!important}
header .sicoob-brand-icon{width:36px;height:36px}
main{padding:20px!important}
section{padding:18px!important;margin-bottom:20px!important;border-radius:12px!important}
.history-filter-heading{margin-bottom:20px!important}
.history-filter-heading b{font-size:18px!important}
.history-filter-heading small{font-size:13px!important}
.history-results{padding:20px 18px 25px!important}
#summary{font-size:14px!important;padding-bottom:18px!important}
table th{font-size:13px!important;background:#edf3fd!important;padding:15px 12px!important}
table th:first-child{border-radius:8px 0 0 8px}
table th:last-child{border-radius:0 8px 8px 0}
table td{padding:10px 12px!important;font-size:13px!important;line-height:1.35!important;border-bottom:1px solid #e2eaf8!important}
table td:nth-child(7){white-space:nowrap}
.status-pill{display:inline-flex;align-items:center;min-height:34px;border-radius:9px;padding:7px 12px;font-weight:750;white-space:nowrap}
td.paid .status-pill{background:#e2f9ed;color:#02834f;border:1px solid #cdf3de}
td.pending .status-pill{background:#fff5e2;color:#bd7000;border:1px solid #ffebc5}
td.cancel .status-pill,td.expired .status-pill{background:#fff0f3;color:#dc1748;border:1px solid #ffd6df}
td.paid::before,td.pending::before,td.cancel::before,td.expired::before{content:'●';display:inline-block;font-size:14px;vertical-align:middle;margin-right:8px}
td.paid::before{color:#03a568}td.pending::before{color:#ff9f00}td.cancel::before,td.expired::before{color:#ee1649}
td button{border-radius:9px!important;min-height:39px!important;padding:9px 11px!important;margin-right:4px!important;font-weight:650!important}
td button:hover{background:#dce9ff!important}
.tools #refresh{font-size:14px!important;font-weight:800!important}
</style><style id="coopex-pix-actions-reference">
table{table-layout:auto!important}
table th:last-child{min-width:365px!important}
table td:last-child{white-space:nowrap!important;min-width:365px!important}
table td:last-child .pix-action{display:inline-flex!important;align-items:center!important;justify-content:center!important;gap:5px!important;vertical-align:middle!important;white-space:nowrap!important;font-size:11px!important;font-weight:650!important;min-height:32px!important;height:32px!important;padding:4px 8px!important;margin:0 3px 0 0!important;border-radius:7px!important;border:1px solid #cdddff!important;background:#edf4ff!important;color:#0656dc!important;box-shadow:none!important;line-height:1!important}
table td:last-child .pix-action:before{display:inline-flex;align-items:center;justify-content:center;font-size:16px!important;font-family:Arial,sans-serif;line-height:1;width:15px;min-width:15px}
.pix-action-verify:before{content:'⌕'}
.pix-action-copy:before{content:'▢'}
.pix-action-edit:before{content:'✎'}
table td:last-child .pix-action-cancel{background:#fff0f2!important;border-color:#ffd3dc!important;color:#d92243!important}
.pix-action-cancel:before{content:'×';font-size:21px!important}
table td:last-child .pix-action-new{background:#e5f8ed!important;border-color:#cef1dd!important;color:#078449!important}
.pix-action-new:before{content:'+';font-size:20px!important}
table td:last-child .pix-action:disabled{opacity:.6}
@media(max-width:1150px){table td:last-child{min-width:340px!important}table th:last-child{min-width:340px!important}}
</style>
<header><strong><svg class="sicoob-brand-icon" viewBox="0 0 156 140" width="37" height="37" aria-label="Símbolo do Sicoob" role="img"><path d="M14 16 L73 16 L44 67 Z" fill="#78bd27"/><path d="M73 16 L142 16 L105 84 Z" fill="#13a998"/><path d="M44 67 L75 123 L106 68 L92 44 L75 72 L60 44 Z" fill="#bfd510"/><path d="M45 66 L75 16 L91 44 L76 70 L62 44 Z" fill="#1c9f8f"/><path d="M75 123 L104 71 L74 71 L59 96 Z" fill="#b6cf13"/></svg><span>COOPEX - Histórico Pix da Cooperativa - Sicoob</span></strong><a href="/admin" target="_blank" rel="noopener">↗ &nbsp; Abrir painel ↗</a></header><main><section class="history-filters"><div class="history-filter-heading"><span class="history-filter-icon">▽</span><span><b>Filtros</b><small>Selecione os filtros para consultar o histórico de transações Pix da cooperativa.</small></span></div><div class="tools"><select id="status"><option value="todos">Todas as cobranças Sicoob</option><option value="pagos">Pagos</option><option value="pendentes">Não pagos</option><option value="cancelados">Cancelados</option></select><select id="tipo"><option value="todos">Avulsos e entregas</option><option value="Avulso">Pix antecipado</option><option value="Entrega">Pix de entrega</option><option value="Crédito">Compra de créditos</option></select><label>De <input id="dataInicio" type="date"></label><label>Até <input id="dataFim" type="date"></label><label style="display:flex;align-items:center;gap:5px"><input id="anteriores" type="checkbox" checked> Pix Cooperativa pendentes anteriores</label><input id="busca" placeholder="Pesquisar cliente ou número da entrega"><button id="refresh">Atualizar agora</button><span id="updated"></span></div></section><section class="history-results"><div id="summary"></div><div class="table"><table><thead><tr><th>Origem</th><th>Cliente</th><th>Valor</th><th>Data</th><th>Pagamento</th><th>Vencimento Pix</th><th>Situação</th><th>Ações</th></tr></thead><tbody id="rows"></tbody></table></div></section></main>
<script>let items=[],busy=false,offset=0;const csrf={{csrf|tojson}};
const el=id=>document.getElementById(id),money=x=>Number(x||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
function date(s){if(!s)return '—';let raw=String(s).trim(),d;if(/^[A-Za-z]{3},? \d/.test(raw)){d=new Date(raw)}else if(/^\d{4}-\d\d-\d\d/.test(raw)){d=new Date(raw.replace(' ','T')+(raw.endsWith('Z')||/[+-]\d\d:\d\d$/.test(raw)?'':'Z'))}else d=new Date(raw);return Number.isNaN(d.getTime())?'Data indisponível':new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Recife',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(d)}
async function post(url){const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf}});const j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.erro||'Erro ao consultar Sicoob');return j}
function countdown(exp){let ms=new Date(String(exp).replace(' ','T')+(String(exp).endsWith('Z')||/[+-]\\d\\d:\\d\\d$/.test(String(exp))?'':'Z')).getTime()-Date.now();if(!Number.isFinite(ms))return 'Indisponível';if(ms<=0)return 'VENCIDO';let m=Math.floor(ms/60000);return Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'min'}
function display(){const body=el('rows');body.replaceChildren();const filtro=el('status').value,tipo=el('tipo').value,q=el('busca').value.trim().toLowerCase();const ini=el('dataInicio').value,fim=el('dataFim').value,manter=el('anteriores').checked;const filtered=items.filter(x=>{const st=x.status;const rawData=String(x.criado_em||'');const criado=new Date(rawData.includes('GMT')?rawData:rawData.replace(' ','T')+(rawData.endsWith('Z')||/[+-]\\d\\d:\\d\\d$/.test(rawData)?'':'Z'));const dia=Number.isNaN(criado.getTime())?'':new Intl.DateTimeFormat('en-CA',{timeZone:'America/Recife',year:'numeric',month:'2-digit',day:'2-digit'}).format(criado);const dentro=(!ini||dia>=ini)&&(!fim||dia<=fim);const pendenteAntigo=manter&&x.tipo==='Entrega'&&st==='PENDENTE';return (dentro||pendenteAntigo)&&(tipo==='todos'||x.tipo===tipo)&&(filtro==='todos'||(filtro==='pagos'&&(st==='PAGO'||st==='PAGO_MANUAL'||st==='APROVADO'))||(filtro==='pendentes'&&st==='PENDENTE')||(filtro==='cancelados'&&st==='CANCELADO'))&&(!q||String(x.cliente_nome||'').toLowerCase().includes(q)||String(x.uid).includes(q))});for(const c of filtered){const tr=document.createElement('tr');const paid=c.status==='PAGO'||c.status==='PAGO_MANUAL'||c.status==='APROVADO';const cancelled=c.status==='CANCELADO';const vals=[c.tipo+(c.tipo==='Entrega'?' #'+c.uid:''),c.cliente_nome||'Não informado',money(c.valor),date(c.criado_em),date(c.pago_em),c.expira_em?(date(c.expira_em)+(paid||cancelled?'':' · '+countdown(c.expira_em))):(paid?'Pago':cancelled?'—':'Vencimento não informado'),c.status==='APROVADO'?'Aprovado · crédito lançado':c.status==='PAGO'?'Pago confirmado pelo Sicoob':c.status==='PAGO_MANUAL'?'Pago no painel (verificar banco)':cancelled?'Cancelado':(c.expira_em&&countdown(c.expira_em)==='VENCIDO'?'Vencido':'Pendente')];vals.forEach((v,i)=>{const td=document.createElement('td');if(i===1&&c.tipo==='Entrega'){const a=document.createElement('a');a.href='/admin?entrega_id='+encodeURIComponent(c.uid);a.target='_blank';a.rel='noopener';a.textContent=v;a.title='Abrir entrega #'+c.uid+' independente da data';a.style.cssText='color:#1551b5;text-decoration:underline;font-weight:700';td.append(a)}else td.textContent=v;if(i===6){td.className=paid?'paid':cancelled?'cancel':v==='Vencido'?'expired':'pending';const pill=document.createElement('span');pill.className='status-pill';pill.textContent=v;td.replaceChildren(pill)}tr.append(td)});const actions=document.createElement('td');if(c.tipo==='Entrega'&&c.status==='PENDENTE'){const change=document.createElement('button');change.textContent='Modificar chave';change.title='Cancelar o Pix antigo e gerar novo Pix com 24 horas';change.onclick=async()=>{if(!confirm('Cancelar a chave antiga da entrega #'+c.uid+' no Sicoob e gerar nova chave com 24 horas?'))return;change.disabled=true;try{const j=await post('/admin/pix-sicoob/entrega/'+encodeURIComponent(c.uid)+'/modificar');try{await navigator.clipboard.writeText(j.brcode);alert('Nova chave Pix copiada. Validade: 24 horas.')}catch(e){window.prompt('Copie a nova chave Pix:',j.brcode)}await load(false)}catch(e){alert(e.message)}finally{change.disabled=false}};actions.append(change)}if(c.tipo==='Avulso'&&c.status==='CANCELADO'){const renew=document.createElement('button');renew.textContent='Gerar novo Pix';renew.onclick=async()=>{renew.disabled=true;try{const j=await post('/admin/pix-sicoob/avulso/'+encodeURIComponent(c.uid)+'/reemitir');if(j.brcode){if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(j.brcode);else{const ta=document.createElement('textarea');ta.value=j.brcode;document.body.append(ta);ta.select();document.execCommand('copy');ta.remove()}alert('Nova cobrança de '+money(c.valor)+' criada com validade de 24 horas. Código Pix copiado.')}await load(false)}catch(e){alert(e.message)}finally{renew.disabled=false}};actions.append(renew)}if(c.tipo==='Avulso'&&c.status!=='CANCELADO'){const copy=document.createElement('button');copy.textContent='Copiar Pix';copy.title='Copiar novamente o código Pix desta cobrança, sem gerar outra';copy.onclick=async()=>{copy.disabled=true;try{const rr=await fetch('/admin/pix-sicoob/avulso/'+encodeURIComponent(c.uid)+'/codigo',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRF-Token':csrf}});const jj=await rr.json().catch(()=>({}));if(!rr.ok||!jj.brcode)throw Error(jj.erro||'Código Pix indisponível');if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(jj.brcode)}else{const ta=document.createElement('textarea');ta.value=jj.brcode;ta.style.position='fixed';ta.style.opacity='0';document.body.append(ta);ta.select();if(!document.execCommand('copy'))throw Error('Não foi possível copiar automaticamente');ta.remove()}copy.textContent=jj.renovado?'Novo Pix copiado ✓':'Copiado ✓';if(jj.renovado)await load(false);setTimeout(()=>{copy.textContent='Copiar Pix'},2200)}catch(e){alert('Não foi possível copiar o Pix: '+e.message)}finally{copy.disabled=false}};actions.append(copy);if(c.status==='PENDENTE'){const change=document.createElement('button');change.textContent='Modificar chave';change.title='Cancelar este Pix no Sicoob e criar outro com o mesmo valor e 24 horas de validade';change.onclick=async()=>{if(!confirm('Cancelar o Pix anterior no Sicoob e gerar um novo código com 24 horas de validade para '+(c.cliente_nome||'este cliente')+'?'))return;change.disabled=true;try{const rr=await fetch('/admin/pix-sicoob/avulso/'+encodeURIComponent(c.uid)+'/codigo',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/json'},body:JSON.stringify({renovar:true})});const jj=await rr.json().catch(()=>({}));if(!rr.ok||!jj.brcode)throw Error(jj.erro||'Não foi possível modificar o Pix');try{await navigator.clipboard.writeText(jj.brcode);alert('Pix anterior cancelado. Nova chave gerada e copiada. Validade: 24 horas.')}catch(e){window.prompt('Pix novo gerado. Copie este código:',jj.brcode)}await load(false)}catch(e){alert('Não foi possível modificar a chave: '+e.message)}finally{change.disabled=false}};actions.append(change)}}if(c.status==='PENDENTE'){const verify=document.createElement('button');verify.textContent='Verificar';verify.onclick=async()=>{verify.disabled=true;try{verify.textContent='Consultando...';const resposta=await post((c.tipo==='Crédito'?'/api/admin/pix-creditos/'+encodeURIComponent(c.uid)+'/verificar':'/admin/pix-sicoob/'+(c.tipo==='Entrega'?'verificar/':'avulso/'+encodeURIComponent(c.uid)+'/verificar')+(c.tipo==='Entrega'?encodeURIComponent(c.uid):'')));await load(false);alert(resposta.status==='PAGO'?'Pagamento confirmado pelo Sicoob.':'Consulta concluída. O Sicoob ainda não confirmou a quitação integral desta cobrança.')}catch(e){alert('Erro na consulta: '+e.message)}finally{verify.disabled=false;verify.textContent='Verificar'}};actions.append(verify);if(c.tipo==='Avulso'){const cancel=document.createElement('button');cancel.textContent='Cancelar';cancel.onclick=async()=>{if(!confirm('Cancelar este Pix pendente no Sicoob?'))return;cancel.disabled=true;try{await post('/admin/pix-sicoob/avulso/'+encodeURIComponent(c.uid)+'/cancelar');await load(false)}catch(e){alert(e.message)}finally{cancel.disabled=false}};actions.append(cancel)}}for(const b of actions.querySelectorAll('button')){const label=b.textContent.trim();b.classList.add('pix-action');if(label==='Verificar')b.classList.add('pix-action-verify');else if(label==='Copiar Pix')b.classList.add('pix-action-copy');else if(label==='Modificar chave')b.classList.add('pix-action-edit');else if(label==='Cancelar')b.classList.add('pix-action-cancel');else if(label==='Gerar novo Pix')b.classList.add('pix-action-new')}tr.append(actions);body.append(tr)}el('summary').textContent=filtered.length+' registros exibidos';}
async function load(reconcile=true){if(busy)return;busy=true;try{let r=await fetch('/admin/pix-sicoob/historico/dados',{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('Erro ao carregar histórico');items=(await r.json()).cobrancas||[];display();el('updated').textContent='Atualizado às '+new Date().toLocaleTimeString('pt-BR');if(reconcile){const pending=items.filter(x=>x.status==='PENDENTE');const batch=pending.length?Array.from({length:Math.min(pending.length,5)},(_,i)=>pending[(offset+i)%pending.length]):[];offset+=5;for(const c of batch){try{await post((c.tipo==='Crédito'?'/api/admin/pix-creditos/'+encodeURIComponent(c.uid)+'/verificar':'/admin/pix-sicoob/'+(c.tipo==='Entrega'?'verificar/':'avulso/'+encodeURIComponent(c.uid)+'/verificar')+(c.tipo==='Entrega'?encodeURIComponent(c.uid):'')))}catch(e){}}if(batch.length){const refreshed=await fetch('/admin/pix-sicoob/historico/dados',{credentials:'same-origin',cache:'no-store'});if(refreshed.ok){items=(await refreshed.json()).cobrancas||[];display()}}}}catch(e){el('summary').textContent=e.message}finally{busy=false}}
function todayNatal(){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/Recife',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())}el('dataInicio').value=todayNatal();el('dataFim').value=todayNatal();['status','tipo','busca','dataInicio','dataFim','anteriores'].forEach(id=>el(id).addEventListener('input',display));el('refresh').onclick=()=>load(true);async function getExpiry(){try{const r=await fetch('/admin/pix-sicoob/avisos',{credentials:'same-origin',cache:'no-store'});if(!r.ok)return;const rows=(await r.json()).cobrancas||[];for(const x of items){const m=rows.find(z=>z.tipo===x.tipo&&String(z.id)===String(x.id));if(m)x.expira_em=m.expira_em}display()}catch(e){}}load(true);setInterval(()=>load(true),10000);setInterval(getExpiry,30000);setTimeout(getExpiry,900);setInterval(display,60000);
</script></html>"""

    @app.get("/admin/pix-sicoob/historico")
    def sicoob_historico_page():
        if not session.get("is_admin"):
            from flask import redirect, url_for
            return redirect(url_for("login"))
        response=app.make_response(render_template_string(HISTORY_PAGE,csrf=csrf()))
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/admin/pix-sicoob/avulso/<uid>/cancelar")
    def sicoob_avulso_cancelar(uid):
        admin();check_csrf()
        row=db.session.execute(text("SELECT * FROM sicoob_pix_avulso WHERE id=:id"),{"id":uid}).mappings().first()
        if not row:abort(404)
        if row["status"]!="PENDENTE":
            return jsonify(erro="Somente cobranças pendentes podem ser canceladas."),409
        try:
            status,_=bank_request("GET","/cob/"+row["txid"])
            if status.get("status")=="CONCLUIDA" or status.get("pix"):
                return jsonify(erro="O Sicoob indica pagamento recebido. Não é possível cancelar."),409
            if status.get("status")=="REMOVIDA_PELO_USUARIO_RECEBEDOR":
                pass
            else:
                bank_request("PATCH","/cob/"+row["txid"],{"status":"REMOVIDA_PELO_USUARIO_RECEBEDOR"})
            confirm,_=bank_request("GET","/cob/"+row["txid"])
            if confirm.get("status")!="REMOVIDA_PELO_USUARIO_RECEBEDOR":
                return jsonify(erro="O Sicoob não confirmou o cancelamento; cobrança permanece pendente."),409
            db.session.execute(text("UPDATE sicoob_pix_avulso SET status='CANCELADO' WHERE id=:id AND status='PENDENTE'"),{"id":uid})
            db.session.commit()
            return jsonify(id=uid,status="CANCELADO")
        except Exception:
            app.logger.exception("Falha ao cancelar cobrança Pix avulsa")
            return jsonify(erro="Não foi possível confirmar o cancelamento no Sicoob."),502

    @app.post("/admin/pix-sicoob/avulso/<uid>/reemitir")
    def sicoob_avulso_reemitir(uid):
        admin();check_csrf()
        import uuid
        try:
            row=db.session.execute(text("SELECT * FROM sicoob_pix_avulso WHERE id=:id"),{"id":uid}).mappings().first()
            if not row:abort(404)
            if row["status"]!="CANCELADO":
                return jsonify(erro="Primeiro cancele a cobrança anterior no Sicoob."),409
            existing=db.session.execute(text("SELECT id,brcode FROM sicoob_pix_avulso WHERE origem_renovacao=:id"),{"id":uid}).mappings().first()
            if existing:
                return jsonify(id=existing["id"],brcode=existing["brcode"],ja_existe=True)
            old,_=bank_request("GET","/cob/"+row["txid"])
            if old.get("pix") or old.get("status")!="REMOVIDA_PELO_USUARIO_RECEBEDOR":
                return jsonify(erro="O Sicoob não confirmou que a cobrança anterior foi cancelada e não paga."),409
            novo_id=str(uuid.uuid4());novo_tx="COOPEXAV"+uuid.uuid4().hex[:26].upper()
            _,chave=conf()[0:2]
            result,_=bank_request("PUT","/cob/"+novo_tx,{
                "calendario":{"expiracao":86400},"valor":{"original":str(row["valor"])},
                "chave":chave,"solicitacaoPagador":"COOPEX Pagamento antecipado"})
            codigo=result.get("brcode") or result.get("pixCopiaECola")
            if not codigo:
                result,_=bank_request("GET","/cob/"+novo_tx)
                codigo=result.get("brcode") or result.get("pixCopiaECola")
            if not codigo:
                return jsonify(erro="O banco criou uma cobrança, mas não retornou seu código. Consulte o Sicoob antes de repetir."),502
            db.session.execute(text("""INSERT INTO sicoob_pix_avulso
                (id,txid,valor,brcode,status,criado_em,cliente_nome,origem_renovacao,expira_em)
                VALUES(:id,:tx,:valor,:br,'PENDENTE',:dt,:nome,:origem,:exp)"""),{
                "id":novo_id,"tx":novo_tx,"valor":row["valor"],"br":codigo,
                "dt":datetime.utcnow().isoformat(),"nome":row["cliente_nome"],"origem":uid,"exp":(datetime.utcnow()+__import__("datetime").timedelta(hours=24)).isoformat()})
            db.session.commit()
            return jsonify(id=novo_id,brcode=codigo,valor=row["valor"],status="PENDENTE")
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha ao reemitir Pix avulso")
            return jsonify(erro="Não foi possível confirmar nova cobrança com o Sicoob."),502

    @app.get("/admin/pix-sicoob/avisos")
    def sicoob_pix_avisos():
        admin()
        # A consulta do painel deve ser rápida: nunca realizar dezenas de chamadas
        # síncronas ao banco Sicoob para descobrir vencimentos antigos.
        def expiry(row,kind):
            return row["expira_em"]
        entregas=db.session.execute(text("""SELECT c.entrega_id AS id,c.txid,c.expira_em,e.cliente AS nome,
            c.valor FROM sicoob_pix_cobranca c JOIN entrega e ON e.id=c.entrega_id
            WHERE c.status='PENDENTE' AND LOWER(TRIM(e.pagamento)) IN
            ('pix (cooperativa)','pix cooperativa','pix da cooperativa')
            AND LOWER(COALESCE(e.status_pagamento,''))<>'pago'
            ORDER BY c.entrega_id DESC LIMIT 200""")).mappings().all()
        avulsos=db.session.execute(text("""SELECT id,txid,expira_em,cliente_nome AS nome,valor
            FROM sicoob_pix_avulso WHERE status='PENDENTE' ORDER BY criado_em DESC LIMIT 200""")).mappings().all()
        rows=[]
        for kind,collection in (("Entrega",entregas),("Avulso",avulsos)):
            for row in collection:
                item=dict(row)
                item["tipo"]=kind
                item["expira_em"]=expiry(row,kind)
                item.pop("txid",None)
                rows.append(item)
        response=jsonify(cobrancas=rows)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.get("/admin/pix-sicoob/historico/dados")
    def sicoob_historico_dados():
        admin()
        avulsos=db.session.execute(text("""SELECT id,cliente_nome,valor,status,criado_em,pago_em,expira_em
            FROM sicoob_pix_avulso
            WHERE (brcode IS NOT NULL AND brcode <> '') OR e2eid IS NOT NULL
            ORDER BY criado_em DESC LIMIT 300""")).mappings().all()
        entregas=db.session.execute(text("""SELECT c.entrega_id AS id, e.cliente AS cliente_nome,
            c.valor AS valor,c.status AS status,
            e.data_envio AS criado_em,c.expira_em AS expira_em,c.pago_em AS pago_em,e.status_pagamento AS entrega_pago
            FROM sicoob_pix_cobranca c JOIN entrega e ON e.id=c.entrega_id
            WHERE LOWER(TRIM(e.pagamento)) IN ('pix (cooperativa)', 'pix cooperativa', 'pix da cooperativa')
            ORDER BY e.data_envio DESC LIMIT 300""")).mappings().all()
        creditos=db.session.execute(text("""SELECT p.solicitacao_id AS id, c.nome AS cliente_nome,
            p.valor AS valor, p.status AS status, s.criado_em AS criado_em, p.pago_em AS pago_em,
            s.status AS solicitacao_status, p.credito_id AS credito_id
            FROM sicoob_credito_pix p JOIN solicitacao_credito_cliente s ON s.id=p.solicitacao_id
            JOIN cliente c ON c.id=p.cliente_id
            WHERE (p.brcode IS NOT NULL AND TRIM(p.brcode) <> '')
               OR (p.status='PAGO' AND p.credito_id IS NOT NULL AND s.status='aprovado')
            ORDER BY s.criado_em DESC LIMIT 300""")).mappings().all()
        dados=[]
        for row in creditos:
            item=dict(row);item["tipo"]="Crédito";item["uid"]=str(item["id"])
            if item["status"]=="PAGO" and item["credito_id"] and item["solicitacao_status"]=="aprovado":
                item["status"]="APROVADO"
            else:
                item["status"]="PENDENTE"
            dados.append(item)
        for row in avulsos:
            item=dict(row);item["tipo"]="Avulso";item["uid"]=str(item["id"])
            dados.append(item)
        for row in entregas:
            item=dict(row);item["tipo"]="Entrega";item["uid"]=str(item["id"])
            if str(item.get("entrega_pago") or "").lower()=="pago" and item["status"]!="PAGO":
                item["status"]="PAGO_MANUAL"
            dados.append(item)
        return jsonify(cobrancas=dados)

    @app.get("/admin/pix-sicoob/avulsos")
    def sicoob_avulsos_listar():
        admin()
        rows=db.session.execute(text("""SELECT id,cliente_nome,valor,status,criado_em,pago_em,brcode
            FROM sicoob_pix_avulso ORDER BY criado_em DESC LIMIT 100""")).mappings().all()
        return jsonify(cobrancas=[dict(row) for row in rows])

    @app.post("/admin/pix-sicoob/avulso/<uid>/codigo")
    def sicoob_avulso_codigo(uid):
        admin();check_csrf()
        import uuid
        from datetime import timedelta
        force=bool((request.get_json(silent=True) or {}).get("renovar"))
        try:
            # Lock prevents duplicate replacement charges on simultaneous clicks.
            suffix=" FOR UPDATE" if db.engine.dialect.name=="postgresql" else ""
            row=db.session.execute(text(
                "SELECT * FROM sicoob_pix_avulso WHERE id=:id"+suffix
            ),{"id":uid}).mappings().first()
            if not row:
                db.session.rollback()
                abort(404)
            if row["status"]=="CANCELADO":
                db.session.rollback()
                return jsonify(erro="Esta cobrança foi cancelada."),409
            if not row["brcode"]:
                db.session.rollback()
                return jsonify(erro="Esta cobrança não possui código Pix gerado."),404
            if row["status"]=="PAGO":
                db.session.rollback()
                return jsonify(erro="Cobrança já paga. Não é necessário gerar outro Pix."),409

            bank_data,_=bank_request("GET","/cob/"+row["txid"])
            pix=bank_data.get("pix") or []
            if bank_data.get("status")=="CONCLUIDA" or pix:
                received=[v for v in pix if v.get("endToEndId") and v.get("txid",row["txid"])==row["txid"]]
                total=sum((Decimal(str(v.get("valor","0"))) for v in received),Decimal("0"))
                if received and total==Decimal(str(row["valor"])):
                    db.session.execute(text("UPDATE sicoob_pix_avulso SET status='PAGO',e2eid=:eid,pago_em=:dt WHERE id=:id"),
                        {"eid":received[0]["endToEndId"],"dt":datetime.utcnow().isoformat(),"id":uid})
                    db.session.commit()
                    return jsonify(erro="Pagamento já confirmado pelo Sicoob. Não foi criado outro Pix.",status="PAGO"),409
                db.session.rollback()
                return jsonify(erro="O Sicoob informa movimentação nessa cobrança. Verifique a conciliação antes de substituir."),409

            cal=bank_data.get("calendario") or {}
            criacao=cal.get("criacao") or row["criado_em"]
            from datetime import timezone
            dt=datetime.fromisoformat(str(criacao).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            secs=int(cal.get("expiracao") or 86400)
            expired=datetime.now(timezone.utc)>=dt+timedelta(seconds=secs)
            if not force and not expired and bank_data.get("status")=="ATIVA":
                db.session.rollback()
                return jsonify(brcode=row["brcode"],status="PENDENTE",renovado=False,valor=row["valor"])
            if bank_data.get("status")=="ATIVA":
                bank_request("PATCH","/cob/"+row["txid"],{"status":"REMOVIDA_PELO_USUARIO_RECEBEDOR"})
            confirm,_=bank_request("GET","/cob/"+row["txid"])
            if confirm.get("status")!="REMOVIDA_PELO_USUARIO_RECEBEDOR" or confirm.get("pix"):
                db.session.rollback()
                return jsonify(erro="Não foi possível confirmar o cancelamento do Pix anterior no banco. Nenhum novo Pix foi criado."),409

            txid="COOPEXAV"+uuid.uuid4().hex[:26].upper()
            _,chave=conf()[0:2]
            created,_=bank_request("PUT","/cob/"+txid,{"calendario":{"expiracao":86400},
                "valor":{"original":str(row["valor"])},"chave":chave,
                "solicitacaoPagador":"COOPEX Pagamento antecipado"})
            newcode=created.get("brcode") or created.get("pixCopiaECola")
            if not newcode:
                created,_=bank_request("GET","/cob/"+txid)
                newcode=created.get("brcode") or created.get("pixCopiaECola")
            if not newcode:
                db.session.rollback()
                return jsonify(erro="Cobrança anterior cancelada, mas o Sicoob não retornou o novo código. Consulte o banco antes de tentar novamente."),502
            db.session.execute(text("""CREATE TABLE IF NOT EXISTS sicoob_pix_avulso_renovacoes(
                id VARCHAR(36) PRIMARY KEY, avulso_id VARCHAR(36) NOT NULL,
                txid_antigo VARCHAR(35) NOT NULL, txid_novo VARCHAR(35) NOT NULL,
                renovado_em VARCHAR(40) NOT NULL)"""))
            db.session.execute(text("""INSERT INTO sicoob_pix_avulso_renovacoes
                (id,avulso_id,txid_antigo,txid_novo,renovado_em)
                VALUES(:rid,:id,:old,:new,:dt)"""),
                {"rid":str(uuid.uuid4()),"id":uid,"old":row["txid"],"new":txid,"dt":datetime.utcnow().isoformat()})
            db.session.execute(text("""UPDATE sicoob_pix_avulso SET txid=:tx,brcode=:br,
                status='PENDENTE',criado_em=:dt,expira_em=:exp WHERE id=:id"""),
                {"tx":txid,"br":newcode,"dt":datetime.utcnow().isoformat(),"id":uid,"exp":(datetime.utcnow()+timedelta(hours=24)).isoformat()})
            db.session.commit()
            return jsonify(brcode=newcode,status="PENDENTE",renovado=True,valor=row["valor"])
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha ao copiar ou renovar Pix avulso")
            return jsonify(erro="Não foi possível confirmar a situação da cobrança no Sicoob. Nenhum código antigo será apresentado como válido."),502

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
