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
group.innerHTML='<div class="cx-travel-modes" role="group" aria-label="Meio de transporte"><button type="button" data-cx-mode="DRIVING" class="active" title="Carro">🚘</button><button type="button" data-cx-mode="TWO_WHEELER" title="Moto">🏍</button><button type="button" data-cx-mode="TRANSIT" title="Transporte público">🚉</button><button type="button" data-cx-mode="WALKING" title="A pé">🚶</button><button type="button" data-cx-mode="BICYCLING" title="Bicicleta">🚲</button></div><div class="cx-origin-mount"></div><div class="cx-destination-mount"></div>';
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
const routeMarker=document.createElement("span");routeMarker.className="cx-waypoint-dot";routeMarker.textContent="●";arrivalRow.prepend(routeMarker);

const coopBox=document.createElement("div");coopBox.id="cxCoopSearch";coopBox.className="glass";
coopBox.innerHTML='<span>⌕</span><input id="cxCoopInput" placeholder="Pesquisar cooperado..." aria-label="Pesquisar cooperado" autocomplete="off"><div id="cxCoopHits" hidden></div>';
document.body.append(coopBox);
const coopInput=coopBox.querySelector("input"),coopHits=coopBox.querySelector("#cxCoopHits");
let cooperados=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[];

function statusOf(i){const online=i.online===true||i.online===1||i.online==="true"||i.online==="1";return !online?"offline":i.status==="ocioso"?"ocioso":"online"}
function renderCoopHits(){
 const normalize=v=>String(v||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
 const query=normalize(coopInput.value.trim());
 coopHits.replaceChildren();coopHits.hidden=!query;
 if(!query)return;
 const matches=cooperados.filter(i=>normalize(i.nome).includes(query)).slice(0,12);
 for(const person of matches){
  const state=statusOf(person);
  const button=document.createElement("button");button.type="button";
  const dot=document.createElement("span");dot.className="cx-coop-search-dot";dot.style.background=state==="online"?"#0cba71":state==="ocioso"?"#f7a52c":"#f14d62";
  const name=document.createElement("span");name.textContent=person.nome||"Cooperado";
  button.append(dot,name);button.onclick=()=>{
   coopInput.value=person.nome||"";coopHits.hidden=true;
   window.COOPEX_MAP_ACTIONS?.selectCooperado?.(person.id);
   const lat=Number(person.lat),lng=Number(person.lng),googleMap=window.COOPEX_GOOGLE_NATIVE;
   if(googleMap&&Number.isFinite(lat)&&Number.isFinite(lng)){
    googleMap.panTo({lat,lng});
    googleMap.setZoom(17);
   }
  };
  coopHits.append(button);
 }
}
coopInput.addEventListener("input",renderCoopHits);
window.addEventListener("coopex:map-data",e=>{
 if(Array.isArray(e.detail?.items)){cooperados=e.detail.items;if(coopInput.value.trim()&&!coopHits.hidden)renderCoopHits()}
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
window.addEventListener("coopex:routes-changed",()=>{
 if(activeTab==="rotas"&&org.value.trim()&&destMirror.value.trim())calculateWhenReady();
});
const navBtns=new Map();
function setTab(id){
 activeTab=id;panel.dataset.cxTab=id;
 for(const [key,b] of navBtns)b.classList.toggle("active",key===id);
 group.hidden=id!=="rotas";
 routeInfo.hidden=id!=="rotas";
 nav.hidden=id==="rotas";
 panel.classList.toggle("cx-directions-mode",id==="rotas");
 if(id!=="rotas"){$("cxRoutes").hidden=true}
 if(id==="rotas"){
  if(org&&org.parentElement!==originMount)originMount.append(org);
  if(org){org.hidden=false;org.placeholder="Selecione o ponto de partida"}
  if(search.value&&!destMirror.value)destMirror.value=search.value;
  $("routeOpen").textContent="Rotas";
  $("routeOpen").hidden=false;
  $("cxUnifiedResults").hidden=false;
  if(org?.value.trim())destMirror.focus();else org?.focus();
 }else{
  if(org)org.hidden=true;
  if(id==="explorar")document.getElementById("cxPlaceDetails")?.remove();
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
$("cxDrawerMenu").addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapse()},true);
const edge=$("addressCollapse"),expandButton=$("sideExpand");

function collapse(){collapsed=true;document.body.classList.add("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","false");edge.textContent="›";edge.setAttribute("aria-label","Expandir menu");if(document.activeElement instanceof HTMLElement)document.activeElement.blur();}
function expand(){collapsed=false;document.body.classList.remove("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","true");edge.textContent="‹";edge.setAttribute("aria-label","Recolher menu");}
edge.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapsed?expand():collapse()},true);
expandButton?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();expand()},true);
const reopen=document.createElement("button");reopen.id="cxReopenMap";reopen.type="button";reopen.textContent="☰";reopen.setAttribute("aria-label","Abrir menu do mapa");reopen.onclick=expand;document.body.append(reopen);
$("routeOpen").addEventListener("click",()=>{if(activeTab!=="rotas")setTab("rotas")},true);
const hint=document.createElement("div");hint.className="cx-route-hint";hint.textContent="";routes?.after(hint);
const welcome=results.querySelector(".cx-drawer-welcome");if(welcome)welcome.innerHTML="<strong>Explore no mapa</strong>";
$("addressMessage").textContent="";
setTab("explorar");
})();
