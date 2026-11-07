/* Command Center: reorganize existing working report UI, do not replace data/actions. */
(function(){
'use strict';
let builtNav=null;
const icons={
 geral:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 cooperados:'<circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
 contratos:'<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3M3 13h18"/>',
 rotas:'<circle cx="6" cy="6" r="2"/><path d="M8 6h9a4 4 0 0 1 0 8h-9a4 4 0 0 0 0 8"/><circle cx="6" cy="20" r="2"/>',
 financeiro:'<path d="M3 7h18v13H3zM3 10h18M7 15h3m4 0h3"/><path d="M3 7V4h18"/>',
 comparativo:'<path d="M3 20V10m6 10V4m6 16v-8m6 8V7"/><path d="m2 9 6-5 6 7 7-7"/>',
 configuracoes:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M4.93 4.93l2.12 2.12m9.9 9.9 2.12 2.12M2 12h3m14 0h3M4.93 19.07l2.12-2.12m9.9-9.9 2.12-2.12"/>'
};
const kpiIcons=[
 '<path d="M3 16l4-8 5 4 3-5 6 9"/><circle cx="7" cy="8" r="2"/>',
 '<path d="M12 2v20M17 7c0-2-2-3-5-3s-5 1-5 4 2 3 5 4 5 1 5 4-2 4-5 4-5-1-5-3"/>',
 '<path d="m3 9 8-6 10 10-8 8L3 11z"/><circle cx="8" cy="9" r="1"/>',
 '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
 '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
 '<path d="M4 12h16M12 4v16"/><circle cx="12" cy="12" r="9"/>',
 '<path d="m4 12 6 6L20 6"/>',
 '<path d="M3 5h18v14H3zM3 9h18m-6 5h3"/>',
 '<path d="M3 21V3h18v18M7 7h3m4 0h3M7 12h3m4 0h3M9 21v-5h6v5"/>',
 '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
 '<circle cx="12" cy="12" r="9"/><path d="M12 6v7l4 3"/>',
 '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M3 12h18M7 4h10"/>'
];
function svg(paths){return '<svg viewBox="0 0 24 24" aria-hidden="true">'+paths+'</svg>'}
function apply(){
 const nav=document.querySelector('.dashboard-tabs-bar');
 if(nav && builtNav===nav && nav.classList.contains('cx-report-rail'))return true;
 const filter=document.querySelector('.dashboard-filter-shell');
 const content=document.querySelector('main.wrap');
 const buttons=nav?.querySelectorAll('[data-tab]');
 if(!nav||!filter||!content||!buttons||buttons.length<5)return false;
 builtNav=nav;
 const body=document.body;
 body.classList.add('cx-command-center');
 nav.classList.add('cx-report-rail');
 nav.setAttribute('aria-label','Áreas do painel gerencial');
 const brand=document.createElement('div');brand.className='cx-rail-brand';
 brand.innerHTML='<a class="cx-rail-back" href="/admin" aria-label="Voltar ao administrador" title="Voltar ao administrador">‹</a><span class="cx-rail-title">COOPEX <small style="display:block;font-size:10px;color:#b3d4ed;font-weight:650">Centro gerencial</small></span>';
 nav.prepend(brand);
 const kicker=document.createElement('div');kicker.className='cx-rail-kicker';kicker.textContent='Áreas de análise';brand.after(kicker);
 const names={
  geral:'Panorama',cooperados:'Cooperados',contratos:'Clientes e contratos',
  rotas:'Bairros e rotas',financeiro:'Financeiro',comparativo:'Evolução',configuracoes:'Configurações'
 };
 buttons.forEach(button=>{
  const id=button.dataset.tab;
  const icon=button.querySelector('.tab-icon');
  if(icon){icon.innerHTML=svg(icons[id]||icons.geral);}
  const spans=button.querySelectorAll('span');
  if(spans.length>1)spans[1].textContent=names[id]||id;
  button.title=names[id]||id;
  button.setAttribute('aria-label',names[id]||id);
 });
 const before=nav.querySelector('.report-back');
 if(before)before.remove();
 // Keep one heading even when an older dashboard patch rebuilds the tab menu.
 let heading=document.querySelector('.cx-report-heading');
 if(!heading){
  heading=document.createElement('header');heading.className='cx-report-heading noPrint';
  heading.innerHTML='<div><h1>Centro Gerencial</h1><div class="cx-report-context">Operação, clientes, cooperados e resultados</div></div><div class="cx-report-head-actions"></div>';
 }
 nav.after(heading);
 const periods=document.getElementById('cx-period-counts');
 if(periods)heading.after(periods);
 const actionHost=heading.querySelector('.cx-report-head-actions');
 const oldActions=nav.querySelector('.dashboard-tabs-actions');
 if(oldActions){[...oldActions.children].forEach(item=>{
  const t=(item.textContent||'').toLowerCase();
  if(/voltar|imprimir|modo (noturno|claro)/i.test(t))return;
  actionHost.appendChild(item);
 });}
 // Keep actual export buttons with their existing click handlers.
 const reportSummary= document.querySelector('.dashboard-tabs-summary');
 if(reportSummary){[...reportSummary.children].forEach(item=>actionHost.appendChild(item));}
 const filterTitle=filter.querySelector('.dashboard-filter-title-row');
 const toggle=document.createElement('button');toggle.type='button';toggle.className='cx-filter-details-button';
 toggle.textContent='Mais filtros';
 toggle.setAttribute('aria-expanded','false');
 filter.classList.add('cx-filters-compact');
 if(filterTitle)filterTitle.appendChild(toggle);
 toggle.addEventListener('click',()=>{
  const compact=filter.classList.toggle('cx-filters-compact');
  toggle.textContent=compact?'Mais filtros':'Menos filtros';
  toggle.setAttribute('aria-expanded',String(!compact));
 });
 const general=document.querySelector('.dashboard-tab-panel[data-panel="geral"]');
 if(general){
  general.querySelectorAll(':scope > .dashboard-panel-title').forEach(node=>node.remove());
  const metrics=general.querySelector(':scope > .gridKpi');
  if(metrics){
   [...metrics.children].forEach((card,i)=>{
    if(!card.classList.contains('kpi'))return;
    card.classList.add('cx-main-kpi');
    card.querySelectorAll('.cx-dash-card-wave').forEach(wave=>wave.remove());
    const lab=card.querySelector('.lab');
    if(lab&&!lab.querySelector('.cx-kpi-icon')){
     const icon=document.createElement('span');icon.className='cx-kpi-icon';
     icon.innerHTML=svg(kpiIcons[i]||kpiIcons[0]);lab.prepend(icon);
    }
    const amount=card.querySelector('.val');
    if(amount){
     const val=(amount.textContent||'').trim();
     amount.classList.toggle('cx-long-value',val.length>=14);
     amount.classList.toggle('cx-extra-long-value',val.length>=18);
     if(val.length>=21)amount.style.fontSize='clamp(12px,.88vw,17px)';
     amount.title=val;
    }
   });
  }
 }
 // Keep all functional existing tab switching, filters and export handlers.
 return true;
}
function start(){
 apply();
 // Older report scripts may replace the navigation once during initialization.
 // Reattach the new layout only when the actual navigation node changes.
 const watcher=new MutationObserver(()=>{
   const nav=document.querySelector('.dashboard-tabs-bar');
   if(nav && nav!==builtNav)apply();
 });
 watcher.observe(document.body,{childList:true});
 let attempts=0;
 const timer=setInterval(()=>{if(apply()||++attempts>=35)clearInterval(timer);},90);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
else start();
})();
