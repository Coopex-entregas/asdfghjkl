/* CoopEX Maps — camadas nativas do Google Maps, sem recriar o mapa e os alfinetes. */
(()=>{
"use strict";
let map=null,traffic=null;
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
'</div><label class="cx-layer-traffic"><input id="cxTrafficLayer" type="checkbox"><span>Mostrar trânsito</span></label></section>'
].join("");
document.body.append(widget);
// Barra de trânsito entre pesquisa de cooperados e clima, alinhada ao topo.
const trafficButton=document.createElement("button");
trafficButton.type="button";trafficButton.id="cxTrafficTopButton";
trafficButton.innerHTML='<span aria-hidden="true" class="cx-traffic-indicator">●</span><strong>Trânsito</strong>';
trafficButton.setAttribute("aria-controls","cxTrafficPanel");
trafficButton.setAttribute("aria-expanded","false");
const trafficPanel=document.createElement("section");
trafficPanel.id="cxTrafficPanel";trafficPanel.hidden=true;
trafficPanel.innerHTML=[
 '<div class="cx-traffic-panel-head"><strong>Trânsito nas rotas</strong><button type="button" id="cxTrafficPanelClose" aria-label="Fechar trânsito">×</button></div>',
 '<div id="cxTrafficFindings">Calcule uma rota para verificar lentidão no trajeto.</div>',
 '<label class="cx-traffic-sound"><input type="checkbox" id="cxTrafficBeep"> Avisar com som ao detectar lentidão</label>',
 '<small class="cx-traffic-help">A camada de trânsito mostra as vias por cores. Avisos de tempo são obtidos das rotas calculadas; motivos de congestionamento só aparecem quando houver fonte confirmada.</small>'
].join("");
document.body.append(trafficPanel);
const trafficFindings=trafficPanel.querySelector("#cxTrafficFindings");
const topDock=document.getElementById("cxTopControlDock");
const weather=topDock?.querySelector(".weather.glass");
if(topDock)topDock.insertBefore(trafficButton,weather||null);
else document.body.append(trafficButton);
let slowRoutes=[],beepEnabled=false,lastAlertKey="",audioContext=null;
function closeTraffic(){trafficPanel.hidden=true;trafficButton.setAttribute("aria-expanded","false")}
function positionTraffic(){
 const box=trafficButton.getBoundingClientRect();
 trafficPanel.style.left=Math.max(8,Math.min(box.left,innerWidth-trafficPanel.offsetWidth-8))+"px";
 trafficPanel.style.top=Math.min(innerHeight-140,box.bottom+9)+"px";
}
function openTraffic(){trafficPanel.hidden=false;trafficButton.setAttribute("aria-expanded","true");positionTraffic()}
trafficButton.addEventListener("click",()=>{
 trafficPanel.hidden?openTraffic():closeTraffic();
 if(map){
  if(!traffic)traffic=new google.maps.TrafficLayer();
  traffic.setMap(map);
  const checkbox=widget.querySelector("#cxTrafficLayer");
  if(checkbox)checkbox.checked=true;
 }
});
trafficPanel.querySelector("#cxTrafficPanelClose").addEventListener("click",closeTraffic);
trafficPanel.querySelector("#cxTrafficBeep").addEventListener("change",e=>{
 beepEnabled=e.target.checked;
 if(beepEnabled&&window.AudioContext)audioContext=audioContext||new AudioContext();
});
window.addEventListener("resize",()=>{if(!trafficPanel.hidden)positionTraffic()});
function playBeep(){
 if(!beepEnabled||!audioContext)return;
 try{
  if(audioContext.state==="suspended")audioContext.resume();
  const when=audioContext.currentTime;
  for(const offset of [0,.31]){
   const tone=audioContext.createOscillator(),gain=audioContext.createGain();
   tone.type="sine";tone.frequency.setValueAtTime(910,when+offset);
   gain.gain.setValueAtTime(0,when+offset);
   gain.gain.linearRampToValueAtTime(.18,when+offset+.025);
   gain.gain.exponentialRampToValueAtTime(.002,when+offset+.23);
   tone.connect(gain);gain.connect(audioContext.destination);
   tone.start(when+offset);tone.stop(when+offset+.25);
  }
 }catch(error){console.debug("Aviso de trânsito sem som:",error)}
}
function showTraffic(){
 trafficFindings.replaceChildren();
 if(!slowRoutes.length){
  trafficFindings.textContent="Nenhuma lentidão significativa identificada nas rotas calculadas. Isso não verifica todas as ruas da cidade.";
  return;
 }
 for(const result of slowRoutes){
  const row=document.createElement("div");row.className="cx-traffic-slow-row";
  const name=document.createElement("strong");
  name.textContent="Trânsito lento · "+result.name;
  const details=document.createElement("span");
  details.textContent="Acréscimo estimado de "+result.delay+" min no trajeto ("+result.current+" min com trânsito).";
  row.append(name,details);trafficFindings.append(row);
 }
}
window.addEventListener("coopex:routes-ready",event=>{
 const routes=event.detail?.routes||[];
 slowRoutes=routes.map((route,index)=>{
  const current=Number(route.durationMillis),free=Number(route.staticDurationMillis);
  if(!Number.isFinite(current)||!Number.isFinite(free)||free<=0)return null;
  const extra=Math.max(0,Math.round((current-free)/60000));
  if(extra<5||(current-free)/free<.25)return null;
  return {name:String(route.description||"alternativa "+(index+1)),delay:extra,current:Math.round(current/60000)};
 }).filter(Boolean).slice(0,3);
 showTraffic();
 const key=slowRoutes.map(item=>item.name+":"+item.delay).join("|");
 if(key&&key!==lastAlertKey){lastAlertKey=key;playBeep()}
});
window.addEventListener("coopex:routes-cleared",()=>{slowRoutes=[];lastAlertKey="";trafficFindings.textContent="Calcule uma rota para verificar lentidão no trajeto."});

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
widget.querySelector("#cxTrafficLayer").addEventListener("change",e=>{
 if(!map)return;
 if(!traffic)traffic=new google.maps.TrafficLayer();
 traffic.setMap(e.target.checked?map:null);
});
document.addEventListener("pointerdown",e=>{if(!widget.contains(e.target))close();if(!trafficButton.contains(e.target)&&!trafficPanel.contains(e.target))closeTraffic()});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!panel.hidden)close()},true);
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