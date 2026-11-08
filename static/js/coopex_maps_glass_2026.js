/* COOPEX Google Maps: navigation, place research and responsive controls. */
(()=>{
"use strict";
const $=id=>document.getElementById(id), panel=$("addressPanel"), inside=panel?.querySelector(".cx-drawer-content"), top=panel?.querySelector(".cx-drawer-top");
if(!panel||!inside||!top)return;
const brand=document.createElement("div");brand.className="cx-maps-brand";brand.innerHTML='<span class="cx-map-pin">📍</span><strong>Google Maps · COOPEX</strong><small>Natal / RN</small>';
inside.insertBefore(brand,inside.firstChild);
const nav=document.createElement("nav");nav.className="cx-map-nav";
const tabs=[["explorar","⌕","Explorar"],["rotas","↗","Rotas"],["cooperados","♙","Cooperados"],["camadas","▱","Camadas"]];
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
destRow.querySelector("button").onclick=()=>{search.value=destMirror.value;$("addressGo")?.click()};
search.addEventListener("input",()=>{if(document.activeElement!==destMirror)destMirror.value=search.value});
const navBtns=new Map();
function setTab(id){
 activeTab=id;panel.dataset.cxTab=id;
 for(const [key,b] of navBtns)b.classList.toggle("active",key===id);
 group.hidden=id!=="rotas";
 if(id==="rotas"){
  if(org&&org.parentElement!==originMount)originMount.append(org);
  if(org){org.hidden=false;org.placeholder="⚪ A — origem: rua, número ou cooperado"}
  if(search.value&&!destMirror.value)destMirror.value=search.value;
  $("routeOpen").textContent="Calcular rotas e quilômetros";
  $("routeOpen").hidden=false;
  $("cxUnifiedResults").hidden=false;
  org?.focus();
 }else{
  if(org)org.hidden=true;
  $("routeOpen").textContent="↗ Rotas A → B";
  if(id==="cooperados"){search.placeholder="Pesquisar cooperado pelo nome";search.value="";search.dispatchEvent(new Event("input",{bubbles:true}));}
  else search.placeholder="Pesquisar rua, número ou estabelecimento";
  if(id==="camadas")$("toolbarToggle")?.click();
 }
}
for(const [id,ic,label] of tabs){
 const b=document.createElement("button");b.type="button";b.dataset.cxNav=id;b.innerHTML='<span aria-hidden="true">'+ic+'</span>'+label;
 b.onclick=()=>setTab(id);nav.append(b);navBtns.set(id,b);
}
top.after(nav);
$("cxDrawerMenu").onclick=()=>{if(collapsed)expand();else setTab("explorar")};
const edge=$("addressCollapse"),expandButton=$("sideExpand");
function collapse(){collapsed=true;body.classList.add("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","false");edge.textContent="›";edge.setAttribute("aria-label","Expandir menu");document.activeElement?.blur?.();}
function expand(){collapsed=false;body.classList.remove("cx-map-sidebar-closed");panel.setAttribute("aria-expanded","true");edge.textContent="‹";edge.setAttribute("aria-label","Recolher menu");}
edge.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();collapsed?expand():collapse()},true);
expandButton?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();expand()},true);
const reopen=document.createElement("button");reopen.id="cxReopenMap";reopen.type="button";reopen.textContent="☰";reopen.setAttribute("aria-label","Abrir menu do mapa");reopen.onclick=expand;document.body.append(reopen);
$("routeOpen").addEventListener("click",()=>{if(activeTab!=="rotas")setTab("rotas")},true);
const hint=document.createElement("div");hint.className="cx-route-hint";hint.textContent="Clique sobre uma alternativa para alterar a rota azul no mapa.";routes?.after(hint);
const welcome=results.querySelector(".cx-drawer-welcome");if(welcome)welcome.innerHTML="<strong>Explorar Natal e região</strong><small>Pesquise estabelecimentos, endereços ou cooperados. Os alfinetes continuam no mapa.</small>";
$("addressMessage").textContent="Google Maps · informações conforme disponibilidade das APIs.";
setTab("explorar");
})();
