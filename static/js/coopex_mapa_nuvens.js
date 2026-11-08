/* CoopEX Maps: condições por região do Brasil.
   Fonte Open-Meteo: estimativas meteorológicas, não observações de radar. */
(()=>{
"use strict";
const BOX={s:-34,n:5.5,w:-74,e:-34};
let map=null,overlays=[],timer=null,busy=false,latest="",lastTime=0;
const cache=new Map();
const emoji={sun:"☀️",partly:"🌤️",cloud:"☁️",rain:"🌧️",storm:"⛈️",snow:"🌨️",fog:"🌫️"};
const names={sun:"Sol",partly:"Sol entre nuvens",cloud:"Nublado",rain:"Chuva prevista",storm:"Trovoada prevista",snow:"Neve prevista",fog:"Névoa"};
function type(code,mm,cover){
 if([71,73,75,77,85,86].includes(code))return "snow";
 if([95,96,99].includes(code))return "storm";
 if(mm>.08||[51,53,55,56,57,61,63,65,66,67,80,81,82].includes(code))return "rain";
 if([45,48].includes(code))return "fog";
 if(code===3||cover>=80)return "cloud";
 if(code===1||code===2||cover>=28)return "partly";
 return "sun";
}
function clear(){for(const o of overlays)o.setMap(null);overlays=[]}
function grid(){
 const z=map?.getZoom()||0,bounds=map?.getBounds();
 if(!bounds||z<3||z>=16)return {key:"hidden",points:[]};
 const sw=bounds.getSouthWest(),ne=bounds.getNorthEast();
 const south=Math.max(BOX.s,sw.lat()),north=Math.min(BOX.n,ne.lat());
 const west=Math.max(BOX.w,sw.lng()),east=Math.min(BOX.e,ne.lng());
 if(south>=north||west>=east)return {key:"outside",points:[]};
 const rows=z<=6?6:z<=9?5:4,columns=z<=6?7:z<=9?6:5;
 const dy=(north-south)/rows,dx=(east-west)/columns,points=[];
 for(let y=0;y<rows;y++)for(let x=0;x<columns;x++)
  points.push({lat:+(south+(y+.5)*dy).toFixed(3),lng:+(west+(x+.5)*dx).toFixed(3)});
 return {z,key:points.map(p=>p.lat+","+p.lng).join("|"),points};
}
function draw(items){
 clear();
 for(const item of items){
  const kind=type(item.code,item.mm,item.cloud);
  const overlay=new google.maps.OverlayView();
  overlay.onAdd=function(){
   const el=document.createElement("span");el.className="cx-weather-map-symbol cx-weather-"+kind;
   el.textContent=emoji[kind];
   el.title=names[kind]+" · previsão Open-Meteo, não radar";
   el.style.setProperty("--w-x",(Math.sin(item.wind*Math.PI/180)*15).toFixed(1)+"px");
   el.style.setProperty("--w-y",(-Math.cos(item.wind*Math.PI/180)*15).toFixed(1)+"px");
   this.el=el;this.getPanes().overlayLayer.append(el);
  };
  overlay.draw=function(){
   if(!this.el)return;
   const pixel=this.getProjection()?.fromLatLngToDivPixel({lat:item.lat,lng:item.lng});
   if(pixel){this.el.style.left=Math.round(pixel.x)+"px";this.el.style.top=Math.round(pixel.y)+"px";}
  };
  overlay.onRemove=function(){this.el?.remove();this.el=null};
  overlay.setMap(map);overlays.push(overlay);
 }
}
async function refresh(){
 if(!map||busy||document.hidden)return;
 const {points,key}=grid();if(!points.length){latest=key;clear();return}
 if(key===latest&&Date.now()-lastTime<15*60*1000)return;
 const cached=cache.get(key);
 if(cached&&Date.now()-cached.when<15*60*1000){latest=key;lastTime=Date.now();draw(cached.items);return}
 busy=true;latest=key;
 try{
  const q=new URLSearchParams({
   latitude:points.map(p=>p.lat).join(","),
   longitude:points.map(p=>p.lng).join(","),
   current:"weather_code,cloud_cover,precipitation,wind_direction_10m",
   forecast_days:"1",timezone:"UTC"
  });
  const response=await fetch("https://api.open-meteo.com/v1/forecast?"+q,{cache:"default"});
  if(!response.ok)throw Error("Open-Meteo "+response.status);
  const raw=await response.json(),rows=Array.isArray(raw)?raw:[raw];
  if(key!==grid().key)return;
  const items=points.map((p,i)=>({
   ...p,code:Number(rows[i]?.current?.weather_code??0),
   cloud:Number(rows[i]?.current?.cloud_cover??0),
   mm:Number(rows[i]?.current?.precipitation??0),
   wind:Number(rows[i]?.current?.wind_direction_10m??0)
  }));
  cache.set(key,{when:Date.now(),items});
  if(cache.size>9)cache.delete(cache.keys().next().value);
  lastTime=Date.now();draw(items);
 }catch(e){console.warn("CoopEX: mapa meteorológico indisponível",e);lastTime=0}
 finally{busy=false;if(grid().key!==key)schedule()}
}
function schedule(){clearTimeout(timer);timer=setTimeout(refresh,800)}
function start(){
 if(map||!window.COOPEX_GOOGLE_NATIVE)return;
 map=window.COOPEX_GOOGLE_NATIVE;
 map.addListener("idle",schedule);
 map.addListener("zoom_changed",schedule);
 document.addEventListener("visibilitychange",()=>{if(!document.hidden)schedule()});
 setInterval(()=>{lastTime=0;schedule()},20*60*1000);
 schedule();
}
window.addEventListener("coopex:google-native-ready",start);
if(document.readyState==="complete")start();else window.addEventListener("load",start);
})();