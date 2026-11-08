/* Camada leve de nuvens no Google Maps. Previsão aproximada, não radar. */
(()=>{
"use strict";
const sites=[["Natal Centro",-5.787,-35.207],["Lagoa Nova",-5.827,-35.212],["Ponta Negra",-5.882,-35.176],["Igapó",-5.755,-35.249],["Potengi",-5.748,-35.271],["Lagoa Azul",-5.723,-35.258],["Parnamirim",-5.914,-35.260],["Macaíba",-5.858,-35.350],["Extremoz",-5.703,-35.302],["Via Costeira",-5.836,-35.181]];
let map=null,overlays=[],loading=false,abort=null;
const valid=x=>Number.isFinite(+x);
function clear(){overlays.forEach(o=>o.setMap(null));overlays=[];}
function render(locations){
 if(!map||!window.google?.maps)return;clear();
 for(const record of locations){
  if(record.cloud<57&&record.rain<0.12)continue;
  const overlay=new google.maps.OverlayView();
  overlay.onAdd=function(){
   const el=document.createElement("div");el.className="cx-cloud-location";
   el.title=record.name+" · previsão aproximada · "+record.rain.toFixed(1)+" mm na última hora";
   el.style.opacity=String(Math.min(.52,Math.max(.15,record.cloud/230)));
   el.innerHTML='<span class="cx-cloud-shape">☁</span>'+(record.rain>=.12?'<span class="cx-cloud-rain">╱╱╱╱╱</span>':"");
   this.node=el;this.getPanes().overlayLayer.appendChild(el);
  };
  overlay.draw=function(){
   if(!this.node)return;const p=this.getProjection().fromLatLngToDivPixel(new google.maps.LatLng(record.lat,record.lng));
   if(!p)return;this.node.style.transform="translate("+Math.round(p.x)+"px,"+Math.round(p.y)+"px) translate(-50%,-50%)";
   const z=map.getZoom()||12;this.node.style.display=z<9||z>17?"none":"";
  };
  overlay.onRemove=function(){this.node?.remove();this.node=null};
  overlay.setMap(map);overlays.push(overlay);
 }
}
async function refresh(){
 if(loading||document.hidden)return;loading=true;
 try{
  const lat=sites.map(x=>x[1]).join(","),lon=sites.map(x=>x[2]).join(",");
  const url="https://api.open-meteo.com/v1/forecast?latitude="+lat+"&longitude="+lon+"&current=cloud_cover,precipitation,rain&forecast_days=1&timezone=America%2FFortaleza";
  const response=await fetch(url,{cache:"default"});if(!response.ok)throw Error("Weather API "+response.status);
  const rows=await response.json(),list=Array.isArray(rows)?rows:[rows];
  const data=sites.map((s,i)=>({name:s[0],lat:s[1],lng:s[2],rain:Number(list[i]?.current?.precipitation||0),cloud:Number(list[i]?.current?.cloud_cover||0)})).filter(x=>valid(x.rain)&&valid(x.cloud));
  render(data);
  const raining=data.filter(x=>x.rain>=.12);
  if(raining.length){const condition=document.getElementById("conditions");if(condition)condition.textContent="Chuva prevista: "+raining.slice(0,2).map(x=>x.name).join(", ")+(raining.length>2?" e região":"");}
 }catch(e){console.debug("Previsão localizada indisponível:",e.message);clear()}
 finally{loading=false}
}
function setup(){if(map||!window.COOPEX_GOOGLE_NATIVE)return;map=window.COOPEX_GOOGLE_NATIVE;refresh();setInterval(refresh,20*60*1000)}
window.addEventListener("coopex:google-native-ready",setup);if(document.readyState==="complete")setup();else window.addEventListener("load",setup);
})();