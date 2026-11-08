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
let geocoder,streetPanorama,streetService,placePoint=null,placeMarker=null,routeActive=false;
let routeInFlight=false,placeSequenceInfo=0;
let routeAlternatives=[],routePolylines=[],routeStops=[],routeMode="DRIVING",searchFocus="destination";
const queryNorm=t=>String(t||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
const historyKey="coopex-map-own-search-history-v1";
function readHistory(){try{return JSON.parse(localStorage.getItem(historyKey)||"[]").filter(s=>typeof s==="string").slice(0,20)}catch(e){return []}}
function remember(q){
 // Apenas texto digitado pelo operador, não resultados/descrições recebidos do Google.
 try{localStorage.setItem(historyKey,JSON.stringify([q,...readHistory().filter(x=>queryNorm(x)!==queryNorm(q))].slice(0,20)))}catch(e){}
}
function info(t){if(message)message.textContent=t;}
function hideRoutes(){routePolylines.forEach(p=>p.setMap(null));routePolylines=[];routeAlternatives=[];if(routesHost){routesHost.replaceChildren();routesHost.hidden=true}}
function destination(p,name,googlePlace=null){
 if(!p||!Number.isFinite(+p.lat)||!Number.isFinite(+p.lng)){info("Local sem GPS válido.");return}
 placePoint={lat:+p.lat,lng:+p.lng,label:name||"Destino"};
 if(!placeMarker)placeMarker=new google.maps.Marker({map:googleMap,position:placePoint,title:placePoint.label});
 else{placeMarker.setPosition(placePoint);placeMarker.setTitle(placePoint.label);placeMarker.setMap(googleMap)}
 googleMap.panTo(placePoint);googleMap.setZoom(Math.max(googleMap.getZoom()||0,15));
 info("Local selecionado: "+placePoint.label);
 hideRoutes();
 window.dispatchEvent(new CustomEvent("coopex:place-selected",{detail:{point:placePoint}}));
 showPlaceDetails(placePoint,googlePlace);
}
async function showPlaceDetails(point,knownPlace=null){
 const seq=++placeSequenceInfo;
 const root=document.getElementById("cxPlaceDetails")||document.createElement("section");
 root.id="cxPlaceDetails";root.className="cx-place-details";root.replaceChildren();
 if(!root.isConnected)(document.querySelector(".cx-map-nav")||document.querySelector(".cx-drawer-top"))?.after(root);
 const heading=document.createElement("strong");heading.className="cx-place-title";heading.textContent=point.label||"Local selecionado";
 const address=document.createElement("small");address.className="cx-place-address";address.textContent=point.label||"Localização selecionada";
 const state=document.createElement("div");state.className="cx-place-state";state.textContent="Buscando detalhes disponíveis do local…";
 const gallery=document.createElement("div");gallery.className="cx-place-gallery";
 const metadata=document.createElement("div");metadata.className="cx-place-metadata";
 const actions=document.createElement("div");actions.className="cx-place-shortcuts";
 for(const [label,action] of [
  ["↗ Rotas",()=>{document.querySelector("[data-cx-nav=rotas]")?.click();const input=document.getElementById("cxDestinationMirror");if(input){input.value=point.label||"";input.dispatchEvent(new Event("change",{bubbles:true}))}}],
  ["◎ Ver em frente",openStreet],
  ["＋ Parada",()=>{document.querySelector("[data-cx-nav=rotas]")?.click();const input=document.getElementById("cxStopInput");if(input){input.value=point.label||"";input.focus()}}]
 ]){
  const b=document.createElement("button");b.type="button";b.textContent=label;b.onclick=action;actions.append(b);
 }
 root.append(heading,address,state,gallery,metadata,actions);
 function detail(text,cls=""){
  const line=document.createElement("div");line.className="cx-place-data "+cls;line.textContent=text;metadata.append(line);return line;
 }
 if(!knownPlace){state.textContent="Endereço localizado pelo Google. Mais informações dependem do cadastro do estabelecimento.";return}
 try{
  await knownPlace.fetchFields({fields:["displayName","formattedAddress","photos","rating","userRatingCount","regularOpeningHours","nationalPhoneNumber","websiteURI","googleMapsURI","businessStatus","primaryTypeDisplayName"]});
  if(seq!==placeSequenceInfo)return;
  if(knownPlace.displayName)heading.textContent=knownPlace.displayName;
  if(knownPlace.formattedAddress)address.textContent=knownPlace.formattedAddress;
  const pStatus=String(knownPlace.businessStatus||"");
  const translateStatus={CLOSED_TEMPORARILY:"Temporariamente fechado",CLOSED_PERMANENTLY:"Fechado permanentemente",FUTURE_OPENING:"Ainda não inaugurado"};
  state.textContent=translateStatus[pStatus]||"";
  const openStatus=typeof knownPlace.isOpen==="function"?await knownPlace.isOpen().catch(()=>undefined):undefined;
  if(seq!==placeSequenceInfo)return;
  if(typeof openStatus==="boolean")state.textContent=openStatus?"Aberto agora":"Fechado agora";
  if(!state.textContent)state.textContent="Informações disponibilizadas pelo Google";
  state.dataset.open=openStatus===true?"yes":openStatus===false?"no":"unknown";
  const photos=(knownPlace.photos||[]).slice(0,8);
  for(const photo of photos){
   try{
    const url=photo.getURI({maxWidth:720,maxHeight:430});
    if(!url)continue;
    const img=document.createElement("img");img.loading="lazy";img.decoding="async";img.src=url;img.alt="Foto do local — Google Maps";gallery.append(img);
   }catch(e){console.debug("Foto indisponível",e)}
  }
  if(photos.length){
   const credit=document.createElement("small");credit.className="cx-place-credit";credit.textContent="Fotos: Google Maps e colaboradores";gallery.after(credit);
  }else gallery.hidden=true;
  if(knownPlace.primaryTypeDisplayName){
   const value=knownPlace.primaryTypeDisplayName;detail(typeof value==="string"?value:value.toString(),"cx-place-category");
  }
  if(typeof knownPlace.rating==="number")detail("★ "+knownPlace.rating.toLocaleString("pt-BR",{maximumFractionDigits:1})+" · "+Number(knownPlace.userRatingCount||0).toLocaleString("pt-BR")+" avaliações","cx-place-rating");
  const hours=knownPlace.regularOpeningHours?.weekdayDescriptions||[];
  if(hours.length){
   const box=document.createElement("details");box.className="cx-place-hours";box.open=true;
   const summary=document.createElement("summary");summary.textContent="Horários de funcionamento";box.append(summary);
   for(const item of hours){const line=document.createElement("div");line.textContent=item;box.append(line)}
   metadata.append(box);
  }
  if(knownPlace.nationalPhoneNumber){
   const link=document.createElement("a");link.href="tel:"+knownPlace.nationalPhoneNumber.replace(/[^\d+]/g,"");link.textContent="☎ "+knownPlace.nationalPhoneNumber;link.className="cx-place-link";metadata.append(link);
  }
  for(const [url,label] of [[knownPlace.websiteURI,"Site do estabelecimento"],[knownPlace.googleMapsURI,"Ver ficha no Google Maps"]]){
   if(typeof url!=="string"||!/^https:\/\//.test(url))continue;
   const link=document.createElement("a");link.href=url;link.target="_blank";link.rel="noopener noreferrer";link.className="cx-place-link";link.textContent=label+" ↗";metadata.append(link);
  }
 }catch(error){
  console.warn("COOPEX Places details:",error);
  if(seq!==placeSequenceInfo)return;
  state.textContent="Detalhes indisponíveis para esta pesquisa. Confira as permissões e a cobrança da Places API (New).";
 }
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
  const note=document.createElement("small");note.textContent="";
  d.append(title,note);hits.append(d);
 }
 let count=0;
 for(const qSaved of readHistory().filter(s=>!q||queryNorm(s).includes(queryNorm(q))).slice(0,5)){
  count++;makeHit(qSaved,"Pesquisa anterior da COOPEX","#5284b5",()=>{field.value=qSaved;searchAddress()});
 }
 if(q.length>=2){count++;makeHit("Pesquisar no Google",q,"#187aff",searchAddress)}
 if(!count)info("Nenhum resultado local. Pressione Enter para pesquisar o endereço no Google.");
}
document.addEventListener("click",event=>{
 const b=event.target.closest?.("[data-cx-mode]");
 if(!b)return;
 const selected=b.dataset.cxMode;
 if(!["DRIVING","TWO_WHEELER","TRANSIT","WALKING","BICYCLING"].includes(selected))return;
 routeMode=selected;
 document.querySelectorAll("[data-cx-mode]").forEach(button=>button.classList.toggle("active",button===b));
 if(document.getElementById("cxRouteOrigin")?.value.trim()&&document.getElementById("cxDestinationMirror")?.value.trim())calculateRoutes();
});
function searchAddress(){
 if(!googleMap||!geocoder){info("Google Maps ainda está inicializando.");return}
 const q=(field?.value||"").trim();if(q.length<3){info("Digite pelo menos três caracteres.");return}
 info("Pesquisando endereço no Google Maps...");
 geocoder.geocode({address:q+", Brasil"},(results,status)=>{
  if(status!=="OK"||!results?.length){info(status==="REQUEST_DENIED"?"Geocoding API não autorizada para esta chave. Verifique a restrição no Google Cloud.":"Endereço não localizado: "+status);return}
  const p=results[0].geometry.location;
  destination({lat:p.lat(),lng:p.lng()},results[0].formatted_address||q);
  // Se a busca geocodificada aponta para um Place ID, também carregue os detalhes.
  if(results[0].place_id&&google.maps.places?.Place){
   try{const p2=new google.maps.places.Place({id:results[0].place_id});
    showPlaceDetails({lat:p.lat(),lng:p.lng(),label:results[0].formatted_address||q},p2)
   }catch(e){console.debug("Sem ficha de local para o endereço:",e)}
  }
  remember(q);suggestions();
 });
}
function resolveLocation(value){
 const query=String(value||"").trim();
 if(!query)return Promise.reject(Error("Informe um endereço."));
 const coordinates=query.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
 if(coordinates)return Promise.resolve({lat:Number(coordinates[1]),lng:Number(coordinates[2]),label:query});
 const coop=lastItems.find(i=>queryNorm(i.nome)===queryNorm(query)&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng));
 if(coop)return Promise.resolve({lat:+coop.lat,lng:+coop.lng,label:coop.nome});
 if(!geocoder)return Promise.reject(Error("Geocodificação ainda indisponível."));
 return new Promise((resolve,reject)=>geocoder.geocode({address:query+", Brasil"},(found,status)=>{
  if(status!=="OK"||!found?.length){reject(Error("Endereço não localizado ("+status+"): "+query));return}
  const p=found[0].geometry.location;resolve({lat:p.lat(),lng:p.lng(),label:found[0].formatted_address||query});
 }));
}
async function addRouteStop(){
 const input=document.getElementById("cxStopInput");const value=(input?.value||"").trim();
 if(!value){info("Informe o endereço da parada.");input?.focus();return}
 if(routeStops.length>=25){info("Máximo de 25 paradas.");return}
 try{const location=await resolveLocation(value);routeStops.push(location);input.value="";stopRender();hideRoutes();info("Parada adicionada: "+location.label);window.dispatchEvent(new Event("coopex:routes-changed"))}
 catch(e){info(String(e.message||e))}
}

function stopRender(){
 stopCounter.textContent=String(routeStops.length);
 stopHost.replaceChildren();
 routeStops.forEach((place,index)=>{
  const line=document.createElement("div");line.className="cx-stop-line";line.draggable=true;
  const handle=document.createElement("span");handle.className="cx-stop-handle";handle.textContent="⠿";line.append(handle);
  line.addEventListener("dragstart",e=>{e.dataTransfer?.setData("text/plain",String(index))});
  line.addEventListener("dragover",e=>e.preventDefault());
  line.addEventListener("drop",e=>{e.preventDefault();const from=Number(e.dataTransfer?.getData("text/plain"));if(!Number.isInteger(from)||from<0||from>=routeStops.length||from===index)return;const item=routeStops.splice(from,1)[0];routeStops.splice(index,0,item);stopRender();hideRoutes();window.dispatchEvent(new Event("coopex:routes-changed"))});
  const name=document.createElement("span");name.textContent=(index+1)+". "+place.label;name.title=place.label;line.append(name);
  const controls=[
   ["↑","Mover para cima",()=>{[routeStops[index-1],routeStops[index]]=[routeStops[index],routeStops[index-1]]},index===0],
   ["↓","Mover para baixo",()=>{[routeStops[index+1],routeStops[index]]=[routeStops[index],routeStops[index+1]]},index===routeStops.length-1],
   ["✎","Editar",()=>{const input=document.getElementById("cxStopInput");if(input){input.value=place.label;input.focus()}routeStops.splice(index,1)},false],
   ["×","Excluir",()=>routeStops.splice(index,1),false]
  ];
  for(const [symbol,title,run,disabled] of controls){
   const control=document.createElement("button");control.type="button";control.textContent=symbol;control.title=title;control.disabled=disabled;
   control.onclick=()=>{run();stopRender();hideRoutes();info("Ordem ou parada alterada.");window.dispatchEvent(new Event("coopex:routes-changed"));};line.append(control);
  }
  stopHost.append(line);
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
 if(!bounds.isEmpty())googleMap.fitBounds(bounds,{top:80,bottom:70,left:Math.min(480,window.innerWidth*.30),right:70});
 window.dispatchEvent(new CustomEvent("coopex:selected-route",{detail:{route:selected,index:idx}}));
}
async function calculateRoutes(){
 if(!googleMap||routeInFlight)return;
 routeActive=true;origin.hidden=false;routeInFlight=true;
 const button=document.getElementById("routeOpen");button.disabled=true;
 try{
  const destinationText=(document.getElementById("cxDestinationMirror")?.value||field.value||"").trim();
  if(!origin.value.trim()||!destinationText)throw Error("Preencha partida e destino.");
  const [a,b]=await Promise.all([resolveLocation(origin.value),resolveLocation(destinationText)]);
  info("");
  const {Route}=await google.maps.importLibrary("routes");
  const options={origin:{lat:a.lat,lng:a.lng},destination:{lat:b.lat,lng:b.lng},travelMode:routeMode,computeAlternativeRoutes:routeStops.length===0,fields:["path","distanceMeters","durationMillis","description","routeLabels"],language:"pt-BR",region:"BR"};
  if(routeMode==="DRIVING"||routeMode==="TWO_WHEELER")options.routingPreference="TRAFFIC_AWARE";
  if(routeStops.length)options.intermediates=routeStops.map(p=>({location:{lat:p.lat,lng:p.lng}}));
  const response=await Route.computeRoutes(options);
  const found=(response.routes||[]).slice(0,3);
  if(!found.length)throw Error("Nenhum trajeto disponível.");
  hideRoutes();
  routeAlternatives=found.map(r=>({distanceMeters:r.distanceMeters,durationMillis:r.durationMillis,description:r.description||"",path:(r.path||[]).map(p=>({lat:typeof p.lat==="function"?p.lat():Number(p.lat),lng:typeof p.lng==="function"?p.lng():Number(p.lng)}))}));
  routesHost.hidden=false;
  routeAlternatives.forEach((r,i)=>{
   const poly=new google.maps.Polyline({map:googleMap,path:r.path,strokeColor:i===0?"#193feb":"#6894ec",strokeOpacity:i===0?1:.75,strokeWeight:i===0?7:4,zIndex:i===0?90:2});
   poly.addListener("click",()=>showRoute(i));routePolylines.push(poly);
   const option=document.createElement("button");option.type="button";option.className="cx-route-option";
   const title=document.createElement("strong");title.textContent=r.description||("Rota "+(i+1));
   const meta=document.createElement("small");meta.textContent=(r.distanceMeters/1000).toLocaleString("pt-BR",{maximumFractionDigits:1})+" km · "+Math.round(r.durationMillis/60000)+" min";
   option.append(title,meta);option.onclick=()=>showRoute(i);routesHost.append(option);
  });
  showRoute(0);
  window.dispatchEvent(new CustomEvent("coopex:routes-ready",{detail:{routes:routeAlternatives}}));
  info("");
 }catch(e){info("Não foi possível calcular: "+String(e.message||e));console.warn("COOPEX Routes",e)}
 finally{routeInFlight=false;button.disabled=false}
}
function openStreet(){
 if(!googleMap)return;
 const p=placePoint||currentCoords();
 if(!p){info("Selecione um local no mapa.");return}
 streetPanorama=googleMap.getStreetView();
 if(!streetService)streetService=new google.maps.StreetViewService();
 info("Procurando imagem de rua próxima ao ponto selecionado…");
 streetService.getPanorama({location:p,radius:120,source:google.maps.StreetViewSource.OUTDOOR},(data,status)=>{
  if(status!=="OK"||!data?.location){info("Não há imagem Street View disponível neste local.");return}
  streetPanorama.setPano(data.location.pano);streetPanorama.setPov({heading:0,pitch:0});streetPanorama.setVisible(true);
  info("Street View aberto na rua mais próxima disponível. Arraste para olhar e use as setas para avançar.");
 });
}
function resetSearch(){
 if(field)field.value="";if(placeMarker)placeMarker.setMap(null);placePoint=null;hideRoutes();document.getElementById("cxPlaceDetails")?.remove();suggestions();
 info("Pesquisa limpa. Os cooperados continuam visíveis.");
}
// Places API (New): previsões reais do Google quando autorizada, sem duplicar a busca.
let placeToken=null,placeTimer=null,placeSequence=0,placesDisabled=false;
async function loadGoogleSuggestions(){
 const q=(field?.value||"").trim(),seq=++placeSequence;
 if(!googleMap||q.length<4||placesDisabled)return;
 try{
  const {AutocompleteSuggestion,AutocompleteSessionToken}=await google.maps.importLibrary("places");
  if(!placeToken)placeToken=new AutocompleteSessionToken();
  const request={input:q,sessionToken:placeToken,language:"pt-BR",region:"br",locationBias:{radius:25000,center:googleMap.getCenter()}};
  const data=await AutocompleteSuggestion.fetchAutocompleteSuggestions(request);
  if(seq!==placeSequence||q!==(field?.value||"").trim())return;
  for(const entry of (data.suggestions||[]).slice(0,6)){
   const prediction=entry.placePrediction;if(!prediction)continue;
   const label=prediction.text.toString();
   makeHit(label,"Sugestão do Google Maps","#438de0",async()=>{
    try{
     info("Abrindo local selecionado...");
     const place=prediction.toPlace();
     await place.fetchFields({fields:["location","displayName","formattedAddress"]});
     if(!place.location)throw Error("O local não possui coordenadas.");
     const p=place.location;
     destination({lat:p.lat(),lng:p.lng()},place.displayName||label,place);
     field.value=label;
     // Guarda somente a pesquisa escrita/selecionada pelo operador.
     remember(q);placeToken=null;suggestions();
    }catch(e){info("Não foi possível obter este local: "+String(e.message||e))}
   });
  }
 }catch(e){
  // Sem Places API (New) ativa: mantém histórico e cooperados, sem chamadas repetidas.
  placesDisabled=true;console.warn("COOPEX: Autocomplete do Google precisa da Places API (New) autorizada.",e);
 }
}
field?.addEventListener("input",()=>{document.getElementById("cxPlaceDetails")?.remove()});
field?.addEventListener("input",suggestions);
field?.addEventListener("input",()=>{
 clearTimeout(placeTimer);const q=(field.value||"").trim();
 if(q.length>=4&&!placesDisabled)placeTimer=setTimeout(loadGoogleSuggestions,750);
});
field?.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();e.stopImmediatePropagation();searchAddress()}},true);
submit?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();searchAddress()},true);
clear?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();resetSearch()},true);
document.getElementById("cxDrawerMenu")?.addEventListener("click",()=>{field.value="";suggestions()});
document.getElementById("routeOpen")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();calculateRoutes()},true);
document.getElementById("streetView")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();openStreet()},true);
document.getElementById("addStop")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();addRouteStop()},true);
document.getElementById("stopsClear")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();routeStops=[];stopRender();hideRoutes();window.dispatchEvent(new Event("coopex:routes-changed"))},true);
window.addEventListener("coopex:map-data",suggestions);
window.addEventListener("coopex:map-mode",e=>{if(!e.detail?.searchingAddress&&streetPanorama)streetPanorama.setVisible(false)});
const markers=new Map(),lastPositions=new Map();
let previewWindow=null;
let searchingAddress=false,selectedId=null,lastItems=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[],syncingFromGoogle=false;
let googleStatusFilter="todos";
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
    map:(googleStatusFilter==="todos"||googleStatusFilter===status)?googleMap:null,position:finalPoint,
    title:(i.nome||"Cooperado")+" · "+(status==="offline"?"Offline":status==="ocioso"?"Ocioso":"Online"),
    icon:iconFor(status,chosenNow),
    label:{text:initials(i.nome),color:"#111c2a",fontWeight:"800",fontSize:"12px"},
    optimized:false,zIndex:chosenNow?100:status==="online"?15:5
   });
   marker.__status=status;marker.__chosen=chosenNow;marker.__initials=initials(i.nome);
   marker.addListener("click",()=>{
    previewWindow?.close();
    window.COOPEX_MAP_ACTIONS?.selectCooperado?.(id);
    const current=lastItems.find(x=>String(x.id)===id);
    if(current&&googleMap){googleMap.panTo(marker.getPosition());if((googleMap.getZoom()||0)<14)googleMap.setZoom(14)}
   });
   marker.addListener("mouseover",()=>{
    if(!previewWindow)previewWindow=new google.maps.InfoWindow({disableAutoPan:true,maxWidth:280});
    const current=lastItems.find(x=>String(x.id)===id);
    if(!current)return;
    const wrap=document.createElement("div");wrap.className="cx-pin-preview";
    const title=document.createElement("strong");title.textContent=current.nome||"Cooperado";
    const sub=document.createElement("small");sub.textContent=(state(current)==="offline"?"🔴 Offline":state(current)==="ocioso"?"🟠 Ocioso":"🟢 Online")+" · "+(current.endereco||"Natal/RN");
    const last=document.createElement("small");last.textContent="GPS: "+(current.ultima_atualizacao||"Não informado");
    wrap.append(title,sub,last);previewWindow.setContent(wrap);previewWindow.open({map:googleMap,anchor:marker,shouldFocus:false});
   });
   marker.addListener("mouseout",()=>{previewWindow?.close()});
   markers.set(id,marker);
  }
 }
 for(const [id,marker]of markers)if(!present.has(id)){marker.setMap(null);markers.delete(id);lastPositions.delete(id);}
 updateMode();
}
function updateMode(){
 if(!googleMap)return;
 for(const marker of markers.values()){
  const wanted=(googleStatusFilter==="todos"||marker.__status===googleStatusFilter)?googleMap:null;
  if(marker.getMap()!==wanted)marker.setMap(wanted);
 }
}
function selectGoogleStatus(status){
 if(!["todos","online","ocioso","offline"].includes(status))return;
 googleStatusFilter=status;
 document.querySelectorAll(".filters [data-filter]").forEach(button=>{
  const active=button.dataset.filter===status;
  button.classList.toggle("active",active);
  button.setAttribute("aria-pressed",String(active));
 });
 updateMode();
}
document.querySelectorAll(".filters [data-filter]").forEach(button=>{
 button.setAttribute("aria-pressed",String(button.dataset.filter==="todos"));
 button.addEventListener("click",()=>selectGoogleStatus(button.dataset.filter));
});
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
  window.COOPEX_GOOGLE_NATIVE=googleMap;
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
  window.dispatchEvent(new CustomEvent("coopex:google-native-ready"));
  geocoder=new google.maps.Geocoder();streetPanorama=googleMap.getStreetView();suggestions();
  googleMap.addListener("click",event=>{
   if(event.latLng&&document.body.classList.contains("cx-address-mode"))
    destination({lat:event.latLng.lat(),lng:event.latLng.lng()},"Ponto marcado no mapa");
  });
  // As ferramentas precisam agir no mapa oficial visível, não no MapLibre oculto.
  const mapActions={
   mapSatellite:()=>googleMap.setMapTypeId("satellite"),
   mapStreet:()=>googleMap.setMapTypeId("roadmap"),
   fit:()=>{
    const bounds=new google.maps.LatLngBounds();let found=0;
    for(const m of markers.values())if(m.getMap()&&m.getPosition()){bounds.extend(m.getPosition());found++}
    if(found)googleMap.fitBounds(bounds,65);else info("Nenhum cooperado com GPS para enquadrar.");
   },
   locate:()=>{
    if(!navigator.geolocation){info("Seu navegador não disponibiliza localização.");return}
    navigator.geolocation.getCurrentPosition(p=>{
     googleMap.panTo({lat:p.coords.latitude,lng:p.coords.longitude});googleMap.setZoom(16)
    },()=>info("Permissão de localização não concedida."),{enableHighAccuracy:true,timeout:9000});
   },
   refresh:()=>{info("Atualizando as posições dos cooperados…");window.COOPEX_MAP_ACTIONS?.refresh?.()}
  };
  for(const [id,fn]of Object.entries(mapActions)){
   document.getElementById(id)?.addEventListener("click",event=>{
    event.preventDefault();event.stopImmediatePropagation();fn();
   },true);
  }
  document.getElementById("streetTool")?.addEventListener("click",event=>{
   event.preventDefault();event.stopImmediatePropagation();openStreet();
  },true);


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
script.onerror=()=>{document.body.classList.add("cx-google-error");reportGoogleStatus("Não foi possível carregar Google Maps. Confira a chave e a disponibilidade da API.");console.warn("Google Maps não carregou.");};
document.head.appendChild(script);
})();