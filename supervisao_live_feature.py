"""Atualização ao vivo do painel Supervisão sem recarregar a página.

- Consulta apenas um contador de versão a cada 2 segundos.
- Não usa location.reload().
- Só busca o HTML do /admin quando alguma operação realmente altera dados.
- Atualiza somente tabela, fila, KPIs e solicitações.
- Mantém pesquisa/filtros, posição da página e edição em andamento.
- Remove o cliente antigo que recarregava a página e que podia quebrar o JS do recibo.
"""

import re
from datetime import datetime

from flask import jsonify, request, session
from sqlalchemy import text


LIVE_STYLE = r"""
<style id="supervisao-live-style">
#supervisao-live-indicator{
  display:inline-flex;align-items:center;gap:6px;
  min-height:30px;padding:5px 9px;margin-left:8px;
  border:1px solid #cdd9f2;border-radius:999px;
  background:#fff;color:#173f9f;
  font:800 10px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
  vertical-align:middle;white-space:nowrap;
}
#supervisao-live-indicator .dot{
  width:7px;height:7px;border-radius:50%;
  background:#16a365;
}
#supervisao-live-indicator.off .dot{background:#d64545}
body.dark #supervisao-live-indicator{
  background:#14243f;border-color:#334b75;color:#dce8ff
}
</style>
"""

LIVE_SCRIPT = r"""
<script id="supervisao-live-sync">
(function(){
  'use strict';

  const URL_LIVE='/api/admin/live-state';
  const INTERVALO=2000;

  let versao=null;
  let verificando=false;
  let atualizando=false;
  let pendente=false;

  function normalizar(s){
    return String(s||'')
      .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
      .toLowerCase().trim();
  }

  function indicador(){
    let el=document.getElementById('supervisao-live-indicator');
    if(el) return el;

    el=document.createElement('span');
    el.id='supervisao-live-indicator';
    el.innerHTML='<span class="dot"></span><span class="txt">AO VIVO</span>';
    el.title='Atualização automática ativa';

    const alvo=[...document.querySelectorAll('button,a')]
      .find(x=>{
        const t=normalizar(x.textContent);
        return t.includes('solicita') && t.includes('valor');
      });

    if(alvo && alvo.parentNode){
      alvo.insertAdjacentElement('afterend',el);
    }else{
      const barra=document.querySelector('.bank-toolbar') ||
                 document.querySelector('.quick-left') ||
                 document.querySelector('header .topbar');
      if(barra) barra.appendChild(el);
    }
    return el;
  }

  function statusOnline(ok){
    const el=indicador();
    if(!el) return;
    el.classList.toggle('off',!ok);
    const txt=el.querySelector('.txt');
    if(txt) txt.textContent=ok?'AO VIVO':'OFFLINE';
  }

  function usuarioEditando(){
    const a=document.activeElement;
    if(a && a.matches &&
       a.matches('input,textarea,select,[contenteditable="true"]')) return true;

    if(document.querySelector(
      '.modal.open,.modal.show,[role="dialog"].open,'+
      '[role="dialog"][aria-hidden="false"],.value-requests-overlay.open'
    )) return true;

    return false;
  }

  function capturarFiltros(){
    const itens=[];
    document.querySelectorAll(
      'input[type="search"],input[id*="busca" i],input[name*="busca" i],'+
      'input[id*="search" i],input[name*="search" i],select[id*="filtro" i],'+
      'select[name*="filtro" i]'
    ).forEach(el=>{
      if(!el.id) return;
      itens.push({id:el.id,value:el.value});
    });
    return itens;
  }

  function reaplicarFiltros(itens){
    (itens||[]).forEach(item=>{
      const el=document.getElementById(item.id);
      if(!el) return;
      if(String(el.value)!==String(item.value)) el.value=item.value;
      try{ el.dispatchEvent(new Event('input',{bubbles:true})); }catch(e){}
      try{ el.dispatchEvent(new Event('change',{bubbles:true})); }catch(e){}
    });
  }

  function trocarConteudo(docNovo,seletor){
    const atual=document.querySelector(seletor);
    const novo=docNovo.querySelector(seletor);
    if(!atual || !novo) return false;
    if(atual.innerHTML!==novo.innerHTML) atual.innerHTML=novo.innerHTML;
    return true;
  }

  function trocarTexto(docNovo,seletor){
    const atual=document.querySelector(seletor);
    const novo=docNovo.querySelector(seletor);
    if(!atual || !novo) return;
    if(atual.textContent!==novo.textContent) atual.textContent=novo.textContent;
  }

  function atualizarFilaContador(docNovo){
    const atual=[...document.querySelectorAll('.bank-group-btn')]
      .find(b=>normalizar(b.textContent).includes('fila de espera'));
    const novo=[...docNovo.querySelectorAll('.bank-group-btn')]
      .find(b=>normalizar(b.textContent).includes('fila de espera'));

    const a=atual&&atual.querySelector('.queue-count');
    const n=novo&&novo.querySelector('.queue-count');
    if(a&&n&&a.textContent!==n.textContent) a.textContent=n.textContent;
  }

  function atualizarContadoresSolicitacoes(docNovo){
    const atuais=[...document.querySelectorAll('[data-request-count]')];
    const novos=[...docNovo.querySelectorAll('[data-request-count]')];
    atuais.forEach((el,i)=>{
      if(novos[i] && el.textContent!==novos[i].textContent){
        el.textContent=novos[i].textContent;
      }
    });
  }

  async function atualizarParcial(){
    if(atualizando) return;

    if(usuarioEditando()){
      pendente=true;
      return;
    }

    atualizando=true;
    pendente=false;

    const y=window.scrollY||0;
    const filtros=capturarFiltros();

    try{
      const r=await fetch(window.location.href,{
        cache:'no-store',
        credentials:'same-origin',
        headers:{
          'Accept':'text/html',
          'X-Requested-With':'supervisao-live'
        }
      });
      if(!r.ok) throw new Error('admin '+r.status);

      const html=await r.text();
      const docNovo=new DOMParser().parseFromString(html,'text/html');

      trocarConteudo(docNovo,'.tabela tbody');
      trocarConteudo(docNovo,'#espera-lista');
      trocarConteudo(docNovo,'#pendingValueRequestsList');
      trocarConteudo(docNovo,'#valueRequestHistoryList');

      trocarTexto(docNovo,'#kpiTotalDia');
      trocarTexto(docNovo,'#kpiTotalMes');
      trocarTexto(docNovo,'#kpiTotalAno');

      atualizarFilaContador(docNovo);
      atualizarContadoresSolicitacoes(docNovo);

      reaplicarFiltros(filtros);

      requestAnimationFrame(()=>{
        window.scrollTo(0,y);
        requestAnimationFrame(()=>window.scrollTo(0,y));
      });

      try{
        document.dispatchEvent(new CustomEvent('supervisao:live-updated'));
      }catch(e){}

      statusOnline(true);
    }catch(e){
      console.warn('Supervisão ao vivo:',e);
      statusOnline(false);
    }finally{
      atualizando=false;
    }
  }

  async function verificar(){
    if(verificando || document.visibilityState==='hidden') return;
    verificando=true;

    try{
      const r=await fetch(URL_LIVE,{
        cache:'no-store',
        credentials:'same-origin',
        headers:{'Accept':'application/json','X-Requested-With':'fetch'}
      });
      if(!r.ok) throw new Error('live-state '+r.status);

      const d=await r.json();
      if(!d.ok || d.versao===undefined || d.versao===null){
        throw new Error('live-state inválido');
      }

      const nova=String(d.versao);

      if(versao===null){
        versao=nova;
        statusOnline(true);
        return;
      }

      if(nova!==versao){
        versao=nova;
        await atualizarParcial();
      }else if(pendente && !usuarioEditando()){
        await atualizarParcial();
      }else{
        statusOnline(true);
      }
    }catch(e){
      statusOnline(false);
    }finally{
      verificando=false;
    }
  }

  function iniciar(){
    indicador();
    verificar();
    setInterval(verificar,INTERVALO);

    document.addEventListener('visibilitychange',()=>{
      if(document.visibilityState==='visible') verificar();
    });

    document.addEventListener('focusout',()=>{
      if(pendente) setTimeout(verificar,180);
    },true);

    window.addEventListener('online',()=>setTimeout(verificar,100));
  }

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',iniciar,{once:true});
  }else{
    iniciar();
  }
})();
</script>
"""


def install(app_module):
    app = app_module.app
    db = app_module.db

    if app.extensions.get("supervisao_live_installed"):
        return
    app.extensions["supervisao_live_installed"] = True

    # Uma única linha no banco. A checagem de 2 em 2 segundos lê só esse número.
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS supervisao_live_version (
                    id INTEGER PRIMARY KEY,
                    versao BIGINT NOT NULL
                )
            """))
            atual = conn.execute(
                text("SELECT versao FROM supervisao_live_version WHERE id=1")
            ).scalar()
            if atual is None:
                conn.execute(text(
                    "INSERT INTO supervisao_live_version (id,versao) VALUES (1,1)"
                ))

    def ler_versao():
        with db.engine.connect() as conn:
            return int(conn.execute(
                text("SELECT versao FROM supervisao_live_version WHERE id=1")
            ).scalar() or 1)

    def incrementar_versao():
        try:
            with db.engine.begin() as conn:
                conn.execute(text("""
                    UPDATE supervisao_live_version
                    SET versao=versao+1
                    WHERE id=1
                """))
        except Exception as exc:
            app.logger.warning(
                "Falha ao incrementar versão do Supervisão ao vivo: %s", exc
            )

    def requisicao_ruidosa(path):
        p=(path or "").lower()
        ignorar=(
            "/api/admin/live-state",
            "/socket.io",
            "/healthz",
            "/readyz",
            "/api/app/localizacao",
            "/api/app/heartbeat",
            "/api/app/ping",
        )
        return any(p.startswith(x) for x in ignorar)

    @app.get("/api/admin/live-state")
    def api_admin_live_state():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False,error="unauthorized"),401
        try:
            return jsonify(
                ok=True,
                versao=ler_versao(),
                servidor=datetime.utcnow().isoformat(timespec="seconds")+"Z",
            )
        except Exception as exc:
            app.logger.warning("Falha no Supervisão ao vivo: %s",exc)
            return jsonify(ok=False,error="live-state indisponível"),503

    @app.after_request
    def supervisao_live_after_request(response):
        try:
            # Qualquer gravação bem-sucedida sinaliza alteração real ao painel.
            if (
                request.method in ("POST","PUT","PATCH","DELETE")
                and response.status_code < 400
                and not requisicao_ruidosa(request.path)
            ):
                incrementar_versao()

            if request.path != "/admin":
                return response
            if response.status_code != 200 or response.mimetype != "text/html":
                return response

            html=response.get_data(as_text=True)

            # Remove completamente os clientes antigos que chamavam location.reload().
            html=re.sub(
                r'<style\s+id=["\']supervisao-live-style["\'][^>]*>.*?</style>',
                '',html,flags=re.I|re.S
            )
            html=re.sub(
                r'<script\s+id=["\']supervisao-live-sync["\'][^>]*>.*?</script>',
                '',html,flags=re.I|re.S
            )
            html=re.sub(
                r'<script[^>]+src=["\'][^"\']*supervisao_live\.js[^"\']*["\'][^>]*>\s*</script>',
                '',html,flags=re.I|re.S
            )

            bloco=LIVE_STYLE+LIVE_SCRIPT

            # IMPORTANTE: injeta no ÚLTIMO </body> real.
            # O recibo possui a string "</body></html>" dentro do JavaScript.
            # Usar replace(...,1) quebrava esse script e fazia o código aparecer na página.
            pos=html.rfind("</body>")
            if pos>=0:
                html=html[:pos]+bloco+html[pos:]
            else:
                html+=bloco

            response.set_data(html)

        except Exception as exc:
            app.logger.warning(
                "Falha ao aplicar Supervisão ao vivo sem recarga: %s",exc
            )

        return response
