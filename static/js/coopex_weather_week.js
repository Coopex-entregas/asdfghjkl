/* Previsão de 7 dias ao clicar no clima do CoopEX Maps. Dados Open-Meteo. */
(()=>{
"use strict";
const find=()=>document.querySelector("#cxTopControlDock>.weather.glass")||document.querySelector("#ui .weather.glass");
let lastFetch=0,forecast=null,request=null,panel=null,source=null;
const ICON=(code)=>code===0?"☀️":code<=3?"⛅":code<=48?"🌫️":code<=67?"🌧️":code<=77?"🌨️":code<=82?"🌦️":code<=86?"🌨️":"⛈️";
const DESC=(code)=>code===0?"Ensolarado":code<=3?"Parcialmente nublado":code<=48?"Neblina":code<=67?"Chuva":code<=77?"Granizo/neve":code<=82?"Pancadas de chuva":code<=86?"Neve":"Trovoadas";
function position(){
 if(!panel||panel.hidden||!source)return;
 const r=source.getBoundingClientRect(),w=panel.offsetWidth||330;
 panel.style.left=Math.max(8,Math.min(window.innerWidth-w-8,r.left))+"px";
 const below=r.bottom+8;
 panel.style.top=(below+panel.offsetHeight<window.innerHeight-8?below:Math.max(8,r.top-panel.offsetHeight-8))+"px";
}
function close(){if(panel){panel.hidden=true;source?.setAttribute("aria-expanded","false")}}
function setup(){
 source=find();if(!source||document.getElementById("cxWeatherWeek"))return;
 source.setAttribute("role","button");source.setAttribute("tabindex","0");
 source.setAttribute("aria-expanded","false");
 source.setAttribute("aria-label","Abrir previsão do tempo dos próximos sete dias em Natal");
 source.title="Clique para ver a previsão dos próximos sete dias";
 panel=document.createElement("section");panel.id="cxWeatherWeek";panel.className="cx-weather-week glass";panel.hidden=true;
 panel.setAttribute("aria-label","Previsão meteorológica semanal para Natal RN");
 document.body.append(panel);
 async function load(){
  panel.textContent="Consultando previsão para os próximos sete dias…";position();
  if(forecast&&Date.now()-lastFetch<15*60*1000){show(forecast);return}
  if(request)return;
  try{
   request=fetch("https://api.open-meteo.com/v1/forecast?latitude=-5.7945&longitude=-35.2110&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum&timezone=America%2FFortaleza&forecast_days=7",{cache:"default"});
   const response=await request;if(!response.ok)throw Error("HTTP "+response.status);
   forecast=(await response.json()).daily;if(!forecast?.time?.length)throw Error("Previsão sem dados");
   lastFetch=Date.now();if(!panel.hidden)show(forecast);
  }catch(e){if(!panel.hidden)panel.textContent="Previsão indisponível no momento. Tente novamente.";console.warn("CoopEX clima:",e)}
  finally{request=null;position()}
 }
 function show(d){
  panel.replaceChildren();
  const head=document.createElement("div");head.className="cx-week-title";
  const title=document.createElement("strong");title.textContent="Natal · Próximos 7 dias";
  const closeBtn=document.createElement("button");closeBtn.type="button";closeBtn.textContent="×";closeBtn.setAttribute("aria-label","Fechar previsão");closeBtn.onclick=close;
  head.append(title,closeBtn);panel.append(head);
  d.time.forEach((date,i)=>{
   const row=document.createElement("div");row.className="cx-week-day";
   const weekday=document.createElement("strong");
   weekday.textContent=new Intl.DateTimeFormat("pt-BR",{weekday:"short",day:"2-digit",timeZone:"UTC"}).format(new Date(date+"T12:00:00Z")).replace(".","");
   const weather=document.createElement("span");weather.textContent=ICON(Number(d.weather_code?.[i]||0))+" "+DESC(Number(d.weather_code?.[i]||0));
   const temp=document.createElement("b");temp.textContent=Math.round(d.temperature_2m_min?.[i])+"° / "+Math.round(d.temperature_2m_max?.[i])+"°";
   const prob=document.createElement("small");prob.textContent="☂ "+Math.round(d.precipitation_probability_max?.[i]||0)+"%";
   row.append(weekday,weather,temp,prob);panel.append(row);
  });
  const foot=document.createElement("small");foot.className="cx-week-source";foot.textContent="Previsão aproximada do modelo Open-Meteo para Natal/RN. Não é radar por bairro.";panel.append(foot);
  position();
 }
 function toggle(){
  if(!panel.hidden){close();return}
  panel.hidden=false;source.setAttribute("aria-expanded","true");position();load();
 }
 source.addEventListener("click",toggle);
 source.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();toggle()}});
 document.addEventListener("pointerdown",e=>{if(!panel.hidden&&!panel.contains(e.target)&&!source.contains(e.target))close()});
 document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!panel.hidden){e.preventDefault();e.stopImmediatePropagation();close()}},true);
 window.addEventListener("resize",position);
 window.addEventListener("scroll",position,true);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",setup);else setup();
})();