"""COOPEX Melhoramento: diagnostico read-only e recomendacoes por IA.
A chave da OpenAI recebida pelo formulario vive somente durante a requisicao.
Nao gravar tokens em sessao, logs, banco ou repositorio.
"""
import os, time, hmac, hashlib, collections
from datetime import datetime, timezone
from flask import Blueprint, request, session, render_template_string, redirect, url_for, jsonify
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

PAGE = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>COOPEX Melhoramento</title><style>\n*{box-sizing:border-box}body{margin:0;background:#eff5ff;font:14px Arial,sans-serif;color:#102752}header{background:linear-gradient(105deg,#1265eb,#0d378e);color:white;padding:18px 25px;display:flex;justify-content:space-between}header a{color:white}main{max-width:1060px;margin:24px auto;padding:0 15px}.card{background:white;border:1px solid #dfe8f7;border-radius:15px;padding:19px;margin:0 0 16px;box-shadow:0 7px 18px #12377a0c}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.check{border:1px solid #dbe6f8;background:#f9fbff;padding:13px;border-radius:11px}.check strong{display:block;margin-bottom:6px}label{display:block;font-weight:bold;margin:12px 0 6px}input,textarea{width:100%;padding:12px;border:1px solid #c0d0ed;border-radius:9px;font:inherit;color:#152e5e}textarea{height:130px}button{background:#155de1;color:white;padding:11px 16px;border:0;border-radius:9px;cursor:pointer;font-weight:bold}button:disabled{opacity:.6}.muted{color:#617395}#answer{white-space:pre-wrap;line-height:1.5;overflow-wrap:anywhere}@media(max-width:620px){.grid{grid-template-columns:1fr}}</style></head><body><header><strong>COOPEX • Melhoramento</strong><a href=\"/estatisticas_cooperado\">Voltar ao Dashboard</a></header><main>\n{% if not access %}<section class=\"card\"><h1>Acesso restrito</h1><p class=\"muted\">Credenciais adicionais da COOPEX</p>{% if error %}<p>{{error}}</p>{% endif %}<form method=\"post\"><label>Usuário</label><input name=\"usuario\" autocomplete=\"username\" required><label>Senha</label><input name=\"senha\" type=\"password\" autocomplete=\"current-password\" required><p><button>Entrar</button></p></form></section>\n{% else %}<h1>Saúde e melhorias do sistema</h1><p class=\"muted\">Diagnóstico local, sujeito a verificações complementares do Render e GitHub.</p><section class=\"card\"><h2>Saúde do sistema</h2><div class=\"grid\">{% for c in checks %}<div class=\"check\"><strong>{{c.name}} · {{c.status}}</strong><span>{{c.details}}</span></div>{% endfor %}</div></section>\n<section class=\"card\"><h2>COOPEX Assistente GPT</h2><p class=\"muted\">Solicite análise de lentidão, erros e melhorias. Sua chave, se digitada, será usada apenas nesta solicitação.</p><form id=\"improve\"><label>Chave OpenAI (opcional caso esteja no Render)</label><input id=\"key\" type=\"password\" autocomplete=\"off\"><label>O que deseja melhorar?</label><textarea id=\"ask\" required maxlength=\"3000\"></textarea><p><button id=\"run\">Analisar com GPT</button></p></form><div class=\"check\" id=\"answer\" hidden></div></section>\n<section class=\"card\"><h2>Aplicação de melhorias</h2><p>O diagnóstico propõe correções. Esta versão não aplica automaticamente código ao sistema: qualquer alteração exige revisão, teste e rollback.</p><form method=\"post\" action=\"/melhoramento/sair\"><button>Bloquear área</button></form></section>\n<script>\ndocument.getElementById('improve').addEventListener('submit',async e=>{e.preventDefault();let b=document.getElementById('run'),a=document.getElementById('answer');a.hidden=false;b.disabled=true;a.textContent='Analisando...';try{let r=await fetch('/melhoramento/analisar',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({pedido:document.getElementById('ask').value,api_key:document.getElementById('key').value})});let j=await r.json();a.textContent=j.ok?j.analise+'\\\\n\\\\n'+j.nota:j.error}catch(e){a.textContent='Falha na conexão.'}finally{b.disabled=false;document.getElementById('key').value=''}})\n</script>{% endif %}</main></body></html>"

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
        return render_template_string(PAGE,access=False,error=erro)

    @bp.route('/')
    def painel():
        if not _auth():return redirect(url_for('coopex_melhoramento.entrar'))
        return render_template_string(PAGE,access=True,checks=_health(app,db),error=None)

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
