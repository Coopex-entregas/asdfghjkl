
(()=>{"use strict";
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const map=L.map("map",{zoomControl:false,preferCanvas:true,maxZoom:19,zoomSnap:.5,worldCopyJump:false}).setView([-5.7945,-35.2110],12);
const road=L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,maxNativeZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',keepBuffer:2,updateWhenIdle:true}).addTo(map);
L.control.zoom({position:"topright",zoomInTitle:"Aproximar",zoomOutTitle:"Afastar"}).addTo(map);
const satellite=L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",{maxZoom:19,maxNativeZoom:18,attribution:"Tiles © Esri — World Imagery",keepBuffer:2});
let items=Array.isArray(window.COOPEX_SEED)?window.COOPEX_SEED:[],filter="todos",selectedId=null,following=false,mode="road",fetching=false,activeSearch="",incidentList=[],lastWeather="",lastData="",lastCenter=false;
const markers=new Map(),iconCache=new Map();
const stops=[],stopPins=L.layerGroup().addTo(map);let selectedAddress=null,viewPoint=null;
function routeDest(){return selectedAddress||(selectedId&&valid(getItem(selectedId))?{lat:Number(getItem(selectedId).lat),lng:Number(getItem(selectedId).lng),label:getItem(selectedId).nome}:null);}
function renderStops(){
 const box=$("stopsList");box.replaceChildren();stopPins.clearLayers();
 stops.forEach((p,n)=>{const line=document.createElement("div");line.className="stop-row";const label=document.createElement("span");label.textContent=(n+1)+". "+p.label;const remove=document.createElement("button");remove.type="button";remove.textContent="×";remove.setAttribute("aria-label","Remover parada");remove.onclick=()=>{stops.splice(n,1);renderStops()};line.append(label,remove);box.append(line);L.marker([p.lat,p.lng]).addTo(stopPins).bindTooltip((n+1)+". "+p.label)});
 $("stopsCount").textContent=String(stops.length);
}
function addStop(p){if(stops.length>=3){$("addressMessage").textContent="O navegador aceita até 3 paradas intermediárias nesta opção.";return;}if(!p||!Number.isFinite(Number(p.lat)))return;stops.push({lat:Number(p.lat),lng:Number(p.lng),label:String(p.label||"Parada")});renderStops();$("addressMessage").textContent="Parada adicionada.";}
function openStreetView(){const p=viewPoint||routeDest();if(!p){$("addressMessage").textContent="Escolha uma rua ou um ponto do mapa.";return;}window.open("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint="+encodeURIComponent(p.lat+","+p.lng),"_blank","noopener,noreferrer");}
function openStopsRoute(){const dest=routeDest()||stops[stops.length-1];if(!dest){$("addressMessage").textContent="Escolha um destino ou cooperado.";return;}const intermediate=stops.filter(p=>p!==dest).slice(0,3);let url="https://www.google.com/maps/dir/?api=1&travelmode=driving&destination="+encodeURIComponent(dest.lat+","+dest.lng);if(intermediate.length)url+="&waypoints="+encodeURIComponent(intermediate.map(p=>p.lat+","+p.lng).join("|"));window.open(url,"_blank","noopener,noreferrer");}
map.on("click",e=>{viewPoint={lat:e.latlng.lat,lng:e.latlng.lng,label:"Ponto selecionado"};});

const norm=s=>String(s||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
const online=i=>i?.online===true||["livre","em_corrida"].includes(i?.status);
const status=i=>!online(i)?"offline":i.status==="ocioso"?"ocioso":"online";
const colors={online:"#14d97b",ocioso:"#ff9f26",offline:"#fa4a58"};
const names={online:"Online",ocioso:"Ocioso",offline:"Offline"};
const valid=i=>i&&Number.isFinite(Number(i.lat))&&Number.isFinite(Number(i.lng))&&i.lat!==null&&i.lng!==null&&Number(i.lat)<0&&Number(i.lng)<0;
const initials=n=>String(n||"C").trim().split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0].toUpperCase()).join("");
const safeUrl=u=>typeof u==="string"&&/^https:\/\/[^"'<> ]+$/i.test(u)?u:"";
const visible=i=>(filter==="todos"||status(i)===filter)&&(!activeSearch||norm(i.nome).includes(norm(activeSearch)));
function pointIcon(i,n){const state=status(i),active=String(i.id)===String(selectedId),key=initials(i.nome)+"-"+state+"-"+n+"-"+active;let ico=iconCache.get(key);if(!ico){ico=L.divIcon({className:"pin",iconSize:[48,58],iconAnchor:[24,51],html:'<div class="pin-body'+(active?' selected':'')+'" style="--status:'+colors[state]+'">'+esc(initials(i.nome))+(n>1?'<span class="pin-count">'+n+'</span>':"")+'</div>'});iconCache.set(key,ico);}return ico;}
function getItem(id){return items.find(i=>String(i.id)===String(id))}
function renderStats() {for(const s of ["online","ocioso","offline"])$(s+"Count").textContent=items.filter(i=>status(i)===s).length;}
function groups(){const a=[];for(const i of items.filter(visible).filter(valid)){let g=a.find(g=>Math.abs(Number(g[0].lat)-Number(i.lat))<.00009&&Math.abs(Number(g[0].lng)-Number(i.lng))<.00009);if(g)g.push(i);else a.push([i]);}return a;}
function render(){
 renderStats();
 const gs=groups(),seen=new Set();
 for(const g of gs){const first=g[0],key=String(first.id),coords=[Number(first.lat),Number(first.lng)];seen.add(key);
 let m=markers.get(key);
 const chosen=g.find(i=>String(i.id)===String(selectedId))||first;
 if(!m){m=L.marker(coords,{icon:pointIcon(chosen,g.length)}).addTo(map);markers.set(key,m)}else{if(m.getLatLng().distanceTo(L.latLng(coords))>1)m.setLatLng(coords);m.setIcon(pointIcon(chosen,g.length))}
 m.off("click");m.on("click",()=>{const current=g.findIndex(i=>String(i.id)===String(selectedId));choose(g[(current+1)%g.length].id);});
 m.bindTooltip(g.length>1?g.length+" cooperados aqui":first.nome,{direction:"top",offset:[0,-42]});
 }
 for(const [id,m]of markers){if(!seen.has(id)){map.removeLayer(m);markers.delete(id)}}
 const sel=getItem(selectedId);if(sel&&valid(sel)&&following){map.panTo([Number(sel.lat),Number(sel.lng)],{animate:false});}
 if(sel)showDetails(sel);
}
function choose(id){selectedId=String(id);const i=getItem(id);if(!i)return;showDetails(i);render();if(valid(i))map.setView([Number(i.lat),Number(i.lng)],Math.max(14,map.getZoom()),{animate:true});}
function showDetails(i){
 const state=status(i),title=names[state];
 $("details").classList.add("visible");$("personAvatar").textContent=initials(i.nome);$("personAvatar").style.background=colors[state];
 const pic=safeUrl(i.foto_url);if(pic){const im=document.createElement("img");im.src=pic;im.alt="Foto do cooperado";im.onerror=()=>{im.remove();$("personAvatar").textContent=initials(i.nome)};$("personAvatar").replaceChildren(im)}
 $("personName").textContent=i.nome||"Cooperado";$("personState").textContent=title;$("personState").style.background=colors[state]+"55";
 $("personArea").textContent=i.endereco||"Natal/RN";$("personSeen").textContent=i.ultima_atualizacao||"Sem sinal";$("personSpeed").textContent=i.speed_kmh==null?"—":Number(i.speed_kmh).toLocaleString("pt-BR",{maximumFractionDigits:1})+" km/h";
 $("personJobs").textContent=Number(i.entregas_abertas||0);$("follow").disabled=!valid(i);$("follow").textContent=following?"Acompanhando":"◎ Seguir";$("stop").disabled=!following;
 let nearby=incidentList.filter(f=>distanceKm(i,f)<1.5);
 const alert=$("nearby");alert.classList.toggle("visible",!!nearby.length);alert.textContent=nearby.length?"⚠ "+nearby.length+" ocorrência(s) próximas à última posição GPS. Isso não confirma que estejam na rota.":"";
 const stack=groups().find(g=>g.some(x=>String(x.id)===String(i.id)))||[];
 const host=$("stack");host.replaceChildren();if(stack.length>1){const label=document.createElement("small");label.textContent=stack.length+" cooperados nesta localização";host.append(label);stack.forEach(x=>{const b=document.createElement("button");b.textContent=initials(x.nome);b.title=x.nome;b.onclick=()=>choose(x.id);host.append(b)})}
}
function distanceKm(i,f){if(!valid(i)||!f.geometry)return 999;let c=f.geometry.coordinates;if(f.geometry.type==="LineString")c=c[Math.floor(c.length/2)];if(f.geometry.type==="MultiLineString")c=c[0]?.[Math.floor(c[0].length/2)];if(!Array.isArray(c)||!Number.isFinite(Number(c[0])))return 999;const R=6371,lat1=Number(i.lat)*Math.PI/180,lat2=Number(c[1])*Math.PI/180,dLat=lat2-lat1,dLng=(Number(c[0])-Number(i.lng))*Math.PI/180,a=Math.sin(dLat/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin(dLng/2)**2;return 2*R*Math.asin(Math.sqrt(a))}
async function poll(){
 if(fetching||document.hidden)return;fetching=true;
 try{const res=await fetch("/mapa_motoboys?format=json",{cache:"no-store",headers:{Accept:"application/json"}});if(!res.ok)throw Error("GPS indisponível");const arr=await res.json();if(Array.isArray(arr)){items=arr;if(!lastCenter){const g=groups();if(g.length){const b=L.latLngBounds(g.map(x=>[Number(x[0].lat),Number(x[0].lng)]));map.fitBounds(b.pad(.18),{maxZoom:13});lastCenter=true}}render();}}
 catch(e){$("gpsStatus").textContent="GPS temporariamente indisponível"}finally{fetching=false}
}
$("search").addEventListener("input",e=>{activeSearch=e.target.value.trim();const box=$("results");box.replaceChildren();const match=items.filter(i=>norm(i.nome).includes(norm(activeSearch))).slice(0,10);if(activeSearch&&match.length){match.forEach(i=>{let b=document.createElement("button");b.className="result";b.innerHTML='<span style="color:'+colors[status(i)]+'">●</span><span><strong>'+esc(i.nome)+'</strong><small>'+names[status(i)]+'</small></span>';b.onclick=()=>{activeSearch="";$("search").value="";box.classList.remove("open");choose(i.id)};box.append(b)});box.classList.add("open")}else box.classList.remove("open");render();});
document.querySelectorAll("[data-filter]").forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll("[data-filter]").forEach(x=>x.classList.toggle("active",x===b));render()});
$("detailsClose").onclick=()=>{$("details").classList.remove("visible");selectedId=null;following=false;render()};
$("follow").onclick=()=>{following=true;render()};$("stop").onclick=()=>{following=false;render()};
$("toolbarToggle").onclick=()=>{$("toolbar").classList.toggle("open");$("toolbarToggle").setAttribute("aria-expanded",String($("toolbar").classList.contains("open")))};
$("mapStreet").onclick=()=>{if(map.hasLayer(satellite))map.removeLayer(satellite);if(!map.hasLayer(road))road.addTo(map);mode="road";$("toolbar").classList.remove("open")};
$("mapSatellite").onclick=()=>{if(map.hasLayer(road))map.removeLayer(road);satellite.addTo(map);mode="sat";$("toolbar").classList.remove("open")};
$("fit").onclick=()=>{const g=groups();if(g.length)map.fitBounds(L.latLngBounds(g.map(x=>[Number(x[0].lat),Number(x[0].lng)])).pad(.15),{maxZoom:15});$("toolbar").classList.remove("open")};
$("refresh").onclick=()=>{poll();$("toolbar").classList.remove("open")};
$("locate").onclick=()=>{map.locate({setView:true,maxZoom:15});$("toolbar").classList.remove("open")};
$("addressToggle").onclick=()=>{$("addressPanel").hidden=!$("addressPanel").hidden;if(!$("addressPanel").hidden)$("address").focus()};
$("addressGo").onclick=async()=>{let q=$("address").value.trim();if(q.length<3)return;try{const r=await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=4&countrycodes=br&q="+encodeURIComponent(q+", Natal, RN"));const a=await r.json();if(!a.length){$("addressMessage").textContent="Endereço não localizado";return;}selectedAddress={lat:Number(a[0].lat),lng:Number(a[0].lon),label:a[0].display_name};viewPoint=selectedAddress;map.setView([selectedAddress.lat,selectedAddress.lng],16);$("addressMessage").textContent="Endereço localizado. Adicione uma parada ou abra a visão de rua.";}catch(e){$("addressMessage").textContent="Pesquisa indisponível"}};
$("address").onkeydown=e=>{if(e.key==="Enter")$("addressGo").click()};
$("openNav").onclick=openStopsRoute;
$("routeOpen").onclick=openStopsRoute;
$("streetView").onclick=openStreetView;
$("streetTool").onclick=openStreetView;
$("addStop").onclick=()=>addStop(selectedAddress||viewPoint||routeDest());
$("stopsClear").onclick=()=>{stops.length=0;renderStops();};
$("addressClose").onclick=()=>{$("addressPanel").hidden=true;};
renderStops();
const incidentLayer=L.geoJSON(null,{style:{color:"#fa5b47",weight:5,opacity:.75},pointToLayer:(f,latlng)=>L.circleMarker(latlng,{radius:7,color:"#fff",fillColor:"#e85d30",fillOpacity:1,weight:2}),onEachFeature:(f,l)=>{const d=f.properties?.description||"Ocorrência";l.bindPopup("TomTom Traffic: "+esc(d))}}).addTo(map);
async function traffic(){try{let r=await fetch("/api/coopex/tomtom/incidentes",{cache:"no-store"}),d=await r.json();if(!r.ok||!d.ok){const code=d.code||"";const err=Error(d.error||"Sem conexão");err.code=code;err.status=d.upstream_status;throw err;}incidentList=Array.isArray(d.features)?d.features:[];incidentLayer.clearLayers();incidentLayer.addData({type:"FeatureCollection",features:incidentList});$("trafficText").textContent=incidentList.length+" ocorrências reportadas na região";$("trafficStatus").textContent="TomTom conectado";$("trafficStatus").style.color="#8cffe1";if(selectedId){const item=getItem(selectedId);if(item)showDetails(item);}}catch(e){$("trafficStatus").textContent="Trânsito indisponível";$("trafficStatus").style.color="#ffca82";$("trafficText").textContent=e.code==="KEY_REJECTED"?"TomTom recusou a chave do Render (HTTP "+e.status+"). É necessária uma chave exclusiva de servidor, sem restrição de domínio.":e.code==="MISSING_KEY"?"Falta configurar TOMTOM_API_KEY no serviço Render.":e.code==="QUOTA"?"Limite de consultas da TomTom atingido.":"Não foi possível consultar ocorrências em tempo real.";}}
function phase(){const hr=Number(new Intl.DateTimeFormat("en-US",{timeZone:"America/Fortaleza",hour:"2-digit",hour12:false}).format(new Date()));document.body.classList.toggle("is-night",hr>=18||hr<6);}
const weatherDots=$("weatherFx");
function rain(level){document.body.classList.toggle("is-rain",level>0);weatherDots.replaceChildren();if(matchMedia("(prefers-reduced-motion: reduce)").matches)return;const n=[0,9,23,45][level];const f=document.createDocumentFragment();for(let i=0;i<n;i++){let d=document.createElement("i");d.className="drop";d.style.left=Math.random()*100+"%";d.style.animationDuration=.8+Math.random()*1.3+"s";d.style.animationDelay=-Math.random()*3+"s";f.append(d)}weatherDots.append(f)}
async function weather(){try{const r=await fetch("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=-5.7945&lon=-35.2110");if(!r.ok)throw Error();const d=await r.json(),p=d.properties.timeseries[0].data,now=p.instant.details,hour=p.next_1_hours||p.next_6_hours||{},mm=Number(hour.details?.precipitation_amount||0),code=hour.summary?.symbol_code||"";const level=mm>=6||/heavyrain|thunder/.test(code)?3:mm>=2||/rain|showers/.test(code)?2:mm>0||/drizzle/.test(code)?1:0;$("temperature").textContent=Math.round(now.air_temperature)+"°C";$("conditions").textContent=["Sem chuva prevista","Previsão de chuva fraca","Previsão de chuva","Previsão de chuva forte"][level];$("humidity").textContent=now.relative_humidity+"%";$("wind").textContent=Math.round(now.wind_speed||0)+" m/s";$("weatherAlert").textContent=level===3?"⚠ Previsão de chuva intensa. Atenção à visibilidade e alagamentos.":level===2?"⚠ Chuva prevista: o deslocamento pode ficar mais lento.":level===1?"Pista possivelmente molhada. Redobre a atenção.":"Sem alerta de chuva na próxima hora.";rain(level);}catch(e){$("conditions").textContent="Previsão temporariamente indisponível";rain(0)}}
phase();render();poll();weather();traffic();setInterval(poll,10000);setInterval(weather,20*60*1000);setInterval(traffic,15*60*1000);setInterval(phase,60000);
})();
