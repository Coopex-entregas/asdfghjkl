/* Retorno compacto: preserva links, eventos e destino, inclusive links rotulados Admin. */
(function(){
 function apply(){
  document.querySelectorAll('a,button').forEach(function(el){
   if(el.dataset.cxBackCompact)return;
   const original=(el.textContent||'').replace(/[←❮‹↩]/g,'').replace(/\s+/g,' ').trim();
   const text=original.toLowerCase();
   const isBack=['voltar','voltar ao painel','voltar para o painel','painel administrativo','admin','administrador','voltar ao admin','voltar para admin'].includes(text);
   if(!isBack)return;
   // "Admin" somente quando for link real de retorno ao dashboard.
   if(['admin','administrador'].includes(text)){
    if(el.tagName!=='A')return;
    const href=el.getAttribute('href')||'';
    if(!/(?:\/admin(?:[?#]|$)|url_for\(['"]admin['"]\))/.test(href))return;
   }
   if(el.closest('table') && el.tagName==='BUTTON')return;
   el.dataset.cxBackCompact='1';el.classList.add('cx-back-compact');
   el.setAttribute('aria-label','Voltar ao painel administrativo');
   el.setAttribute('title','Voltar ao painel administrativo');
   el.replaceChildren(document.createTextNode('←'));
  });
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply);else apply();
})();
