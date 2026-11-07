import * as ml from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
(()=>{
"use strict";
const $=id=>document.getElementById(id),esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const DAY_STYLE="https://tiles.openfreemap.org/styles/liberty";
const night=false;
document.body.classList.remove("is-night");
const map=new ml.Map({container:"map",style:DAY_STYLE,center:[-35.211,-5.7945],zoom:12,pitch:0,bearing:0,antialias:true,maxPitch:0,attributionControl:true,dragRotate:false,touchPitch:false});
map.addControl(new ml.NavigationControl({showCompass:false,showZoom:true,visualizePitch:false}),"top-right");
let selectedId=null,following=false,filter="todos",items=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[],searchText="",trafficEvents=[],selectedAddress=null,viewPoint=null,fetching=false,lastFit=false,satellite=false,weatherLevel=0;
let dynamicMarkers=new Map(),stops=[],stopsMarkers=[],placePin=null;
const hasCoord=i=>i&&i.lat!=null&&i.lng!=null&&Number.isFinite(+i.lat)&&Number.isFinite(+i.lng)&&(+i.lat<0)&&(+i.lng<0);
const state=i=>!i.online?"offline":i.status==="ocioso"?"ocioso":"online";
const stateNames={online:"Online",ocioso:"Ocioso",offline:"Offline"};
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
map.on("style.load",()=>{buildings();if(satellite)installSatellite();});
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
function render(){
 counts();
 const gs=clusters(),keep=new Set();
 for(const group of gs){
   const target=group.find(i=>String(i.id)===String(selectedId))||group[0],key=String(group[0].id),coord=[+group[0].lng,+group[0].lat];keep.add(key);
   let m=dynamicMarkers.get(key);
   if(!m){const el=pin(target,group.length);m=new ml.Marker({element:el,anchor:"bottom",pitchAlignment:"viewport",rotationAlignment:"viewport"}).setLngLat(coord).addTo(map);dynamicMarkers.set(key,m);}
   else{m.setLngLat(coord);const old=m.getElement(),newEl=pin(target,group.length);old.className=newEl.className;old.innerHTML=newEl.innerHTML;old.title=newEl.title;old.setAttribute("aria-label",newEl.getAttribute("aria-label"));}
   m.getElement().onclick=e=>{e.preventDefault();const at=group.findIndex(i=>String(i.id)===String(selectedId));selectCooperado(group[(at+1)%group.length].id);};
 }
 for(const [key,m]of dynamicMarkers){if(!keep.has(key)){m.remove();dynamicMarkers.delete(key)}}
 const sel=itemById(selectedId);if(sel){showDetails(sel);if(following&&hasCoord(sel))map.easeTo({center:[+sel.lng,+sel.lat],duration:350});}
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
function selectCooperado(id){selectedId=String(id);following=false;const i=itemById(id);if(!i)return;render();if(hasCoord(i))map.flyTo({center:[+i.lng,+i.lat],zoom:Math.max(map.getZoom(),15),pitch:0,bearing:0,duration:650});}
async function updateGps(){
 if(fetching||document.hidden)return;fetching=true;
 try{
  const response=await fetch("/mapa_motoboys?format=json",{cache:"no-store",credentials:"same-origin",headers:{Accept:"application/json"}});
  if(!response.ok)throw Error("GPS HTTP "+response.status);
  const data=await response.json();if(!Array.isArray(data))throw Error("Formato GPS inválido");
  items=data;render();$("gpsStatus").textContent="GPS ativo";
  if(!lastFit){const good=clusters().map(g=>g[0]);if(good.length){let b=new ml.LngLatBounds();good.forEach(i=>b.extend([+i.lng,+i.lat]));map.fitBounds(b,{padding:{top:160,right:365,bottom:180,left:320},maxZoom:13,duration:0});lastFit=true;}}
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
function installSatellite(){if(!map.getSource("cx-sat"))map.addSource("cx-sat",{type:"raster",tiles:["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],tileSize:256,attribution:"Tiles © Esri"});if(!map.getLayer("cx-sat-layer"))map.addLayer({id:"cx-sat-layer",type:"raster",source:"cx-sat"});if(map.getLayer("coopex-buildings-3d"))map.moveLayer("coopex-buildings-3d");}
$("mapStreet").onclick=()=>{satellite=false;if(map.getLayer("cx-sat-layer"))map.removeLayer("cx-sat-layer");$("toolbar").classList.remove("open")};
$("mapSatellite").onclick=()=>{satellite=true;if(map.isStyleLoaded())installSatellite();$("toolbar").classList.remove("open")};
const dest=()=>selectedAddress||(hasCoord(itemById(selectedId))?{lat:+itemById(selectedId).lat,lng:+itemById(selectedId).lng,label:itemById(selectedId).nome}:null);
function stopRender(){for(const m of stopsMarkers)m.remove();stopsMarkers=[];const container=$("stopsList");container.replaceChildren();$("stopsCount").textContent=stops.length;
 stops.forEach((p,index)=>{const line=document.createElement("div");line.className="stop-row";const label=document.createElement("span");label.textContent=(index+1)+". "+p.label;const remove=document.createElement("button");remove.textContent="×";remove.title="Remover parada";remove.onclick=()=>{stops.splice(index,1);stopRender()};line.append(label,remove);container.append(line);const marker=new ml.Marker({color:"#ffb13c",pitchAlignment:"viewport"}).setLngLat([p.lng,p.lat]).addTo(map);stopsMarkers.push(marker);});
}
function openRoute(){const destination=dest()||stops[stops.length-1];if(!destination){$("addressMessage").textContent="Selecione um cooperado ou um endereço.";return}const middle=stops.filter(x=>x!==destination).slice(0,3);let url="https://www.google.com/maps/dir/?api=1&travelmode=driving&destination="+encodeURIComponent(destination.lat+","+destination.lng);if(middle.length)url+="&waypoints="+encodeURIComponent(middle.map(p=>p.lat+","+p.lng).join("|"));window.open(url,"_blank","noopener,noreferrer")}
function street(){const p=viewPoint||dest();if(!p){$("addressMessage").textContent="Selecione um endereço ou ponto do mapa.";return}window.open("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint="+encodeURIComponent(p.lat+","+p.lng),"_blank","noopener,noreferrer");}
$("addressToggle").onclick=()=>{$("addressPanel").hidden=!$("addressPanel").hidden;if(!$("addressPanel").hidden)$("address").focus()};
$("addressClose").onclick=()=>{$("addressPanel").hidden=true};
$("addressGo").onclick=async()=>{
 const q=$("address").value.trim();if(q.length<3)return;
 try{const r=await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=3&countrycodes=br&q="+encodeURIComponent(q+", Natal, Rio Grande do Norte"));if(!r.ok)throw Error();const list=await r.json();if(!list.length){$("addressMessage").textContent="Endereço não encontrado";return;}
 const first=list[0];selectedAddress={lat:+first.lat,lng:+first.lon,label:first.display_name};viewPoint=selectedAddress;map.flyTo({center:[selectedAddress.lng,selectedAddress.lat],zoom:16,pitch:55});$("addressMessage").textContent="Endereço encontrado. Pode adicionar como parada.";
 if(placePin)placePin.remove();placePin=new ml.Marker({color:"#276bff",pitchAlignment:"viewport"}).setLngLat([selectedAddress.lng,selectedAddress.lat]).addTo(map);
 }catch(e){$("addressMessage").textContent="Busca indisponível no momento";}
};
$("address").onkeydown=e=>{if(e.key==="Enter")$("addressGo").click()};
$("addStop").onclick=()=>{const p=selectedAddress||viewPoint||dest();if(!p){$("addressMessage").textContent="Pesquise ou marque um ponto no mapa.";return}if(stops.length>=3){$("addressMessage").textContent="Até 3 paradas intermediárias";return}stops.push({...p});stopRender();$("addressMessage").textContent="Parada adicionada."};
$("stopsClear").onclick=()=>{stops=[];stopRender();};
$("routeOpen").onclick=openRoute;$("openNav").onclick=openRoute;$("streetView").onclick=street;$("streetTool").onclick=street;
function drawTraffic(){
 if(!map.isStyleLoaded())return;
 if(!map.getSource("coopex-traffic"))map.addSource("coopex-traffic",{type:"geojson",data:{type:"FeatureCollection",features:trafficEvents}});
 else map.getSource("coopex-traffic").setData({type:"FeatureCollection",features:trafficEvents});
 if(!map.getLayer("coopex-traffic-lines"))map.addLayer({id:"coopex-traffic-lines",type:"line",source:"coopex-traffic",paint:{"line-color":"#f04a36","line-width":["interpolate",["linear"],["zoom"],10,3,16,7],"line-opacity":.85}});
 if(!map.getLayer("coopex-traffic-points"))map.addLayer({id:"coopex-traffic-points",type:"circle",source:"coopex-traffic",filter:["==",["geometry-type"],"Point"],paint:{"circle-color":"#f15d31","circle-radius":8,"circle-stroke-color":"#ffffff","circle-stroke-width":2}});
}
async function traffic(){
 try{
 const r=await fetch("/api/coopex/tomtom/incidentes",{credentials:"same-origin",cache:"no-store"});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.code==="KEY_REJECTED"?"TomTom HTTP 403: chave sem autorização":d.error||"Serviço de trânsito indisponível");
 trafficEvents=Array.isArray(d.features)?d.features:[];drawTraffic();$("trafficStatus").textContent="TomTom conectado";$("trafficStatus").style.color="#71ffd4";$("trafficText").textContent=trafficEvents.length+" ocorrência(s) reportada(s) na região.";if(selectedId){const p=itemById(selectedId);if(p)showDetails(p)}
 }catch(e){$("trafficStatus").textContent="Sem dados de trânsito";$("trafficStatus").style.color="var(--orange)";$("trafficText").textContent="Integração TomTom não autorizada. O mapa e o GPS continuam operando.";console.warn("TomTom:",e.message)}
}
function phase(){document.body.classList.remove("is-night");}
const rainFx=$("weatherFx");function rain(level){weatherLevel=level;document.body.classList.toggle("is-rain",level>0);rainFx.replaceChildren();if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;const count=[0,8,18,35][level]||0;const frag=document.createDocumentFragment();for(let i=0;i<count;i++){const d=document.createElement("i");d.className="drop";d.style.left=(Math.random()*100)+"%";d.style.animationDelay=(-Math.random()*3)+"s";d.style.animationDuration=(.7+Math.random()*1.4)+"s";frag.append(d)}rainFx.append(frag)}
async function weather(){
 try{const r=await fetch("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=-5.7945&lon=-35.2110",{cache:"default"});if(!r.ok)throw Error("Weather HTTP");const data=await r.json(),t=data.properties.timeseries[0].data,details=t.instant.details,next=t.next_1_hours||t.next_6_hours||{},mm=Number(next.details?.precipitation_amount||0),code=next.summary?.symbol_code||"";const level=mm>=6||/heavyrain|thunder/.test(code)?3:mm>=2||/rain|showers/.test(code)?2:mm>0||/drizzle/.test(code)?1:0;
 $("temperature").textContent=Math.round(details.air_temperature)+"°C";$("conditions").textContent=["Sem chuva prevista","Previsão de chuva fraca","Previsão de chuva","Previsão de chuva forte"][level];$("humidity").textContent=details.relative_humidity+"%";$("wind").textContent=Math.round(details.wind_speed||0)+" m/s";$("weatherAlert").textContent=["Sem alerta de chuva na próxima hora.","Possibilidade de pista molhada.","⚠ Chuva prevista: atenção ao deslocamento.","⚠ Previsão de chuva intensa e possíveis alagamentos."][level];rain(level);
 }catch(e){$("conditions").textContent="Previsão indisponível";$("weatherAlert").textContent="Sem informação meteorológica atualizada";rain(0)}
}
function gpsSocket(){
 // GPS periódico moderado e consulta de fallback, sem criar mais um mapa.
 const socketApi=window.io;if(typeof socketApi!=="function")return;
 try{const sock=socketApi({transports:["websocket","polling"]});sock.on("posicao_motoboy_atualizada",d=>{if(!d?.id)return;let old=itemById(d.id);if(old){Object.assign(old,d);render()}else updateGps()});}catch(e){}
}
map.on("load",()=>{buildings();updateGps();});
render();updateGps();stopRender();weather();traffic();gpsSocket();
setInterval(updateGps,12000);setInterval(traffic,15*60*1000);setInterval(weather,20*60*1000);setInterval(phase,60*1000);
})();
