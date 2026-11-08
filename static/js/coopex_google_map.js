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
const HISTORY_LIMIT=30;
function historyCutoff(){
 const d=new Date();d.setMonth(d.getMonth()-3);return d.getTime();
}
function readHistory(){
 try{
  const now=Date.now(),cutoff=historyCutoff();
  const raw=JSON.parse(localStorage.getItem(historyKey)||"[]");
  const valid=(Array.isArray(raw)?raw:[]).map(item=>{
   if(typeof item==="string")return {q:item,ts:now}; // migração sem perder buscas antigas
   return item&&typeof item.q==="string"?{q:item.q,ts:Number(item.ts)}:null;
  }).filter(item=>item&&item.q.trim()&&Number.isFinite(item.ts)&&item.ts>=cutoff&&item.ts<=now+60000)
   .slice(0,HISTORY_LIMIT);
  localStorage.setItem(historyKey,JSON.stringify(valid));
  return valid;
 }catch(error){return []}
}
function remember(q){
 const label=String(q||"").trim();if(!label)return;
 try{
  const items=[{q:label,ts:Date.now()},...readHistory().filter(item=>queryNorm(item.q)!==queryNorm(label))].slice(0,HISTORY_LIMIT);
  localStorage.setItem(historyKey,JSON.stringify(items));
 }catch(error){}
}
function forgetHistory(q){
 try{
  localStorage.setItem(historyKey,JSON.stringify(readHistory().filter(item=>queryNorm(item.q)!==queryNorm(q))));
 }catch(error){}
}
function info(t){if(message)message.textContent=t;}
let cxSearchNotice=null;
function searchNotice(text,kind="loading"){
 if(!cxSearchNotice){
  cxSearchNotice=document.createElement("div");cxSearchNotice.id="cxSearchNotice";
  cxSearchNotice.setAttribute("role","status");
  (document.getElementById("addressPanel")||document.body).append(cxSearchNotice);
 }
 cxSearchNotice.textContent=text||"";
 cxSearchNotice.dataset.kind=kind;
 cxSearchNotice.hidden=!text;
}
let routeGeneration=0;
function hideRoutes(){
 routePolylines.forEach(p=>p.setMap(null));routePolylines=[];routeAlternatives=[];
 if(routesHost){routesHost.replaceChildren();routesHost.hidden=true}
 window.dispatchEvent(new Event("coopex:routes-cleared"));
}
function invalidateRoutes(){
 // Uma edição em qualquer endereço invalida imediatamente o trajeto anterior.
 ++routeGeneration;
 routeActive=false;
 routeInFlight=false;
 const button=document.getElementById("routeOpen");if(button)button.disabled=false;
 hideRoutes();
}
function exitDirections(){
 invalidateRoutes();
 routeStops=[];
 const result=document.getElementById("cxStopInput");if(result)result.value="";
 const originField=document.getElementById("cxRouteOrigin");if(originField)originField.value="";
 const destField=document.getElementById("cxDestinationMirror");if(destField)destField.value="";
 stopRender();
}
function destination(p,name,googlePlace=null){
 if(!p||!Number.isFinite(+p.lat)||!Number.isFinite(+p.lng)){info("Local sem GPS válido.");return}
 placePoint={lat:+p.lat,lng:+p.lng,label:name||"Destino"};
 if(!placeMarker)placeMarker=new google.maps.Marker({map:googleMap,position:placePoint,title:placePoint.label});
 else{placeMarker.setPosition(placePoint);placeMarker.setTitle(placePoint.label);placeMarker.setMap(googleMap)}
 googleMap.panTo(placePoint);googleMap.setZoom(Math.max(googleMap.getZoom()||0,15));
 info("");
 hideRoutes();
 // Pesquisa e destinos sincronizados com a ficha que foi selecionada no mapa.
 if(field)field.value=placePoint.label;
 const mirror=document.getElementById("cxDestinationMirror");
 if(mirror)mirror.value=placePoint.label;
 window.dispatchEvent(new CustomEvent("coopex:place-selected",{detail:{point:placePoint}}));
 showPlaceDetails(placePoint,googlePlace);
}
async function showPlaceDetails(point,knownPlace=null){
 const seq=placeSequenceInfo;
 const root=document.getElementById("cxPlaceDetails")||document.createElement("section");
 root.id="cxPlaceDetails";root.className="cx-place-details";root.replaceChildren();
 if(!root.isConnected)(document.querySelector(".cx-map-nav")||document.querySelector(".cx-drawer-top"))?.after(root);
 const heading=document.createElement("strong");heading.className="cx-place-title";heading.textContent=point.label||"Local selecionado";
 const address=document.createElement("small");address.className="cx-place-address";address.textContent=point.label||"Localização selecionada";
 const state=document.createElement("div");state.className="cx-place-state";state.textContent="Buscando detalhes disponíveis do local…";
 const gallery=document.createElement("div");gallery.className="cx-place-gallery";
 const overview=document.createElement("div");overview.className="cx-place-overview";
 const metadata=document.createElement("div");metadata.className="cx-place-metadata";
 const actions=document.createElement("div");actions.className="cx-place-shortcuts";
 for(const [label,action] of [
  ["↗ Direções",()=>{const input=document.getElementById("cxDestinationMirror");if(input)input.value=(knownPlace?.formattedAddress||point.label||"");document.querySelector("[data-cx-nav=rotas]")?.click();}],
  ["◎ Ver em frente",openStreet],
  ["＋ Parada",()=>{document.querySelector("[data-cx-nav=rotas]")?.click();const input=document.getElementById("cxStopInput");if(input){input.value=point.label||"";input.focus()}}]
 ]){
  const b=document.createElement("button");b.type="button";b.textContent=label;b.onclick=action;actions.append(b);
 }
 root.append(gallery,heading,overview,address,state,actions,metadata);
 const mainPanel=document.getElementById("addressPanel");
 mainPanel?.classList.add("cx-place-view");
 mainPanel?.classList.remove("cx-search-idle","cx-search-suggesting");
 root.hidden=false;
 window.dispatchEvent(new Event("coopex:place-details-ready"));
 function detail(text,cls=""){
  const line=document.createElement("div");line.className="cx-place-data "+cls;line.textContent=text;metadata.append(line);return line;
 }
 if(!knownPlace){state.textContent="Endereço localizado pelo Google. Mais informações dependem do cadastro do estabelecimento.";return}
 try{
  await knownPlace.fetchFields({fields:["displayName","formattedAddress","photos","rating","userRatingCount","regularOpeningHours","currentOpeningHours","nationalPhoneNumber","websiteURI","googleMapsURI","businessStatus","primaryTypeDisplayName","editorialSummary","addressComponents"]});
  if(seq!==placeSequenceInfo)return;
  if(knownPlace.displayName)heading.textContent=knownPlace.displayName;
  if(knownPlace.formattedAddress)address.textContent=knownPlace.formattedAddress;
  // O trajeto permanece interno e usa o endereço preciso fornecido pelo Google.
  const routeDestination=document.getElementById("cxDestinationMirror");
  if(routeDestination&&!document.getElementById("addressPanel")?.classList.contains("cx-directions-mode"))routeDestination.value=knownPlace.formattedAddress||point.label||"";
  const pStatus=String(knownPlace.businessStatus||"");
  const translateStatus={CLOSED_TEMPORARILY:"Temporariamente fechado",CLOSED_PERMANENTLY:"Fechado permanentemente",FUTURE_OPENING:"Ainda não inaugurado"};
  state.textContent=translateStatus[pStatus]||"";
  let openStatus=typeof knownPlace.isOpen==="function"?await knownPlace.isOpen().catch(()=>undefined):undefined;
  if(typeof openStatus!=="boolean"&&typeof knownPlace.currentOpeningHours?.openNow==="boolean")
   openStatus=knownPlace.currentOpeningHours.openNow;
  if(seq!==placeSequenceInfo)return;
  if(typeof openStatus==="boolean")state.textContent=openStatus?"Aberto agora":"Fechado agora";
  if(!state.textContent)state.textContent="Informações disponibilizadas pelo Google";
  state.dataset.open=openStatus===true?"yes":openStatus===false?"no":"unknown";
  const photos=(knownPlace.photos||[]).slice(0,8);
  if(photos.length){
   const mainPhoto=document.createElement("img");
   mainPhoto.className="cx-place-hero-photo";mainPhoto.loading="eager";mainPhoto.alt="Foto do estabelecimento";
   const first=photos[0].getURI({maxWidth:900,maxHeight:600});
   mainPhoto.src=first;gallery.append(mainPhoto);
   if(photos.length>1){
    const photoButton=document.createElement("button");photoButton.type="button";photoButton.className="cx-place-photo-next";
    photoButton.textContent="Ver fotos";let position=0;
    photoButton.onclick=()=>{position=(position+1)%photos.length;mainPhoto.src=photos[position].getURI({maxWidth:900,maxHeight:600})};
    gallery.append(photoButton);
   }
   const credit=document.createElement("small");credit.className="cx-place-credit";credit.textContent="Fotos: Google Maps e colaboradores";gallery.after(credit);
  }else gallery.hidden=true;
  if(knownPlace.primaryTypeDisplayName){
   const value=knownPlace.primaryTypeDisplayName;
   const category=document.createElement("span");category.className="cx-place-category";category.textContent=typeof value==="string"?value:value.toString();overview.append(category);
  }
  if(typeof knownPlace.rating==="number"){
   const rating=document.createElement("span");rating.className="cx-place-rating";
   rating.textContent="★ "+knownPlace.rating.toLocaleString("pt-BR",{maximumFractionDigits:1})+" · "+Number(knownPlace.userRatingCount||0).toLocaleString("pt-BR")+" avaliações";
   overview.prepend(rating);
  }
  const hours=(knownPlace.currentOpeningHours?.weekdayDescriptions||knownPlace.regularOpeningHours?.weekdayDescriptions||[]);
  if(hours.length){
   const box=document.createElement("details");box.className="cx-place-hours";
   const summary=document.createElement("summary");summary.className="cx-hours-summary";
   const status=document.createElement("span");status.className="cx-hours-status";status.textContent=typeof openStatus==="boolean"?(openStatus?"Aberto agora":"Fechado agora"):"Horários";
   status.dataset.open=openStatus===true?"yes":openStatus===false?"no":"unknown";
   const label=document.createElement("span");label.className="cx-hours-label";label.textContent=" · Horários de funcionamento";
   const chevron=document.createElement("span");chevron.className="cx-hours-chevron";chevron.setAttribute("aria-hidden","true");chevron.textContent="⌄";
   summary.append(status,label,chevron);box.append(summary);
   const days=document.createElement("div");days.className="cx-hours-days";
   for(const item of hours){const line=document.createElement("div");line.className="cx-hours-day";line.textContent=item;days.append(line)}
   box.append(days);
   metadata.append(box);
  }
  if(knownPlace.editorialSummary){
   const summary=knownPlace.editorialSummary;detail(typeof summary==="string"?summary:summary.text||"","cx-place-description");
  }
  if(knownPlace.formattedAddress)detail("📍 "+knownPlace.formattedAddress,"cx-place-location");
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
 const sub=document.createElement("small");sub.textContent=secondary;body.append(title,sub);b.append(dot,body);b.onclick=onClick;hits.append(b);return b;
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
 for(const saved of readHistory().filter(item=>!q||queryNorm(item.q).includes(queryNorm(q))).slice(0,q?6:8)){
  const label=saved.q;
  const button=makeHit(label,"Pesquisa recente · COOPEX","#5284b5",()=>{
   field.value=label;++placeSequence;clearTimeout(placeTimer);
   hits.hidden=true;searchAddress();
  });
  button.classList.add("cx-history-hit");
  const remove=document.createElement("button");
  remove.type="button";remove.className="cx-history-remove";
  remove.textContent="×";remove.title="Excluir do histórico";
  remove.setAttribute("aria-label","Excluir "+label+" do histórico");
  remove.addEventListener("click",event=>{
   event.preventDefault();event.stopPropagation();forgetHistory(label);suggestions();
   hits.hidden=false;
  });
  // O botão Excluir não pode ficar dentro de outro botão (HTML inválido).
  const row=document.createElement("div");row.className="cx-history-row";
  button.replaceWith(row);row.append(button,remove);
  count++;
 }
 if(q.length>=2){count++;makeHit("Pesquisar no Google",q,"#187aff",()=>{
  ++placeSequence;clearTimeout(placeTimer);hits.hidden=true;searchAddress();
 })}
 if(!count&&q)info("Nenhum resultado local. Pressione Enter para pesquisar o endereço no Google.");
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
async function searchAddress(){
 if(!googleMap||!geocoder){info("Google Maps ainda está inicializando.");return}
 const q=(field?.value||"").trim();
 if(q.length<3){info("Digite pelo menos três caracteres.");return}
 const seq=++placeSequenceInfo;
 if(hits)hits.hidden=true;
 searchNotice("Pesquisando: "+q+"…");
 info("Localizando estabelecimento ou endereço...");
 // Text Search identifica estabelecimentos pelo nome; Geocoding é apenas fallback.
 try{
  const {Place}=await google.maps.importLibrary("places");
  const response=await Place.searchByText({
   textQuery:q,fields:["id","displayName","formattedAddress","location","primaryTypeDisplayName"],
   locationBias:googleMap.getCenter(),language:"pt-BR",region:"BR",maxResultCount:10
  });
  if(seq!==placeSequenceInfo)return;
  const candidates=(response.places||[]).filter(place=>place.location);
  if(candidates.length){
   // Evita selecionar um município quando a consulta é o nome de uma empresa.
   const normalize=x=>queryNorm(String(x||"").replace(/[^\p{L}\p{N} ]/gu," "));
   const query=normalize(q);
   const exact=candidates.find(place=>normalize(place.displayName)===query);
   const contains=candidates.find(place=>normalize(place.displayName).includes(query));
   // O bairro pesquisado pode fazer parte do endereço, sem fazer parte do nome comercial.
   // Ex.: "China in Box Ponta Negra" corresponde ao estabelecimento "China in Box".
   // O nome comercial pode vir sem a referência de bairro.
   const words=query.split(/\s+/).filter(w=>w.length>2);
   const rankings=candidates.map(place=>{
    const name=normalize(place.displayName),address=normalize(place.formattedAddress);
    const hits=words.filter(word=>name.split(" ").some(part=>part===word||part.startsWith(word))).length;
    const addressHits=words.filter(word=>address.includes(word)).length;
    return {place,hits,score:hits*6+addressHits+(name===query?20:0)};
   }).sort((a,b)=>b.score-a.score);
   const best=rankings[0];
   const choice=exact||contains||(best?.hits>=1?best.place:null);
   if(!choice)throw Error("Nenhum estabelecimento com nome compatível; tentando endereço");
   const p=choice.location;
   searchNotice("");
   destination({lat:p.lat(),lng:p.lng()},choice.displayName||choice.formattedAddress||q,choice);
   remember(q);suggestions();return;
  }
 }catch(error){
  console.warn("Text Search (Places New):",error);
 }
 if(seq!==placeSequenceInfo)return;
 geocoder.geocode({address:q+", Brasil"},async (results,status)=>{
  if(seq!==placeSequenceInfo)return;
  if(status!=="OK"||!results?.length){
   info("Não foi possível localizar: "+q);
   searchNotice("Não encontrei '"+q+"'. Tente acrescentar Natal/RN ou o nome da rua.","error");
   return;
  }
  const r=results[0],p=r.geometry.location;
  const addressQuery=/\d|\b(rua|av\.?|avenida|travessa|estrada|rodovia|praça|alameda)\b/i.test(q);
  const addressTypes=r.types||[];
  if(!addressQuery&&addressTypes.some(type=>["locality","administrative_area_level_1","administrative_area_level_2","country"].includes(type))){
   info("A busca retornou apenas a cidade, não o estabelecimento.");
   searchNotice("Não localizei esse estabelecimento. Tente informar o bairro e Natal/RN.","error");return;
  }
  let place=null;
  if(r.place_id){
   try{const {Place}=await google.maps.importLibrary("places");place=new Place({id:r.place_id})}
   catch(error){console.debug("Place Details indisponível",error)}
  }
  if(seq!==placeSequenceInfo)return;
  searchNotice("");
  destination({lat:p.lat(),lng:p.lng()},r.formatted_address||q,place);
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
// Arrastar origem, destinos intermediários e destino final, como no Google Maps.
let coopexReordering=false;
async function reorderEntireRoute(from,to){
 const fromField=document.getElementById("cxRouteOrigin");
 const toField=document.getElementById("cxDestinationMirror");
 if(!fromField||!toField)return;
 // A sequência visual é partida → primeiro destino → demais destinos.
 const original=[{label:fromField.value.trim()},{label:toField.value.trim()},...routeStops.map(p=>({...p}))];
 if(!original[0].label||!original[1].label){
  info("Preencha partida e destino antes de mudar a ordem.");
  return;
 }
 if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||to<0||from>=original.length||to>=original.length||from===to)return;
 if(coopexReordering)return;
 coopexReordering=true;
 try{
  invalidateRoutes();
  const moving=original.splice(from,1)[0];
  original.splice(to,0,moving);
  const intermediate=await Promise.all(original.slice(2).map(async item=>{
   if(Number.isFinite(item.lat)&&Number.isFinite(item.lng))return item;
   return resolveLocation(item.label);
  }));
  fromField.value=original[0].label;
  toField.value=original[1].label;
  if(field)field.value=toField.value;
  routeStops=intermediate;
  stopRender();
  window.dispatchEvent(new Event("coopex:routes-changed"));
 }catch(error){info("Não foi possível mudar a ordem: "+String(error.message||error))}
 finally{coopexReordering=false}
}
window.COOPEX_ROUTE_ACTIONS={...(window.COOPEX_ROUTE_ACTIONS||{}),reorder:reorderEntireRoute};
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

/* Preços e Rotas — utiliza a tabela existente no financeiro (/api/precos).
   O preço por bairro prevalece; sem correspondência, utiliza o valor por km configurado. */
let cxPricesRequest=null;
const cxMoney=n=>Number(n).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
const cxBillableKm=meters=>Math.max(1,Math.round(Number(meters)/1000));
const cxNorm=s=>String(s||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().replace(/[^a-z0-9 ]/g," ").replace(/\s+/g," ").trim();
function cxFetchPrices(){
 if(!cxPricesRequest)cxPricesRequest=fetch("/api/precos",{
  credentials:"same-origin",cache:"no-store",
  headers:{"Accept":"application/json","X-Requested-With":"fetch"}
 }).then(async response=>{
  if(!response.ok)throw Error("Tabela de preços inacessível");
  const data=await response.json();
  if(!data.ok)throw Error(data.error||"Tabela de preços não autorizada");
  return data;
 }).catch(error=>{cxPricesRequest=null;throw error});
 return cxPricesRequest;
}
async function cxBairros(point){
 const {results=[]}=await geocoder.geocode({location:{lat:point.lat,lng:point.lng}});
 const names=new Set();
 for(const result of results.slice(0,6)){
  for(const c of result.address_components||[]){
   if(c.types?.some(type=>["sublocality_level_1","sublocality","neighborhood","administrative_area_level_3"].includes(type)))
    names.add(cxNorm(c.long_name));
  }
 }
 return [...names].filter(Boolean);
}
/* Valor padronizado por sequência de entregas:
   1) Preço cadastrado para bairro de origem -> bairro de destino de cada trecho.
   2) Apenas para trecho sem cadastro: quilômetros do trecho x preço/km.
   As rotas alternativas compartilham o mesmo orçamento; só distância e tempo variam.
*/
function cxBairroCandidates(label,geocoded,tableNames){
 const text=cxNorm(label);
 const variants=new Set((geocoded||[]).map(cxNorm).filter(Boolean));
 const known=(tableNames||[]).map(cxNorm).filter(Boolean).sort((a,b)=>b.length-a.length);
 // Nomes "Planalto - Natal" e "Planalto" devem corresponder.
 const short=name=>name.replace(/\s+(natal|rn|rio grande do norte)$/,"").trim();
 const add=value=>{if(value){variants.add(value);variants.add(short(value));}};
 for(const item of known){
  const bare=short(item);
  if(bare.length>2&&(" "+text+" ").includes(" "+bare+" "))add(item);
 }
 for(const part of String(label||"").split(/[,–—]/)){
  const value=cxNorm(part);
  if(value.length>2&&value.length<65)add(value);
 }
 for(const value of [...variants])add(value);
 return [...variants];
}
function cxFindFixed(rows,originNames,destinationNames){
 // Respeitar o sentido: Planalto -> Lagoa Nova ≠ Lagoa Nova -> Planalto.
 for(const a of originNames){
  for(const b of destinationNames){
   const match=rows.find(r=>r.o===a&&r.d===b&&Number.isFinite(r.price));
   if(match)return match;
  }
 }
 return null;
}
async function cxAddPrices(routes,waypoints,generation){
 const cards=[...routesHost.querySelectorAll(".cx-route-option")];
 try{
  const data=await cxFetchPrices();
  if(generation!==routeGeneration)return;
  const rows=(data.items||[]).map(r=>({o:cxNorm(r.origem),d:cxNorm(r.destino),price:Number(r.valor)}));
  const knownNames=[...new Set(rows.flatMap(r=>[r.o,r.d]))];
  const perKm=Number(data.per_km)>0?Number(data.per_km):2;
  const candidates=await Promise.all(waypoints.map(async point=>{
   let names=[];
   try{names=await cxBairros(point)}catch(error){console.debug("Bairro não identificado:",point.label)}
   return cxBairroCandidates(point.label,names,knownNames);
  }));
  if(generation!==routeGeneration)return;
  // Um único preço da sequência, independente do caminho alternativo desenhado.
  // A referência de distância de cada trecho sem tabela é o menor trajeto
  // retornado pela API para aquela sequência, evitando cobranças diferentes
  // para os mesmos endereços só por alternar a linha azul.
  const segmentCount=waypoints.length-1;
  const valid=routes.filter(route=>Array.isArray(route.legs)&&route.legs.length===segmentCount);
  if(!valid.length)throw Error("A resposta da rota não separou as entregas em trechos.");
  const segments=Array.from({length:segmentCount},(_,j)=>{
   const fixed=cxFindFixed(rows,candidates[j],candidates[j+1]);
   const minMeters=Math.min(...valid.map(route=>Number(route.legs[j]?.distanceMeters)).filter(Number.isFinite));
   if(fixed)return {amount:fixed.price,fixed:true,from:candidates[j][0],to:candidates[j+1][0]};
   if(!Number.isFinite(minMeters))return {amount:null,fixed:false};
   return {amount:cxBillableKm(minMeters)*perKm,fixed:false,km:cxBillableKm(minMeters)};
  });
  if(segments.some(segment=>segment.amount===null))throw Error("Distância indisponível para algum trecho.");
  const total=segments.reduce((sum,segment)=>sum+segment.amount,0);
  for(const option of cards){
   option.querySelector(".cx-route-price")?.remove();
   const wrap=document.createElement("div");wrap.className="cx-route-price";
   const headline=document.createElement("strong");
   headline.textContent="Total: "+cxMoney(total);
   wrap.append(headline);
   const breakdown=document.createElement("div");breakdown.className="cx-route-price-breakdown";
   segments.forEach((segment,j)=>{
    const row=document.createElement("div");row.className="cx-leg-price";
    const description=document.createElement("span");
    const from=waypoints[j].label?.split(",")[0]||("Ponto "+(j+1));
    const to=waypoints[j+1].label?.split(",")[0]||("Ponto "+(j+2));
    description.textContent=(j+1)+". "+from+" → "+to+(segment.fixed?" · Tabela":" · "+segment.km+" km");
    const amount=document.createElement("b");amount.textContent=cxMoney(segment.amount);
    row.append(description,amount);breakdown.append(row);
   });
   wrap.append(breakdown);
   if(segments.some(seg=>!seg.fixed)){
    const note=document.createElement("small");
    note.textContent="Sem preço por bairro: R$ "+perKm.toLocaleString("pt-BR",{minimumFractionDigits:2})+"/km";
    wrap.append(note);
   }
   option.append(wrap);
  }
 }catch(error){
  if(generation!==routeGeneration)return;
  console.warn("COOPEX Preços e Rotas:",error);
  cards.forEach(option=>{
   option.querySelector(".cx-route-price")?.remove();
   const label=document.createElement("span");label.className="cx-route-price";
   label.textContent="Não foi possível calcular o preço por trecho";
   option.append(label);
  });
 }
}

async function calculateRoutes(){
 if(!googleMap||routeInFlight)return;
 const generation=++routeGeneration;
 routeActive=true;origin.hidden=false;routeInFlight=true;
 const button=document.getElementById("routeOpen");button.disabled=true;
 try{
  const destinationText=(document.getElementById("cxDestinationMirror")?.value||"").trim();
  if(!origin.value.trim()||!destinationText)throw Error("Preencha partida e destino.");
  const [a,b]=await Promise.all([resolveLocation(origin.value),resolveLocation(destinationText)]);
  // 'Adicionar destino' acrescenta uma entrega DEPOIS do destino atual.
  const waypoints=[a,b,...routeStops.map(p=>({...p}))];
  const final=waypoints[waypoints.length-1];
  const intermediates=waypoints.slice(1,-1);
  info("");
  const {Route}=await google.maps.importLibrary("routes");
  const options={origin:{lat:a.lat,lng:a.lng},destination:{lat:final.lat,lng:final.lng},travelMode:routeMode,computeAlternativeRoutes:routeStops.length===0,fields:["path","distanceMeters","durationMillis","description","routeLabels","legs"],language:"pt-BR",region:"BR"};
  if(routeMode==="DRIVING"||routeMode==="TWO_WHEELER")options.routingPreference="TRAFFIC_AWARE";
  if(intermediates.length)options.intermediates=intermediates.map(p=>({location:{lat:p.lat,lng:p.lng}}));
  const response=await Route.computeRoutes(options);
  if(generation!==routeGeneration||!document.getElementById("addressPanel")?.classList.contains("cx-directions-mode"))return;
  const found=(response.routes||[]).slice(0,3);
  if(!found.length)throw Error("Nenhum trajeto disponível.");
  hideRoutes();
  routeAlternatives=found.map(r=>({distanceMeters:r.distanceMeters,durationMillis:r.durationMillis,description:r.description||"",legs:(r.legs||[]).map(leg=>({distanceMeters:leg.distanceMeters,durationMillis:leg.durationMillis})),path:(r.path||[]).map(p=>({lat:typeof p.lat==="function"?p.lat():Number(p.lat),lng:typeof p.lng==="function"?p.lng():Number(p.lng)}))}));
  routesHost.hidden=false;
  routeAlternatives.forEach((r,i)=>{
   const poly=new google.maps.Polyline({map:googleMap,path:r.path,strokeColor:i===0?"#193feb":"#6894ec",strokeOpacity:i===0?1:.75,strokeWeight:i===0?7:4,zIndex:i===0?90:2});
   poly.addListener("click",()=>showRoute(i));routePolylines.push(poly);
   const option=document.createElement("button");option.type="button";option.className="cx-route-option";
   const title=document.createElement("strong");title.textContent=r.description||("Rota "+(i+1));
   const meta=document.createElement("small");meta.textContent=cxBillableKm(r.distanceMeters).toLocaleString("pt-BR")+" km · "+Math.round(r.durationMillis/60000)+" min";
   option.append(title,meta);option.onclick=()=>showRoute(i);routesHost.append(option);
  });
  showRoute(0);
  remember(origin.value);remember(destinationText);
  cxAddPrices(routeAlternatives,waypoints,generation);
  window.dispatchEvent(new CustomEvent("coopex:routes-ready",{detail:{routes:routeAlternatives,origin:a,destination:final}}));
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
 ++placeSequenceInfo; // Cancela qualquer ficha pendente e impede que a busca antiga reapareça.
 ++placeSequence;
 clearTimeout(placeTimer);
 if(streetPanorama?.getVisible())streetPanorama.setVisible(false);
 if(field)field.value="";
 searchNotice("");
 if(placeMarker){placeMarker.setMap(null);placeMarker=null}
 placePoint=null;exitDirections();
 document.getElementById("cxPlaceDetails")?.remove();
 if(hits){hits.replaceChildren();hits.hidden=true}
 info("");
 window.dispatchEvent(new Event("coopex:search-reset"));
}
// Places API (New): previsões reais do Google quando autorizada, sem duplicar a busca.
let placeToken=null,placeTimer=null,placeSequence=0,placesDisabled=false;
async function loadGoogleSuggestions(){
 const q=(field?.value||"").trim(),seq=++placeSequence;
 if(!googleMap||q.length<3||placesDisabled)return;
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
 if(q.length>=3&&!placesDisabled)placeTimer=setTimeout(loadGoogleSuggestions,230);
});
field?.addEventListener("keydown",e=>{
 if(e.key==="Enter"&&!e.isComposing){
  e.preventDefault();e.stopImmediatePropagation();
  ++placeSequence;clearTimeout(placeTimer);hits.hidden=true;
  searchAddress();
 }
},true);
submit?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();searchAddress()},true);
clear?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();resetSearch()},true);
document.getElementById("cxDrawerMenu")?.addEventListener("click",()=>{field.value="";suggestions()});
document.getElementById("routeOpen")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();calculateRoutes()},true);
document.getElementById("streetView")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();openStreet()},true);
document.addEventListener("keydown",event=>{
 if(event.key!=="Escape"||event.repeat||event.defaultPrevented)return;
 if(streetPanorama?.getVisible()){
  event.preventDefault();event.stopImmediatePropagation();
  streetPanorama.setVisible(false);
  return;
 }
 const panel=document.getElementById("addressPanel");
 const hasSearch=!!field?.value.trim()||!!document.getElementById("cxPlaceDetails");
 // Inclui alfinete colocado diretamente no mapa, mesmo sem endereço digitado.
 const hasPin=!!placePoint||!!placeMarker?.getMap();
 if(hasSearch||hasPin||panel?.classList.contains("cx-place-view")){
  event.preventDefault();event.stopImmediatePropagation();
  resetSearch();
 }
},true);
document.getElementById("addStop")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();addRouteStop()},true);
document.getElementById("stopsClear")?.addEventListener("click",e=>{e.preventDefault();e.stopImmediatePropagation();routeStops=[];stopRender();hideRoutes();window.dispatchEvent(new Event("coopex:routes-changed"))},true);
// Os resultados só são válidos enquanto os dois endereços permanecem preenchidos.
for(const input of [origin,document.getElementById("cxDestinationMirror")]){
 if(!input)continue;
 input.addEventListener("input",invalidateRoutes);
 input.addEventListener("change",()=>{
  if(!origin.value.trim()||!document.getElementById("cxDestinationMirror")?.value.trim())invalidateRoutes();
 });
}
window.addEventListener("coopex:route-fields-cleared",invalidateRoutes);
window.addEventListener("coopex:exit-directions",exitDirections);
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
const neighborhoodCache=new Map();
let neighborhoodSelection="";
function reverseNeighborhood(){
 const entry=lastItems.find(i=>String(i.id)===String(selectedId));
 if(!entry||!Number.isFinite(+entry.lat)||!Number.isFinite(+entry.lng))return;
 if(!document.getElementById("details")?.classList.contains("visible"))return;
 const lat=+entry.lat,lng=+entry.lng;
 const key=lat.toFixed(4)+","+lng.toFixed(4),token=String(entry.id)+":"+key;
 if(token===neighborhoodSelection)return;
 neighborhoodSelection=token;
 const label=document.getElementById("personArea");
 if(neighborhoodCache.has(key)){if(label)label.textContent=neighborhoodCache.get(key);return}
 if(!geocoder||neighborhoodCache.size>180)return;
 if(label)label.textContent="Localizando bairro…";
 geocoder.geocode({location:{lat,lng}},(results,status)=>{
  if(neighborhoodSelection!==token)return;
  let found="";
  if(status==="OK"&&results?.length){
   for(const result of results){
    for(const component of result.address_components||[]){
     if(component.types?.includes("sublocality_level_1")||component.types?.includes("neighborhood")){
      found=component.long_name;break;
     }
    }
    if(found)break;
   }
  }
  if(!found)found=entry.endereco||"Bairro não identificado";
  neighborhoodCache.set(key,found);
  if(neighborhoodSelection===token&&label)label.textContent=found;
 });
}

function onMapData(event){
 const detail=event.detail||{};
 if(Array.isArray(detail.items))lastItems=detail.items;
 selectedId=detail.selectedId;
 if(typeof detail.searchingAddress==="boolean")searchingAddress=detail.searchingAddress;
 updateMarkers(lastItems,selectedId);
 if(selectedId!=null)reverseNeighborhood();else neighborhoodSelection="";
}
window.addEventListener("coopex:map-data",onMapData);
window.addEventListener("coopex:map-mode",event=>{
 searchingAddress=!!event.detail?.searchingAddress;
 updateMode();
});
window.addEventListener("coopex:map-view",event=>{
 if(document.body.classList.contains("cx-google-only"))return;
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
   mapTypeControl:false,zoomControl:true,scaleControl:true,
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
  geocoder=new google.maps.Geocoder();streetPanorama=googleMap.getStreetView();
  streetPanorama.addListener("visible_changed",()=>{
   document.body.classList.toggle("cx-street-visible",!!streetPanorama.getVisible());
   if(!streetPanorama.getVisible())window.dispatchEvent(new Event("coopex:street-closed"));
  });
  suggestions();
  googleMap.addListener("click",async event=>{
   if(event.placeId){
    event.stop();
    const position=event.latLng?{lat:event.latLng.lat(),lng:event.latLng.lng()}:null;
    try{
     const {Place}=await google.maps.importLibrary("places");
     const place=new Place({id:event.placeId});
     await place.fetchFields({fields:["location","displayName","formattedAddress"]});
     const p=place.location||position;
     if(!p)throw Error("Sem coordenadas");
     const lat=typeof p.lat==="function"?p.lat():p.lat;
     const lng=typeof p.lng==="function"?p.lng():p.lng;
     destination({lat,lng},place.displayName||place.formattedAddress||"Local selecionado",place);
    }catch(error){
     console.warn("CoopEX: não foi possível carregar os dados do local:",error);
     if(position)destination(position,"Local selecionado");
     info("Detalhes adicionais indisponíveis para este local.");
    }
    return;
   }
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