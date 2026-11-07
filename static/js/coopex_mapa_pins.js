/* COOPEX Map — avatares GPS e seleção de cooperados sobrepostos. */
(function(){
"use strict";
if(!window.L || typeof iconFor!=="function" || typeof renderMarkers!=="function" || typeof markers==="undefined") return;
function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function initials(name){return String(name||"CO").trim().split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0].toLocaleUpperCase("pt-BR")).join("");}
function color(item){const s=visualState(item);return s==="online_busy"?"#e18a1e":s==="online_free"?"#09a86b":s==="offline_busy"?"#d9593b":"#8590a5";}
function personIcon(item,stackCount){
 const bg=color(item), photo=item.foto_url&&/^https?:\/\//i.test(item.foto_url)?'<img src="'+esc(item.foto_url)+'" alt="" referrerpolicy="no-referrer" onerror="this.remove()">':esc(initials(item.nome));
 const badge=stackCount>1?'<span class="cx-marker-count">'+stackCount+'</span>':"";
 return L.divIcon({className:"cx-person-pin",html:'<div class="cx-pin-shape" style="--cx-pin:'+bg+'"><div class="cx-pin-avatar">'+photo+'</div><span class="cx-pin-tip"></span>'+badge+'</div>',iconSize:[48,56],iconAnchor:[24,55],popupAnchor:[0,-54]});
}
iconFor=function(item){return personIcon(item,1);};
const originalRender=renderMarkers;
let lastSelection="", clusterCycle={};
function itemId(x){return String(x.id);}
function groupNearby(items){
 const list=items.filter(hasCoord), groups=[];
 for(const item of list){
   const lat=Number(item.lat),lng=Number(item.lng);
   let group=groups.find(g=>Math.abs(Number(g[0].lat)-lat)<.00009&&Math.abs(Number(g[0].lng)-lng)<.00009);
   if(group)group.push(item);else groups.push([item]);
 }
 return groups;
}
function showMember(group,pos) {
 const item=group[pos];if(!item)return;
 if(typeof setInfo==="function")setInfo(item);
 const host=document.getElementById("cx-selected");
 if(host){
  const profile=document.getElementById("cx-profile");
  if(profile) {
   const old=profile.querySelector(".cx-co-located");if(old)old.remove();
   if(group.length>1){
    const block=document.createElement("div");block.className="cx-co-located";
    const head=document.createElement("div");head.className="cx-co-located-heading";head.textContent=group.length+" cooperados no mesmo local";block.appendChild(head);
    const rail=document.createElement("div");rail.className="cx-co-located-rail";
    group.forEach((member,i)=>{
      const btn=document.createElement("button");btn.type="button";btn.className="cx-member"+(i===pos?" is-active":"");
      btn.textContent=initials(member.nome);
      btn.title=member.nome||"Cooperado";
      btn.setAttribute("aria-label","Ver "+(member.nome||"Cooperado"));
      btn.addEventListener("click",()=>showMember(group,i));
      rail.appendChild(btn);
    });
    block.appendChild(rail);profile.appendChild(block);
   }
  }
  host.classList.add("open");
 }
 lastSelection=itemId(item);
}
renderMarkers=function(items){
 originalRender(items);
 const groups=groupNearby(items);
 for(const group of groups){
   const leader=markers.get(group[0].id);if(!leader)continue;
   for(let i=0;i<group.length;i++){
      const marker=markers.get(group[i].id);
      if(!marker)continue;
      marker.setIcon(personIcon(group[i],i===0?group.length:1));
      marker.setOpacity(i===0?1:0);
      const node=marker.getElement();if(node){node.style.pointerEvents=i===0?"auto":"none";node.style.zIndex=i===0?"1000":"0";}
   }
   if(group.length>1){
     leader.off("click mouseover mouseout");
     leader.on("click",()=>{
        const key=group.map(itemId).sort().join("|");
        const next=(clusterCycle[key]||0)%group.length;
        clusterCycle[key]=next+1;
        showMember(group,next);
     });
     leader.on("mouseover",()=>leader.bindTooltip(group.length+" cooperados aqui • clique para alternar",{direction:"top",offset:[0,-35]}).openTooltip());
     leader.on("mouseout",()=>leader.closeTooltip());
   }else{
     leader.off("click");
     leader.on("click",()=>showMember(group,0));
   }
 }
};
if(typeof renderAll==="function")renderAll();
})();