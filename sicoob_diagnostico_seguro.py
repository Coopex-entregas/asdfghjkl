"""Diagnostico Sicoob somente em branch de testes. Nunca persiste PFX, senha ou token."""
import os, secrets
from flask import request, session, jsonify, render_template_string, abort
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from requests_pkcs12 import Pkcs12Adapter
import requests

AUTH="https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"
CLIENT_ID="de2e9902-639d-496d-ad80-1389eee1e0f9"
HTML="""<!DOCTYPE html><html lang=pt-br><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>COOPEX · Diagnóstico Sicoob</title>
<style>body{font:16px system-ui;background:#f4f7fd;color:#132946;max-width:700px;margin:30px auto;padding:20px}main{padding:24px;background:white;border-radius:16px}label{display:block;margin-top:20px;font-weight:600}input{display:block;margin-top:7px;padding:12px;border:1px solid #ccd7ea;border-radius:8px;box-sizing:border-box;width:100%}button{margin-top:20px;padding:13px 20px;background:#1754bb;color:white;border:0;border-radius:8px;cursor:pointer}pre{white-space:pre-wrap;background:#f5f7fa;padding:15px;border-radius:8px}</style><main><h2>Diagnóstico da API Pix Sicoob</h2><p>Aplicativo: <strong>Supervisao</strong>. Teste somente de autenticação. Não gera Pix e não movimenta dinheiro.</p><form id=f enctype=multipart/form-data><input type=hidden name=csrf value="{{csrf}}"><label>Arquivo A1 .pfx/.p12 (somente para esta requisição)</label><input type=file name=cert accept=".pfx,.p12" required><label>Senha do A1</label><input type=password name=senha required autocomplete=off><button>Testar autenticação mTLS</button></form><pre id=result>Aguardando teste.</pre><p>O certificado e a senha não são gravados no servidor. O teste deve ser realizado apenas em ambiente de prévia isolado.</p></main>
<script>document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();const out=document.getElementById('result');out.textContent='Testando...';try{const res=await fetch(location.pathname,{method:'POST',body:new FormData(e.target),credentials:'same-origin'});const data=await res.json();out.textContent=data.mensagem||'Falha na consulta';}catch(_){out.textContent='Falha na comunicação com o servidor.'}e.target.querySelector('[name=senha]').value='';});</script></html>"""

def install(host):
    app=host.app
    def master():
        if not(session.get("is_admin") and session.get("is_master")): abort(403)
    def csrf():
        if "sicoob_diagnostic_csrf" not in session: session["sicoob_diagnostic_csrf"]=secrets.token_urlsafe(32)
        return session["sicoob_diagnostic_csrf"]
    @app.route("/admin/diagnostico-sicoob",methods=["GET","POST"])
    def diagnostico_sicoob_seguro():
        master()
        if request.method=="GET":
            res=app.make_response(render_template_string(HTML,csrf=csrf()))
            res.headers["Cache-Control"]="no-store"
            res.headers["X-Frame-Options"]="DENY"
            return res
        if not secrets.compare_digest(request.form.get("csrf",""),csrf()): abort(403)
        upload=request.files.get("cert")
        if not upload or not upload.filename.lower().endswith((".pfx",".p12")):
            return jsonify(mensagem="Selecione um arquivo .pfx ou .p12"),400
        pfx=upload.read(65537)
        if len(pfx)>65536: return jsonify(mensagem="Arquivo muito grande."),400
        password=request.form.get("senha","")
        try:
            key,cert,_=load_key_and_certificates(pfx,password.encode("utf-8"))
            if not key or not cert: raise ValueError()
        except Exception:
            return jsonify(mensagem="Certificado inválido, sem chave privada ou senha incorreta."),400
        try:
            with requests.Session() as s:
                s.mount("https://",Pkcs12Adapter(pkcs12_data=pfx,pkcs12_password=password))
                response=s.post(AUTH,data={"grant_type":"client_credentials","client_id":CLIENT_ID,"scope":"cob.read pix.read webhook.read"},timeout=18)
                if response.status_code==200 and response.json().get("access_token"):
                    return jsonify(mensagem="SUCESSO: Sicoob emitiu um token OAuth. Nenhum Pix foi criado.")
                return jsonify(mensagem="Sicoob retornou HTTP "+str(response.status_code)+". Verifique a vinculação do certificado e os escopos. Token e credenciais não foram exibidos.")
        except requests.exceptions.SSLError:
            return jsonify(mensagem="Falha de TLS/mTLS com o Sicoob. Verifique certificado e conectividade.")
        except requests.exceptions.Timeout:
            return jsonify(mensagem="Tempo de resposta do Sicoob esgotado.")
        except (requests.exceptions.RequestException,ValueError):
            return jsonify(mensagem="Falha de comunicação ou resposta de autenticação não reconhecida.")
