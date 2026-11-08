/* Barra de navegação COOPEX: controles sobre o Google Maps, sem mapa duplicado. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const host=document.querySelector(".cx-drawer-content"),top=document.querySelector(".cx-drawer-top");
if(!host||!top)return;
const brand=document.createElement("div");brand.className="cx-maps-brand";brand.innerHTML='<span class="cx-map-pin">📍</span><strong>Google Maps · COOPEX</strong><span class="cx-map-sub">NATAL / RN</span>';
host.insertBefore(brand,host.firstChild);
const nav=document.createElement("nav");nav.className="cx-map-nav";nav.setAttribute("aria-label","Ferramentas do mapa");
const entries=[["explorar","🔎","Explorar"],["rotas","↗","Rotas"],["cooperados","🏍️","Cooperados"],["camadas","▱","Camadas"]];
for(const [id,ic,label]of entries){const b=document.createElement("button");b.type="button";b.dataset.cxNav=id;b.innerHTML="<span aria-hidden='true'>"+ic+"</span>"+label;b.addEventListener("click",()=>{
nav.querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));
if(id==="rotas"){ $("routeOpen")?.click();$("cxRouteOrigin")?.focus(); }
if(id==="explorar"){$("address")?.focus();}
if(id==="cooperados"){$("address").value="";$("address").dispatchEvent(new Event("input",{bubbles:true}));}
if(id==="camadas"){$("toolbarToggle")?.click();}
});nav.append(b)}
top.after(nav);nav.querySelector("[data-cx-nav='explorar']").classList.add("active");
$("address").placeholder="Rua, número, estabelecimento ou cooperado";
$("cxRouteOrigin")?.setAttribute("placeholder","A — Origem: rua, número ou cooperado");
$("routeOpen").textContent="↗ Rotas A → B";
const note=document.createElement("div");note.className="cx-route-guide";note.textContent="Rotas: informe A (origem), pesquise B (destino), depois clique em Calcular. Clique na linha ou opção para alternar caminhos.";
$("cxRoutes")?.before(note);
const collapse=$("addressCollapse"),expand=$("sideExpand");
collapse?.addEventListener("click",()=>{collapse.textContent="›"});
expand?.addEventListener("click",()=>{collapse.textContent="‹"});
})();
