/* COOPEX Google Maps: navigation, place research and responsive controls. */
(()=>{
"use strict";
const $=id=>document.getElementById(id), panel=$("addressPanel"), inside=panel?.querySelector(".cx-drawer-content"), top=panel?.querySelector(".cx-drawer-top");
if(!panel||!inside||!top)return;
const brand=document.createElement("div");brand.className="cx-maps-brand";brand.innerHTML='<span class="cx-map-pin">📍</span><strong>CoopEX Maps</strong>';
inside.insertBefore(brand,inside.firstChild);
const stats=document.querySelector("#ui>.stats");
const statsHome=stats?.parentElement||null;
const statsPlaceholder=document.createComment("coopex-stats-home");
if(stats&&statsHome)statsHome.insertBefore(statsPlaceholder,stats);
function animateStats(nextParent,before=null){
 if(!stats||!nextParent||(before?stats.parentElement===nextParent:stats.parentElement===nextParent))return;
 const old=stats.getBoundingClientRect();
 if(before)nextParent.insertBefore(stats,before);else nextParent.append(stats);
 const now=stats.getBoundingClientRect();
 const deltaX=old.left-now.left,deltaY=old.top-now.top;
 if(old.width&&now.width&&Math.abs(deltaX)<2000&&Math.abs(deltaY)<2000&&stats.animate){
  stats.animate([{transform:`translate(${deltaX}px,${deltaY}px) scale(.92)`,opacity:.65},{transform:"translate(0,0) scale(1)",opacity:1}],
  {duration:360,easing:"cubic-bezier(.2,1.2,.24,1)"});
 }
}
function syncStatsLocation(){
 const compact=panel.classList.contains("cx-search-idle")||document.body.classList.contains("cx-map-sidebar-closed");
 if(compact){if(statsHome)animateStats(statsHome,statsPlaceholder.nextSibling);document.body.classList.remove("cx-stats-in-menu")}
 else{animateStats(brand);document.body.classList.add("cx-stats-in-menu")}
}
const nav=document.createElement("nav");nav.className="cx-map-nav";
const tabs=[["explorar","⌕","Explorar"],["rotas","↗","Rotas"],["camadas","▱","Camadas"]];
let activeTab="explorar",collapsed=false;
const results=$("cxUnifiedResults"),routes=$("cxRoutes"),search=$("address"),org=$("cxRouteOrigin");
const group=document.createElement("section");group.id="cxRouteEditor";group.className="cx-route-editor";group.hidden=true;
group.innerHTML='<div class="cx-travel-modes" role="group" aria-label="Rota econômica de carro"><button type="button" data-cx-mode="DRIVING" class="active" title="Rota econômica">🚘</button></div><div class="cx-origin-mount"></div><div class="cx-destination-mount"></div>';
top.after(group);
const destinationMount=group.querySelector(".cx-destination-mount");const originMount=group.querySelector(".cx-origin-mount");
const destRow=document.createElement("div");destRow.className="cx-dest-row";destRow.innerHTML='<span>📍</span><input id="cxDestinationMirror" type="search" placeholder="Escolha o destino" aria-label="Destino"><button type="button" id="cxDestSearch" aria-label="Localizar destino">⌕</button>';
destinationMount.append(destRow);
const destMirror=destRow.querySelector("input");
destMirror.addEventListener("input",()=>{search.value=destMirror.value;search.dispatchEvent(new Event("input",{bubbles:true}));});
destRow.querySelector("button").onclick=()=>{search.value=destMirror.value;calculateWhenReady()};
search.addEventListener("input",()=>{if(document.activeElement!==destMirror)destMirror.value=search.value});

const swap=document.createElement("button");swap.type="button";swap.id="cxSwapRoute";swap.textContent="⇅";swap.title="Inverter partida e destino";swap.setAttribute("aria-label","Inverter partida e destino");
swap.onclick=()=>{const first=org.value;org.value=destMirror.value;destMirror.value=first;search.value=first;calculateWhenReady()};
group.append(swap);
const stopRow=document.createElement("div");stopRow.id="cxStopEntry";stopRow.className="cx-stop-input-row";
stopRow.innerHTML='<input id="cxStopInput" type="search" placeholder="Adicionar destino" aria-label="Adicionar destino"><button id="cxStopSubmit" type="button" title="Adicionar destino">＋</button>';
group.append(stopRow);
stopRow.querySelector("button").onclick=()=>document.getElementById("addStop")?.click();
stopRow.querySelector("input").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();document.getElementById("addStop")?.click()}});

/* Organização visual como o painel de direções do Google Maps. */
const routeHeader=document.createElement("div");routeHeader.id="cxDirectionsHeader";
routeHeader.innerHTML='<button id="cxDirectionsBack" type="button" title="Voltar ao mapa" aria-label="Voltar">←</button><strong>Rotas</strong><button id="cxDirectionsClose" type="button" title="Recolher o menu" aria-label="Recolher menu">☰</button>';
group.prepend(routeHeader);
routeHeader.querySelector("#cxDirectionsBack").onclick=()=>setTab("explorar");
routeHeader.querySelector("#cxDirectionsClose").onclick=()=>collapse();
const stopsPanel=$("stopsList");
if(stopsPanel)group.append(stopsPanel);
const addDestination=stopRow.querySelector("input");
addDestination.placeholder="Adicionar destino";
const routeResultPanel=$("cxRoutes");
if(routeResultPanel)group.after(routeResultPanel);
const routeInfo=document.createElement("div");routeInfo.className="cx-route-summary";
routeInfo.innerHTML='<strong>Rotas sugeridas</strong>';
if(routeResultPanel)routeResultPanel.before(routeInfo);
const depRow=originMount;depRow.classList.add("cx-waypoint-row","cx-waypoint-start");
const arrivalRow=destinationMount;arrivalRow.classList.add("cx-waypoint-row","cx-waypoint-finish");
const routeMarker=document.createElement("span");routeMarker.className="cx-waypoint-dot";routeMarker.innerHTML='<svg viewBox="0 0 24 24" width="19" height="22" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>';arrivalRow.prepend(routeMarker);
function syncFinalMarker(){
 const stops=[...stopsPanel?.querySelectorAll(".cx-stop-line")||[]];
 const target=stops.length?stops[stops.length-1]:arrivalRow;
 if(routeMarker.parentElement!==target)target.prepend(routeMarker);
 // O pin vermelho identifica somente o último destino, inclusive após arrastar.
 arrivalRow.classList.toggle("cx-last-destination",!stops.length);
 stops.forEach((row,index)=>row.classList.toggle("cx-last-destination",index===stops.length-1));
}
// Origem, destinos e botão de inverter compõem uma única coluna compacta.
const waypoints=document.createElement("div");waypoints.className="cx-waypoints";
group.insertBefore(waypoints,originMount);
waypoints.append(originMount,arrivalRow,swap);
if(stopsPanel)group.insertBefore(stopsPanel,stopRow);
const grip='<svg viewBox="0 0 15 22" width="15" height="22" aria-hidden="true"><g fill="currentColor"><circle cx="4" cy="3" r="1.5"/><circle cx="11" cy="3" r="1.5"/><circle cx="4" cy="9" r="1.5"/><circle cx="11" cy="9" r="1.5"/><circle cx="4" cy="15" r="1.5"/><circle cx="11" cy="15" r="1.5"/></g></svg>';
for(const row of [originMount,arrivalRow]){
 const handle=document.createElement("span");handle.className="cx-waypoint-grip";handle.innerHTML=grip;handle.title="Arraste para mudar a ordem dos destinos";
 row.prepend(handle);row.draggable=true;
}
const routeDrop=(from,to)=>window.COOPEX_ROUTE_ACTIONS?.reorder?.(from,to);
function wireWaypointDnD(){
 const stopRows=[...stopsPanel.querySelectorAll(".cx-stop-line")];
 // O painel exibe partida, primeiro destino e depois os demais destinos.
  const all=[originMount,arrivalRow,...stopRows];
 all.forEach((node,i)=>{
  node.dataset.cxRouteIndex=String(i);node.draggable=true;
  if(node.dataset.cxRouteDnD==="yes")return;
  node.dataset.cxRouteDnD="yes";
  node.addEventListener("dragstart",e=>{e.dataTransfer?.setData("application/x-coopex-route",node.dataset.cxRouteIndex);e.dataTransfer.effectAllowed="move";},true);
  node.addEventListener("dragover",e=>{e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect="move";node.classList.add("cx-route-drag-over");},true);
  node.addEventListener("dragleave",()=>node.classList.remove("cx-route-drag-over"),true);
  node.addEventListener("dragend",()=>document.querySelectorAll(".cx-route-drag-over").forEach(el=>el.classList.remove("cx-route-drag-over")),true);
  node.addEventListener("drop",e=>{
   const index=e.dataTransfer?.getData("application/x-coopex-route");
   if(index===undefined||index==="")return;
   e.preventDefault();e.stopImmediatePropagation();
   document.querySelectorAll(".cx-route-drag-over").forEach(el=>el.classList.remove("cx-route-drag-over"));
   routeDrop(Number(index),Number(node.dataset.cxRouteIndex));
  },true);
 });
}
const waypointObserver=new MutationObserver(wireWaypointDnD);
if(stopsPanel)waypointObserver.observe(stopsPanel,{childList:true});
const finalMarkerObserver=new MutationObserver(()=>syncFinalMarker());
if(stopsPanel)finalMarkerObserver.observe(stopsPanel,{childList:true});
syncFinalMarker();
wireWaypointDnD();

const coopBox=document.createElement("div");coopBox.id="cxCoopSearch";coopBox.className="glass";
coopBox.innerHTML='<span>⌕</span><input id="cxCoopInput" placeholder="Pesquisar cooperado..." aria-label="Pesquisar cooperado" autocomplete="off"><button id="cxCoopClear" type="button" aria-label="Limpar pesquisa do cooperado" title="Limpar pesquisa" hidden>×</button><div id="cxCoopHits" hidden></div>';
document.body.append(coopBox);
// Uma única barra responsiva: cooperados | clima | filtros | Voltar.
const topDock=document.createElement("div");topDock.id="cxTopControlDock";
topDock.setAttribute("aria-label","Pesquisa de cooperados, clima e filtros");
document.body.append(topDock);
for(const element of [coopBox,$("ui")?.querySelector(".weather.glass"),$("ui")?.querySelector(".filters.glass"),$("ui")?.querySelector(".back-link")]){
 if(element)topDock.append(element);
}
const coopInput=coopBox.querySelector("input"),coopHits=coopBox.querySelector("#cxCoopHits"),coopClear=coopBox.querySelector("#cxCoopClear");
function syncCoopClear(){coopClear.hidden=!coopInput.value.trim()}
function clearCoopQuery(){coopInput.value="";closeCoopHits();syncCoopClear()}
coopClear.addEventListener("click",()=>{clearCoopQuery();coopInput.focus()});
let cooperados=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[];
// Resultados fora da barra para que o overflow do topo não esconda a lista.
document.body.append(coopHits);
coopHits.setAttribute("role","listbox");
coopInput.setAttribute("aria-controls","cxCoopHits");
coopInput.setAttribute("aria-expanded","false");
const coopNormalize=v=>String(v||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().trim();
function statusOf(i){const online=i.online===true||i.online===1||i.online==="true"||i.online==="1";return !online?"offline":i.status==="ocioso"?"ocioso":"online"}
function positionCoopHits(){
 const rect=coopBox.getBoundingClientRect();
 const width=Math.max(230,Math.min(rect.width,window.innerWidth-16));
 coopHits.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+"px";
 coopHits.style.top=Math.min(window.innerHeight-100,rect.bottom+6)+"px";
 coopHits.style.width=width+"px";
}
function closeCoopHits(){coopHits.hidden=true;coopInput.setAttribute("aria-expanded","false")}
function chooseCooperado(person){
 if(!person)return;
 // Após localizar, liberar a pesquisa para outro cooperado sem fechar a ficha.
 clearCoopQuery();
 const selected=window.COOPEX_MAP_ACTIONS?.selectCooperado;
 if(typeof selected==="function")selected(person.id);
 const map=window.COOPEX_GOOGLE_NATIVE;
 const lat=Number(person.lat),lng=Number(person.lng);
 if(map&&Number.isFinite(lat)&&Number.isFinite(lng)){
  map.panTo({lat,lng});map.setZoom(17);
 }
}
function renderCoopHits(){
 const query=coopNormalize(coopInput.value);
 syncCoopClear();
 coopHits.replaceChildren();
 if(!query){closeCoopHits();return}
 const matches=cooperados.filter(i=>
  i&&i.id!=null&&i.ativo!==false&&coopNormalize(i.nome).includes(query)
 ).slice(0,14);
 if(!matches.length){
  const empty=document.createElement("div");empty.className="cx-coop-empty";
  empty.textContent="Nenhum cooperado com esse nome";
  coopHits.append(empty);
 }else for(const person of matches){
  const state=statusOf(person),button=document.createElement("button");
  button.type="button";button.setAttribute("role","option");
  const dot=document.createElement("span");
  dot.className="cx-coop-search-dot";
  dot.style.background=state==="online"?"#0cba71":state==="ocioso"?"#f7a52c":"#f14d62";
  const title=document.createElement("span");title.textContent=person.nome||"Cooperado";
  const status=document.createElement("small");status.textContent=state==="online"?"Online":state==="ocioso"?"Ocioso":"Offline";
  button.append(dot,title,status);
  button.addEventListener("pointerdown",e=>e.preventDefault());
  button.addEventListener("click",()=>chooseCooperado(person));
  coopHits.append(button);
 }
 positionCoopHits();
 coopHits.hidden=false;
 coopInput.setAttribute("aria-expanded","true");
}
coopInput.addEventListener("input",renderCoopHits);
coopInput.addEventListener("focus",()=>{if(coopInput.value.trim())renderCoopHits()});
coopInput.addEventListener("keydown",event=>{
 if(event.key==="Escape"){event.stopPropagation();closeCoopHits();return}
 if(event.key==="Enter"&&!event.isComposing){
  event.preventDefault();event.stopImmediatePropagation();
  const first=cooperados.find(i=>i&&i.id!=null&&i.ativo!==false&&coopNormalize(i.nome).includes(coopNormalize(coopInput.value)));
  if(first)chooseCooperado(first);else renderCoopHits();
 }
 if(event.key==="ArrowDown"&&!coopHits.hidden){
  event.preventDefault();coopHits.querySelector("button")?.focus();
 }
});
coopHits.addEventListener("keydown",event=>{
 if(event.key==="Escape"){event.stopPropagation();closeCoopHits();coopInput.focus();return}
 if(event.key==="Enter"&&document.activeElement?.tagName==="BUTTON")document.activeElement.click();
});
document.addEventListener("pointerdown",event=>{
 if(!coopBox.contains(event.target)&&!coopHits.contains(event.target))closeCoopHits();
});
window.addEventListener("resize",()=>{if(!coopHits.hidden)positionCoopHits()});
window.addEventListener("scroll",()=>{if(!coopHits.hidden)positionCoopHits()},true);
window.addEventListener("coopex:map-data",event=>{
 if(Array.isArray(event.detail?.items)){
  cooperados=event.detail.items;
  if(coopInput.value.trim()&&document.activeElement===coopInput)renderCoopHits();
 }
});

let lastAutoQuery="",lastAutoAt=0;
function calculateWhenReady(){
 if(activeTab!=="rotas")return;
 if(!org?.value.trim()||!destMirror.value.trim())return;
 const key=[org.value.trim(),destMirror.value.trim(),
  ...[...document.querySelectorAll("#stopsList input")].map(input=>input.value.trim())].join("|").toLowerCase();
 // Uma mudança pode disparar change, routes-changed e Enter simultaneamente.
 // Evitar múltiplas cobranças de Compute Routes para a MESMA sequência.
 if(key===lastAutoQuery&&Date.now()-lastAutoAt<20000)return;
 lastAutoQuery=key;lastAutoAt=Date.now();
 search.value=destMirror.value;
 document.getElementById("routeOpen")?.click();
}
destMirror.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();e.stopPropagation();calculateWhenReady()}});
org?.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();destMirror.focus();calculateWhenReady()}});
destMirror.addEventListener("change",calculateWhenReady);
window.addEventListener("coopex:routes-changed",()=>{
 if(activeTab==="rotas"&&org.value.trim()&&destMirror.value.trim())calculateWhenReady();
});
// A pesquisa começa como uma barra compacta. O painel completo só surge ao selecionar um lugar.
const searchDirections=document.createElement("button");
searchDirections.type="button";searchDirections.id="cxQuickDirections";
searchDirections.title="Direções";searchDirections.setAttribute("aria-label","Abrir rotas");
searchDirections.innerHTML='<svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 3 3 11l8 8 8-8-8-8Z"/><path d="M8 12h7m-3-3 3 3-3 3"/></svg>';
searchDirections.onclick=()=>setTab("rotas");
top.append(searchDirections);
function syncSearchStage(){
 const hasPlace=!!$("cxPlaceDetails")?.isConnected&&activeTab!=="rotas";
 const typed=!!search.value.trim();
 const idle=activeTab==="explorar"&&!hasPlace;
 panel.classList.toggle("cx-search-idle",idle);
 panel.classList.toggle("cx-search-suggesting",idle&&typed&&document.activeElement===search);
 panel.classList.toggle("cx-place-view",hasPlace);
 if(activeTab==="explorar")results.hidden=!(typed||document.activeElement===search);
 syncStatsLocation();
}
search.addEventListener("input",()=>{
 if($("cxPlaceDetails")?.isConnected){$("cxPlaceDetails").remove()}
 syncSearchStage();
});
search.addEventListener("focus",()=>{
 syncSearchStage();
 if(activeTab==="explorar"&&!search.value.trim()){
  results.hidden=false;
  // Recarrega Casa, Trabalho e pesquisas compartilhadas para todos os admins.
  window.dispatchEvent(new Event("coopex:history-focus"));
 }
});
search.addEventListener("blur",()=>setTimeout(syncSearchStage,180));
function returnToEmptySearch(){
 $("cxPlaceDetails")?.remove();
 search.value="";
 destMirror.value="";
 setTab("explorar");
 panel.classList.remove("cx-place-view","cx-directions-mode","cx-search-suggesting");
 panel.classList.add("cx-search-idle");
 $("cxUnifiedResults").hidden=true;
 $("addressMessage").textContent="";
 syncSearchStage();
}
$("addressClose")?.addEventListener("click",returnToEmptySearch);
window.addEventListener("coopex:search-reset",returnToEmptySearch);
window.addEventListener("coopex:place-details-ready",()=>{panel.classList.remove("cx-search-idle");panel.classList.add("cx-place-view");syncStatsLocation();});
const navBtns=new Map();
function setTab(id){
 const wasDirections=activeTab==="rotas";
 if(wasDirections&&id!=="rotas")window.dispatchEvent(new Event("coopex:exit-directions"));
 activeTab=id;panel.dataset.cxTab=id;
 panel.classList.toggle("cx-search-idle",id==="explorar"&&!$("cxPlaceDetails")?.isConnected);
 const placeDetails=$("cxPlaceDetails");
 if(placeDetails)placeDetails.hidden=id==="rotas";
 // A pesquisa de locais e a tela de direções não podem coexistir visualmente.
 panel.classList.toggle("cx-place-view",!!placeDetails&&id!=="rotas");
 inside.scrollTop=0;
 for(const [key,b] of navBtns)b.classList.toggle("active",key===id);
 group.hidden=id!=="rotas";
 routeInfo.hidden=id!=="rotas";
 nav.hidden=id==="rotas";
 panel.classList.toggle("cx-directions-mode",id==="rotas");
 syncStatsLocation();
 if(id!=="rotas"){$("cxRoutes").hidden=true}
 if(id==="rotas"){
  if(org&&org.parentElement!==originMount)originMount.append(org);
  if(org){org.hidden=false;org.placeholder="Selecione o ponto de partida"}
  if(search.value&&!destMirror.value)destMirror.value=search.value;
  $("routeOpen").textContent="Rotas";
  $("routeOpen").hidden=false;
  $("cxUnifiedResults").hidden=true;
  if(org?.value.trim())destMirror.focus();else org?.focus();
 }else{
  if(org)org.hidden=true;
  if(id==="explorar"&&document.getElementById("cxPlaceDetails"))document.getElementById("cxPlaceDetails").hidden=false;
  $("routeOpen").textContent="↗ Direções";
  search.placeholder="Pesquisar rua, número ou estabelecimento";
  if(id==="camadas")$("toolbarToggle")?.click();
 }
}
for(const [id,ic,label] of tabs){
 const b=document.createElement("button");b.type="button";b.dataset.cxNav=id;b.innerHTML='<span aria-hidden="true">'+ic+'</span>'+label;
 b.onclick=()=>setTab(id);nav.append(b);navBtns.set(id,b);
}
top.after(nav);

/* Ícones vetoriais uniformes, sem emojis desproporcionais. */
const iconPaths={
 search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
 compass:'<circle cx="12" cy="12" r="9"/><path d="m15.8 8.2-2.5 5.1-5.1 2.5 2.5-5.1z"/>',
 directions:'<path d="M4 6h8v6h8"/><path d="m15 7 5 5-5 5"/><circle cx="4" cy="6" r="2"/><circle cx="20" cy="18" r="2"/>',
 layers:'<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
 car:'<path d="M5 11 7 6h10l2 5"/><rect x="3" y="11" width="18" height="7" rx="2"/><path d="M7 18v2m10-2v2M7 14h.01M17 14h.01"/>',
 motorcycle:'<circle cx="5" cy="17" r="3"/><circle cx="19" cy="17" r="3"/><path d="m8 17 4-7 3 7h4m-7-7-3 0m9-5 2 3"/>',
 train:'<rect x="5" y="3" width="14" height="15" rx="3"/><path d="M5 10h14M8 22l3-4m5 4-3-4M9 14h.01M15 14h.01"/>',
 walk:'<circle cx="13" cy="4" r="2"/><path d="m7 21 4-7 2-3 3 2 3 2m-7-4-3 5m3-8 2 2-2 5 5 6"/>',
 bike:'<circle cx="5" cy="17" r="3.5"/><circle cx="19" cy="17" r="3.5"/><path d="m5 17 5-9 5 9H5m10 0 3-8h-3M8 8h4"/>',
 arrow:'<path d="m15 18-6-6 6-6"/>',
 swap:'<path d="M7 20V4m0 0-4 4m4-4 4 4M17 4v16m0 0-4-4m4 4 4-4"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 menu:'<path d="M4 7h16M4 12h16M4 17h16"/>'
};
function vector(name){
 const path=iconPaths[name];if(!path)return null;
 const span=document.createElement("span");span.className="cx-icon-vector";span.innerHTML='<svg class="cx-icon-svg" viewBox="0 0 24 24" aria-hidden="true">'+path+'</svg>';return span;
}
for(const button of nav.querySelectorAll("button")){
 const named={explorar:"compass",rotas:"directions",camadas:"layers"}[button.dataset.cxNav];
 if(!named)continue;const target=button.querySelector("span");
 if(target){target.replaceWith(vector(named))}
}
for(const button of group.querySelectorAll("[data-cx-mode]")){
 const named={DRIVING:"car",TWO_WHEELER:"motorcycle",TRANSIT:"train",WALKING:"walk",BICYCLING:"bike"}[button.dataset.cxMode];
 if(named){button.replaceChildren(vector(named))}
}
for(const [button,name] of [[routeHeader.querySelector("#cxDirectionsBack"),"arrow"],[routeHeader.querySelector("#cxDirectionsClose"),"menu"],[swap,"swap"],[stopRow.querySelector("#cxStopSubmit"),"plus"]]){
 if(button)button.replaceChildren(vector(name));
}
const hamburger=$("cxDrawerMenu");if(hamburger)hamburger.replaceChildren(vector("menu"));

$("cxDrawerMenu").addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapse()},true);
const edge=$("addressCollapse"),expandButton=$("sideExpand");

function collapse(){
 // No Street View, recolher só o painel: nunca limpar o local nem fechar a imagem.
 if(window.COOPEX_GOOGLE_NATIVE?.getStreetView()?.getVisible()){
  collapsed=true;
  document.body.classList.add("cx-map-sidebar-closed","cx-street-sidebar-collapsed");
  panel.setAttribute("aria-expanded","false");
  edge.setAttribute("aria-label","Reabrir menu");
  syncStatsLocation();
  return;
 }
 collapsed=false;
 document.body.classList.remove("cx-map-sidebar-closed");
 panel.setAttribute("aria-expanded","true");
 edge.textContent="‹";
 if(document.getElementById("addressClose"))document.getElementById("addressClose").click();
 else returnToEmptySearch();
 if(document.activeElement instanceof HTMLElement)document.activeElement.blur();
 syncStatsLocation();
}
function expand(){collapsed=false;document.body.classList.remove("cx-map-sidebar-closed","cx-street-sidebar-collapsed");panel.setAttribute("aria-expanded","true");edge.textContent="‹";edge.setAttribute("aria-label","Recolher menu");syncStatsLocation();}
edge.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapsed?expand():collapse()},true);
expandButton?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();expand()},true);
const reopen=document.createElement("button");reopen.id="cxReopenMap";reopen.type="button";reopen.textContent="";reopen.append(vector("menu"));reopen.setAttribute("aria-label","Abrir menu do mapa");reopen.onclick=expand;document.body.append(reopen);
$("routeOpen").addEventListener("click",()=>{if(activeTab!=="rotas")setTab("rotas")},true);
const hint=document.createElement("div");hint.className="cx-route-hint";hint.textContent="";routes?.after(hint);
const welcome=results.querySelector(".cx-drawer-welcome");if(welcome)welcome.innerHTML="<strong>Explore no mapa</strong>";
$("addressMessage").textContent="";
// Seleção de estabelecimentos e ícones no Google: ficha aberta sem sair do CoopEX Maps.
window.addEventListener("coopex:place-selected",event=>{
 const place=event.detail?.point;
 if(!place?.label)return;
 search.value=place.label;
 destMirror.value=place.label;
 // Se o operador já está montando uma rota, selecionar o mapa define o destino,
 // mas NÃO volta para a ficha de um estabelecimento.
 if(activeTab==="rotas"){
  panel.classList.remove("cx-place-view","cx-search-idle","cx-search-suggesting");
  expand();
  calculateWhenReady();
  return;
 }
 setTab("explorar");
 panel.classList.remove("cx-search-idle","cx-search-suggesting");
 expand();
 const info=$("cxPlaceDetails");
 if(info){info.hidden=false;panel.classList.add("cx-place-view")}
});
// Permitir recolher a ficha sobre o Street View e reabri-la sem perder a posição.
function syncStreetCollapse(){
 const pano=window.COOPEX_GOOGLE_NATIVE?.getStreetView();
 if(!pano)return;
 if(!pano.getVisible()&&document.body.classList.contains("cx-street-sidebar-collapsed"))expand();
}
window.addEventListener("coopex:street-closed",syncStreetCollapse);
// Não recuperar o hamburger antigo ao fechar uma busca.
document.body.classList.remove("cx-map-sidebar-closed");
setTab("explorar");
syncSearchStage();
})();
