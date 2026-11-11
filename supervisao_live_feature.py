"""Atualização ao vivo do painel Supervisão sem recarregar a página.

- Consulta apenas um contador de versão a cada 2 segundos.
- Não usa location.reload().
- Só busca o HTML do /admin quando alguma operação realmente altera dados.
- Atualiza somente tabela, fila, KPIs e solicitações.
- Mantém pesquisa/filtros, posição da página e edição em andamento.
- Remove o cliente antigo que recarregava a página e que podia quebrar o JS do recibo.
"""

import re
import time
from collections import defaultdict, deque
from threading import Lock
from datetime import datetime

from flask import g, jsonify, request, session, has_request_context
from sqlalchemy import text, event


LIVE_STYLE = r"""
<style id="supervisao-live-style">
#supervisao-live-indicator,.portal-alert-chip{
  display:inline-flex;align-items:center;gap:5px;min-height:22px;padding:4px 8px;margin-left:6px;
  border-radius:999px;font:900 9px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
  vertical-align:middle;white-space:nowrap
}
#supervisao-live-indicator{border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.12);color:inherit}
#supervisao-live-indicator .dot{width:7px;height:7px;border-radius:50%;background:#16a365}
#supervisao-live-indicator.off .dot{background:#d64545}
.portal-alert-chip{display:none;cursor:pointer;border:1px solid}
.portal-alert-chip.show{display:inline-flex}
.portal-alert-chip.credito{background:#fff5dd;color:#8a4d00;border-color:#f2ce76}
.portal-alert-chip.cancel{background:#fff0f0;color:#b42318;border-color:#efb8b8}
.portal-alert-chip .count{display:grid;place-items:center;min-width:17px;height:17px;padding:0 4px;border-radius:999px;background:currentColor;color:#fff}
#portal-requests-overlay{
  position:fixed;inset:0;z-index:11000;display:none;align-items:center;justify-content:center;
  padding:16px;background:rgba(8,18,42,.58)
}
#portal-requests-overlay.open{display:flex}
#portal-requests-modal{
  width:min(780px,100%);max-height:90vh;overflow:auto;background:#fff;color:#14213d;
  border-radius:22px;border:1px solid #dce5f5;box-shadow:0 28px 90px rgba(8,18,42,.34)
}
.pr-head{position:sticky;top:0;z-index:2;background:#fff;padding:17px 20px;border-bottom:1px solid #e4eaf4;display:flex;justify-content:space-between;gap:12px}
.pr-head h2{margin:0;font-size:20px;color:#0a3daf}.pr-head p{margin:4px 0 0;font-size:12px;color:#6d7a93}
.pr-close{width:40px;height:40px;border-radius:12px;border:1px solid #dce4f0;background:#fff;font-size:20px;cursor:pointer}
.pr-body{padding:16px 20px 22px}.pr-section{margin-bottom:18px}.pr-title{font-size:12px;font-weight:900;color:#697790;text-transform:uppercase;margin:0 0 9px}
.pr-list{display:flex;flex-direction:column;gap:10px}.pr-empty{border:1px dashed #dbe4f2;border-radius:14px;padding:18px;text-align:center;color:#75829a}
.pr-card{border:1px solid #dbe4f2;border-radius:16px;padding:13px 14px;background:#fbfdff}
.pr-card.danger{border-color:#efb8b8;background:#fffafa}
.pr-top{display:flex;justify-content:space-between;gap:12px}.pr-name{font-weight:900;color:#153d99}.pr-value{font-weight:950;font-size:18px;color:#0a3daf}
.pr-meta{font-size:12px;color:#687793;line-height:1.45;margin-top:5px}.pr-route{font-size:13px;color:#34445f;margin-top:7px;font-weight:700}
.pr-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}
.pr-btn{border:0;border-radius:11px;padding:9px 12px;font-weight:900;cursor:pointer}
.pr-btn.ok{background:#0e9f6e;color:#fff}.pr-btn.no{background:#fff0f0;color:#b42318;border:1px solid #efc5c5}
body.dark #portal-requests-modal,body.dark .pr-head{background:#101d32;color:#e9efff;border-color:#33445e}
body.dark .pr-card{background:#14233e;border-color:#33445e}

@keyframes portalCancelFlash{
  0%,100%{background:#ffeded;color:#b42318;box-shadow:0 0 0 0 rgba(217,45,32,.20)}
  50%{background:#d92d20;color:#fff;box-shadow:0 0 0 7px rgba(217,45,32,.10)}
}
.portal-alert-chip.cancel.show{
  animation:portalCancelFlash .95s ease-in-out infinite;
}

</style>
"""

LIVE_SCRIPT = r"""
<script id="supervisao-live-sync">
(function(){
  'use strict';

  const URL_LIVE='/api/admin/live-state';
  const INTERVALO=8000; // fallback leve; evita atrasar entregas, com controle de atualização pesada

  let versao=null;
  let verificando=false;
  let atualizando=false;
  let pendente=false;
  let ultimoRefresh=0;
  const REFRESH_MIN_MS=12000;
  const FETCH_TIMEOUT_MS=12000;

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


  let portalLoading=false;

  function money(v){return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});}
  function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}

  function portalChip(id,label,cls){
    let el=document.getElementById(id);
    if(el) return el;
    el=document.createElement('span');
    el.id=id; el.className='portal-alert-chip '+cls;
    el.innerHTML=`${label} <span class="count">0</span>`;
    indicador().insertAdjacentElement('afterend',el);
    el.addEventListener('click',openPortalRequests);
    return el;
  }

  function portalModal(){
    let o=document.getElementById('portal-requests-overlay');
    if(o) return o;
    o=document.createElement('div');o.id='portal-requests-overlay';
    o.innerHTML=`
      <div id="portal-requests-modal">
        <div class="pr-head">
          <div><h2 id="prModalTitle">Solicitações do cliente</h2><p id="prModalSub">Pendências que precisam de decisão administrativa.</p></div>
          <button class="pr-close" type="button">×</button>
        </div>
        <div class="pr-body">
          <div class="pr-section"><div class="pr-title">Pendentes</div><div id="prPending" class="pr-list"></div></div>
          <div class="pr-section"><div class="pr-title">Processadas recentemente</div><div id="prRecent" class="pr-list"></div></div>
        </div>
      </div>`;
    document.body.appendChild(o);
    o.querySelector('.pr-close').addEventListener('click',()=>o.classList.remove('open'));
    o.addEventListener('click',e=>{if(e.target===o)o.classList.remove('open');});
    return o;
  }

  function openPortalRequests(){
    const o=portalModal();o.classList.add('open');
    renderPortalCurrent();
  }

  let portalData={credito:{pendentes:[],recentes:[]}};

  function creditCard(x,pending){
    return `<div class="pr-card">
      <div class="pr-top"><div><div class="pr-name">${esc(x.cliente_nome)}</div><div class="pr-meta">Solicitação #${x.id} • ${esc(x.criado_em||'')} ${x.cliente_telefone?'• '+esc(x.cliente_telefone):''}</div></div><div class="pr-value">${money(x.valor)}</div></div>
      ${pending?`<div class="pr-actions"><button class="pr-btn ok" data-credit-ok="${x.id}">Aprovar e creditar</button><button class="pr-btn no" data-credit-no="${x.id}">Recusar</button></div>`:`<div class="pr-meta">Status: ${esc(x.status||'')}</div>`}
    </div>`;
  }
  function renderPortalCurrent(){
    const data=portalData.credito;
    const modal=portalModal();
    modal.querySelector('#prModalTitle').textContent='Solicitações de crédito';
    modal.querySelector('#prModalSub').textContent='Aprove somente depois de confirmar o pagamento.';
    const card=creditCard;
    modal.querySelector('#prPending').innerHTML=data.pendentes.length?data.pendentes.map(x=>card(x,true)).join(''):'<div class="pr-empty">Nenhuma solicitação pendente.</div>';
    modal.querySelector('#prRecent').innerHTML=data.recentes.length?data.recentes.map(x=>card(x,false)).join(''):'<div class="pr-empty">Nenhuma solicitação processada recentemente.</div>';

    modal.querySelectorAll('[data-credit-ok]').forEach(b=>b.onclick=()=>portalAction(`/api/admin/solicitacoes-credito/${b.dataset.creditOk}/aprovar`,b));
    modal.querySelectorAll('[data-credit-no]').forEach(b=>b.onclick=()=>portalAction(`/api/admin/solicitacoes-credito/${b.dataset.creditNo}/recusar`,b));
  }

  async function portalAction(url,btn){
    const approve=url.endsWith('/aprovar');
    const msg=approve?'Confirmar o pagamento e lançar este crédito?':'Recusar esta solicitação de crédito?';
    if(!confirm(msg)) return;
    btn.disabled=true;
    try{
      const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{Accept:'application/json','X-Requested-With':'fetch'}});
      const d=await r.json().catch(()=>null);
      if(!r.ok||!d||!d.ok) throw new Error(d?.error||d?.msg||'Não foi possível concluir.');
      await loadPortalRequests(false);
    }catch(e){alert(e.message||'Falha ao processar.');btn.disabled=false;}
  }

  async function loadPortalRequests(openOnNew){
    if(portalLoading)return;portalLoading=true;
    try{
      const r=await fetch('/api/admin/solicitacoes-credito',{cache:'no-store',credentials:'same-origin'});
      if(!r.ok) throw new Error('Falha ao consultar créditos: '+r.status);
      const cr=await r.json();
      if(cr&&cr.ok) portalData.credito={pendentes:cr.pendentes||[],recentes:cr.recentes||[]};
      const cchip=portalChip('portal-credit-chip','CRÉDITO','credito');
      const cq=portalData.credito.pendentes.length;
      cchip.querySelector('.count').textContent=cq;
      cchip.classList.toggle('show',cq>0);
      if(portalModal().classList.contains('open')) renderPortalCurrent();
    }catch(e){console.warn('Solicitações de crédito:',e);}
    finally{portalLoading=false;}
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


  // Mantém o documento e cada linha existente. Busca somente IDs/assinaturas
  // e solicita HTML das linhas novas/alteradas (limite de 100 por requisição).
  async function atualizarParcial(force=false){
    if(atualizando){pendente=true;return;}
    if(document.visibilityState==='hidden' && !force){pendente=true;return;}
    if(usuarioEditando() && !force){pendente=true;return;}
    atualizando=true;
    pendente=false;
    ultimoRefresh=Date.now();
    try{
      const current=document.querySelector('#tbl table.tabela tbody');
      if(!current)throw Error('Tabela não encontrada');
      const scrollHost=document.getElementById('tbl');
      const y=window.scrollY||0, scrollY=scrollHost?.scrollTop||0,scrollX=scrollHost?.scrollLeft||0;
      const qs=window.location.search||'';
      const r=await fetch('/api/admin/entregas/manifesto'+qs,{
        cache:'no-store',credentials:'same-origin',
        signal:AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers:{Accept:'application/json'}
      });
      if(!r.ok)throw Error('Manifesto '+r.status);
      const d=await r.json();
      if(!d.ok||!Array.isArray(d.rows))throw Error('Manifesto inválido');
      const existing=new Map([...current.querySelectorAll('tr[data-id]')].map(tr=>[String(tr.dataset.id),tr]));
      const desired=d.rows.map(x=>String(x.id));
      const changed=d.rows.filter(x=>{
        const tr=existing.get(String(x.id));
        return !tr || tr.dataset.cxFingerprint!==x.fp;
      }).map(x=>String(x.id));
      const fetched=new Map();
      for(let i=0;i<changed.length;i+=100){
        const ids=changed.slice(i,i+100);
        const u='/api/admin/entregas/linhas?ids='+encodeURIComponent(ids.join(','));
        const res=await fetch(u,{cache:'no-store',credentials:'same-origin',
          signal:AbortSignal.timeout(FETCH_TIMEOUT_MS),headers:{Accept:'application/json'}});
        if(!res.ok)throw Error('Linhas '+res.status);
        const result=await res.json();
        if(!result.ok)throw Error('Falha ao obter linhas');
        const tpl=document.createElement('template');
        tpl.innerHTML=result.html||'';
        tpl.content.querySelectorAll('tr[data-id]').forEach(tr=>fetched.set(String(tr.dataset.id),tr));
        if(ids.some(id=>!fetched.has(id)))throw Error('Linha ausente no servidor');
      }
      const kept=new Set(desired);
      const changedIds=new Set(changed);
      for(const [id,row] of existing){if(!kept.has(id))row.remove();}
      // Ordem devolvida pelo servidor. Não substitui linhas sem mudança.
      desired.forEach((id,index)=>{
        let row=fetched.get(id)||existing.get(id);
        if(!row)return;
        if(changedIds.has(id)&&existing.has(id)&&existing.get(id).isConnected){
          existing.get(id).replaceWith(row);
        }
        const at=current.children[index];
        if(at!==row)current.insertBefore(row,at||null);
      });
      if(changed.length||existing.size!==desired.length){
        window.dispatchEvent(new Event('coopex-admin-table-updated'));
        document.dispatchEvent(new Event('coopex-admin-table-updated'));
      }
      if(scrollHost){scrollHost.scrollTop=scrollY;scrollHost.scrollLeft=scrollX;}
      if(window.scrollY!==y)window.scrollTo(0,y);
      statusOnline(true);
    }catch(e){
      pendente=true;
      console.warn('Atualização incremental:',e);
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
        // A fila usa sua própria API pequena, sem consultar a tabela de entregas.
        window.dispatchEvent(new Event('coopex-led-socket-ready'));
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
    // O crédito é confirmado e lançado pelo fluxo Sicoob.
    // Não consultar solicitações de crédito em polling nem criar alertas de crédito.
    verificar();
    setInterval(verificar,INTERVALO);

    document.addEventListener('visibilitychange',()=>{
      if(document.visibilityState==='visible'){verificar();}
    });

    document.addEventListener('focusout',()=>{
      if(pendente) setTimeout(verificar,180);
    },true);

    window.addEventListener('online',()=>setTimeout(verificar,100));
    // A Entrega Rápida confirma o cadastro na janela, sem sair do Admin.
    window.addEventListener('coopex-admin-delivery-saved',()=>atualizarParcial(true));
    window.addEventListener('coopex-admin-modal-closed',()=>{
      if(pendente)atualizarParcial(true);
    });

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
        # Não repintar a tabela para telemetria, heartbeats, consultas ou
        # atualizações administrativas que não alteram entregas/fila.
        # As alterações de entrega e fila seguem acionando o contador.
        ignorar=(
            "/api/admin/live-state",
            "/api/telemetria/navegador",
            "/socket.io",
            "/healthz",
            "/readyz",
            "/api/app/",
            "/cooperado/atualizar_localizacao",
            "/cooperado/localizacao",
            "/api/cooperado/presenca",
            "/api/cooperado/gps/",
            "/api/admin/gps/",
            "/api/admin/pix-led",
            "/api/admin/pix-creditos/",  # verificações de vendas/PIX não reconstroem a tabela de entregas
            "/api/cliente/cotar-entrega",  # consulta de preço não altera entregas
            "/admin/pix-sicoob/",
            "/api/admin/supervisao/recado",
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

    # Consulta incremental: manifesto leve + HTML somente das linhas alteradas.
    # A página /admin continua responsável pela primeira carga e pelos filtros.
    import hashlib
    from flask import render_template
    from sqlalchemy import func, or_
    from sqlalchemy.orm import joinedload

    def _delivery_fingerprint(entrega):
        # Não inclui trajetos de GPS nem objetos em memória permanentes.
        fields = (
            "id", "cliente", "bairro", "valor", "status", "status_pagamento",
            "pagamento", "cooperado_id", "data_envio", "data_atribuida",
            "origem_json", "destino_json", "paradas_json", "recebido_por",
        )
        data = tuple(str(getattr(entrega, field, "") or "") for field in fields)
        return hashlib.blake2s(repr(data).encode("utf-8"), digest_size=10).hexdigest()

    app.jinja_env.globals["delivery_fingerprint"] = _delivery_fingerprint

    def _admin_filtered_delivery_query():
        Entrega = app_module.Entrega
        q = Entrega.query
        direct = request.args.get("entrega_id", type=int)
        if direct is not None:
            return q.filter(Entrega.id == direct)
        start = request.args.get("data_inicio")
        end = request.args.get("data_fim")
        if not start and not end:
            today = datetime.now(app_module.BRAZIL_TZ).date()
            low, high = app_module.local_date_window_to_utc_range(today)
            q = q.filter(Entrega.data_envio >= low, Entrega.data_envio <= high)
        try:
            if start:
                low, _ = app_module.local_date_window_to_utc_range(datetime.strptime(start, "%Y-%m-%d").date())
                q = q.filter(Entrega.data_envio >= low)
            if end:
                _, high = app_module.local_date_window_to_utc_range(datetime.strptime(end, "%Y-%m-%d").date())
                q = q.filter(Entrega.data_envio <= high)
        except ValueError:
            from werkzeug.exceptions import BadRequest
            raise BadRequest("Período inválido")
        coop = request.args.get("cooperado_id", "todos")
        if coop != "todos":
            try:
                q = q.filter(Entrega.cooperado_id == int(coop))
            except (TypeError, ValueError):
                pass
        status = request.args.get("status_pagamento", "todos")
        if status == "pago":
            q = q.filter(func.lower(Entrega.status_pagamento) == "pago")
        elif status == "pendente":
            q = q.filter(or_(Entrega.status_pagamento.is_(None),
                             func.lower(Entrega.status_pagamento) == "pendente"))
        payment = (request.args.get("forma_pagamento") or "todos").strip().lower()
        if payment not in ("todos", "todas"):
            q = q.filter(func.lower(func.coalesce(Entrega.pagamento, "")) == payment)
        client = (request.args.get("cliente") or "").strip().lower()
        if client:
            q = q.filter(func.lower(Entrega.cliente).like("%" + client + "%"))
        address = (request.args.get("endereco") or "").strip().lower()
        if address:
            like = "%" + address + "%"
            q = q.filter(or_(
                func.lower(func.coalesce(Entrega.bairro, "")).like(like),
                func.lower(func.coalesce(Entrega.origem_json, "")).like(like),
                func.lower(func.coalesce(Entrega.destino_json, "")).like(like),
                func.lower(func.coalesce(Entrega.paradas_json, "")).like(like),
            ))
        return q

    @app.get("/api/admin/entregas/manifesto")
    def coopex_admin_entregas_manifesto():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 403
        Entrega = app_module.Entrega
        found = (_admin_filtered_delivery_query()
                 .order_by(Entrega.data_envio.desc()).limit(500).all())
        # Mesma prioridade visual da tabela: sem cooperado antes de atribuídos.
        found = [e for e in found if not e.cooperado_id] + [e for e in found if e.cooperado_id]
        response = jsonify(ok=True, rows=[
            {"id": int(e.id), "fp": _delivery_fingerprint(e)} for e in found
        ])
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.get("/api/admin/entregas/linhas")
    def coopex_admin_entregas_linhas():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 403
        raw = (request.args.get("ids") or "").split(",")
        try:
            ids = list(dict.fromkeys(int(x) for x in raw if x.strip()))
        except (ValueError, TypeError):
            return jsonify(ok=False, error="ids invalidos"), 400
        if not ids or len(ids) > 100 or any(x <= 0 for x in ids):
            return jsonify(ok=False, error="limite de 100 IDs"), 400
        Entrega = app_module.Entrega
        found = (Entrega.query.options(joinedload(Entrega.cooperado))
                 .filter(Entrega.id.in_(ids)).all())
        indexed = {int(e.id): app_module._enriquecer_entrega(e) for e in found}
        ordered = [indexed[x] for x in ids if x in indexed]
        html = render_template(
            "_admin_delivery_rows.html", entregas=ordered,
            to_brasilia=app_module.to_brasilia
        )
        response = jsonify(ok=True, html=html)
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.get("/api/admin/credito/saldos-ativos")
    def coopex_admin_credito_saldos_ativos():
        """Somente IDs e saldos não zerados; não percorre histórico financeiro."""
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 403
        C = app_module.Cliente
        rows = (db.session.query(C.id, C.saldo_atual)
                .filter(C.saldo_atual.isnot(None))
                .filter(C.saldo_atual != 0)
                .all())
        response = jsonify(ok=True, items=[
            {"id": int(cid), "saldo": round(float(saldo), 2)}
            for cid, saldo in rows
        ])
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.get("/api/admin/solicitacoes-valor/fragmento")
    def coopex_admin_solicitacoes_valor_fragmento():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 403
        S = app_module.SolicitacaoAlteracaoValor
        hist = request.args.get("historico") == "1"
        query = S.query.options(joinedload(S.entrega), joinedload(S.cooperado))
        if hist:
            items = (query.filter(S.status != "pendente")
                     .order_by(S.criado_em.desc()).limit(500).all())
            partial = "_admin_value_history.html"
        else:
            items = (query.filter(S.status == "pendente")
                     .order_by(S.criado_em.desc()).all())
            partial = "_admin_value_pending.html"
        result = render_template(partial, solicitacoes_valor=items,
                                 to_brasilia=app_module.to_brasilia)
        response = jsonify(ok=True, html=result, count=len(items), historico=hist)
        response.headers["Cache-Control"] = "no-store, private"
        return response

    # Temporização passiva de SQL. Não captura SQL, parâmetros nem dados pessoais.
    # Só registra consultas de requisições já monitoradas.
    with app.app_context():
        _perf_sql_engine = db.engine

    @event.listens_for(_perf_sql_engine, "before_cursor_execute")
    def _cx_perf_sql_start(conn, cursor, statement, parameters, context, executemany):
        if has_request_context() and getattr(g, "supervisao_perf_key", None):
            context._cx_perf_sql_at = time.perf_counter()

    @event.listens_for(_perf_sql_engine, "after_cursor_execute")
    def _cx_perf_sql_finish(conn, cursor, statement, parameters, context, executemany):
        started = getattr(context, "_cx_perf_sql_at", None)
        if started is not None and has_request_context():
            g.supervisao_sql_ms = getattr(g, "supervisao_sql_ms", 0.0) + max(0.0, (time.perf_counter()-started)*1000)
            g.supervisao_sql_count = getattr(g, "supervisao_sql_count", 0) + 1

    # supervisao_performance_metrics_v1
    # Medição passiva, limitada e sem GPS, dados pessoais ou alterações de negócios.
    perf_samples = defaultdict(lambda: deque(maxlen=240))
    perf_lock = Lock()
    # Ring buffer limitado por processo: sem GPS, IP, nome, IDs ou payload.
    perf_events = deque(maxlen=180)
    perf_browser = defaultdict(lambda: deque(maxlen=120))
    PERF_WINDOW_SECONDS = 600  # últimos 10 minutos; coleta somente operações reais

    def _perf_group(route):
        p = route.lower()
        if "pix-sicoob" in p or "pix-creditos" in p:
            return "sicoob"
        if "/fila/" in p or "fila_espera" in p:
            return "fila"
        if "/login" in p or p.endswith(" /"):
            return "login"
        if "/painel_cooperado" in p or "/cooperado/" in p:
            return "cooperado"
        if "/admin" in p or "/cadastrar_entrega" in p:
            return "admin"
        return "outros"

    def _perf_reason(route, status, ms, browser=False, sql_ms=0.0):
        if status >= 500:
            return "Erro no servidor ou serviço externo; consultar logs."
        if status in (408, 429):
            return "Tempo esgotado ou limite de requisições."
        if status >= 400:
            return "Solicitação rejeitada (HTTP %s)." % status
        if ms < 2500:
            return "Dentro do limite de alerta."
        if browser:
            return "Demora percebida no navegador. Pode incluir rede, GPS e servidor; causa não isolada."
        if sql_ms >= 500 and sql_ms >= ms*.5:
            return "Banco PostgreSQL consumiu %.0f ms (%.0f%% do tempo do servidor)." % (sql_ms,100*sql_ms/max(1,ms))
        if _perf_group(route) == "sicoob":
            return "Tempo elevado em operação Sicoob. Verificar timeout e resposta bancária nos logs."
        return "Tempo elevado no processamento do servidor. Etapa exata ainda não identificada."

    def _perf_push_event(source, key, ms, status, sql_ms=0.0):
        if ms < 2500 and status < 500:
            return
        perf_events.appendleft({
            "_at": time.monotonic(),
            "hora": datetime.utcnow().strftime("%H:%M:%S UTC"),
            "origem": source, "rota": key, "grupo": _perf_group(key),
            "tempo_ms": round(ms, 1), "http": status,
            "motivo": _perf_reason(key,status,ms,source=="navegador",sql_ms),
            "banco_ms": round(sql_ms,1) if source=="servidor" else None
        })

    def _perf_route():
        path = request.path or ""
        method = request.method.upper()
        if path in ("/", "/login", "/admin", "/painel_cooperado", "/cadastrar_entrega"):
            return method + " " + path
        if path.startswith("/api/admin/") and not any(
            x in path for x in ("/gps", "/live-state", "/localizacao", "/pix-led", "/performance", "/diagnostico-performance")
        ):
            return method + " " + str(request.url_rule.rule if request.url_rule else path)
        if path.startswith("/api/cooperado/") and not any(
            x in path for x in ("/gps", "/presenca", "/localizacao")
        ):
            return method + " " + str(request.url_rule.rule if request.url_rule else path)
        if (path.startswith("/cooperado/") or path.startswith("/admin/")) and not any(
            x in path for x in ("/gps", "/localizacao", "/rastreio")
        ):
            return method + " " + str(request.url_rule.rule if request.url_rule else path)
        return None

    @app.before_request
    def supervisao_perf_begin():
        key = _perf_route()
        if key is not None:
            g.supervisao_perf_key = key
            g.supervisao_perf_started = time.perf_counter()

    @app.after_request
    def supervisao_perf_complete(response):
        started = getattr(g, "supervisao_perf_started", None)
        key = getattr(g, "supervisao_perf_key", None)
        if key is not None and started is not None:
            duration_ms = max(0, (time.perf_counter() - started) * 1000)
            sql_ms = getattr(g, "supervisao_sql_ms", 0.0)
            sql_count = getattr(g, "supervisao_sql_count", 0)
            with perf_lock:
                perf_samples[key].append((duration_ms, response.status_code, sql_ms, sql_count, time.monotonic()))
                _perf_push_event("servidor", key, duration_ms, response.status_code, sql_ms)
        return response

    @app.post("/api/telemetria/navegador")
    def supervisao_perf_browser():
        # Medição do tempo percebido, apenas sessões autenticadas.
        if session.get("user_id") is None and not session.get("is_admin"):
            return jsonify(ok=False), 403
        data = request.get_json(silent=True) or {}
        rota = str(data.get("rota") or "")[:100]
        if not re.fullmatch(r"(?:GET|POST|PATCH|PUT|DELETE) /[a-zA-Z0-9_/<.>/-]*", rota):
            return jsonify(ok=False), 400
        try:
            ms = float(data.get("tempo_ms"))
        except (TypeError, ValueError):
            return jsonify(ok=False), 400
        if ms < 0 or ms > 180000:
            return jsonify(ok=False), 400
        try:
            code = int(data.get("http") or 0)
        except (ValueError, TypeError):
            code = 0
        grupo = _perf_group(rota)
        if grupo not in ("fila","admin","cooperado","sicoob","login"):
            return jsonify(ok=True)
        with perf_lock:
            perf_browser[rota].append((ms,code,time.monotonic()))
            _perf_push_event("navegador", rota, ms, code)
        return jsonify(ok=True)

    @app.get("/api/admin/diagnostico-performance")
    def supervisao_admin_performance():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 403

        def percentile(values, q):
            if not values:
                return None
            # Percentil por nearest rank (mais adequado para amostras pequenas).
            # Com duas medições, p95 é a mais lenta, não a mais rápida.
            from math import ceil
            ordered = sorted(values)
            return round(ordered[min(len(ordered)-1, max(0, ceil(q*len(ordered))-1))], 1)

        # Não exibir tempos antigos como se fossem a velocidade atual.
        # Ler dados já coletados não executa nenhum teste ou operação bancária.
        cutoff = time.monotonic() - PERF_WINDOW_SECONDS
        with perf_lock:
            snapshot = {
                key:[v for v in values if v[4] >= cutoff]
                for key, values in perf_samples.items()
            }
            browser_snapshot = {
                key:[v for v in values if v[2] >= cutoff]
                for key, values in perf_browser.items()
            }
            events_snapshot = [
                {k:v for k,v in ev.items() if k != "_at"}
                for ev in perf_events if ev["_at"] >= cutoff
            ]
        report=[]
        for name, entries in snapshot.items():
            if not entries:
                continue
            durations = [sample[0] for sample in entries]
            report.append({
                "rota":name, "amostras":len(entries),
                "media_ms":round(sum(durations)/len(durations), 1),
                "mediana_ms":percentile(durations, .50),
                "p95_ms":percentile(durations, .95),
                "max_ms":round(max(durations), 1),
                "banco_media_ms":round(sum(sample[2] for sample in entries)/len(entries),1),
                "banco_p95_ms":percentile([sample[2] for sample in entries],.95),
                "consultas_media":round(sum(sample[3] for sample in entries)/len(entries),1),
                "falhas":sum(1 for sample in entries if sample[1]>=500)
            })
        browser_report=[]
        for name, entries in browser_snapshot.items():
            if not entries:
                continue
            durations=[x[0] for x in entries]
            browser_report.append({
                "rota": name, "amostras": len(entries),
                "media_ms": round(sum(durations)/len(durations),1),
                "p95_ms": percentile(durations,.95),
                "max_ms": round(max(durations),1),
                "falhas": sum(1 for _,status,_ in entries if status>=500)
            })
        report.sort(key=lambda item: item["p95_ms"], reverse=True)
        browser_report.sort(key=lambda item: item["p95_ms"], reverse=True)
        response=jsonify(ok=True,total_rotas=len(report),
                         medicao="Amostras reais dos últimos 10 minutos; sem atividade não há p95",
                         janela_minutos=10,amostras_por_rota=240,rotas=report,
                         navegador=browser_report,eventos=events_snapshot[:75])
        response.headers["Cache-Control"]="no-store, private"
        return response

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
                # Impede que os indicadores em cache fiquem desatualizados
                # após qualquer alteração real de entrega, pagamento ou fila.
                # Mudanças exclusivas da fila não alteram estatísticas de
                # entregas nem formas de pagamento: preservar estes caches.
                # O evento da fila e o contador live continuam sendo emitidos.
                if not request.path.startswith("/api/cooperado/fila/"):
                    try:
                        app_module._invalidar_admin_resumo_cache()
                    except AttributeError:
                        pass

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
