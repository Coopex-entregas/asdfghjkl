import * as ml from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
(()=>{
"use strict";
const $=id=>document.getElementById(id),esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
// Com a chave do Google configurada, MapLibre serve apenas ao legado interno
// e não faz download de tiles/fontes de outro provedor.
const DAY_STYLE=window.COOPEX_GOOGLE_KEY?{version:8,sources:{},layers:[]}: "https://tiles.openfreemap.org/styles/liberty";
const night=false;
document.body.classList.remove("is-night");
const map=new ml.Map({container:"map",style:DAY_STYLE,center:[-35.211,-5.7945],zoom:12,pitch:0,bearing:0,antialias:true,maxPitch:0,attributionControl:true,dragRotate:false,touchPitch:false});
map.addControl(new ml.NavigationControl({showCompass:false,showZoom:true,visualizePitch:false}),"top-right");
window.COOPEX_FREE_GLOBE=map; // Instância existente, sem ativação de API Google paga.
let selectedId=null,following=false,filter="todos",items=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[],searchText="",trafficEvents=[],selectedAddress=null,viewPoint=null,fetching=false,lastFit=false,satellite=false,weatherLevel=0;
let dynamicMarkers=new Map(),stops=[],stopsMarkers=[],placePin=null;
const lastConfirmedGps=new Map();
let mapReady=false;
// Guardar o último ponto conhecido desde os dados iniciais do servidor.
for(const i of items){
 if(i&&i.id!=null&&i.lat!=null&&i.lng!=null&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng)){
  lastConfirmedGps.set(String(i.id),{lat:+i.lat,lng:+i.lng,source:i.gps_ao_vivo?"live":"last-known"});
 }
}

function keepLastPosition(arr){
  // A coordenada offline fica imutável até chegar um novo GPS real.
  // O servidor pode alternar entre memória e localização histórica; isso NÃO
  // pode deslocar um alfinete offline durante zoom, filtro ou atualização.
  for(const v of arr){
    const id=String(v.id);
    if(v.gps_ao_vivo===true && hasCoord(v)){
      lastConfirmedGps.set(id,{lat:Number(v.lat),lng:Number(v.lng),source:"live"});
      continue;
    }
    const stored=lastConfirmedGps.get(id);
    if(stored){
      v.lat=stored.lat;v.lng=stored.lng;
    }else if(hasCoord(v)){
      lastConfirmedGps.set(id,{lat:Number(v.lat),lng:Number(v.lng),source:"last-known"});
    }
  }
  return arr;
}
const hasCoord=i=>i&&i.lat!=null&&i.lng!=null&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng)&&(+i.lat<0)&&(+i.lng<0);
const MAP_SIGNAL_TIMEOUT_MS=180000; // 3 min sem GPS: desconectado. Preserva a última posição.
const MAP_SEARCHING_AFTER_MS=30000; // 30 s sem GPS: procurando conexão, sem ocultar o marcador.
for(const i of items){
 const age=Number(i.sinal_idade_segundos);
 if(i.online&&i.sinal_idade_segundos!=null&&Number.isFinite(age)&&age>=0)i.__lastSignalAt=Date.now()-age*1000;
}
const state=i=>{
 if(!i.online)return "offline";
 if(Number.isFinite(i.__lastSignalAt)){
  const age=Date.now()-i.__lastSignalAt;
  if(age>=MAP_SIGNAL_TIMEOUT_MS)return "offline";
  if(age>=MAP_SEARCHING_AFTER_MS)return "ocioso";
 }
 return i.status==="ocioso"?"ocioso":"online";
};
const stateNames={online:"Online",ocioso:"Procurando conexão",offline:"Offline"};
const colors={online:"#0cdb7b",ocioso:"#f5a12c",offline:"#ff4758"};
const initials=n=>String(n||"CO").trim().split(/\s+/).filter(Boolean).slice(0,2).map(v=>v[0].toLocaleUpperCase("pt-BR")).join("");
const norm=v=>String(v||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
const itemById=id=>items.find(i=>String(i.id)===String(id));
const inFilter=i=>(filter==="todos"||state(i)===filter)&&(!searchText||norm(i.nome).includes(norm(searchText)));
function buildings(){
 if(!map.isStyleLoaded())return;
 const style=map.getStyle();if(!style)return;
 if(!map.getLayer("coopex-buildings-3d")){
   const entry=Object.entries(style.sources||{}).find(([key,src])=>src.type==="vector");
   if(entry)try{
     const firstSymbol=(style.layers||[]).find(l=>l.type==="symbol");
     map.addLayer({id:"coopex-buildings-3d",type:"fill-extrusion",source:entry[0],"source-layer":"building",
       minzoom:14,filter:["!=",["get","hide_3d"],true],
       paint:{"fill-extrusion-color":["interpolate",["linear"],["zoom"],14,night?"#39658b":"#91b8d8",17,night?"#609bc3":"#e0eaf9"],
        "fill-extrusion-height":["interpolate",["linear"],["zoom"],14,0,14.5,["coalesce",["to-number",["get","render_height"]],["to-number",["get","height"]],12]],
        "fill-extrusion-base":["coalesce",["to-number",["get","render_min_height"]],["to-number",["get","min_height"]],0],
        "fill-extrusion-opacity":.86}},firstSymbol?.id);
   }catch(e){console.warn("COOPEX 3D:",e.message)}
 }
 if(trafficEvents.length)drawTraffic();
}
function setStyle(){document.body.classList.remove("is-night");}

/* Paleta cartográfica COOPEX: água azul e nomes de ruas nítidos.
   Usa somente atributos públicos do estilo vetorial, sem tiles do Google. */
function colorizeCoopexMap(){
 const style=map.getStyle();
 if(!style?.layers)return;
 for(const layer of style.layers){
  const id=String(layer.id||"").toLowerCase(), source=String(layer["source-layer"]||"").toLowerCase();
  const isWater=/water|ocean|sea|lake|river|stream|canal|bay/.test(id+" "+source);
  const isWaterway=/waterway|river|stream|canal/.test(id+" "+source);
  const isRoad=/road|street|highway|motorway|primary|secondary|tertiary|transportation/.test(id+" "+source);
  const isLanduse=/park|grass|forest|garden|wood|nature|landcover/.test(id+" "+source);
  try{
   if(layer.type==="background"){
    map.setPaintProperty(layer.id,"background-color","#f5f5f2");
   }else if(layer.type==="fill" && isWater){
    map.setPaintProperty(layer.id,"fill-color","#79c9f4");
    map.setPaintProperty(layer.id,"fill-opacity",1);
   }else if(layer.type==="line" && isWaterway){
    map.setPaintProperty(layer.id,"line-color","#58b6ed");
    map.setPaintProperty(layer.id,"line-opacity",1);
   }else if(layer.type==="fill" && isLanduse){
    map.setPaintProperty(layer.id,"fill-color","#cfeacb");
   }else if(layer.type==="line" && isRoad){
    // Estilos de camada individual (túneis, vias principais e contornos).
    if(/motorway|trunk|primary|major/.test(id)){
     if(!/casing|outline|bridge/.test(id))map.setPaintProperty(layer.id,"line-color","#f4bd70");
    }else if(/secondary|tertiary/.test(id)){
     if(!/casing|outline|bridge/.test(id))map.setPaintProperty(layer.id,"line-color","#fff4cf");
    }
   }else if(layer.type==="symbol"){
    const layout=layer.layout||{};
    if(layout["text-field"]==null)continue;
    const roadLabel=isRoad||/road_label|street_label|street-name/.test(id);
    const waterLabel=isWater;
    map.setPaintProperty(layer.id,"text-color",waterLabel?"#075b9d":roadLabel?"#25466a":"#365169");
    map.setPaintProperty(layer.id,"text-halo-color","#ffffff");
    map.setPaintProperty(layer.id,"text-halo-width",roadLabel?2.2:1.7);
    map.setPaintProperty(layer.id,"text-halo-blur",0.2);
    if(roadLabel){
     map.setLayoutProperty(layer.id,"text-optional",true);
     // Preserve o tamanho que o estilo escolhe para cada zoom.
    }
   }
  }catch(error){ /* Atributo não compatível nesta camada: preserva o padrão. */ }
 }
}

map.on("style.load",()=>{
 mapReady=true;
 // O planeta aparece quando o usuário afasta o mapa; em zoom de rua, MapLibre volta suavemente à projeção local.
 try{map.setProjection({type:"globe"});map.setSky({"atmosphere-blend":["interpolate",["linear"],["zoom"],0,1,5,1,7,0]});}catch(e){console.warn("Globo não suportado neste navegador:",e.message)}
 colorizeCoopexMap();if(satellite)installSatellite();installPins();applySearchVisibility();
});
map.on("click",e=>{viewPoint={lat:e.lngLat.lat,lng:e.lngLat.lng,label:"Ponto no mapa"};});
function pin(i,count){
 const el=document.createElement("button");el.type="button";el.className="cx-map-marker";el.title=i.nome||"Cooperado";el.setAttribute("aria-label",(i.nome||"Cooperado")+" "+stateNames[state(i)]);
 el.innerHTML='<span class="cx-pin-orb" style="--pin:'+colors[state(i)]+'">'+esc(initials(i.nome))+'</span>'+(count>1?'<span class="cx-pin-counter">'+count+'</span>':"");
 el.classList.toggle("cx-chosen",String(i.id)===String(selectedId));return el;
}
function clusters(){
 const list=items.filter(inFilter).filter(hasCoord),groups=[];
 for(const i of list){let g=groups.find(g=>Math.abs(+g[0].lat-(+i.lat))<.00009&&Math.abs(+g[0].lng-(+i.lng))<.00009);if(g)g.push(i);else groups.push([i]);}
 return groups;
}
function counts(){for(const k of ["online","ocioso","offline"])$(k+"Count").textContent=items.filter(x=>state(x)===k).length;}
function updateSidebarList(){
 const host=$("sideCourierList");if(!host)return;
 const visible=items.filter(inFilter);
 $("sideCount").textContent=String(visible.length);
 const fragment=document.createDocumentFragment();
 for(const i of visible.slice(0,125)){
  const btn=document.createElement("button");btn.type="button";btn.className="coopex-side-person";
  const color=colors[state(i)];
  btn.innerHTML='<span class="coopex-person-dot" style="background:'+color+'"></span><span class="coopex-side-person-name">'+esc(i.nome)+'</span><small>'+stateNames[state(i)]+'</small>';
  btn.onclick=()=>{if(searchingAddress)setAddressMode(false);selectCooperado(i.id);};
  fragment.appendChild(btn);
 }
 if(!visible.length){const label=document.createElement("p");label.className="coopex-side-empty";label.textContent="Nenhum cooperado neste filtro.";fragment.appendChild(label)}
 host.replaceChildren(fragment);
}


/* Pontos nativos WebGL ancorados a longitude/latitude: não usam CSS transform,
   não são reagrupados ao aplicar filtros e nunca mudam por causa do zoom. */
const PINS_SOURCE="coopex-cooperados-gps";
function pinsGeoJson(){
 return {type:"FeatureCollection",features:(searchingAddress?[]:items.filter(inFilter).filter(hasCoord)).map(i=>({
  type:"Feature",id:Number(i.id),geometry:{type:"Point",coordinates:[+i.lng,+i.lat]},
  properties:{id:String(i.id),name:i.nome||"Cooperado",initials:initials(i.nome),status:state(i),selected:String(i.id)===String(selectedId)?1:0}
 }))};
}
function installPins(){
 if(!map.isStyleLoaded())return;
 const data=pinsGeoJson();
 if(!map.getSource(PINS_SOURCE))map.addSource(PINS_SOURCE,{type:"geojson",data});
 else map.getSource(PINS_SOURCE).setData(data);
 if(!map.getLayer("coopex-gps-halo"))map.addLayer({
  id:"coopex-gps-halo",type:"circle",source:PINS_SOURCE,
  filter:["==",["get","selected"],1],
  paint:{"circle-radius":27,"circle-color":"#1485f9","circle-opacity":0.22,"circle-blur":0.45}
 });
 if(!map.getLayer("coopex-gps-pins"))map.addLayer({
  id:"coopex-gps-pins",type:"circle",source:PINS_SOURCE,
  paint:{
   "circle-radius":["interpolate",["linear"],["zoom"],8,14,12,19,17,22],
   "circle-color":["match",["get","status"],"online","#08c977","ocioso","#f4a230","offline","#ef485e","#1977d5"],
   "circle-stroke-color":"#ffffff","circle-stroke-width":2.5,
   "circle-opacity":0.98,"circle-blur":0
  }
 });
 // Vidro translúcido dentro do aro de status, desenhado no mapa (sem marcador HTML).
 if(!map.getLayer("coopex-gps-glass"))map.addLayer({
  id:"coopex-gps-glass",type:"circle",source:PINS_SOURCE,
  paint:{
   "circle-radius":["interpolate",["linear"],["zoom"],8,10,12,14.4,17,17.1],
   "circle-color":"#f6fcff",
   "circle-opacity":0.9,
   "circle-stroke-width":1.2,
   "circle-stroke-color":"#ffffff"
  }
 });
 if(!map.getLayer("coopex-gps-initials"))map.addLayer({
  id:"coopex-gps-initials",type:"symbol",source:PINS_SOURCE,
  layout:{"text-field":["get","initials"],"text-size":["interpolate",["linear"],["zoom"],8,11,12,12.5,17,14],
   "text-allow-overlap":true,"text-ignore-placement":true ,"text-anchor":"center"},
  paint:{"text-color":"#0b1928","text-halo-color":"#ffffff","text-halo-width":0.25}
 });
}
map.on("click","coopex-gps-pins",e=>{
 const feature=e.features&&e.features[0];if(!feature)return;
 e.originalEvent?.stopPropagation();
 selectCooperado(feature.properties.id);
});
map.on("mouseenter","coopex-gps-pins",()=>{map.getCanvas().style.cursor="pointer"});
map.on("mouseleave","coopex-gps-pins",()=>{map.getCanvas().style.cursor=""});
function render(){
 counts();updateSidebarList();
 if(mapReady&&map.isStyleLoaded()){installPins();applySearchVisibility();}
 const current=itemById(selectedId);
 if(current){showDetails(current);
  if(following&&hasCoord(current))map.easeTo({center:[+current.lng,+current.lat],duration:350});
 }
 window.dispatchEvent(new CustomEvent("coopex:map-data",{detail:{items,selectedId,filter,searchingAddress:typeof searchingAddress!=="undefined"&&searchingAddress}}));
}
function kmDistance(a,b){const dx=(a.lat-b.lat)*111,dy=(a.lng-b.lng)*111*Math.cos(a.lat*Math.PI/180);return Math.hypot(dx,dy);}
function toIncidentPoint(f){let c=f?.geometry?.coordinates;if(!Array.isArray(c))return null;if(f.geometry.type==="LineString")c=c[Math.floor(c.length/2)];else if(f.geometry.type==="MultiLineString")c=c[0]?.[Math.floor(c[0].length/2)];return c&&Number.isFinite(+c[0])?{lng:+c[0],lat:+c[1]}:null;}
function showDetails(i){
 $("details").classList.add("visible");
 $("personName").textContent=i.nome||"Cooperado";
 const st=state(i),avatar=$("personAvatar");avatar.style.background=colors[st];avatar.textContent=initials(i.nome);
 $("personState").textContent=stateNames[st];$("personState").style.background=colors[st]+"4b";
 $("personArea").textContent=i.endereco||"Natal/RN";$("personSeen").textContent=i.ultima_atualizacao||"Sem GPS";$("personSpeed").textContent=i.speed_kmh==null?"—":Number(i.speed_kmh).toLocaleString("pt-BR",{maximumFractionDigits:1})+" km/h";
 $("personJobs").textContent=Number(i.entregas_abertas||0);$("follow").disabled=!hasCoord(i);$("follow").textContent=following?"✓ Seguindo":"◎ Seguir";$("stop").disabled=!following;
 const count=trafficEvents.filter(f=>{let p=toIncidentPoint(f);return p&&hasCoord(i)&&kmDistance({lat:+i.lat,lng:+i.lng},p)<1.5}).length;
 const near=$("nearby");near.classList.toggle("visible",count>0);near.textContent=count?("⚠ "+count+" ocorrência(s) de trânsito próximas à localização GPS. Não significa que estão na rota."):"";
 const group=clusters().find(g=>g.some(x=>String(x.id)===String(i.id)))||[],host=$("stack");host.replaceChildren();
 if(group.length>1){const title=document.createElement("small");title.textContent=group.length+" cooperados neste ponto:";host.append(title);for(const p of group){const b=document.createElement("button");b.textContent=initials(p.nome);b.title=p.nome;b.onclick=()=>selectCooperado(p.id);host.append(b);}}
}
function selectCooperado(id){
 const next=String(id);
 const i=itemById(next);if(!i)return;
 if(selectedId===next&&$("details").classList.contains("visible")){
  selectedId=null;following=false;$("details").classList.remove("visible");render();return;
 }
 selectedId=next;following=false;
 if(cameraPanelOpen)renderCameraPanel();
 render();
 // Somente um mapa visível: movimenta Google quando disponível, mantendo o GPS original.
 if(hasCoord(i)){
  if(window.COOPEX_GOOGLE_NATIVE){window.COOPEX_GOOGLE_NATIVE.panTo({lat:+i.lat,lng:+i.lng});window.COOPEX_GOOGLE_NATIVE.setZoom(17)}
  else map.flyTo({center:[+i.lng,+i.lat],zoom:Math.max(map.getZoom(),17),pitch:0,bearing:0,duration:650});
 }
}
async function updateGps(){
 if(fetching||document.hidden)return;fetching=true;
 try{
  const response=await fetch("/mapa_motoboys?format=json",{cache:"no-store",credentials:"same-origin",headers:{Accept:"application/json"}});
  if(!response.ok)throw Error("GPS HTTP "+response.status);
  const data=await response.json();if(!Array.isArray(data))throw Error("Formato GPS inválido");
  items=keepLastPosition(data);
   const clock=Date.now();
   for(const coop of items){
    const age=Number(coop.sinal_idade_segundos);
    coop.__lastSignalAt=coop.online&&Number.isFinite(age)&&age>=0?clock-age*1000:null;
   }
   render();$("gpsStatus").textContent="GPS ativo";
  if(!lastFit&&mapReady){const good=clusters().map(g=>g[0]);if(good.length){let b=new ml.LngLatBounds();good.forEach(i=>b.extend([+i.lng,+i.lat]));map.fitBounds(b,{padding:{top:160,right:365,bottom:180,left:320},maxZoom:13,duration:0});lastFit=true;}}
 }catch(e){$("gpsStatus").textContent="GPS temporariamente indisponível";console.warn("GPS:",e.message)}
 finally{fetching=false;}
}
$("search").addEventListener("input",e=>{
 searchText=e.target.value.trim();const results=$("results");results.replaceChildren();
 if(searchText){const matching=items.filter(i=>norm(i.nome).includes(norm(searchText))).slice(0,10);for(const i of matching){let b=document.createElement("button");b.className="result";b.innerHTML='<span style="color:'+colors[state(i)]+'">●</span><span><strong>'+esc(i.nome)+'</strong><small>'+stateNames[state(i)]+'</small></span>';b.onclick=()=>{searchText="";$("search").value="";results.classList.remove("open");selectCooperado(i.id)};results.append(b)}results.classList.toggle("open",matching.length>0)}else results.classList.remove("open");
 render();
});
document.querySelectorAll("[data-filter]").forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll("[data-filter]").forEach(v=>v.classList.toggle("active",v===b));render();});
$("detailsClose").onclick=()=>{selectedId=null;following=false;$("details").classList.remove("visible");render();};
$("follow").onclick=()=>{const p=itemById(selectedId);if(!p||!hasCoord(p))return;following=true;render();};
$("stop").onclick=()=>{following=false;render();};
$("toolbarToggle").onclick=()=>{$("toolbar").classList.toggle("open");$("toolbarToggle").setAttribute("aria-expanded",String($("toolbar").classList.contains("open")))};
$("fit").onclick=()=>{const i=clusters().map(x=>x[0]);if(i.length){let b=new ml.LngLatBounds();i.forEach(x=>b.extend([+x.lng,+x.lat]));map.fitBounds(b,{padding:100,maxZoom:15})}$("toolbar").classList.remove("open")};
$("refresh").onclick=()=>{updateGps();$("toolbar").classList.remove("open")};
$("locate").onclick=()=>{navigator.geolocation?.getCurrentPosition(p=>map.flyTo({center:[p.coords.longitude,p.coords.latitude],zoom:16,pitch:0,bearing:0}),()=>{}, {enableHighAccuracy:true,timeout:10000});$("toolbar").classList.remove("open")};
function installSatellite(){if(!map.getSource("cx-sat"))map.addSource("cx-sat",{type:"raster",tiles:["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],tileSize:256,attribution:"Tiles © Esri"});if(!map.getLayer("cx-sat-layer"))map.addLayer({id:"cx-sat-layer",type:"raster",source:"cx-sat"});}


const dest=()=>selectedAddress||(hasCoord(itemById(selectedId))?{lat:+itemById(selectedId).lat,lng:+itemById(selectedId).lng,label:itemById(selectedId).nome}:null);
function stopRender(){for(const m of stopsMarkers)m.remove();stopsMarkers=[];const container=$("stopsList");container.replaceChildren();$("stopsCount").textContent=stops.length;
 stops.forEach((p,index)=>{const line=document.createElement("div");line.className="stop-row";const label=document.createElement("span");label.textContent=(index+1)+". "+p.label;const remove=document.createElement("button");remove.textContent="×";remove.title="Remover parada";remove.onclick=()=>{stops.splice(index,1);stopRender()};line.append(label,remove);container.append(line);const marker=new ml.Marker({color:"#ffb13c",pitchAlignment:"viewport"}).setLngLat([p.lng,p.lat]).addTo(map);stopsMarkers.push(marker);});
}
function openRoute(){const destination=dest()||stops[stops.length-1];if(!destination){$("addressMessage").textContent="Selecione um cooperado ou um endereço.";return}const middle=stops.filter(x=>x!==destination).slice(0,3);let url="https://www.google.com/maps/dir/?api=1&travelmode=driving&destination="+encodeURIComponent(destination.lat+","+destination.lng);if(middle.length)url+="&waypoints="+encodeURIComponent(middle.map(p=>p.lat+","+p.lng).join("|"));window.open(url,"_blank","noopener,noreferrer")}
function street(){const p=viewPoint||dest();if(!p){$("addressMessage").textContent="Selecione um endereço ou ponto do mapa.";return}window.open("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint="+encodeURIComponent(p.lat+","+p.lng),"_blank","noopener,noreferrer");}

let searchingAddress=false,oldMapView=null;
function applySearchVisibility(){
 for(const id of ["coopex-gps-halo","coopex-gps-pins","coopex-gps-glass","coopex-gps-initials"]){
  if(map.getLayer(id))map.setLayoutProperty(id,"visibility","visible");
 }
 document.body.classList.toggle("cx-address-mode",searchingAddress);
 window.dispatchEvent(new CustomEvent("coopex:map-mode",{detail:{searchingAddress}}));
 if(searchingAddress)document.body.classList.remove("cx-side-collapsed");
}
function setAddressMode(enabled){
 if(enabled===searchingAddress){if(enabled&&!$("addressPanel").classList.contains("collapsed"))$("address").focus();return;}
 searchingAddress=enabled;
 if(enabled){
  const center=map.getCenter();
  oldMapView={center:[center.lng,center.lat],zoom:map.getZoom()};
  $("addressPanel").hidden=false;
  $("addressPanel").classList.remove("collapsed");
  $("addressCollapse").textContent="‹";
  $("addressCollapse").setAttribute("aria-label","Recolher pesquisa");
  following=false;
  $("details").classList.remove("visible");
  $("search").value="";searchText="";
  $("results").classList.remove("open");
  $("address").focus();
 }else{
  $("addressPanel").hidden=true;
  if(placePin){placePin.remove();placePin=null;}
  selectedAddress=null;viewPoint=null;
  $("addressMessage").textContent="";
  $("address").value="";
  if(oldMapView)map.easeTo({...oldMapView,duration:300});
  oldMapView=null;
 }
 if(mapReady&&map.isStyleLoaded())installPins();
 applySearchVisibility();
}
$("sideToggle").onclick=()=>document.body.classList.add("cx-side-collapsed");
$("sideExpand").onclick=()=>setAddressMode(true);
$("sideSearchAddress").onclick=()=>setAddressMode(true);
$("addressToggle").onclick=()=>setAddressMode(true);
$("addressClose").onclick=()=>{const q=$("address");if(q)q.value="";};
window.addEventListener("coopex:search-reset",()=>{
 if(placePin){placePin.remove();placePin=null}
 selectedAddress=null;
 viewPoint=null;
 stops=[];stopsMarkers.forEach(marker=>marker.remove());stopsMarkers=[];
});
$("addressCollapse").onclick=()=>{
 // Recolher significa voltar ao mapa COOPEX, e não continuar no modo de busca.
 setAddressMode(false);
};

$("addressGo").onclick=async()=>{
 const q=$("address").value.trim();if(q.length<3)return;
 try{const r=await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=3&countrycodes=br&q="+encodeURIComponent(q+", Natal, Rio Grande do Norte"));if(!r.ok)throw Error();const list=await r.json();if(!list.length){$("addressMessage").textContent="Endereço não encontrado";return;}
 const first=list[0];selectedAddress={lat:+first.lat,lng:+first.lon,label:first.display_name};viewPoint=selectedAddress;map.flyTo({center:[selectedAddress.lng,selectedAddress.lat],zoom:16,pitch:0,bearing:0});$("addressMessage").textContent="Endereço encontrado. Pode adicionar como parada.";
 if(placePin)placePin.remove();placePin=new ml.Marker({color:"#276bff",pitchAlignment:"viewport"}).setLngLat([selectedAddress.lng,selectedAddress.lat]).addTo(map);
 }catch(e){$("addressMessage").textContent="Busca indisponível no momento";}
};
/* A busca Google controla Enter. */
$("addStop").onclick=()=>{const p=selectedAddress||viewPoint||dest();if(!p){$("addressMessage").textContent="Pesquise ou marque um ponto no mapa.";return}if(stops.length>=3){$("addressMessage").textContent="Até 3 paradas intermediárias";return}stops.push({...p});stopRender();$("addressMessage").textContent="Parada adicionada."};
$("stopsClear").onclick=()=>{stops=[];stopRender();};
$("routeOpen").onclick=openRoute;$("openNav").onclick=openRoute;$("streetView").onclick=street;$("streetTool").onclick=street;

function showSttuStatus(){
 const title=$("trafficStatus"),message=$("trafficText");
 title.textContent="Avisos oficiais STTU";
 title.style.color="#175aa3";
 message.textContent="Interdições, obras e alterações de circulação. Ainda não há feed público validado para congestionamentos automáticos.";
}
function moonPhaseNow(){
 // Ciclo sinódico aproximado; referência astronômica da lua nova de 06/01/2000 às 18:14 UTC.
 const synodic=29.530588853*86400000;
 const fraction=((Date.now()-Date.UTC(2000,0,6,18,14))%synodic+synodic)%synodic/synodic;
 const index=Math.floor(fraction*8+.5)%8;
 const icons=["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"];
 const labels=["Lua nova","Lua crescente fina","Quarto crescente","Lua gibosa crescente","Lua cheia","Lua gibosa minguante","Quarto minguante","Lua minguante fina"];
 return {icon:icons[index],label:labels[index]};
}
function phase(){document.body.classList.remove("is-night");}
const rainFx=$("weatherFx");function rain(level){weatherLevel=level;document.body.classList.toggle("is-rain",level>0);rainFx.replaceChildren();if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;const count=0;/* Sem efeito de chuva em toda a cidade: exige chuva geográfica confiável. */const frag=document.createDocumentFragment();for(let i=0;i<count;i++){const d=document.createElement("i");d.className="drop";d.style.left=(Math.random()*100)+"%";d.style.animationDelay=(-Math.random()*3)+"s";d.style.animationDuration=(.7+Math.random()*1.4)+"s";frag.append(d)}rainFx.append(frag)}
async function weather(){
 try{const r=await fetch("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=-5.7945&lon=-35.2110",{cache:"default"});if(!r.ok)throw Error("Weather HTTP");const data=await r.json(),t=data.properties.timeseries[0].data,details=t.instant.details,next=t.next_1_hours||t.next_6_hours||{},mm=Number(next.details?.precipitation_amount||0),code=next.summary?.symbol_code||"";const level=mm>=6||/heavyrain|thunder/.test(code)?3:mm>=2||/rain|showers/.test(code)?2:mm>0||/drizzle/.test(code)?1:0;
 $("temperature").textContent=Math.round(details.air_temperature)+"°C";const ico=$("weatherIcon");if(ico){const hour=Number(new Intl.DateTimeFormat("pt-BR",{hour:"numeric",hour12:false,timeZone:"America/Fortaleza"}).format(new Date()));const lunar=moonPhaseNow();const hot=Number(details.air_temperature)>=32;const isNight=hour>=18||hour<6;
 ico.textContent=level>0?"🌧️":isNight?lunar.icon:"☀";
 ico.title=level>0?"Previsão de chuva":isNight?lunar.label:(hot?"Sol forte e calor":"Sol");
 ico.classList.toggle("cx-sun-hot",!isNight&&level===0&&hot);
 ico.classList.toggle("cx-sun-warm",!isNight&&level===0&&!hot);
 ico.classList.toggle("cx-moon",isNight&&level===0);}$("conditions").textContent=["Sem chuva prevista","Previsão de chuva fraca","Previsão de chuva","Previsão de chuva forte"][level];$("humidity").textContent=details.relative_humidity+"%";$("wind").textContent=Math.round(details.wind_speed||0)+" m/s";$("weatherAlert").textContent=["Sem alerta de chuva na próxima hora.","Possibilidade de pista molhada.","⚠ Chuva prevista: atenção ao deslocamento.","⚠ Previsão de chuva intensa e possíveis alagamentos."][level];rain(level);
 }catch(e){$("conditions").textContent="Previsão indisponível";$("weatherAlert").textContent="Sem informação meteorológica atualizada";rain(0)}
}

/* Fontes públicas de vídeo: apenas URLs verificadas, jamais câmeras privadas.
   Coordenadas só entram quando geocodificadas; streams sem ponto preciso são listados como gerais. */
const publicCameras=[
 {id:"roberto",name:"Av. Eng. Roberto Freire",region:"Ponta Negra / Capim Macio",
  url:"https://seetheview.com/cam/2717/natal-traffic-live-av-roberto-freire",
  geocode:"Avenida Engenheiro Roberto Freire, Natal, Rio Grande do Norte"},
 {id:"geral",name:"Trânsito 360º — Natal",region:"Várias vias de Natal",
  url:"https://seetheview.com/cam/1589/natal-traffic-live-multiple-camera-views",general:true},
 {id:"sttu",name:"STTU — Câmeras de Trânsito",region:"Fonte municipal · disponibilidade a conferir",
  url:"https://natal.rn.gov.br/sttu",general:true}
];
let cameraMode="map",cameraLocationReady=false,cameraPanelOpen=false;
function cameraReference(){
 const selected=itemById(selectedId);
 if(cameraMode==="coop"&&selected&&hasCoord(selected))return {lat:+selected.lat,lng:+selected.lng,description:"próximas ao cooperado "+selected.nome};
 const p=map.getCenter();return {lat:p.lat,lng:p.lng,description:"próximas ao centro do mapa"};
}
function cameraDistanceKm(p,q){let deltaLat=(p.lat-q.lat)*111.2,deltaLng=(p.lng-q.lng)*111.2*Math.cos(p.lat*Math.PI/180);return Math.sqrt(deltaLat**2+deltaLng**2);}

let cameraZoom=1,activeCamera=null;
function renderCameraViewer(){
 const host=$("cameraViewport");
 if(!activeCamera)return;
 const frame=$("cameraFrame");
 frame.style.transform="scale("+cameraZoom+")";
 $("cameraZoomLevel").textContent=Math.round(cameraZoom*100)+"%";
 $("cameraTitle").textContent=activeCamera.name;
 $("cameraOriginal").href=activeCamera.url;
}
function showCameraViewer(cam){
 activeCamera=cam;cameraZoom=1;
 const frame=$("cameraFrame");
 frame.src=cam.url;
 $("cameraViewer").hidden=false;
 renderCameraViewer();
}
$("cameraZoomIn").onclick=()=>{cameraZoom=Math.min(3,Math.round((cameraZoom+.25)*100)/100);renderCameraViewer();};
$("cameraZoomOut").onclick=()=>{cameraZoom=Math.max(1,Math.round((cameraZoom-.25)*100)/100);renderCameraViewer();};
$("cameraZoomReset").onclick=()=>{cameraZoom=1;renderCameraViewer();};
$("cameraViewerClose").onclick=()=>{$("cameraViewer").hidden=true;$("cameraFrame").src="about:blank";activeCamera=null;};

function renderCameraPanel(){
 if(!cameraPanelOpen)return;
 const ref=cameraReference(),host=$("cameraList");host.replaceChildren();
 $("cameraContext").textContent="Buscando fontes "+ref.description+". Atualiza ao movimentar o mapa.";
 const local=publicCameras.filter(c=>c.coords&&cameraDistanceKm(ref,c.coords)<=12).sort((a,b)=>cameraDistanceKm(ref,a.coords)-cameraDistanceKm(ref,b.coords));
 const general=publicCameras.filter(c=>c.general);
 const rows=local.concat(general);
 if(!local.length){const info=document.createElement("p");info.className="cx-camera-empty";info.textContent="Não há transmissão com ponto de câmera verificado nesta área. Veja abaixo fontes gerais e avenidas com cobertura aproximada.";host.append(info);}
 for(const c of rows){const a=document.createElement("button");a.type="button";a.className="cx-camera-row";a.onclick=()=>showCameraViewer(c);a.innerHTML='<span class="cx-camera-symbol">▣</span><span><strong>'+esc(c.name)+'</strong><small>'+esc(c.region)+(c.coords?" · via próxima à região do mapa (ponto exato não informado)":" · localização não individualizada")+'</small></span><span aria-hidden="true">↗</span>';host.append(a);}
 $("camerasCoop").disabled=!(itemById(selectedId)&&hasCoord(itemById(selectedId)));
}
async function resolveCameraCoordinates(){
 if(cameraLocationReady)return;cameraLocationReady=true;
 const camera=publicCameras.find(c=>c.id==="roberto");
 try{
 const url="https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=br&q="+encodeURIComponent(camera.geocode);
 const response=await fetch(url,{headers:{Accept:"application/json"}});if(!response.ok)return;
 const rows=await response.json(),first=rows?.[0];
 if(first&&Number.isFinite(+first.lat)&&Number.isFinite(+first.lon)){
   const coords={lat:+first.lat,lng:+first.lon};
   if(coords.lat< -5.6&&coords.lat> -6.05&&coords.lng< -35.05&&coords.lng> -35.45){camera.coords=coords;renderCameraPanel();}
 }
 }catch(e){}
}
$("camerasTool").onclick=()=>{
 cameraPanelOpen=!cameraPanelOpen;$("cameraPanel").hidden=!cameraPanelOpen;
 $("toolbar").classList.remove("open");
 if(cameraPanelOpen){resolveCameraCoordinates();renderCameraPanel();}
};
$("camerasClose").onclick=()=>{cameraPanelOpen=false;$("cameraPanel").hidden=true;};
$("camerasHere").onclick=()=>{cameraMode="map";$("camerasHere").classList.add("active");$("camerasCoop").classList.remove("active");renderCameraPanel();};
$("camerasCoop").onclick=()=>{if(!itemById(selectedId)||!hasCoord(itemById(selectedId)))return;cameraMode="coop";$("camerasCoop").classList.add("active");$("camerasHere").classList.remove("active");renderCameraPanel();};
map.on("moveend",()=>{if(cameraMode==="map")renderCameraPanel();});
function updateSpaceMode(){
 document.body.classList.toggle("cx-globe-space",map.getZoom()<3.25);
}
map.on("zoom",updateSpaceMode);
map.on("load",updateSpaceMode);

function gpsSocket(){
 const connect=window.io;
 if(typeof connect!=="function"){console.warn("Socket.IO não carregado. Mapa usa atualização de segurança.");return}
 try{
  const sock=connect({transports:["websocket","polling"],reconnection:true,reconnectionDelay:1000});
  function applyEvent(event){
   if(!event||event.id==null)return;
   const record=itemById(event.id);
   if(!record){updateGps();return}
   const oldLat=record.lat,oldLng=record.lng;
   Object.assign(record,event);
    if(event.online===false)record.__lastSignalAt=null;
    else if(event.online===true)record.__lastSignalAt=Date.now();
   if(event.velocidade_kmh!=null&&event.speed_kmh==null)record.speed_kmh=event.velocidade_kmh;
   if(event.lat==null)record.lat=oldLat;
   if(event.lng==null)record.lng=oldLng;
   if(hasCoord(record)&&event.lat!=null&&event.lng!=null)lastConfirmedGps.set(String(record.id),{lat:+record.lat,lng:+record.lng,source:"live"});
   render();
  }
  sock.on("posicao_motoboy_atualizada",applyEvent);
  sock.on("coopex_presenca_atualizada",applyEvent);
  sock.on("connect",()=>updateGps());
  sock.on("reconnect",()=>updateGps());
  window.addEventListener("pageshow",()=>updateGps());
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)updateGps()});
  window.COOPEX_MAP_SOCKET=sock;
 }catch(error){console.warn("Conexão ao vivo indisponível:",error)}
}
window.COOPEX_MAP_ACTIONS={
 selectCooperado:id=>selectCooperado(id),
 setAddressMode:enabled=>setAddressMode(enabled),
 syncMapView:(lat,lng,zoom)=>{if(Number.isFinite(lat)&&Number.isFinite(lng)&&Number.isFinite(zoom))map.jumpTo({center:[lng,lat],zoom,pitch:0,bearing:0})},
 refresh:()=>updateGps()
};
map.on("moveend",()=>{
 const c=map.getCenter();
 window.dispatchEvent(new CustomEvent("coopex:map-view",{detail:{lat:c.lat,lng:c.lng,zoom:map.getZoom()}}));
});
map.on("load",()=>{updateGps();});
render();setAddressMode(true);updateGps();stopRender();gpsSocket();
setInterval(()=>{if(!window.COOPEX_MAP_SOCKET?.connected)updateGps()},15000);
 setInterval(updateGps,60000);
  // Muda de verde para laranja e depois offline, sem novas consultas.
  let lastStatusSignature="";
  setInterval(()=>{
   if(document.hidden)return;
   const signature=items.map(x=>String(x.id)+":"+state(x)).join("|");
   if(signature===lastStatusSignature)return;
   lastStatusSignature=signature;
   for(const x of items){
    if(x.online&&Number.isFinite(x.__lastSignalAt)&&Date.now()-x.__lastSignalAt>=MAP_SIGNAL_TIMEOUT_MS){
     x.online=false;x.gps_ao_vivo=false;x.presenca_painel=false;x.status="offline";
    }
   }
   render();
  },5000);
 /* Clima mantido por coopex_weather_week.js (Open-Meteo, fonte única). */setInterval(phase,60*1000);
})();
