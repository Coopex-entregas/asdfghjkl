/* Tema herdado exclusivamente do administrador; Escape volta ao painel. */
(function(){
 'use strict';
 function theme(){
  const b=document.body;if(!b)return;
  b.classList.add('cx-delivery-glass');
  const saved=localStorage.getItem('cx_sup_brightness');
  const level=saved===null?100:Math.max(0,Math.min(100,Number(saved)));
  const fallback=localStorage.getItem('coopex_theme')==='dark';
  b.classList.toggle('dark',saved!==null?level<80:fallback);
 }
 function escapeBack(event){
  if(event.key!=='Escape'||event.defaultPrevented||event.ctrlKey||event.altKey||event.metaKey)return;
  const modal=document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]:not([hidden]), .modal.show, .modal[style*="display: block"]');
  if(modal)return;
  event.preventDefault();
  window.location.assign(document.querySelector('.cx-back-arrow')?.href||'/admin');
 }
 theme();
 document.addEventListener('keydown',escapeBack);
 window.addEventListener('pageshow',theme);
 window.addEventListener('focus',theme);
})();
