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
  display:inline-flex;align-items:center;gap:4px;
  min-height:20px;padding:3px 6px;margin-left:7px;
  border:1px solid rgba(255,255,255,.35);border-radius:999px;
  background:rgba(255,255,255,.12);color:inherit;
  font:800 9px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
  vertical-align:middle;white-space:nowrap;
  flex:none;
}
#supervisao-live-indicator .dot{width:7px;height:7px;border-radius:50%;background:#16a365}
#supervisao-live-indicator.off .dot{background:#d64545}
body.dark #supervisao-live-indicator{
  background:rgba(255,255,255,.10);border-color:rgba(255,255,255,.25);color:inherit
}

#credito-solicitacoes-live{
  display:none;align-items:center;gap:5px;margin-left:6px;
  min-height:22px;padding:4px 8px;border-radius:999px;
  background:#fff4d8;color:#8a4d00;border:1px solid #f3ca69;
  font:900 10px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
  cursor:pointer;vertical-align:middle;white-space:nowrap;
  box-shadow:0 3px 10px rgba(138,77,0,.12)
}
#credito-solicitacoes-live.show{display:inline-flex}
#credito-solicitacoes-live .count{
  min-width:17px;height:17px;padding:0 4px;border-radius:999px;
  display:inline-grid;place-items:center;background:#d97706;color:#fff;font-size:9px
}
body.dark #credito-solicitacoes-live{
  background:#3a2a0a;color:#ffd98a;border-color:#755315
}

#credito-solicitacoes-overlay{
  position:fixed;inset:0;z-index:10050;display:none;
  background:rgba(7,18,45,.50);padding:18px;
  align-items:center;justify-content:center
}
#credito-solicitacoes-overlay.open{display:flex}
#credito-solicitacoes-modal{
  width:min(760px,100%);max-height:min(760px,90vh);overflow:auto;
  background:#fff;color:#14213d;border-radius:22px;
  border:1px solid #dbe5f7;box-shadow:0 28px 80px rgba(7,18,45,.30)
}
.credito-modal-head{
  position:sticky;top:0;z-index:2;background:#fff;
  padding:18px 20px;border-bottom:1px solid #e5ebf6;
  display:flex;align-items:center;justify-content:space-between;gap:12px
}
.credito-modal-head h3{margin:0;color:#0a3daf;font-size:18px}
.credito-modal-close{
  width:38px;height:38px;border-radius:12px;border:1px solid #dde5f2;
  background:#fff;color:#20304f;font-size:20px;cursor:pointer
}
.credito-modal-body{padding:16px 20px 22px}
.credito-section-title{
  margin:3px 0 10px;font:900 12px/1.2 system-ui;color:#53627e;text-transform:uppercase
}
.credito-request-list{display:flex;flex-direction:column;gap:10px}
.credito-request{
  border:1px solid #dce5f5;border-radius:16px;padding:13px 14px;background:#fbfdff
}
.credito-request-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.credito-request-name{font-weight:900;color:#153d99}
.credito-request-value{font-weight:950;font-size:18px;color:#0a3daf;white-space:nowrap}
.credito-request-meta{margin-top:5px;font-size:12px;color:#697892;line-height:1.45}
.credito-request-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}
.credito-action{
  border:0;border-radius:11px;padding:9px 12px;font-weight:900;cursor:pointer
}
.credito-action.approve{background:#0e9f6e;color:#fff}
.credito-action.reject{background:#fff0f0;color:#b42318;border:1px solid #f2caca}
.credito-empty{padding:18px;border:1px dashed #dce5f5;border-radius:14px;text-align:center;color:#76839b}
.credito-history{margin-top:18px}
.credito-status{
  display:inline-flex;padding:4px 8px;border-radius:999px;font-size:10px;font-weight:900;margin-left:5px
}
.credito-status.aprovado{background:#e8f8f0;color:#0b7c56}
.credito-status.recusado{background:#fff0f0;color:#b42318}
body.dark #credito-solicitacoes-modal,
body.dark .credito-modal-head{
  background:#0e1b31;color:#e8efff;border-color:#32435f
}
body.dark .credito-request{background:#14233e;border-color:#32435f}
body.dark .credito-modal-head h3,
body.dark .credito-request-name,
body.dark .credito-request-value{color:#dce8ff}
body.dark .credito-request-meta{color:#a9b7d0}
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

    if(alvo){
      // Fica DENTRO do botão "Solicitações de valor", na mesma linha do nome.
      alvo.appendChild(el);
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


  const URL_CREDITOS='/api/admin/solicitacoes-credito';
  let carregandoCreditos=false;

  function moneyBR(v){
    return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  }

  function esc(s){
    return String(s||'').replace(/[&<>"']/g,m=>({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    })[m]);
  }

  function indicadorCreditos(){
    let el=document.getElementById('credito-solicitacoes-live');
    if(el) return el;

    const aoVivo=indicador();
    el=document.createElement('span');
    el.id='credito-solicitacoes-live';
    el.setAttribute('role','button');
    el.setAttribute('tabindex','0');
    el.title='Solicitações de crédito pendentes';
    el.innerHTML='CRÉDITO <span class="count">0</span>';

    aoVivo.insertAdjacentElement('afterend',el);
    el.addEventListener('click',abrirCreditos);
    el.addEventListener('keydown',e=>{
      if(e.key==='Enter'||e.key===' '){e.preventDefault();abrirCreditos();}
    });
    return el;
  }

  function modalCreditos(){
    let overlay=document.getElementById('credito-solicitacoes-overlay');
    if(overlay) return overlay;

    overlay=document.createElement('div');
    overlay.id='credito-solicitacoes-overlay';
    overlay.innerHTML=`
      <div id="credito-solicitacoes-modal" role="dialog" aria-modal="true" aria-label="Solicitações de crédito">
        <div class="credito-modal-head">
          <div>
            <h3>Solicitações de crédito</h3>
            <div style="font-size:12px;color:#6f7c94;margin-top:3px">Aprove somente depois de confirmar o pagamento.</div>
          </div>
          <button type="button" class="credito-modal-close" title="Fechar">×</button>
        </div>
        <div class="credito-modal-body">
          <div class="credito-section-title">Pendentes</div>
          <div id="credito-pendentes-list" class="credito-request-list"></div>
          <div class="credito-history">
            <div class="credito-section-title">Processadas recentemente</div>
            <div id="credito-recentes-list" class="credito-request-list"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector('.credito-modal-close').addEventListener('click',fecharCreditos);
    overlay.addEventListener('click',e=>{if(e.target===overlay) fecharCreditos();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&overlay.classList.contains('open'))fecharCreditos();});
    return overlay;
  }

  function fecharCreditos(){
    modalCreditos().classList.remove('open');
  }

  function abrirCreditos(){
    modalCreditos().classList.add('open');
    carregarSolicitacoesCredito(true);
  }

  function renderCreditoRequest(x,pendente){
    const tel=x.cliente_telefone ? ` • ${esc(x.cliente_telefone)}` : '';
    const user=x.cliente_username ? ` • Login: ${esc(x.cliente_username)}` : '';
    const status=esc(x.status||'pendente');
    const obs=x.observacao ? `<div class="credito-request-meta">${esc(x.observacao)}</div>` : '';
    return `
      <div class="credito-request" data-credito-request="${x.id}">
        <div class="credito-request-top">
          <div>
            <div class="credito-request-name">
              ${esc(x.cliente_nome||'Cliente')}
              ${pendente?'':`<span class="credito-status ${status}">${status.toUpperCase()}</span>`}
            </div>
            <div class="credito-request-meta">Solicitação #${x.id} • ${esc(x.criado_em||'')}${tel}${user}</div>
            ${obs}
          </div>
          <div class="credito-request-value">${moneyBR(x.valor)}</div>
        </div>
        ${pendente?`
          <div class="credito-request-actions">
            <button type="button" class="credito-action approve" data-credit-approve="${x.id}">Aprovar e creditar</button>
            <button type="button" class="credito-action reject" data-credit-reject="${x.id}">Recusar</button>
          </div>`:''}
      </div>`;
  }

  function renderSolicitacoesCredito(data){
    const chip=indicadorCreditos();
    const qtd=Number(data.quantidade_pendente||0);
    const count=chip.querySelector('.count');
    if(count) count.textContent=String(qtd);
    chip.classList.toggle('show',qtd>0);

    const modal=modalCreditos();
    const p=modal.querySelector('#credito-pendentes-list');
    const r=modal.querySelector('#credito-recentes-list');

    p.innerHTML=(data.pendentes||[]).length
      ? data.pendentes.map(x=>renderCreditoRequest(x,true)).join('')
      : '<div class="credito-empty">Nenhuma solicitação de crédito pendente.</div>';

    r.innerHTML=(data.recentes||[]).length
      ? data.recentes.map(x=>renderCreditoRequest(x,false)).join('')
      : '<div class="credito-empty">Nenhuma solicitação processada recentemente.</div>';

    modal.querySelectorAll('[data-credit-approve]').forEach(btn=>{
      btn.addEventListener('click',()=>aprovarCredito(btn.dataset.creditApprove,btn));
    });
    modal.querySelectorAll('[data-credit-reject]').forEach(btn=>{
      btn.addEventListener('click',()=>recusarCredito(btn.dataset.creditReject,btn));
    });
  }

  async function carregarSolicitacoesCredito(forcar){
    if(carregandoCreditos) return;
    carregandoCreditos=true;
    try{
      const resp=await fetch(URL_CREDITOS,{
        cache:'no-store',credentials:'same-origin',
        headers:{Accept:'application/json','X-Requested-With':'fetch'}
      });
      if(!resp.ok) throw new Error('creditos '+resp.status);
      const data=await resp.json();
      if(data&&data.ok) renderSolicitacoesCredito(data);
    }catch(e){
      if(forcar) console.warn('Solicitações de crédito:',e);
    }finally{
      carregandoCreditos=false;
    }
  }

  async function aprovarCredito(id,btn){
    if(!confirm('Confirmar pagamento e lançar este valor como crédito para o cliente?')) return;
    btn.disabled=true;
    try{
      const resp=await fetch(`/api/admin/solicitacoes-credito/${id}/aprovar`,{
        method:'POST',credentials:'same-origin',
        headers:{Accept:'application/json','X-Requested-With':'fetch'}
      });
      const data=await resp.json().catch(()=>null);
      if(!resp.ok||!data||!data.ok) throw new Error(data?.error||'Não foi possível aprovar.');
      await carregarSolicitacoesCredito(true);
      try{document.dispatchEvent(new CustomEvent('supervisao:live-updated'));}catch(e){}
    }catch(e){
      alert(e.message||'Falha ao aprovar solicitação.');
      btn.disabled=false;
    }
  }

  async function recusarCredito(id,btn){
    if(!confirm('Recusar esta solicitação? Nenhum crédito será lançado.')) return;
    btn.disabled=true;
    try{
      const resp=await fetch(`/api/admin/solicitacoes-credito/${id}/recusar`,{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json',Accept:'application/json','X-Requested-With':'fetch'},
        body:JSON.stringify({motivo:'Pagamento não confirmado pelo administrador.'})
      });
      const data=await resp.json().catch(()=>null);
      if(!resp.ok||!data||!data.ok) throw new Error(data?.error||'Não foi possível recusar.');
      await carregarSolicitacoesCredito(true);
    }catch(e){
      alert(e.message||'Falha ao recusar solicitação.');
      btn.disabled=false;
    }
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
      await carregarSolicitacoesCredito(false);

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
      await carregarSolicitacoesCredito(false);

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
    indicadorCreditos();
    modalCreditos();
    carregarSolicitacoesCredito(false);
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
