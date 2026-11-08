/* Autocomplete de partida, destino e paradas: Google Places API (New). */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const panel=$("addressPanel"),inputs=[$("cxRouteOrigin"),$("cxDestinationMirror"),$("cxStopInput")].filter(Boolean);
if(!panel||inputs.length!==3)return;
const menu=document.createElement("div");
menu.id="cxRouteSuggestions";menu.setAttribute("role","listbox");menu.hidden=true;document.body.append(menu);
let timer=null,sequence=0,currentInput=null,placesDisabled=false,session=null,items=[];
function close(){menu.hidden=true;menu.replaceChildren();currentInput=null;items=[]}
function position(input){
 const box=input.getBoundingClientRect(),width=Math.min(450,Math.max(225,box.width+30));
 menu.style.width=width+"px";menu.style.left=Math.max(6,Math.min(window.innerWidth-width-6,box.left))+"px";
 const available=window.innerHeight-box.bottom-12;
 if(available<185)menu.style.top=Math.max(6,box.top-Math.min(320,window.innerHeight*.40))+"px";
 else menu.style.top=(box.bottom+5)+"px";
}
async function query(input){
 const value=input.value.trim(),id=++sequence;
 if(value.length<3||!panel.classList.contains("cx-directions-mode")){close();return}
 if(!window.google?.maps?.importLibrary||placesDisabled)return;
 try{
  const {AutocompleteSuggestion,AutocompleteSessionToken}=await google.maps.importLibrary("places");
  if(!session)session=new AutocompleteSessionToken();
  const options={input:value,sessionToken:session,language:"pt-BR",region:"br"};
  const map=window.COOPEX_GOOGLE_NATIVE;
  if(map?.getCenter())options.locationBias={radius:33000,center:map.getCenter()};
  const data=await AutocompleteSuggestion.fetchAutocompleteSuggestions(options);
  if(id!==sequence||document.activeElement!==input||input.value.trim()!==value)return;
  const matches=(data.suggestions||[]).filter(x=>x.placePrediction).slice(0,6);
  menu.replaceChildren();items=matches;currentInput=input;
  if(!matches.length){close();return}
  for(let index=0;index<matches.length;index++){
   const prediction=matches[index].placePrediction;
   const description=prediction.text.toString();
   const button=document.createElement("button");button.type="button";button.setAttribute("role","option");
   const main=document.createElement("strong");main.textContent=description.split(",")[0];
   const sub=document.createElement("small");sub.textContent=description;
   button.append(main,sub);
   button.addEventListener("pointerdown",e=>e.preventDefault());
   button.onclick=()=>select(prediction,input);
   menu.append(button);
  }
  position(input);menu.hidden=false;
 }catch(error){console.warn("Autocomplete de rotas indisponível:",error);placesDisabled=true;close()}
}
async function select(prediction,input){
 const label=prediction.text.toString();close();sequence++;
 // O texto exibido é sempre o nome escolhido, o Google resolve o trajeto na API de rotas.
 input.value=label;input.dispatchEvent(new Event("change",{bubbles:true}));
 if(input.id==="cxStopInput")$("cxStopSubmit")?.click();
 if(input.id==="cxRouteOrigin")$("cxDestinationMirror")?.focus();
 session=null;
}
for(const input of inputs){
 input.setAttribute("autocomplete","off");
 input.addEventListener("input",()=>{
  clearTimeout(timer);sequence++;close();
  if(input.value.trim().length>=3)timer=setTimeout(()=>query(input),450);
 });
 input.addEventListener("focus",()=>{if(input.value.trim().length>=3){clearTimeout(timer);timer=setTimeout(()=>query(input),300)}});
 input.addEventListener("keydown",e=>{
  if(e.key==="Escape"){close();return}
  if(menu.hidden||currentInput!==input)return;
  if(e.key==="ArrowDown"){e.preventDefault();const first=menu.querySelector("button");first?.focus()}
 });
}
menu.addEventListener("keydown",e=>{
 const buttons=[...menu.querySelectorAll("button")],idx=buttons.indexOf(document.activeElement);
 if(e.key==="ArrowDown"){e.preventDefault();buttons[(idx+1)%buttons.length]?.focus()}
 else if(e.key==="ArrowUp"){e.preventDefault();buttons[(idx-1+buttons.length)%buttons.length]?.focus()}
 else if(e.key==="Escape"){e.preventDefault();close();currentInput?.focus()}
});
document.addEventListener("pointerdown",e=>{if(!menu.contains(e.target)&&!inputs.includes(e.target))close()});
document.addEventListener("click",e=>{if(e.target.closest?.("[data-cx-nav]")||e.target.closest?.("#cxDirectionsBack"))close()});
window.addEventListener("resize",()=>{if(!menu.hidden&&currentInput)position(currentInput)});
window.addEventListener("scroll",()=>{if(!menu.hidden&&currentInput)position(currentInput)},true);
})();
