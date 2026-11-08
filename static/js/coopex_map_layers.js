/* CoopEX Maps — camadas nativas do Google Maps, sem recriar o mapa e os alfinetes. */
(()=>{
"use strict";
let map=null;
const widget=document.createElement("div");widget.id="cxLayersWidget";
widget.innerHTML=[
'<button id="cxLayersToggle" type="button" aria-label="Abrir camadas" aria-expanded="false" aria-controls="cxLayersPanel">',
'<span class="cx-layer-thumb" aria-hidden="true"><span class="cx-layer-thumb-lines"></span></span>',
'<span class="cx-layer-caption">◈ Camadas</span></button>',
'<section id="cxLayersPanel" aria-label="Opções de camadas" hidden>',
'<div class="cx-layers-panel-header"><strong>Camadas do mapa</strong><button id="cxLayersClose" type="button" aria-label="Fechar">×</button></div>',
'<div class="cx-layers-grid">',
'<button type="button" data-cx-maptype="roadmap" aria-pressed="true"><span class="cx-layer-preview cx-layer-road"></span>Mapa</button>',
'<button type="button" data-cx-maptype="hybrid" aria-pressed="false"><span class="cx-layer-preview cx-layer-satellite"></span>Satélite</button>',
'<button type="button" data-cx-maptype="terrain" aria-pressed="false"><span class="cx-layer-preview cx-layer-terrain"></span>Relevo</button>',
'</div></section>'
].join("");
document.body.append(widget);
// Painel de trânsito removido por solicitação da COOPEX.
const toggle=widget.querySelector("#cxLayersToggle"),panel=widget.querySelector("#cxLayersPanel");
const buttons=[...widget.querySelectorAll("[data-cx-maptype]")];
function sync(){
 const type=map?.getMapTypeId()||"roadmap";
 buttons.forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.cxMaptype===type)));
 widget.dataset.type=type;
 toggle.querySelector(".cx-layer-caption").textContent=type==="hybrid"?"◈ Satélite":type==="terrain"?"◈ Relevo":"◈ Camadas";
}
function close(){panel.hidden=true;toggle.setAttribute("aria-expanded","false")}
function open(){panel.hidden=false;toggle.setAttribute("aria-expanded","true")}
toggle.addEventListener("click",()=>panel.hidden?open():close());
widget.querySelector("#cxLayersClose").addEventListener("click",close);
buttons.forEach(button=>button.addEventListener("click",()=>{
 if(!map)return;
 map.setMapTypeId(button.dataset.cxMaptype);sync();close();
}));
document.addEventListener("pointerdown",e=>{if(!widget.contains(e.target))close()});
// Esc antes do atalho global de endereço e Street View.
window.addEventListener("keydown",event=>{
 if(event.key!=="Escape"||event.repeat)return;
 
 if(!panel.hidden){event.preventDefault();event.stopImmediatePropagation();close();toggle.blur()}
},true);
function connect(){
 if(map||!window.COOPEX_GOOGLE_NATIVE)return;
 map=window.COOPEX_GOOGLE_NATIVE;
 map.addListener("maptypeid_changed",sync);
 sync();
}
window.addEventListener("coopex:google-native-ready",connect);
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",connect);else connect();
window.addEventListener("coopex:open-layers",open);
})();