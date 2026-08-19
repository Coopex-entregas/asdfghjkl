"""Atualização automática do painel Supervisão sem recarregar a página inteira.

- Consulta um estado leve a cada 2 segundos.
- Quando há mudança, busca o HTML atual do /admin em segundo plano.
- Atualiza apenas tabela, fila, KPIs e solicitações.
- Não usa window.location.reload(), evitando o painel piscar.
"""

import hashlib
import json
import re
from datetime import datetime

from flask import jsonify, request, session


LIVE_STYLE = r"""
<style id="supervisao-live-style">
#supervisao-live-indicator{
  position:fixed;right:14px;bottom:14px;z-index:9998;
  display:flex;align-items:center;gap:7px;padding:7px 10px;
  border:1px solid #cdd9f2;border-radius:999px;
  background:rgba(255,255,255,.96);
  box-shadow:0 8px 28px rgba(18,54,125,.14);
  font:800 11px/1.1 system-ui,-apple-system,"Segoe UI",sans-serif;
  color:#173f9f;user-select:none;backdrop-filter:blur(8px)
}
#supervisao-live-indicator .dot{
  width:8px;height:8px;border-radius:50%;
  background:#16a365;box-shadow:0 0 0 3px rgba(22,163,101,.12)
}
#supervisao-live-indicator.wait .dot{
  background:#e69a16;box-shadow:0 0 0 3px rgba(230,154,22,.14)
}
#supervisao-live-indicator.off .dot{
  background:#d64545;box-shadow:0 0 0 3px rgba(214,69,69,.14)
}
body.dark #supervisao-live-indicator{
  background:rgba(10,23,45,.96);border-color:#334b75;color:#dce8ff
}
</style>
"""

LIVE_SCRIPT = r"""
<script id="supervisao-live-sync">
(function(){
  'use strict';

  const URL_LIVE = '/api/admin/live-state';
  const INTERVALO = 2000;

  let assinatura = null;
  let verificando = false;
  let atualizando = false;
  let atualizacaoPendente = false;

  function indicador(){
    let el = document.getElementById('supervisao-live-indicator');
    if(!el){
      el = document.createElement('div');
      el.id = 'supervisao-live-indicator';
      el.innerHTML = '<span class="dot"></span><span class="txt">AO VIVO</span>';
      document.body.appendChild(el);
    }
    return el;
  }

  function status(tipo, texto){
    const el = indicador();
    el.classList.toggle('wait', tipo === 'wait');
    el.classList.toggle('off', tipo === 'off');
    const tx = el.querySelector('.txt');
    if(tx) tx.textContent = texto;
  }

  function usuarioEditando(){
    const a = document.activeElement;
    if(a && a.matches && a.matches('input,textarea,select,[contenteditable="true"]')) return true;

    if(document.querySelector(
      '.modal.open,.modal.show,[role="dialog"].open,' +
      '[role="dialog"][aria-hidden="false"],.value-requests-overlay.open'
    )) return true;

    if(document.body.classList.contains('locked')) return true;
    return false;
  }

  function copiarHtmlSeExiste(docNovo, seletor, modo){
    const atual = document.querySelector(seletor);
    const novo = docNovo.querySelector(seletor);
    if(!atual || !novo) return false;

    if(modo === 'outer'){
      if(atual.outerHTML !== novo.outerHTML) atual.replaceWith(novo.cloneNode(true));
    }else{
      if(atual.innerHTML !== novo.innerHTML) atual.innerHTML = novo.innerHTML;
    }
    return true;
  }

  function atualizarTexto(docNovo, seletor){
    const atual = document.querySelector(seletor);
    const novo = docNovo.querySelector(seletor);
    if(!atual || !novo) return;
    if(atual.textContent !== novo.textContent) atual.textContent = novo.textContent;
  }

  function atualizarFilaContador(docNovo){
    const botoesAtuais = [...document.querySelectorAll('.bank-group-btn')];
    const botoesNovos = [...docNovo.querySelectorAll('.bank-group-btn')];

    const atual = botoesAtuais.find(b => (b.textContent || '').toLowerCase().includes('fila de espera'));
    const novo = botoesNovos.find(b => (b.textContent || '').toLowerCase().includes('fila de espera'));

    const ca = atual && atual.querySelector('.queue-count');
    const cn = novo && novo.querySelector('.queue-count');

    if(ca && cn && ca.textContent !== cn.textContent){
      ca.textContent = cn.textContent;
    }
  }

  function atualizarSolicitacoesContadores(docNovo){
    const atuais = [...document.querySelectorAll('[data-request-count]')];
    const novos = [...docNovo.querySelectorAll('[data-request-count]')];

    atuais.forEach((el, i) => {
      if(novos[i] && el.textContent !== novos[i].textContent){
        el.textContent = novos[i].textContent;
      }
    });
  }

  function reaplicarEstadoVisual(){
    try{
      if(typeof window.atualizarBadgeAbertas === 'function'){
        window.atualizarBadgeAbertas();
      }
    }catch(e){}

    try{
      document.dispatchEvent(new CustomEvent('supervisao:live-updated'));
    }catch(e){}
  }

  async function atualizarParcial(){
    if(atualizando) return;

    if(usuarioEditando()){
      atualizacaoPendente = true;
      status('wait', 'NOVA ATUALIZAÇÃO');
      return;
    }

    atualizando = true;
    atualizacaoPendente = false;
    status('wait', 'ATUALIZANDO');

    const scrollY = window.scrollY || 0;

    try{
      const r = await fetch(window.location.href, {
        cache:'no-store',
        credentials:'same-origin',
        headers:{
          'Accept':'text/html',
          'X-Requested-With':'supervisao-live'
        }
      });

      if(!r.ok) throw new Error('admin ' + r.status);

      const html = await r.text();
      const docNovo = new DOMParser().parseFromString(html, 'text/html');

      // Tabela principal: troca somente as linhas, sem recarregar a página.
      copiarHtmlSeExiste(docNovo, '.tabela tbody', 'inner');

      // Fila: mantém o <ul> atual e troca só os itens.
      copiarHtmlSeExiste(docNovo, '#espera-lista', 'inner');

      // Solicitações de valor.
      copiarHtmlSeExiste(docNovo, '#pendingValueRequestsList', 'inner');
      copiarHtmlSeExiste(docNovo, '#valueRequestHistoryList', 'inner');

      // KPIs.
      atualizarTexto(docNovo, '#kpiTotalDia');
      atualizarTexto(docNovo, '#kpiTotalMes');
      atualizarTexto(docNovo, '#kpiTotalAno');

      atualizarFilaContador(docNovo);
      atualizarSolicitacoesContadores(docNovo);

      // Evita salto visual.
      if(Math.abs((window.scrollY || 0) - scrollY) > 2){
        window.scrollTo(0, scrollY);
      }

      reaplicarEstadoVisual();
      status('ok', 'AO VIVO');
    }catch(e){
      console.warn('Supervisão ao vivo:', e);
      status('off', 'SEM CONEXÃO');
    }finally{
      atualizando = false;
    }
  }

  async function verificar(){
    if(verificando || document.visibilityState === 'hidden') return;

    verificando = true;
    try{
      const r = await fetch(URL_LIVE, {
        cache:'no-store',
        credentials:'same-origin',
        headers:{
          'Accept':'application/json',
          'X-Requested-With':'fetch'
        }
      });

      if(!r.ok) throw new Error('live-state ' + r.status);

      const d = await r.json();
      if(!d.ok || !d.assinatura) throw new Error('live-state inválido');

      if(assinatura === null){
        assinatura = d.assinatura;
        status('ok', 'AO VIVO');
        return;
      }

      if(d.assinatura !== assinatura){
        assinatura = d.assinatura;
        await atualizarParcial();
      }else if(atualizacaoPendente && !usuarioEditando()){
        await atualizarParcial();
      }else{
        status('ok', 'AO VIVO');
      }
    }catch(e){
      status('off', 'SEM CONEXÃO');
    }finally{
      verificando = false;
    }
  }

  function iniciar(){
    indicador();
    verificar();

    setInterval(verificar, INTERVALO);

    document.addEventListener('visibilitychange', function(){
      if(document.visibilityState === 'visible') verificar();
    });

    document.addEventListener('focusout', function(){
      if(atualizacaoPendente) setTimeout(verificar, 220);
    }, true);

    window.addEventListener('online', function(){
      setTimeout(verificar, 150);
    });
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', iniciar, {once:true});
  }else{
    iniciar();
  }
})();
</script>
"""


def install(app_module):
    app = app_module.app

    if app.extensions.get("supervisao_live_installed"):
        return

    app.extensions["supervisao_live_installed"] = True

    Entrega = app_module.Entrega
    ListaEspera = app_module.ListaEspera
    Cooperado = app_module.Cooperado
    SolicitacaoAlteracaoValor = app_module.SolicitacaoAlteracaoValor
    ConfigSistema = getattr(app_module, "ConfigSistema", None)

    @app.get("/api/admin/live-state")
    def api_admin_live_state():
        if not session.get("is_admin") and not session.get("is_master"):
            return jsonify(ok=False, error="unauthorized"), 401

        try:
            partes = []

            entregas = (
                Entrega.query
                .order_by(Entrega.id.desc())
                .limit(600)
                .all()
            )
            for e in entregas:
                partes.append((
                    "e",
                    e.id,
                    e.cliente,
                    e.bairro,
                    round(float(e.valor or 0), 2),
                    e.cooperado_id,
                    e.status,
                    e.status_pagamento,
                    e.pagamento,
                    e.recebido_por,
                    e.status_corrida,
                    e.data_envio.isoformat() if e.data_envio else "",
                    e.data_atribuida.isoformat() if e.data_atribuida else "",
                ))

            fila = (
                ListaEspera.query
                .order_by(
                    ListaEspera.pos.asc(),
                    ListaEspera.created_at.asc(),
                    ListaEspera.id.asc(),
                )
                .all()
            )
            for item in fila:
                partes.append((
                    "f",
                    item.id,
                    item.cooperado_id,
                    item.nome,
                    item.pos,
                    item.created_at.isoformat() if item.created_at else "",
                ))

            solicitacoes = (
                SolicitacaoAlteracaoValor.query
                .order_by(SolicitacaoAlteracaoValor.id.desc())
                .limit(300)
                .all()
            )
            for item in solicitacoes:
                partes.append((
                    "s",
                    item.id,
                    item.entrega_id,
                    item.cooperado_id,
                    round(float(item.valor_original or 0), 2),
                    round(float(item.valor_solicitado or 0), 2),
                    item.status,
                    item.criado_em.isoformat() if item.criado_em else "",
                    item.analisado_em.isoformat() if item.analisado_em else "",
                ))

            cooperados = Cooperado.query.order_by(Cooperado.id.asc()).all()
            for c in cooperados:
                # GPS/ping/online não entram para não gerar atualização visual constante.
                partes.append(("c", c.id, c.nome, bool(c.ativo)))

            if ConfigSistema is not None:
                configs = ConfigSistema.query.order_by(ConfigSistema.chave.asc()).all()
                for item in configs:
                    partes.append(("cfg", item.chave, item.valor))

            bruto = json.dumps(
                partes,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )

            assinatura = hashlib.blake2s(
                bruto.encode("utf-8"),
                digest_size=16,
            ).hexdigest()

            return jsonify(
                ok=True,
                assinatura=assinatura,
                entregas=len(entregas),
                fila=len(fila),
                solicitacoes=len(solicitacoes),
                servidor=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            )

        except Exception as exc:
            app.logger.warning("Falha no Supervisão ao vivo: %s", exc)
            return jsonify(ok=False, error="live-state indisponível"), 503

    @app.after_request
    def supervisao_injetar_live(response):
        """Substitui o cliente antigo que recarregava a página por atualização parcial."""
        try:
            if request.path != "/admin":
                return response

            if response.status_code != 200 or response.mimetype != "text/html":
                return response

            html = response.get_data(as_text=True)

            # Remove apenas os blocos antigos do Supervisão ao vivo.
            html = re.sub(
                r'<style\s+id=["\']supervisao-live-style["\'][^>]*>.*?</style>',
                '',
                html,
                flags=re.I | re.S,
            )
            html = re.sub(
                r'<script\s+id=["\']supervisao-live-sync["\'][^>]*>.*?</script>',
                '',
                html,
                flags=re.I | re.S,
            )

            # Não usa o antigo supervisao_live.js, pois ele chama location.reload().
            html = re.sub(
                r'<script[^>]+src=["\'][^"\']*supervisao_live\.js[^"\']*["\'][^>]*>\s*</script>',
                '',
                html,
                flags=re.I | re.S,
            )

            bloco = LIVE_STYLE + LIVE_SCRIPT

            # Usa o último </body> real da resposta.
            pos = html.rfind("</body>")
            if pos >= 0:
                html = html[:pos] + bloco + html[pos:]
            else:
                html += bloco

            response.set_data(html)

        except Exception as exc:
            app.logger.warning("Falha ao aplicar Supervisão ao vivo sem piscar: %s", exc)

        return response
