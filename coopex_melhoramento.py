"""COOPEX Melhoramento: diagnostico read-only e recomendacoes por IA.
A chave da OpenAI recebida pelo formulario vive somente durante a requisicao.
Nao gravar tokens em sessao, logs, banco ou repositorio.
"""
import os, time, hmac, hashlib, collections
from datetime import datetime, timezone
from flask import Blueprint, request, session, render_template, redirect, url_for, jsonify
from sqlalchemy import text
import requests

bp = Blueprint('coopex_melhoramento', __name__, url_prefix='/melhoramento')
_TIMES=collections.deque(maxlen=250)
_SALT=b'coopex-melhoramento-v1'
_PASS_HASH='d2175f972765f982229cc618784bdb4380e2dd935f676df6e1a04ff563b7824c'
_MAX_PROMPT=3000

def _auth():
    return bool(session.get('is_admin') and session.get('is_master') and session.get('coopex_melhoramento_ok'))

def _master():
    return bool(session.get('is_admin') and session.get('is_master'))

def _health(app, db):
    checks=[]
    def add(name,status,details):
        checks.append(dict(name=name,status=status,details=details))
    start=time.monotonic()
    try:
        db.session.execute(text('SELECT 1')).scalar()
        add('Banco de dados','bom','Conexão SQL respondendo (%.0f ms)'%((time.monotonic()-start)*1000))
    except Exception:
        db.session.rollback()
        add('Banco de dados','critico','Falha na consulta de verificação; conferir logs do Render')
    try:
        n=len(_TIMES)
        if n:
            ms=sorted(_TIMES)
            p95=ms[min(n-1,int(n*.95))]
            add('Tempo de resposta','atencao' if p95>1500 else 'bom','Amostra local: %s requisições; p95 %.0f ms'%(n,p95))
        else:
            add('Tempo de resposta','neutro','Ainda sem amostras nesta instância')
    except Exception:
        add('Tempo de resposta','neutro','Medição não disponível')
    mem=os.environ.get('WEB_CONCURRENCY','')
    add('Integridade de configuração','bom' if os.environ.get('SECRET_KEY') else 'atencao',
        'SECRET_KEY configurada no ambiente' if os.environ.get('SECRET_KEY') else 'Configure uma SECRET_KEY própria no Render')
    add('Chave OpenAI','bom' if os.environ.get('OPENAI_API_KEY') else 'neutro',
        'Configurada no ambiente' if os.environ.get('OPENAI_API_KEY') else 'Informe a chave no formulário de análise; ela não será armazenada')
    add('Implantação','neutro','A verificação local não mede automaticamente o estado Live do Render')
    return checks

def install(app,db):
    @app.before_request
    def _cx_perf_start():
        from flask import g
        g._cx_start=time.monotonic()

    @app.after_request
    def _cx_perf_end(resp):
        from flask import g
        start=getattr(g,'_cx_start',None)
        if start is not None and not request.path.startswith('/static/'):
            _TIMES.append((time.monotonic()-start)*1000)
        return resp

    @bp.route('/entrar',methods=['GET','POST'])
    def entrar():
        if not _master():return redirect('/admin')
        erro=None
        if request.method=='POST':
            username=(request.form.get('usuario') or '').strip()
            password=(request.form.get('senha') or '')
            target=os.environ.get('COOPEX_MELHORAMENTO_PASSWORD')
            correct=hmac.compare_digest(username,'coopex')
            if target:
                correct=correct and hmac.compare_digest(password,target)
            else:
                guess=hashlib.pbkdf2_hmac('sha256',password.encode(),_SALT,180000).hex()
                correct=correct and hmac.compare_digest(guess,_PASS_HASH)
            if correct:
                session['coopex_melhoramento_ok']=True
                return redirect(url_for('coopex_melhoramento.painel'))
            erro='Acesso inválido'
        return render_template('melhoramento.html',access=False,error=erro)

    @bp.route('/')
    def painel():
        if not _auth():return redirect(url_for('coopex_melhoramento.entrar'))
        return render_template('melhoramento.html',access=True,checks=_health(app,db),error=None)

    @bp.route('/sair',methods=['POST'])
    def sair():
        session.pop('coopex_melhoramento_ok',None)
        return redirect(url_for('coopex_melhoramento.entrar'))

    @bp.route('/saude')
    def saude():
        if not _auth():return jsonify(ok=False,error='Não autorizado'),403
        return jsonify(ok=True,checks=_health(app,db),measured_at=datetime.now(timezone.utc).isoformat())

    @bp.route('/analisar',methods=['POST'])
    def analisar():
        if not _auth():return jsonify(ok=False,error='Não autorizado'),403
        payload=request.get_json(silent=True) or {}
        prompt=str(payload.get('pedido') or '').strip()[:_MAX_PROMPT]
        key=str(payload.get('api_key') or '').strip() or os.environ.get('OPENAI_API_KEY','')
        if not prompt:return jsonify(ok=False,error='Descreva o que deseja melhorar.'),400
        if not key:return jsonify(ok=False,error='Insira sua chave da API OpenAI ou configure OPENAI_API_KEY no Render.'),400
        if len(key)>260:return jsonify(ok=False,error='Chave inválida.'),400
        checks=_health(app,db)
        system=('Você é um auditor de engenharia de software para um app de entregas Flask em produção. '
                'Identifique hipóteses, impacto, verificações concretas, riscos, passos de correção e rollback. '
                'Não diga que executou alterações, não invente resultados de logs, não solicite senhas nem dados pessoais. '
                'Não proponha executar código da resposta diretamente em produção. Responda em português.')
        context='Sinais locais de saúde (limitados): '+str(checks)
        try:
            resp=requests.post('https://api.openai.com/v1/responses',
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
                json={'model':os.environ.get('COOPEX_MELHORAMENTO_MODEL','gpt-4.1-mini'),
                      'instructions':system,'input':context+'\nPedido do administrador: '+prompt,'max_output_tokens':1100},
                timeout=45)
            data=resp.json()
            if not resp.ok:
                return jsonify(ok=False,error='Falha na API OpenAI (%s): %s'%(resp.status_code,str(data.get('error',{}).get('message','Verifique a chave e os limites.'))[:180])),502
            parts=[]
            for item in data.get('output',[]):
                for part in item.get('content',[]):
                    if part.get('type')=='output_text':parts.append(part.get('text',''))
            result='\n'.join(parts).strip()
            if not result:return jsonify(ok=False,error='A IA não retornou uma análise de texto.'),502
            return jsonify(ok=True,analise=result,aplicado=False,
               nota='Esta análise é uma proposta. Nenhuma mudança de código foi executada.')
        except requests.RequestException:
            return jsonify(ok=False,error='Tempo excedido ou falha na conexão com a API OpenAI.'),502

    app.register_blueprint(bp)
