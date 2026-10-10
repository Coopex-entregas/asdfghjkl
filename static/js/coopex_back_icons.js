/* Transforma apenas botoes/links cujo texto seja exatamente Voltar, sem alterar eventos ou destinos. */
(function(){function apply(){document.querySelectorAll('a,button').forEach(function(el){
 if(el.dataset.cxBackCompact)return;
 const t=(el.textContent||'').replace(/[\u2190\u276E\u2039]/g,'').trim().toLowerCase();
 if(t!=='voltar' && t!=='voltar ao painel' && t!=='voltar para o painel')return;
 if(el.children.length>1)return;
 el.dataset.cxBackCompact='1';el.classList.add('cx-back-compact');el.setAttribute('aria-label','Voltar');el.setAttribute('title','Voltar');
 el.textContent='←';
});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply);else apply();
})();
