/* Nuvens leves localizadas. Open-Meteo é MODELO, não radar de chuva observada.
   Cobertura adaptativa do mapa em território brasileiro: grade regional dinâmica,
   sem consultas a cada movimento e sem impedir visualização de GPS. */
(()=>{
"use strict";
let map=null,overlays=[],timer=0,loading=false,firstLoad=true,previousKey="",lastLoad=0;
let precipitationLayer=null;
function precipitationFrame(hoursAgo=1){
 const d=new Date(Date.now()-hoursAgo*3600000);
 return d.getUTCFullYear()+String(d.getUTCMonth()+1).padStart(2,"0")+
 String(d.getUTCDate()).padStart(2,"0")+String(d.getUTCHours()).padStart(2,"0");
}
function installPrecipitation(){
 if(!map||precipitationLayer||!google.maps.ImageMapType)return;
 // WeatherAPI oferece mosaicos globais por hora, gratuitos e sem chave.
 // O mosaico representa precipitação estimada por modelo, não radar observado.
 precipitationLayer=new google.maps.ImageMapType({
  name:"Precipitação estimada",
  tileSize:new google.maps.Size(256,256),
  opacity:.54,
  minZoom:2,maxZoom:9,
  getTile(coord,zoom,ownerDocument){
   const holder=ownerDocument.createElement("div");
   holder.style.cssText="width:256px;height:256px;pointer-events:none";
   if(zoom<2||zoom>9)return holder;
   const n=2**zoom,x=((coord.x%n)+n)%n,y=coord.y;
   if(y<0||y>=n)return holder;
   const img=ownerDocument.createElement("img");
   img.alt="";img.draggable=false;
   img.style.cssText="width:256px;height:256px;display:block;opacity:.82;pointer-events:none";
   const base="https://weathermaps.weatherapi.com/precip/tiles/";
   img.src=base+precipitationFrame(1)+"/"+zoom+"/"+x+"/"+y+".png";
   img.onerror=()=>{img.onerror=null;img.src=base+precipitationFrame(2)+"/"+zoom+"/"+x+"/"+y+".png"};
   holder.append(img);return holder;
  }
 });
 // A camada é associada ao Google Maps; não altera posições dos cooperados.
 map.overlayMapTypes.push(precipitationLayer);
 if(!document.getElementById("cxPrecipitationCredit")){
  const credit=document.createElement("div");credit.id="cxPrecipitationCredit";
  credit.innerHTML='Chuva prevista · <a href="https://www.weatherapi.com/" target="_blank" rel="noopener noreferrer">WeatherAPI</a> + <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a>';
  document.body.append(credit);
 }
}

const BRAZIL={south:-34,north:5.5,west:-74,east:-34};
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
function clean(){overlays.forEach(o=>o.setMap(null));overlays=[]}
function sample(){
 if(!map)return {key:"",locations:[]};
 const z=map.getZoom()||10,b=map.getBounds();
 if(!b||z<3||z>=15)return {key:"hidden",locations:[]};
 const ne=b.getNorthEast(),sw=b.getSouthWest();
 const south=Math.max(BRAZIL.south,sw.lat()),north=Math.min(BRAZIL.north,ne.lat());
 const west=Math.max(BRAZIL.west,sw.lng()),east=Math.min(BRAZIL.east,ne.lng());
 if(south>=north||west>=east)return {key:"outside",locations:[]};
 const rows=z<=7?6:z>=12?4:4,cols=z<=7?8:z>=12?5:5;
 const locations=[];
 for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
  const lat=south+(r+.5)/rows*(north-south),lng=west+(c+.5)/cols*(east-west);
  locations.push({lat:+lat.toFixed(4),lng:+lng.toFixed(4)});
 }
 // Aproximar cache a ~1/3 da grade, evitando chamadas a cada pequena mudança.
 const dl=(north-south)/rows,dx=(east-west)/cols;
 const key=[Math.round(south/(dl||.01)),Math.round(west/(dx||.01)),Math.round(z)].join(":");
 return {key,locations};
}
function render(data){
 clean();
 if(!map||!window.google?.maps)return;
 for(const row of data){
  if(row.rain<.08)continue; // Evitar símbolos para precipitação arredondada residual
  const overlay=new google.maps.OverlayView();
  overlay.onAdd=function(){
   const el=document.createElement("div");
   el.className="cx-cloud-location";
   el.title="Estimativa de precipitação "+row.rain.toFixed(1)+" mm · Open-Meteo (modelo, não radar)";
   el.style.opacity=String(clamp(.16+row.rain/11,.17,.43));
   const radians=(row.windDir||0)*Math.PI/180,dx=Math.sin(radians)*18,dy=-Math.cos(radians)*18;
   el.style.setProperty("--cx-cloud-wind-x",dx.toFixed(1)+"px");
   el.style.setProperty("--cx-cloud-wind-y",dy.toFixed(1)+"px");
   el.style.setProperty("--cx-cloud-wind-duration",clamp(78-row.windSpeed*3,23,85)+"s");
   el.innerHTML='<span class="cx-cloud-shape">☁</span>'+(row.rain>=.3?'<span class="cx-cloud-rain">╱╱╱</span>':"");
   this.node=el;this.getPanes().overlayLayer.append(el);
  };
  overlay.draw=function(){
   if(!this.node)return;
   const pixel=this.getProjection()?.fromLatLngToDivPixel(new google.maps.LatLng(row.lat,row.lng));
   if(!pixel)return;
   this.node.style.transform="translate("+Math.round(pixel.x)+"px,"+Math.round(pixel.y)+"px) translate(-50%,-50%)";
   const zoom=map.getZoom()||12;this.node.style.display=zoom<3||zoom>=15?"none":"";
  };
  overlay.onRemove=function(){this.node?.remove();this.node=null};
  overlay.setMap(map);overlays.push(overlay);
 }
}
async function refresh(){
 if(!map||loading||document.hidden)return;
 const {key,locations}=sample();
 if(!locations.length){previousKey=key;clean();return}
 if(!firstLoad&&key===previousKey&&Date.now()-lastLoad<20*60*1000)return;
 previousKey=key;firstLoad=false;loading=true;
 try{
  const url="https://api.open-meteo.com/v1/forecast?latitude="+locations.map(x=>x.lat).join(",")+
   "&longitude="+locations.map(x=>x.lng).join(",")+
   "&current=precipitation,cloud_cover,wind_speed_10m,wind_direction_10m&forecast_days=1&timezone=auto";
  const response=await fetch(url,{cache:"default"});
  if(!response.ok)throw Error("Precipitation "+response.status);
  const raw=await response.json(),parts=Array.isArray(raw)?raw:[raw];
  // Não aplicar dados de uma região se o mapa já mudou para outra.
  if(previousKey!==key)return;
  render(locations.map((point,i)=>({
   ...point,
   rain:Math.max(0,Number(parts[i]?.current?.precipitation||0)),
   windDir:Number(parts[i]?.current?.wind_direction_10m||0),
   windSpeed:Number(parts[i]?.current?.wind_speed_10m||0)
  })));
  lastLoad=Date.now();
 }catch(error){console.debug("Nuvens por região indisponíveis:",error.message);clean()}
 finally{loading=false}
}
function schedule(){
 clearTimeout(timer);
 timer=setTimeout(refresh,1100);
}
function setup(){
 if(map||!window.COOPEX_GOOGLE_NATIVE)return;
 map=window.COOPEX_GOOGLE_NATIVE;
 installPrecipitation();
 map.addListener("idle",schedule);
 map.addListener("zoom_changed",()=>{
  if((map.getZoom()||0)>=15)clean();
  schedule();
 });
 // As previsões são horárias: renovar os tiles sem recarregar a página.
 setInterval(()=>{
  if(document.hidden||!precipitationLayer)return;
  const index=map.overlayMapTypes.getArray().indexOf(precipitationLayer);
  if(index>=0)map.overlayMapTypes.removeAt(index);
  precipitationLayer=null;installPrecipitation();
 },60*60*1000);
 schedule();
 setInterval(refresh,20*60*1000);
 document.addEventListener("visibilitychange",()=>{if(!document.hidden)schedule()});
}
window.addEventListener("coopex:google-native-ready",setup);
if(document.readyState==="complete")setup();else window.addEventListener("load",setup);
})();