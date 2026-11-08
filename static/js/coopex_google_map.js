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
// Busca única; Google Maps permanece na tela com os alfinetes COOPEX.
const field=document.getElementById("address"),submit=document.getElementById("addressGo"),
 clear=document.getElementById("addressClose"),hits=document.getElementById("cxUnifiedResults"),
 message=document.getElementById("addressMessage"),routesHost=document.getElementById("cxRoutes"),
 stopHost=document.getElementById("stopsList"),stopCounter=document.getElementById("stopsCount");
const origin=document.createElement("input");
origin.id="cxRouteOrigin";origin.placeholder="Origem (endereço ou cooperado)";
origin.setAttribute("aria-label","Origem da rota");origin.hidden=true;
document.querySelector(".cx-drawer-top")?.after(origin);
let geocoder,streetPanorama,placePoint=null,placeMarker=null,routeActive=false;
let routeAlternatives=[],routePolylines=[],routeStops=[],searchFocus="destination";
const queryNorm=t=>String(t||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
const historyKey="coopex-map-own-search-history-v1";
function readHistory(){try{return JSON.parse(localStorage.getItem(historyKey)||"[]").filter(s=>typeof s==="string").slice(0,20)}catch(e){return []}}
function remember(q){
 // Apenas texto digitado pelo operador, não resultados/descrições recebidos do Google.
 try{localStorage.setItem(historyKey,JSON.stringify([q,...readHistory().filter(x=>queryNorm(x)!==queryNorm(q))].slice(0,20)))}catch(e){}
}
function info(t){if(message)message.textContent=t;}
function hideRoutes(){routePolylines.forEach(p=>p.setMap(null));routePolylines=[];routeAlternatives=[];if(routesHost){routesHost.replaceChildren();routesHost.hidden=true}}
function destination(p,name){
 if(!p||!Number.isFinite(+p.lat)||!Number.isFinite(+p.lng)){info("Local sem GPS válido.");return}
 placePoint={lat:+p.lat,lng:+p.lng,label:name||"Destino"};
 if(!placeMarker)placeMarker=new google.maps.Marker({map:googleMap,position:placePoint,title:placePoint.label});
 else{placeMarker.setPosition(placePoint);placeMarker.setTitle(placePoint.label);placeMarker.setMap(googleMap)}
 googleMap.panTo(placePoint);googleMap.setZoom(Math.max(googleMap.getZoom()||0,15));
 info("Local selecionado: "+placePoint.label);
 hideRoutes();
}
function makeHit(primary,secondary,color,onClick){
 const b=document.createElement("button");b.type="button";b.className="cx-drawer-hit";
 const dot=document.createElement("span");dot.className="cx-hit-dot";dot.style.background=color;
 const body=document.createElement("span");const title=document.createElement("strong");title.textContent=primary;
 const sub=document.createElement("small");sub.textContent=secondary;body.append(title,sub);b.append(dot,body);b.onclick=onClick;hits.append(b);
}
function suggestions(){
 if(!hits)return;
 hits.replaceChildren();
 const q=(field?.value||"").trim();if(!q){
  const d=document.createElement("div");d.className="cx-drawer-welcome";
  const title=document.createElement("strong");title.textContent="Pesquisar no mapa";
  const note=document.createElement("small");note.textContent="Busque cooperados, ruas, bairros ou locais. A cartografia e os alfinetes permanecem no Google Maps.";
  d.append(title,note);hits.append(d);
 }
 let count=0;
 const matching=lastItems.filter(i=>i.ativo!==false&&i.id!=null&&(!q||queryNorm(i.nome).includes(queryNorm(q)))).slice(0,q?12:5);
 for(const i of matching){count++;const st=state(i);
  makeHit(i.nome||"Cooperado",st==="offline"?"Offline":st==="ocioso"?"Ocioso":"Online",palette[st],()=>{
   field.value=i.nome||"";
   if(routeActive){destination({lat:i.lat,lng:i.lng},i.nome)}else{
    window.COOPEX_MAP_ACTIONS?.selectCooperado?.(i.id);
    if(Number.isFinite(+i.lat)&&Number.isFinite(+i.lng)){googleMap.panTo({lat:+i.lat,lng:+i.lng});googleMap.setZoom(16)}
   }
   info("Cooperado: "+i.nome);
  });
 }
 for(const qSaved of readHistory().filter(s=>!q||queryNorm(s).includes(queryNorm(q))).slice(0,5)){
  count++;makeHit(qSaved,"Pesquisa anterior da COOPEX","#5284b5",()=>{field.value=qSaved;searchAddress()});
 }
 if(q.length>=2){count++;makeHit("Pesquisar no Google",q,"#187aff",searchAddress)}
 if(!count)info("Nenhum resultado local. Pressione Enter para pesquisar o endereço no Google.");
}
function searchAddress(){
 if(!googleMap||!geocoder){info("Google Maps ainda está inicializando.");return}
 const q=(field?.value||"").trim();if(q.length<3){info("Digite pelo menos três caracteres.");return}
 const coop=lastItems.find(i=>queryNorm(i.nome)===queryNorm(q)&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng));
 if(coop){destination({lat:+coop.lat,lng:+coop.lng},coop.nome);window.COOPEX_MAP_ACTIONS?.selectCooperado?.(coop.id);return}
 info("Pesquisando endereço no Google Maps...");
 geocoder.geocode({address:q+", Brasil"},(results,status)=>{
  if(status!=="OK"||!results?.length){info(status==="REQUEST_DENIED"?"Geocoding API não autorizada para esta chave. Verifique a restrição no Google Cloud.":"Endereço não localizado: "+status);return}
  const p=results[0].geometry.location;
  destination({lat:p.lat(),lng:p.lng()},results[0].formatted_address||q);
  remember(q);suggestions();
 });
}
function stopRender(){
 if(stopCounter)stopCounter.textContent=String(routeStops.length);
 if(!stopHost)return;stopHost.replaceChildren();
 routeStops.forEach((p,i)=>{
  const item=document.createElement("button");item.type="button";item.className="cx-stop-entry";
  item.textContent=(i+1)+". "+(p.label||"Parada")+"  ×";
  item.title="Remover esta parada";item.onclick=()=>{routeStops.splice(i,1);stopRender();hideRoutes()};
  stopHost.append(item);
 });
}
function currentCoords(){
 const center=googleMap?.getCenter();
 return center?{lat:center.lat(),lng:center.lng()}:null;
}
function resolveOrigin(){
 const q=origin.value.trim();if(!q)return Promise.resolve(currentCoords());
 const coop=lastItems.find(i=>queryNorm(i.nome)===queryNorm(q)&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng));
 if(coop)return Promise.resolve({lat:+coop.lat,lng:+coop.lng});
 return new Promise((resolve,reject)=>{
  geocoder.geocode({address:q+", Brasil"},(r,status)=>{
   if(status==="OK"&&r?.length)resolve({lat:r[0].geometry.location.lat(),lng:r[0].geometry.location.lng()});
   else reject(Error("Origem não encontrada: "+status));
  });
 });
}
function showRoute(idx){
 routePolylines.forEach((p,i)=>{p.setOptions({strokeColor:i===idx?"#1379ee":"#94a5bb",strokeOpacity:i===idx?1:.7,strokeWeight:i===idx?7:5,zIndex:i===idx?100:1})});
 if(routesHost)routesHost.querySelectorAll(".cx-route-option").forEach((n,i)=>n.classList.toggle("active",i===idx));
 const selected=routeAlternatives[idx];if(!selected)return;
 const bounds=new google.maps.LatLngBounds();
 for(const p of selected.path||[])bounds.extend({lat:p.lat,lng:p.lng});
 if(!bounds.isEmpty())googleMap.fitBounds(bounds,{top:80,bottom:70,left:Math.min(410,window.innerWidth*.25),right:70});
}
async function calculateRoutes(){
 if(!googleMap)return;
 if(!routeActive){routeActive=true;origin.hidden=false;document.getElementById("routeOpen").textContent="🚘 Calcular";info("Defina a origem. Se ficar vazia, será usado o centro do mapa.");return}
 try{
  if(!placePoint){await new Promise((resolve,reject)=>{
    const q=(field.value||"").trim();if(!q){reject(Error("Pesquise primeiro um destino."));return}
    geocoder.geocode({address:q+", Brasil"},(r,status)=>{
      if(status==="OK"&&r?.length){const p=r[0].geometry.location;destination({lat:p.lat(),lng:p.lng()},r[0].formatted_address);resolve()}
      else reject(Error("Destino não encontrado: "+status));
    });
  })}
  const first=await resolveOrigin();if(!first||!placePoint)throw Error("Defina a origem e o destino.");
  const {Route}=await google.maps.importLibrary("routes");
  info("Calculando quilômetros e rotas pelas vias do Google...");
  const opts={origin:first,destination:{lat:placePoint.lat,lng:placePoint.lng},travelMode:"DRIVING",
   fields:["path","distanceMeters","durationMillis","routeLabels"],computeAlternativeRoutes:routeStops.length===0,
   language:"pt-BR",region:"BR"};
  if(routeStops.length)opts.intermediates=routeStops.map(p=>({location:{lat:p.lat,lng:p.lng},stopover:true}));
  const result=await Route.computeRoutes(opts);
  const routes=(result.routes||[]).slice(0,3);if(!routes.length)throw Error("Não há rota disponível para esse trajeto.");
  hideRoutes();routeAlternatives=routes.map(r=>({
   distanceMeters:r.distanceMeters,durationMillis:r.durationMillis,
   path:(r.path||[]).map(p=>({lat:typeof p.lat==="function"?p.lat():+p.lat,lng:typeof p.lng==="function"?p.lng():+p.lng}))
  }));
  routesHost.hidden=false;
  routeAlternatives.forEach((route,i)=>{
   const line=new google.maps.Polyline({path:route.path,map:googleMap,strokeColor:i===0?"#1379ee":"#94a5bb",strokeOpacity:i===0?1:.7,strokeWeight:i===0?7:5,zIndex:i===0?100:1});
   line.addListener("click",()=>showRoute(i));routePolylines.push(line);
   const b=document.createElement("button");b.type="button";b.className="cx-route-option";
   const kms=(route.distanceMeters/1000).toLocaleString("pt-BR",{maximumFractionDigits:1});
   const mins=Math.round(route.durationMillis/60000);
   b.textContent=(i===0?"Melhor rota":"Alternativa "+(i+1))+" · "+kms+" km · "+mins+" min";
   b.onclick=()=>showRoute(i);routesHost.append(b);
  });
  showRoute(0);info(routeStops.length?"Rota completa pelas vias, passando pelas paradas.":"Selecione uma rota: azul é a ativa; cinza são alternativas.");
 }catch(e){info("Rotas indisponíveis: "+String(e.message||e).slice(0,180)+". Confira a Routes API e as restrições da chave.");console.warn("COOPEX Routes",e)}
}
function openStreet(){
 if(!googleMap)return;
 const p=placePoint||currentCoords();
 if(!p){info("Selecione um local no mapa.");return}
 streetPanorama=googleMap.getStreetView();
 streetPanorama.setPosition(p);streetPanorama.setPov({heading:0,pitch:0});streetPanorama.setVisible(true);
 info("Street View: arraste para olhar e clique nas setas da imagem para avançar pela rua.");
}
function resetSearch(){
 if(field)field.value="";if(placeMarker)placeMarker.setMap(null);placePoint=null;hideRoutes();suggestions();
 info("Pesquisa limpa. Os cooperados continuam visíveis.");
}
field?.addEventListener("input",suggestions);
field?.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();e.stopImmediatePropagation();searchAddress()}},true);
submit?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();searchAddress()},true);
clear?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();resetSearch()},true);
document.getElementById("cxDrawerMenu")?.addEventListener("click",()=>{field.value="";suggestions()});
document.getElementById("routeOpen")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();calculateRoutes()},true);
document.getElementById("streetView")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();openStreet()},true);
document.getElementById("addStop")?.addEventListener("click",e=>{
 e.preventDefault();e.stopImmediatePropagation();
 if(!placePoint){info("Pesquise ou selecione uma localização para adicionar como parada.");return}
 if(routeStops.length>=25){info("Limite de 25 paradas.");return}
 routeStops.push({...placePoint});stopRender();hideRoutes();info("Parada adicionada. Escolha o próximo destino.");
},true);
document.getElementById("stopsClear")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();routeStops=[];stopRender();hideRoutes()},true);
window.addEventListener("coopex:map-data",suggestions);
window.addEventListener("coopex:map-mode",e=>{if(!e.detail?.searchingAddress&&streetPanorama)streetPanorama.setVisible(false)});
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
  const wanted=googleMap;
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
function setGoogleCanvasSize(){
 const height=Math.max(document.documentElement.clientHeight||0,window.innerHeight||0,400);
 googleRoot.style.setProperty("height",height+"px","important");
 googleRoot.style.setProperty("width","100vw","important");
}
window.addEventListener("resize",setGoogleCanvasSize,{passive:true});
window.COOPEX_GOOGLE_MAP_ONLOAD=()=>{
 try{
  reportGoogleStatus("Carregando a cartografia oficial do Google Maps…","loading");
  setGoogleCanvasSize();
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
   if(googleRoot.querySelector(".gm-err-container")){
    authFailed=true;
    reportGoogleStatus("Google Maps informou erro de autorização ou faturamento. Consulte o Console para o código exato.");
    return;
   }
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
  // O Google controla a própria navegação, sem ciclo de retorno para o MapLibre.
  googleMap.addListener("idle",finishGoogleLoad);
  googleMap.addListener("bounds_changed",finishGoogleLoad);
  googleMap.addListener("projection_changed",finishGoogleLoad);
  googleMap.addListener("tilesloaded",finishGoogleLoad);
  geocoder=new google.maps.Geocoder();streetPanorama=googleMap.getStreetView();suggestions();

  // Em caso de falha de autorização, gm_authFailure mostra o diagnóstico.
  setTimeout(()=>{
   if(!loadFinished&&!authFailed){
    const rect=googleRoot.getBoundingClientRect();
    const hasStyle=!!googleRoot.querySelector(".gm-style");
    const hasError=!!googleRoot.querySelector(".gm-err-container");
    const text="Google Maps: "+(hasError?"erro de autorização/faturamento no Google":!hasStyle?"interface não foi criada":"interface criada, mas sem confirmar renderização")+
      " · área "+Math.round(rect.width)+"×"+Math.round(rect.height)+
      ". Confira o Console por mensagens de Maps JavaScript API.";
    reportGoogleStatus(text);
    console.warn(text);
   }
  },14000);
 }catch(e){document.body.classList.add("cx-google-error");reportGoogleStatus("Falha ao iniciar Google Maps: "+String(e.message||e).slice(0,170));console.warn("COOPEX Google Maps:",e);}
};
const script=document.createElement("script");
script.src="https://maps.googleapis.com/maps/api/js?key="+encodeURIComponent(key)+"&callback=COOPEX_GOOGLE_MAP_ONLOAD&loading=async&v=beta&language=pt-BR&region=BR";
script.async=true;
script.onerror=()=>{document.body.classList.add("cx-google-error");reportGoogleStatus("Não foi possível baixar a biblioteca do Google Maps. Confira a chave, a rede e a API ativada.");console.warn("Google Maps não carregou. Mapa alternativo mantido.");};
document.head.appendChild(script);
})();