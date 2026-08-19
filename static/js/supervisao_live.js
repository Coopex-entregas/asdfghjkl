(function(){
  'use strict';

  var URL_LIVE='/api/admin/live-state';
  var INTERVALO=2000;
  var SCROLL_KEY='supervisao_live_scroll_y';
  var assinatura=null;
  var verificando=false;
  var atualizacaoPendente=false;

  function instalarEstilo(){
    if(document.getElementById('supervisao-live-style')) return;
    var st=document.createElement('style');
    st.id='supervisao-live-style';
    st.textContent=[
      '#supervisao-live-indicator{position:fixed;right:14px;bottom:14px;z-index:9998;display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid #cdd9f2;border-radius:999px;background:rgba(255,255,255,.96);box-shadow:0 8px 28px rgba(18,54,125,.14);font:800 11px/1.1 system-ui,-apple-system,"Segoe UI",sans-serif;color:#173f9f;user-select:none;backdrop-filter:blur(8px)}',
      '#supervisao-live-indicator .dot{width:8px;height:8px;border-radius:50%;background:#16a365;box-shadow:0 0 0 3px rgba(22,163,101,.12)}',
      '#supervisao-live-indicator.wait .dot{background:#e69a16;box-shadow:0 0 0 3px rgba(230,154,22,.14)}',
      '#supervisao-live-indicator.off .dot{background:#d64545;box-shadow:0 0 0 3px rgba(214,69,69,.14)}',
      'body.dark #supervisao-live-indicator{background:rgba(10,23,45,.96);border-color:#334b75;color:#dce8ff}'
    ].join('');
    document.head.appendChild(st);
  }

  function indicador(){
    instalarEstilo();
    var el=document.getElementById('supervisao-live-indicator');
    if(!el){
      el=document.createElement('div');
      el.id='supervisao-live-indicator';
      el.innerHTML='<span class="dot"></span><span class="txt">AO VIVO</span>';
      document.body.appendChild(el);
    }
    return el;
  }

  function status(tipo,texto){
    var el=indicador();
    el.classList.toggle('wait',tipo==='wait');
    el.classList.toggle('off',tipo==='off');
    var txt=el.querySelector('.txt');
    if(txt) txt.textContent=texto;
  }

  function usuarioEditando(){
    var a=document.activeElement;
    if(a && a.matches && a.matches('input,textarea,select,[contenteditable="true"]')) return true;
    if(document.querySelector('.modal.open,.modal.show,[role="dialog"].open,[role="dialog"][aria-hidden="false"],.value-requests-overlay.open')) return true;
    if(document.body.classList.contains('locked')) return true;
    return false;
  }

  function restaurarScroll(){
    var raw=sessionStorage.getItem(SCROLL_KEY);
    if(raw===null) return;
    sessionStorage.removeItem(SCROLL_KEY);
    var y=Number(raw);
    if(Number.isFinite(y) && y>0){
      requestAnimationFrame(function(){window.scrollTo(0,y);});
    }
  }

  function atualizarTela(){
    if(usuarioEditando()){
      atualizacaoPendente=true;
      status('wait','NOVA ATUALIZAÇÃO');
      return;
    }
    atualizacaoPendente=false;
    sessionStorage.setItem(SCROLL_KEY,String(Math.max(0,window.scrollY||0)));
    status('wait','ATUALIZANDO');
    window.location.reload();
  }

  async function verificar(){
    if(verificando || document.visibilityState==='hidden') return;
    verificando=true;
    try{
      var r=await fetch(URL_LIVE,{
        cache:'no-store',
        credentials:'same-origin',
        headers:{Accept:'application/json','X-Requested-With':'fetch'}
      });
      if(!r.ok) throw new Error('live-state '+r.status);
      var d=await r.json();
      if(!d.ok || !d.assinatura) throw new Error('live-state inválido');

      status('ok','AO VIVO');
      if(assinatura===null){
        assinatura=d.assinatura;
        return;
      }
      if(d.assinatura!==assinatura){
        assinatura=d.assinatura;
        atualizarTela();
      }else if(atualizacaoPendente && !usuarioEditando()){
        atualizarTela();
      }
    }catch(e){
      status('off','SEM CONEXÃO');
    }finally{
      verificando=false;
    }
  }

  function iniciar(){
    restaurarScroll();
    indicador();
    verificar();
    setInterval(verificar,INTERVALO);
    document.addEventListener('visibilitychange',function(){
      if(document.visibilityState==='visible') verificar();
    });
    document.addEventListener('focusout',function(){
      if(atualizacaoPendente) setTimeout(verificar,180);
    },true);
    window.addEventListener('online',function(){setTimeout(verificar,100);});
  }

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',iniciar,{once:true});
  }else{
    iniciar();
  }
})();
