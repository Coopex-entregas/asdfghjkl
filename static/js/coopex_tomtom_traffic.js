/* TomTom Traffic — ocorrências reais, protegidas por proxy no Render. */
(function(){
"use strict";
if(typeof map==="undefined" || !window.L)return;
const banner=document.createElement("div");
banner.id="cx-tt-status";banner.className="card";banner.textContent="🚦 TomTom: aguardando informações de trânsito";
document.querySelector(".overlay").appendChild(banner);
const layer=L.geoJSON(null,{
  style:()=>({color:"#e5483d",weight:5,opacity:.88}),
  pointToLayer:(feature,latlng)=>{
    const code=Number(feature.properties?.category);
    const colors={1:"#ed493d",2:"#ed493d",3:"#efac23",4:"#f59c32",5:"#e63946",6:"#df3d36",7:"#f7a929",8:"#f3a52c",9:"#f59c32",10:"#e63946",11:"#e63946",12:"#ed493d",13:"#ed493d",14:"#efac23"};
    return L.circleMarker(latlng,{radius:8,weight:2,color:"#fff",fillColor:colors[code]||"#ef8733",fillOpacity:1});
  },
  onEachFeature:(f,l)=>{
    const p=f.properties||{};
    const node=document.createElement("div");
    const strong=document.createElement("strong");strong.textContent="🚦 "+(p.description||"Ocorrência no trânsito");node.append(strong);
    if(p.from||p.to){const place=document.createElement("p");place.textContent=[p.from,p.to].filter(Boolean).join(" → ");node.append(place);}
    const attribution=document.createElement("small");attribution.textContent="Fonte: TomTom Traffic";node.append(attribution);
    l.bindPopup(node);
  }
}).addTo(map);
function setStatus(msg,ok){banner.textContent=msg;banner.classList.toggle("cx-tt-failed",ok===false);banner.title=ok===false?"A chave TomTom está recusando a consulta do servidor Render. Configure uma chave exclusiva para servidor, sem restrição de domínio, guardada somente em TOMTOM_API_KEY.":"";}
async function load(){
 try{
  const r=await fetch("/api/coopex/tomtom/incidentes",{credentials:"same-origin",cache:"no-store"});
  const data=await r.json();
  if(!r.ok||!data.ok)throw Error(data.error||"Falha ao receber o trânsito");
  layer.clearLayers();
  layer.addData({type:"FeatureCollection",features:data.features||[]});
  const n=(data.features||[]).length;
  setStatus("🚦 Trânsito • "+n+" ocorrência"+(n===1?"":"s"),true);
 }catch(e){setStatus("🚦 Trânsito indisponível",false);console.warn("COOPEX TomTom:",e.message);}
}
load();setInterval(load,15*60*1000);
})();
