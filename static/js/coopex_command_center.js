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

const titles={geral:"Panorama",cooperados:"Cooperados",contratos:"Clientes e contratos",rotas:"Bairros e rotas",financeiro:"Financeiro",comparativo:"Evolução",configuracoes:"Configurações"};
let activeNav=null;
function formatKpis(){
 const overview=document.querySelector('.dashboard-tab-panel[data-panel="geral"]');
 const grid=overview?.querySelector(':scope > .gridKpi');
 if(!grid)return;
 [...grid.children].filter(el=>el.classList.contains('kpi')).forEach((card,i)=>{
  card.classList.add('cx-main-kpi');
  // The four period counters already show today's volume; do not show it twice.
  if(i===0){card.classList.add('cx-kpi-duplicated');return;}
  // Legacy dashboard injected a second emoji/icon outside the label.
  // Remove those separate decorations; keep the SVG badge inside the label.
  card.querySelectorAll('.cx-dash-card-wave,.cx-dash-card-icon,.kpi-icon,.legacy-icon').forEach(node=>node.remove());
  [...card.childNodes].filter(n=>n.nodeType===3).forEach(n=>{
   if(/[\p{Extended_Pictographic}\uFE0F\u200D]/u.test(n.textContent||'')){
    n.textContent=n.textContent.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu,'').trim();
   }
  });
  const label=card.querySelector('.lab');
  if(label&&!label.querySelector('.cx-kpi-icon')){
   label.querySelectorAll('.cx-dash-card-icon,.legacy-icon,.kpi-icon').forEach(node=>node.remove());
   const oldIcon=label.querySelector('img,.icon');
   if(oldIcon)oldIcon.remove();
   [...label.childNodes].filter(n=>n.nodeType===3).forEach(n=>{
     n.textContent=n.textContent.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu,'').trim();
   });
   const icon=document.createElement('span');icon.className='cx-kpi-icon';
   icon.innerHTML=svg(kpiIcons[i]||kpiIcons[0]);label.prepend(icon);
  }
  const val=card.querySelector('.val');
  if(val){
   const t=(val.textContent||'').trim();
   val.title=t;
   val.classList.toggle('cx-long-value',t.length>12);
   val.classList.toggle('cx-extra-long-value',t.length>17);
  }
 });
}
function chartColors(){
 const dark=document.body.classList.contains('dark-mode');
 const ink=dark?'#d9eaff':'#355b84';
 const grid=dark?'rgba(182,218,247,.105)':'rgba(57,111,169,.12)';
 try{
  if(!window.Chart)return;
  const charts=window.Chart.instances?Object.values(window.Chart.instances):[];
  charts.forEach(chart=>{
   if(!chart?.options)return;
   const scales=chart.options.scales||{};
   Object.values(scales).forEach(axis=>{
    if(!axis)return;
    axis.grid=axis.grid||{};
    axis.grid.color=grid;axis.grid.lineWidth=.6;
    axis.ticks=axis.ticks||{};
    axis.ticks.color=ink;axis.ticks.font={size:11,weight:'600'};
    axis.title=axis.title||{};axis.title.color=ink;
    axis.border=axis.border||{};axis.border.color=grid;
   });
   chart.options.plugins=chart.options.plugins||{};
   chart.options.plugins.legend=chart.options.plugins.legend||{};
   chart.options.plugins.legend.labels=chart.options.plugins.legend.labels||{};
   chart.options.plugins.legend.labels.color=ink;
   chart.options.plugins.legend.labels.font={size:11,weight:'700'};
   chart.options.plugins.tooltip=chart.options.plugins.tooltip||{};
   chart.options.plugins.tooltip.backgroundColor=dark?'rgba(7,29,53,.98)':'rgba(19,55,92,.96)';
   chart.options.plugins.tooltip.titleColor='#f5fbff';
   chart.options.plugins.tooltip.bodyColor='#e1f2ff';
   chart.data?.datasets?.forEach((d,i)=>{
     if(chart.config.type==='doughnut') {
      d.borderColor=dark?'#163958':'#f1f8ff';
      return;
     }
     if(d.type==='line'||chart.config.type==='line'){
      d.borderColor=dark?'#79d7ff':'#177ec0';
      d.backgroundColor=d.borderColor;
      d.pointBackgroundColor=d.borderColor;
      d.borderWidth=2.6;d.tension=.36;
      d.pointRadius=chart.data.labels?.length>15?1.5:3;
     }else if(chart.canvas?.id==='chartDia'){
      d.backgroundColor=dark?'#42c7a6':'#20aa86';
      d.borderRadius=7;d.maxBarThickness=36;
     }else{
      d.backgroundColor=i===0?(dark?'#5aa8ee':'#276db5'):(dark?'#44c7ad':'#19a37d');
      d.borderRadius=7;
     }
   });
   chart.update('none');
  });
 }catch(e){}
}
function apply(){
 const nav=document.querySelector('.dashboard-tabs-bar');
 const filter=document.querySelector('.dashboard-filter-shell');
 const main=document.querySelector('main.wrap');
 if(!nav||!filter||!main||nav.querySelectorAll('[data-tab]').length<5)return false;
 const body=document.body;
 body.classList.add('cx-command-center','cx-report-glass');
 // The old scripts can rebuild this menu once; reconstruct only for a NEW navigation node.
 if(nav===activeNav&&nav.classList.contains('cx-horizontal-nav')){
  const exp=document.getElementById('cxExpOpen');
  const host=nav.querySelector('.cx-nav-tools');
  if(exp&&host&&!host.contains(exp))host.appendChild(exp);
  formatKpis();return true;
 }
 activeNav=nav;
 nav.classList.remove('cx-report-rail');
 nav.classList.add('cx-horizontal-nav');
 nav.setAttribute('aria-label','Seções do centro gerencial');
 nav.querySelectorAll('.cx-rail-brand,.cx-rail-kicker,.cx-rail-footer,.report-back,.cx-menu-brand,.cx-nav-tools').forEach(el=>el.remove());
 const brand=document.createElement('div');brand.className='cx-menu-brand';
 brand.innerHTML='<a href="/admin" class="cx-back" title="Voltar ao administrador" aria-label="Voltar ao administrador">‹</a><span class="cx-brand-name">Centro Gerencial</span>';
 nav.prepend(brand);
 nav.querySelectorAll('.dashboard-tab-button').forEach(btn=>{
  const id=btn.dataset.tab;
  const icon=btn.querySelector('.tab-icon');
  if(icon)icon.innerHTML=svg(icons[id]||icons.geral);
  const spans=btn.querySelectorAll('span');
  if(spans.length>1)spans[1].textContent=titles[id]||id;
  btn.title=titles[id]||id;
 });
 const tools=document.createElement('div');tools.className='cx-nav-tools';nav.appendChild(tools);
 // Keep one actual export button, preserving the original event listener.
 const exportBtn=document.getElementById('cxExpOpen');
 if(exportBtn)tools.appendChild(exportBtn);
 const oldActions=nav.querySelector('.dashboard-tabs-actions');
 if(oldActions){
  const otherExport=[...oldActions.querySelectorAll('button,a')].find(x=>/exportar/i.test(x.textContent||''));
  if(!exportBtn&&otherExport)tools.appendChild(otherExport);
  oldActions.remove();
 }
 const heading=document.querySelector('.cx-report-heading');
 if(heading){
   const otherExport=heading.querySelector('#cxExpOpen');
   if(otherExport)tools.appendChild(otherExport);
   heading.remove();
 }
 const periods=document.getElementById('cx-period-counts');
 if(periods)nav.after(periods);
 filter.classList.add('cx-filters-compact');
 let toggle=filter.querySelector('.cx-filter-details-button');
 if(!toggle){
  toggle=document.createElement('button');toggle.type='button';
  toggle.className='cx-filter-details-button';
  filter.querySelector('.dashboard-filter-title-row')?.appendChild(toggle);
  toggle.addEventListener('click',()=>{
   const compact=filter.classList.toggle('cx-filters-compact');
   toggle.textContent=compact?'Mais filtros':'Menos filtros';
   toggle.setAttribute('aria-expanded',String(!compact));
  });
 }
 toggle.textContent=filter.classList.contains('cx-filters-compact')?'Mais filtros':'Menos filtros';
 const grid=main.querySelector('.dashboard-tab-panel[data-panel="geral"] > .gridKpi');
 formatKpis();
 chartColors();
 return true;
}
function start(){
 apply();
 const watch=new MutationObserver(()=>{
  const nav=document.querySelector('.dashboard-tabs-bar');
  if(nav&&nav!==activeNav)apply();
 });
 watch.observe(document.body,{childList:true,subtree:false});
 let count=0;
 const interval=setInterval(()=>{if(apply()||++count>30)clearInterval(interval);},100);
 window.addEventListener('pageshow',()=>{apply();chartColors()});
 window.addEventListener('focus',()=>{apply();chartColors()});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();