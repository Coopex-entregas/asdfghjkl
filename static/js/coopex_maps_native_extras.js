/* Complements the native Google Maps SDK; no replacement basemap. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
let map=null,streetService=null,previewPano=null,selected=null,routeTags=[],routePositions=[];
function start(){
 if(map||!window.COOPEX_GOOGLE_NATIVE)return;
 map=window.COOPEX_GOOGLE_NATIVE;
 streetService=new google.maps.StreetViewService();
 const preview=document.createElement("button");
 preview.type="button";preview.id="cxStreetPreview";preview.title="Ver rua no Street View";
 preview.setAttribute("aria-label","Abrir visualização da rua selecionada");
 const image=document.createElement("div");image.id="cxStreetPreviewImage";
 const caption=document.createElement("span");caption.textContent="Ver rua";
 preview.append(image,caption);document.body.append(preview);
 preview.onclick=()=>{
  if(!selected){$("streetView")?.click();return}
  const panorama=map.getStreetView();
  streetService.getPanorama({location:selected,radius:150,source:google.maps.StreetViewSource.OUTDOOR},(data,status)=>{
   if(status!=="OK"||!data?.location)return;
   panorama.setPano(data.location.pano);
   panorama.setPov({heading:0,pitch:0});
   panorama.setVisible(true);
  });
 };
 function updateStreet(point){
  if(!point||!Number.isFinite(+point.lat)||!Number.isFinite(+point.lng))return;
  selected={lat:+point.lat,lng:+point.lng};
  preview.hidden=false;
  streetService.getPanorama({location:selected,radius:150,source:google.maps.StreetViewSource.OUTDOOR},(data,status)=>{
   if(!preview.isConnected)return;
   if(status!=="OK"||!data?.location){preview.classList.add("cx-street-unavailable");image.replaceChildren();return}
   preview.classList.remove("cx-street-unavailable");
   try{
    if(!previewPano)previewPano=new google.maps.StreetViewPanorama(image,{
     pano:data.location.pano,visible:true,disableDefaultUI:true,clickToGo:false,linksControl:false,
     scrollwheel:false,panControl:false,zoomControl:false,addressControl:false,motionTracking:false
    });
    else previewPano.setPano(data.location.pano);
   }catch(e){console.debug("Street View preview:",e)}
  });
 }
 window.addEventListener("coopex:place-selected",e=>updateStreet(e.detail?.point));
 function clearRouteTags(){for(const tag of routeTags)tag.setMap(null);routeTags=[]}
 function labels({routes,selectedIndex=0}){
  clearRouteTags();if(!Array.isArray(routes))return;
  routes.forEach((route,i)=>{
   const path=route.path;if(!Array.isArray(path)||!path.length)return;
   const point=path[Math.max(0,Math.floor(path.length*.56))];
   const label=document.createElement("button");label.type="button";
   label.className="cx-route-maplabel"+(i===selectedIndex?" selected":"");
   label.textContent=Math.round((route.durationMillis||0)/60000)+" min";
   const small=document.createElement("small");small.textContent=(Number(route.distanceMeters||0)/1000).toLocaleString("pt-BR",{maximumFractionDigits:1})+" km";label.append(small);
   label.onclick=()=>{$("cxRoutes")?.querySelectorAll(".cx-route-option")[i]?.click()};
   const overlay=new google.maps.OverlayView();
   overlay.onAdd=function(){this.node=label;this.getPanes().floatPane.append(label)};
   overlay.draw=function(){const pixel=this.getProjection()?.fromLatLngToDivPixel(new google.maps.LatLng(point.lat,point.lng));if(pixel){label.style.left=pixel.x+"px";label.style.top=pixel.y+"px"}};
   overlay.onRemove=function(){label.remove()};
   overlay.setMap(map);routeTags.push(overlay);
  });
 }
 window.addEventListener("coopex:routes-ready",e=>{routePositions=e.detail?.routes||[];labels({routes:routePositions})});
 window.addEventListener("coopex:selected-route",e=>{if(routePositions.length)labels({routes:routePositions,selectedIndex:e.detail?.index??0})});
}
window.addEventListener("coopex:google-native-ready",start);
if(document.readyState==="complete")start();else window.addEventListener("load",start);
})();