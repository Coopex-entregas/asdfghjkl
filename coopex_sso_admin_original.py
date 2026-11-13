"""SSO para o ADMIN ORIGINAL da Supervisao, sem replicar HTML/CSS/JS.

Autenticacao unificada no Financeiro. Secret exclusivo obrigatorio e token
temporario com audiencia fixa, nonce de uso unico (protecao basica contra replay).
A gravacao e os dados permanecem no servico/banco da Supervisao.
"""
import os
import threading
import time
from flask import abort, redirect, request, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

AUDIENCIA="coopex-supervisao-admin-original-v1"
EMISSOR="coopex-financeiro"
SALT="coopex-supervisao-original-v2"
_consumidos={}
_mutex=threading.Lock()


def sso_habilitado():
    return (os.getenv("COOPEX_SUPERVISAO_SSO_ATIVO")=="1"
            and len(os.getenv("COOPEX_SUPERVISAO_SSO_SECRET",""))>=48)


def instalar(app):
    @app.get("/sso/financeiro/supervisao-original")
    def entrar_original():
        if not sso_habilitado():
            return "Login integrado nao configurado",503
        token=request.args.get("token","")
        if not token or len(token)>2048:
            abort(403)
        try:
            data=URLSafeTimedSerializer(
                os.environ["COOPEX_SUPERVISAO_SSO_SECRET"],
                salt=SALT).loads(token,max_age=45)
        except (SignatureExpired,BadSignature):
            abort(403)
        if not isinstance(data,dict):
            abort(403)
        # Somente MASTER por enquanto: o admin antigo concede todas as acoes.
        # Um perfil de leitura do Financeiro nao pode ganhar poderes de escrita.
        if (data.get("aud")!=AUDIENCIA or data.get("iss")!=EMISSOR
                or data.get("role")!="master"
                or type(data.get("sub")) is not int or data["sub"]<=0):
            abort(403)
        nonce=data.get("nonce")
        if not isinstance(nonce,str) or len(nonce)<32 or len(nonce)>128:
            abort(403)
        agora=time.monotonic()
        with _mutex:
            for k,vence in list(_consumidos.items()):
                if vence<=agora:
                    del _consumidos[k]
            if nonce in _consumidos:
                abort(403)
            _consumidos[nonce]=agora+90
        session.clear()
        session["user_id"]=0
        session["user_nome"]="Administrador Financeiro"
        session["is_admin"]=True
        session["is_master"]=True
        session["tipo"]="admin"
        session["coopex_sso_financeiro"]=True
        session["coopex_sso_sub"]=data["sub"]
        return redirect("/admin",code=303)
