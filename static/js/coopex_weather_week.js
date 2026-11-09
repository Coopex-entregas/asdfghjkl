/* Previsão de 7 dias ao clicar no clima do CoopEX Maps. Dados Open-Meteo. */
(()=>{
"use strict";
const find=()=>document.querySelector("#cxTopControlDock>.weather.glass")||document.querySelector("#ui .weather.glass");
let lastFetch=0,forecast=null,request=null,panel=null,source=null;
const readWeather=async()=>{
 const sources=["/api/coopex/clima/previsao","https://api.open-meteo.com/v1/forecast?latitude=-5.7945&longitude=-35.2110&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,precipitation,weather_code,is_day&hourly=precipitation_probability,weather_code,temperature_2m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum&timezone=America%2FFortaleza&forecast_days=7"];
 let error=null;
 for(const url of sources){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),9000);
  try{
   const response=await fetch(url,{cache:"no-store",credentials:url.startsWith("/")?"same-origin":"omit",signal:controller.signal});
   if(!response.ok)throw Error("HTTP "+response.status);
   const data=await response.json();
   if(!data?.current||!Array.isArray(data?.daily?.time))throw Error("Dados meteorológicos incompletos");
   return data;
  }catch(e){error=e}
  finally{clearTimeout(timeout)}
 }
 throw error||Error("Clima temporariamente indisponível");
};
const ICON=(code,isDay=true)=>code===0?(isDay?"☀️":"🌙"):code<=3?(isDay?"⛅":"☁️"):code<=48?"🌫️":code<=67?"🌧️":code<=77?"🌨️":code<=82?"🌦️":code<=86?"🌨️":"⛈️";
const DESC=(code,isDay=true)=>code===0?(isDay?"Céu limpo":"Céu limpo à noite"):code<=3?(isDay?"Parcialmente nublado":"Nuvens à noite"):code<=48?"Neblina":code<=67?"Chuva possível":code<=77?"Granizo/neve":code<=82?"Pancadas possíveis":code<=86?"Neve":"Trovoadas";
const localHour=()=>Number(new Intl.DateTimeFormat("en-US",{timeZone:"America/Fortaleza",hour:"2-digit",hour12:false}).format(new Date()));
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
 const waiting=document.getElementById("conditions");
 if(waiting)waiting.textContent="Atualizando clima…";
 source.setAttribute("role","button");source.setAttribute("tabindex","0");
 source.style.cursor="pointer";source.style.pointerEvents="auto";
 source.setAttribute("aria-expanded","false");
 source.setAttribute("aria-label","Abrir previsão do tempo dos próximos sete dias em Natal");
 source.title="Clique para ver a previsão dos próximos sete dias";
 panel=document.createElement("section");panel.id="cxWeatherWeek";panel.className="cx-weather-week glass";panel.hidden=true;
 panel.style.cssText="position:fixed;z-index:3000;width:min(430px,calc(100vw - 16px));max-height:75dvh;overflow-y:auto;padding:16px;background:rgba(242,249,255,.97);color:#14324e;border:1px solid #b9d6ed;border-radius:18px;box-shadow:0 12px 40px #143b5a55;font:13px/1.45 system-ui,sans-serif;pointer-events:auto";
 panel.setAttribute("aria-label","Previsão meteorológica semanal para Natal RN");
 document.body.append(panel);
 async function load(){
  if(!panel.hidden){panel.textContent="Atualizando previsão meteorológica…";position();}
  if(forecast&&Date.now()-lastFetch<10*60*1000){show(forecast);return}
  if(request)return;
  try{
   request=readWeather();
   const data=await request;forecast=data;if(!forecast?.daily?.time?.length)throw Error("Previsão sem dados");
   lastFetch=Date.now();show(forecast);
  }catch(e){
   const condition=document.getElementById("conditions");if(condition)condition.textContent="Clima indisponível · toque para tentar";
   const temperature=document.getElementById("temperature");if(temperature&&temperature.textContent.trim()==="—")temperature.textContent="—°C";
   if(!panel.hidden)panel.textContent="Previsão indisponível. Feche e abra para tentar novamente.";
   console.warn("CoopEX clima:",e)
  }
  finally{request=null;position()}
 }
 function show(data){
  const d=data.daily,h=data.hourly||{},current=data.current||{};
  panel.replaceChildren();
  const head=document.createElement("div");head.className="cx-week-title";
  const title=document.createElement("strong");title.textContent="Natal · Previsão do tempo";
  const closeBtn=document.createElement("button");closeBtn.type="button";closeBtn.textContent="×";closeBtn.setAttribute("aria-label","Fechar previsão");closeBtn.onclick=close;
  head.append(title,closeBtn);panel.append(head);
  const nowCode=Number(current.weather_code);
  const dayFlag=current.is_day===1||current.is_day===true;
  const nowDescription=Number.isFinite(nowCode)?DESC(nowCode,dayFlag):"Não disponível";
  const weatherIcon=document.getElementById("weatherIcon"),temperature=document.getElementById("temperature");
  if(weatherIcon){weatherIcon.textContent=Number.isFinite(nowCode)?ICON(nowCode,dayFlag):"—";weatherIcon.title=nowDescription;weatherIcon.classList.toggle("cx-moon",!dayFlag&&nowCode<=3);weatherIcon.classList.toggle("cx-sun-hot",dayFlag&&nowCode===0&&Number(current.temperature_2m)>=32);weatherIcon.classList.toggle("cx-sun-warm",dayFlag&&nowCode===0&&Number(current.temperature_2m)<32);}
  if(temperature)temperature.textContent=Number.isFinite(Number(current.temperature_2m))?Math.round(current.temperature_2m)+"°C":"—";
  const humidity=document.getElementById("humidity"),wind=document.getElementById("wind"),alert=document.getElementById("weatherAlert");
  if(humidity)humidity.textContent=Number.isFinite(Number(current.relative_humidity_2m))?Math.round(current.relative_humidity_2m)+"%":"—";
  if(wind)wind.textContent=Number.isFinite(Number(current.wind_speed_10m))?Math.round(current.wind_speed_10m)+" km/h":"—";
  if(alert)alert.textContent=Number(current.precipitation)>0?"Precipitação indicada no período atual. Cuidado ao dirigir.":"Previsão meteorológica regional, sem confirmação por bairro.";
  const observedAt=current.time?String(current.time).slice(11,16):"";
  const observedText=observedAt?" · Atualizado às "+observedAt+"h (Natal)":"";
  const topIcon=source.querySelector("#weatherIcon");
  if(topIcon)topIcon.title=nowDescription+observedText;
  const topCondition=source.querySelector("#conditions");
  if(topCondition)topCondition.textContent=nowDescription+" agora";
  const currentText=document.createElement("div");currentText.className="cx-weather-now";
  currentText.textContent="Agora: "+nowDescription+(Number.isFinite(Number(current.temperature_2m))?" · "+Math.round(current.temperature_2m)+"°C":"")+observedText;
  panel.append(currentText);
  const today=(new Intl.DateTimeFormat("en-CA",{timeZone:"America/Fortaleza",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date()));
  const hourly=[];
  for(let i=0;i<(h.time||[]).length;i++){
   const iso=String(h.time[i]),day=iso.slice(0,10),hour=Number(iso.slice(11,13));
   if(day===today&&hour>=18&&hour<=22)hourly.push({hour,prob:Number(h.precipitation_probability?.[i]),code:Number(h.weather_code?.[i])});
  }
  const headline=document.createElement("div");headline.className="cx-weather-evening";
  const header=document.createElement("strong");header.textContent="Hoje · 18h às 22h";
  headline.append(header);
  if(hourly.length){
   const valid=hourly.filter(x=>Number.isFinite(x.prob));
   const max=valid.length?Math.max(...valid.map(x=>x.prob)):null;
   const statement=document.createElement("p");
   statement.textContent=max===null?"Probabilidade indisponível":max<20?"Baixa chance de chuva no período (até "+max+"%)":max<50?"Existe possibilidade de chuva (até "+max+"%)":"Chance elevada de chuva em parte do período (até "+max+"%)";
   headline.append(statement);
   const hours=document.createElement("div");hours.className="cx-weather-hours";
   hourly.forEach(x=>{
    const item=document.createElement("div");item.className="cx-weather-hour";
    const at=document.createElement("strong");at.textContent=String(x.hour).padStart(2,"0")+"h";
    const icon=document.createElement("span");icon.textContent=ICON(x.code,false);
    const prob=document.createElement("small");prob.textContent=Number.isFinite(x.prob)?x.prob+"%":"—";
    item.append(at,icon,prob);hours.append(item);
   });
   headline.append(hours);
  }else{const note=document.createElement("p");note.textContent="Previsão horária das 18h às 22h disponível ao longo do dia.";headline.append(note)}
  panel.append(headline);
  const weekTitle=document.createElement("strong");weekTitle.className="cx-week-subtitle";weekTitle.textContent="Próximos 7 dias";panel.append(weekTitle);
  d.time.forEach((date,i)=>{
   const row=document.createElement("div");row.className="cx-week-day";
   const weekday=document.createElement("strong");
   weekday.textContent=new Intl.DateTimeFormat("pt-BR",{weekday:"short",day:"2-digit",timeZone:"UTC"}).format(new Date(date+"T12:00:00Z")).replace(".","");
   const code=Number(d.weather_code?.[i]||0);
   const percent=Math.round(d.precipitation_probability_max?.[i]||0);
   const weather=document.createElement("span");
   weather.textContent=ICON(code,true)+" "+(percent<20&&code>=51?"Chuva pouco provável":DESC(code,true));
   const temp=document.createElement("b");temp.textContent=Math.round(d.temperature_2m_min?.[i])+"° / "+Math.round(d.temperature_2m_max?.[i])+"°";
   const prob=document.createElement("small");prob.textContent="☂ "+percent+"%";
   row.append(weekday,weather,temp,prob);panel.append(row);
  });
  const disclaimer=document.createElement("small");disclaimer.className="cx-weather-disclaimer";
  disclaimer.textContent="Probabilidades são previsões da Open-Meteo, não confirmação de chuva no bairro. A chance diária representa o maior percentual previsto em uma hora.";
  panel.append(disclaimer);
  position();
 }

 function toggle(){
  if(!panel.hidden){close();return}
  panel.hidden=false;source.setAttribute("aria-expanded","true");window.dispatchEvent(new Event("coopex:weather-panel-open"));position();load();
 }
 source.addEventListener("click",toggle);
 source.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();toggle()}});
 document.addEventListener("pointerdown",e=>{if(!panel.hidden&&!panel.contains(e.target)&&!source.contains(e.target))close()});
 document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!panel.hidden){e.preventDefault();e.stopImmediatePropagation();close()}},true);
 window.addEventListener("coopex:traffic-panel-open",close);
 window.addEventListener("resize",position);
 window.addEventListener("scroll",position,true);
 // Uma fonte meteorológica para a faixa superior e para a previsão.
 source.style.touchAction="manipulation";
 // Atualizações periódicas sem consultar a API a cada movimento do mapa.
 load();
 // Mesmo quando o provedor estiver bloqueado, nunca deixar o mapa mostrando "Consultando".
 setTimeout(()=>{
  const condition=document.getElementById("conditions");
  if(condition&&/consultando|atualizando/i.test(condition.textContent||""))condition.textContent="Clima indisponível · toque para tentar";
 },22000);
 setInterval(()=>{if(Date.now()-lastFetch>=10*60*1000)load()},10*60*1000);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",setup);else setup();
})();