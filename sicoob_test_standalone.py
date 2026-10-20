"""COOPEX - diagnóstico Sicoob isolado (sem banco de dados ou geração de Pix)."""
import os, secrets, hmac
from flask import Flask, session, request, jsonify, render_template_string, abort
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from requests_pkcs12 import Pkcs12Adapter
import requests

app=Flask(__name__)
app.config.update(SECRET_KEY=os.environ["SICOOB_TEST_SESSION_KEY"],SESSION_COOKIE_SECURE=True,
                  SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Strict",MAX_CONTENT_LENGTH=100000)
CLIENT_ID="de2e9902-639d-496d-ad80-1389eee1e0f9"
TOKEN="https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"
HTML='''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COOPEX - Teste Sicoob</title><style>body{font:16px system-ui;background:#f2f6fc;color:#172e54;max-width:660px;margin:40px auto;padding:16px}main{background:white;padding:26px;border-radius:16px}label{display:block;margin:15px 0 6px}input{display:block;width:100%;padding:12px;box-sizing:border-box;border-radius:8px;border:1px solid #b7c7df}button{background:#1650b6;color:white;border:0;padding:12px 20px;border-radius:8px;cursor:pointer;margin-top:16px}pre{white-space:pre-wrap;background:#f2f6fc;padding:14px;border-radius:8px}</style><main><h2>COOPEX · Teste de autenticação Sicoob</h2><p>Aplicativo Supervisao · teste somente de conexão, sem Pix real.</p>
{% if not logged %}<form method="post" action="/entrar"><input type="hidden" name="csrf" value="{{csrf}}"><label>Senha temporária do teste</label><input type="password" name="senha" autocomplete="off" required><button>Entrar</button></form>
{% else %}<form method="post" action="/testar" enctype="multipart/form-data" id="teste"><input type="hidden" name="csrf" value="{{csrf}}"><label>Arquivo A1 (.pfx ou .p12)</label><input type="file" name="cert" accept=".pfx,.p12" required><label>Senha do certificado A1</label><input type="password" name="senha" required autocomplete="off"><button>Testar conexão Sicoob</button></form><pre id="resultado">Aguardando.</pre><p>O arquivo, a senha e o token não são gravados em arquivos ou banco de dados.</p>
<script>document.getElementById('teste').onsubmit=async ev=>{ev.preventDefault();const result=document.getElementById('resultado');result.textContent='Verificando...';try{const res=await fetch('/testar',{method:'POST',body:new FormData(ev.target)});const d=await res.json();result.textContent=d.mensagem||'Falha de resposta'}catch(e){result.textContent='Erro de comunicação'}ev.target.querySelector('input[name=senha]').value='';}</script>
{% endif %}</main></html>'''
def token_csrf():
    if "csrf" not in session:session["csrf"]=secrets.token_urlsafe(32)
    return session["csrf"]
def form_valid():
    return hmac.compare_digest(request.form.get("csrf",""),token_csrf())
@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"]="no-store"
    resp.headers["X-Frame-Options"]="DENY"
    resp.headers["X-Content-Type-Options"]="nosniff"
    return resp
@app.get("/")
def home():
    return render_template_string(HTML,logged=session.get("logged",False),csrf=token_csrf())
@app.get("/healthz")
def health():
    return "OK"
@app.post("/entrar")
def login():
    if not form_valid():abort(403)
    actual=os.environ["SICOOB_TEST_PASSWORD"]
    if hmac.compare_digest(request.form.get("senha",""),actual):
        session.clear();session["logged"]=True;return render_template_string(HTML,logged=True,csrf=token_csrf())
    return "Credencial inválida.",401
@app.post("/testar")
def test():
    if not session.get("logged"):abort(403)
    if not form_valid():abort(403)
    file=request.files.get("cert")
    if not file or not file.filename.lower().endswith((".pfx",".p12")):
        return jsonify(mensagem="Selecione seu A1 em formato PFX ou P12."),400
    pfx=file.read(65537)
    if len(pfx)>65536:return jsonify(mensagem="Arquivo excede o limite."),400
    password=request.form.get("senha","")
    try:
        key,cert,_=load_key_and_certificates(pfx,password.encode("utf-8"))
        if not key or not cert:raise ValueError()
    except Exception:
        return jsonify(mensagem="Certificado inválido ou senha incorreta."),400
    try:
        with requests.Session() as client:
            client.mount("https://",Pkcs12Adapter(pkcs12_data=pfx,pkcs12_password=password))
            response=client.post(TOKEN,data={"grant_type":"client_credentials","client_id":CLIENT_ID,
                         "scope":"cob.read pix.read webhook.read"},timeout=20)
            if response.status_code==200 and response.json().get("access_token"):
                return jsonify(mensagem="SUCESSO: O Sicoob aceitou o certificado e emitiu token OAuth! Nenhuma cobrança foi criada.")
            return jsonify(mensagem="Sicoob retornou HTTP "+str(response.status_code)+". Verifique certificado vinculado, autorização do aplicativo e escopos.")
    except requests.exceptions.SSLError:
        return jsonify(mensagem="Erro de TLS/mTLS. Certificado ou conexão recusados.")
    except requests.exceptions.Timeout:
        return jsonify(mensagem="Tempo de resposta do Sicoob excedido.")
    except (requests.exceptions.RequestException,ValueError):
        return jsonify(mensagem="Falha de comunicação ou resposta não reconhecida." )
