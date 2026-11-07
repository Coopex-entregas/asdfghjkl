/* COOPEX Google Maps: cartografia Google com alfinetes GPS do sistema.
 * Chave obtida do Render via template, nunca gravada no repositório.
 * O mapa é criado uma vez por carregamento, sem reconstruir a cada atualização.
 */
(()=>{
"use strict";
const key=window.COOPEX_GOOGLE_KEY;
function reportGoogleStatus(message,kind="error"){
 const node=document.getElementById("googleMapStatus");if(!node)return;
 node.textContent=message;node.hidden=false;node.dataset.kind=kind;
}
// O Google é a única cartografia visível, inclusive durante o carregamento.
document.body.classList.add("cx-google-only");
if(typeof key!=="string"||!key.trim()){
 reportGoogleStatus("A chave Google Maps não foi encontrada no Render. O mapa da COOPEX está configurado para usar somente Google Maps.");
 return;
}
window.gm_authFailure=()=>{
 authFailed=true;
 document.body.classList.remove("cx-google-ready");
 document.body.classList.add("cx-google-error");
 reportGoogleStatus("Google recusou a chave. Confira se Maps JavaScript API está ativada, faturamento e restrição de site no Google Cloud.");
};
const googleRoot=document.getElementById("googleMap");
if(!googleRoot)return;
let googleMap=null,tilesVisible=false,authFailed=false,loadFinished=false;
const markers=new Map(),lastPositions=new Map();
let searchingAddress=false,selectedId=null,lastItems=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[],syncingFromGoogle=false;
const palette={online:"#04bd70",ocioso:"#f0a12a",offline:"#ee475d"};
const state=i=>!i.online?"offline":i.status==="ocioso"?"ocioso":"online";
const normPos=i=>i&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng)&&+i.lat<-3&&+i.lng<-32;
const initials=n=>String(n||"CO").trim().split(/\s+/).filter(Boolean).slice(0,2).map(x=>x.charAt(0).toUpperCase()).join("");
function iconFor(status,selected){
 const ring=palette[status]||palette.offline;
 const radius=selected?24:21;
 const svg='<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60" viewBox="0 0 60 60">'+
 '<defs><linearGradient id="glass" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#fff" stop-opacity=".98"/><stop offset=".5" stop-color="#eff9ff" stop-opacity=".87"/><stop offset="1" stop-color="#c6e4f8" stop-opacity=".91"/></linearGradient></defs>'+
 '<circle cx="30" cy="32" r="'+(radius+4)+'" fill="#164877" opacity=".16"/>'+
 '<circle cx="30" cy="29" r="'+radius+'" fill="'+ring+'" stroke="#fff" stroke-width="2"/>'+
 '<circle cx="30" cy="29" r="'+(radius-4)+'" fill="url(#glass)" stroke="#fff" stroke-width="1.2"/>'+
 '<path d="M17 21 Q27 12 42 20" fill="none" stroke="#fff" stroke-width="2" opacity=".7"/>'+
 '</svg>';
 return {url:"data:image/svg+xml;charset=UTF-8,"+encodeURIComponent(svg),
  size:new google.maps.Size(60,60),scaledSize:new google.maps.Size(60,60),anchor:new google.maps.Point(30,29),
  labelOrigin:new google.maps.Point(30,29)};
}
function updateMarkers(list,chosen){
 if(!googleMap)return;
 const present=new Set();
 for(const i of list){
  if(i.id==null||i.ativo===false||!normPos(i))continue;
  const id=String(i.id),status=state(i),point={lat:+i.lat,lng:+i.lng};
  // Status offline apenas altera a cor: mantém a última coordenada válida.
  const previous=lastPositions.get(id);
  const finalPoint=status==="offline"&&previous?previous:point;
  if(status!=="offline"||!previous)lastPositions.set(id,finalPoint);
  present.add(id);
  const chosenNow=id===String(chosen);
  const existing=markers.get(id);
  if(existing){
   const p=existing.getPosition();
   if(Math.abs(p.lat()-finalPoint.lat)>0.0000001||Math.abs(p.lng()-finalPoint.lng)>0.0000001)existing.setPosition(finalPoint);
   if(existing.__status!==status||existing.__chosen!==chosenNow||existing.__initials!==initials(i.nome)){
    existing.setIcon(iconFor(status,chosenNow));
    existing.setLabel({text:initials(i.nome),color:"#111c2a",fontWeight:"800",fontSize:"12px"});
    existing.__status=status;existing.__chosen=chosenNow;existing.__initials=initials(i.nome);
   }
  }else{
   const marker=new google.maps.Marker({
    map:searchingAddress?null:googleMap,position:finalPoint,
    title:(i.nome||"Cooperado")+" · "+(status==="offline"?"Offline":status==="ocioso"?"Ocioso":"Online"),
    icon:iconFor(status,chosenNow),
    label:{text:initials(i.nome),color:"#111c2a",fontWeight:"800",fontSize:"12px"},
    optimized:false,zIndex:chosenNow?100:status==="online"?15:5
   });
   marker.__status=status;marker.__chosen=chosenNow;marker.__initials=initials(i.nome);
   marker.addListener("click",()=>window.COOPEX_MAP_ACTIONS?.selectCooperado?.(id));
   markers.set(id,marker);
  }
 }
 for(const [id,marker]of markers)if(!present.has(id)){marker.setMap(null);markers.delete(id);lastPositions.delete(id);}
 updateMode();
}
function updateMode(){
 if(!googleMap)return;
 for(const marker of markers.values()){
  const wanted=searchingAddress?null:googleMap;
  if(marker.getMap()!==wanted)marker.setMap(wanted);
 }
}
function onMapData(event){
 const detail=event.detail||{};
 if(Array.isArray(detail.items))lastItems=detail.items;
 selectedId=detail.selectedId;
 if(typeof detail.searchingAddress==="boolean")searchingAddress=detail.searchingAddress;
 updateMarkers(lastItems,selectedId);
}
window.addEventListener("coopex:map-data",onMapData);
window.addEventListener("coopex:map-mode",event=>{
 searchingAddress=!!event.detail?.searchingAddress;
 updateMode();
});
window.addEventListener("coopex:map-view",event=>{
 if(!googleMap||syncingFromGoogle)return;
 const d=event.detail||{};
 if(!Number.isFinite(d.lat)||!Number.isFinite(d.lng))return;
 const center=googleMap.getCenter();
 if(!center||Math.abs(center.lat()-d.lat)>0.00005||Math.abs(center.lng()-d.lng)>0.00005)googleMap.panTo({lat:d.lat,lng:d.lng});
 if(Number.isFinite(d.zoom)&&Math.abs((googleMap.getZoom()||0)-d.zoom)>0.4)googleMap.setZoom(Math.round(d.zoom));
});
window.COOPEX_GOOGLE_MAP_ONLOAD=()=>{
 try{
  reportGoogleStatus("Carregando a cartografia oficial do Google Maps…","loading");
  googleMap=new google.maps.Map(googleRoot,{
   center:{lat:-5.7945,lng:-35.211},zoom:12,
   mapTypeId:"roadmap",streetViewControl:true,fullscreenControl:false,
   mapTypeControl:true,zoomControl:true,scaleControl:true,
   rotateControl:false,gestureHandling:"greedy",
   clickableIcons:true
  });
  function finishGoogleLoad(){
   if(loadFinished||authFailed)return;
   const styled=!!googleRoot.querySelector(".gm-style");
   const size=googleRoot.getBoundingClientRect();
   if(!styled||size.width<100||size.height<100)return;
   loadFinished=true;tilesVisible=true;
   updateMarkers(lastItems,selectedId);
   document.body.classList.remove("cx-google-error");
   document.body.classList.add("cx-google-ready");
   const statusNode=document.getElementById("googleMapStatus");
   if(statusNode)statusNode.hidden=true;
   window.dispatchEvent(new CustomEvent("coopex:google-ready"));
  }
  // Os mapas vetoriais recentes do Google podem não emitir tilesloaded.
  // O evento idle é a confirmação de que o mapa terminou a primeira renderização.
  googleMap.addListener("idle",()=>{
   const pos=googleMap.getCenter();
   if(pos&&window.COOPEX_MAP_ACTIONS?.syncMapView){
    syncingFromGoogle=true;
    window.COOPEX_MAP_ACTIONS.syncMapView(pos.lat(),pos.lng(),googleMap.getZoom());
    Promise.resolve().then(()=>{syncingFromGoogle=false});
   }
   finishGoogleLoad();
  });
  googleMap.addListener("tilesloaded",finishGoogleLoad);
  // Em caso de falha de autorização, gm_authFailure mantém o mapa alternativo.
  setTimeout(()=>{
   if(!loadFinished&&!authFailed){
    document.body.classList.add("cx-google-error");
    reportGoogleStatus("Google Maps não terminou de iniciar. Abra o Console e procure um erro da API do Google (sem compartilhar a chave).");
   }
  },14000);
 }catch(e){document.body.classList.add("cx-google-error");reportGoogleStatus("Falha ao iniciar Google Maps: "+String(e.message||e).slice(0,170));console.warn("COOPEX Google Maps:",e);}
};
const script=document.createElement("script");
script.src="https://maps.googleapis.com/maps/api/js?key="+encodeURIComponent(key)+"&callback=COOPEX_GOOGLE_MAP_ONLOAD&loading=async&v=weekly&language=pt-BR&region=BR";
script.async=true;
script.onerror=()=>{document.body.classList.add("cx-google-error");reportGoogleStatus("Não foi possível baixar a biblioteca do Google Maps. Confira a chave, a rede e a API ativada.");console.warn("Google Maps não carregou. Mapa alternativo mantido.");};
document.head.appendChild(script);
})();