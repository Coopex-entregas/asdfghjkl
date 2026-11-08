/* COOPEX Google Maps: navigation, place research and responsive controls. */
(()=>{
"use strict";
const $=id=>document.getElementById(id), panel=$("addressPanel"), inside=panel?.querySelector(".cx-drawer-content"), top=panel?.querySelector(".cx-drawer-top");
if(!panel||!inside||!top)return;
const brand=document.createElement("div");brand.className="cx-maps-brand";brand.innerHTML='<span class="cx-map-pin">📍</span><strong>Google Maps · COOPEX</strong><small>Natal / RN</small>';
inside.insertBefore(brand,inside.firstChild);
const nav=document.createElement("nav");nav.className="cx-map-nav";
const tabs=[["explorar","⌕","Explorar"],["rotas","↗","Rotas"],["camadas","▱","Camadas"]];
let activeTab="explorar",collapsed=false;
const results=$("cxUnifiedResults"),routes=$("cxRoutes"),search=$("address"),org=$("cxRouteOrigin");
const group=document.createElement("section");group.id="cxRouteEditor";group.className="cx-route-editor";group.hidden=true;
group.innerHTML='<div class="cx-route-title"><strong>🚘 Planejar trajeto</strong><small>De A até B · percurso pelas ruas</small></div><div class="cx-origin-mount"></div><div class="cx-destination-mount"></div><div class="cx-route-tip">Adicione paradas e escolha entre as rotas disponibilizadas pelo Google. O tempo e os km aparecerão abaixo.</div>';
top.after(group);
const destinationMount=group.querySelector(".cx-destination-mount");const originMount=group.querySelector(".cx-origin-mount");
const destRow=document.createElement("div");destRow.className="cx-dest-row";destRow.innerHTML='<span>📍 B</span><input id="cxDestinationMirror" type="search" placeholder="Destino B — rua e número" aria-label="Destino da rota B"><button type="button" id="cxDestSearch" aria-label="Localizar destino">⌕</button>';
destinationMount.append(destRow);
const destMirror=destRow.querySelector("input");
destMirror.addEventListener("input",()=>{search.value=destMirror.value;search.dispatchEvent(new Event("input",{bubbles:true}));});
destRow.querySelector("button").onclick=()=>{search.value=destMirror.value;calculateWhenReady()};
search.addEventListener("input",()=>{if(document.activeElement!==destMirror)destMirror.value=search.value});

const swap=document.createElement("button");swap.type="button";swap.id="cxSwapRoute";swap.textContent="⇅ Inverter origem e destino";
swap.onclick=()=>{const first=org.value;org.value=destMirror.value;destMirror.value=first;search.value=first;calculateWhenReady()};
group.append(swap);
const stopRow=document.createElement("div");stopRow.id="cxStopEntry";stopRow.className="cx-stop-input-row";
stopRow.innerHTML='<input id="cxStopInput" type="search" placeholder="Adicionar parada: rua e número" aria-label="Adicionar parada"><button id="cxStopSubmit" type="button" title="Adicionar parada">＋</button>';
group.append(stopRow);
stopRow.querySelector("button").onclick=()=>document.getElementById("addStop")?.click();
stopRow.querySelector("input").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();document.getElementById("addStop")?.click()}});
const coopBox=document.createElement("div");coopBox.id="cxCoopSearch";coopBox.className="glass";
coopBox.innerHTML='<span>⌕</span><input id="cxCoopInput" placeholder="Pesquisar cooperado..." aria-label="Pesquisar cooperado" autocomplete="off"><div id="cxCoopHits" hidden></div>';
document.body.append(coopBox);
const coopInput=coopBox.querySelector("input"),coopHits=coopBox.querySelector("#cxCoopHits");
let cooperados=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[];
window.addEventListener("coopex:map-data",e=>{if(Array.isArray(e.detail?.items))cooperados=e.detail.items;});
coopInput.addEventListener("input",()=>{
 const normalize=v=>String(v||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
 const query=normalize(coopInput.value.trim());coopHits.replaceChildren();coopHits.hidden=!query;
 if(!query)return;
 cooperados.filter(i=>normalize(i.nome).includes(query)).slice(0,10).forEach(i=>{
  const button=document.createElement("button");button.type="button";button.textContent=(i.online?"🟢 ":"🔴 ")+(i.nome||"Cooperado");
  button.onclick=()=>{coopInput.value=i.nome||"";coopHits.hidden=true;window.COOPEX_MAP_ACTIONS?.selectCooperado?.(i.id)};
  coopHits.append(button);
 });
});

let lastAutoQuery="";
function calculateWhenReady(){
 if(activeTab!=="rotas")return;
 if(!org?.value.trim()||!destMirror.value.trim())return;
 search.value=destMirror.value;
 lastAutoQuery=org.value.trim()+"|"+destMirror.value.trim();
 document.getElementById("routeOpen")?.click();
}
destMirror.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();e.stopPropagation();calculateWhenReady()}});
org?.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();destMirror.focus();calculateWhenReady()}});
destMirror.addEventListener("change",calculateWhenReady);
const navBtns=new Map();
function setTab(id){
 activeTab=id;panel.dataset.cxTab=id;
 for(const [key,b] of navBtns)b.classList.toggle("active",key===id);
 group.hidden=id!=="rotas";
 if(id==="rotas"){
  if(org&&org.parentElement!==originMount)originMount.append(org);
  if(org){org.hidden=false;org.placeholder="⚪ A — origem: rua e número"}
  if(search.value&&!destMirror.value)destMirror.value=search.value;
  $("routeOpen").textContent="Calcular rotas e quilômetros";
  $("routeOpen").hidden=false;
  $("cxUnifiedResults").hidden=false;
  if(org?.value.trim())destMirror.focus();else org?.focus();
 }else{
  if(org)org.hidden=true;
  $("routeOpen").textContent="↗ Rotas A → B";
  search.placeholder="Pesquisar rua, número ou estabelecimento";
  if(id==="camadas")$("toolbarToggle")?.click();
 }
}
for(const [id,ic,label] of tabs){
 const b=document.createElement("button");b.type="button";b.dataset.cxNav=id;b.innerHTML='<span aria-hidden="true">'+ic+'</span>'+label;
 b.onclick=()=>setTab(id);nav.append(b);navBtns.set(id,b);
}
top.after(nav);
$("cxDrawerMenu").addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapse()},true);
const edge=$("addressCollapse"),expandButton=$("sideExpand");

function collapse(){collapsed=true;document.body.classList.add("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","false");edge.textContent="›";edge.setAttribute("aria-label","Expandir menu");if(document.activeElement instanceof HTMLElement)document.activeElement.blur();}
function expand(){collapsed=false;document.body.classList.remove("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","true");edge.textContent="‹";edge.setAttribute("aria-label","Recolher menu");}
edge.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapsed?expand():collapse()},true);
expandButton?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();expand()},true);
const reopen=document.createElement("button");reopen.id="cxReopenMap";reopen.type="button";reopen.textContent="☰";reopen.setAttribute("aria-label","Abrir menu do mapa");reopen.onclick=expand;document.body.append(reopen);
$("routeOpen").addEventListener("click",()=>{if(activeTab!=="rotas")setTab("rotas")},true);
const hint=document.createElement("div");hint.className="cx-route-hint";hint.textContent="Clique sobre uma alternativa para alterar a rota azul no mapa.";routes?.after(hint);
const welcome=results.querySelector(".cx-drawer-welcome");if(welcome)welcome.innerHTML="<strong>Explorar Natal e região</strong><small>Pesquise estabelecimentos, endereços ou cooperados. Os alfinetes continuam no mapa.</small>";
$("addressMessage").textContent="Google Maps · informações conforme disponibilidade das APIs.";
setTab("explorar");
})();
