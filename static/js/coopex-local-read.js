/* COOPEX – leituras locais no painel cooperado. Gravações SEMPRE vão ao servidor. */
(function(){
 'use strict';
 const uid=document.body?.dataset?.userId;
 if(!uid || !/^\/painel_cooperado(?:\/|$)/.test(location.pathname))return;
 if(!window.CoopexReadCache || !window.fetch)return;
 const db=window.CoopexReadCache,original=window.fetch.bind(window);
 let pending=0;
 function warning(on){
  let el=document.getElementById('coopex-local-read-warning');
  if(!on){if(el)el.remove();return;}
  if(!el){
   el=document.createElement('div');el.id='coopex-local-read-warning';
   el.setAttribute('role','status');
   el.style.cssText='position:fixed;bottom:12px;left:12px;right:12px;z-index:9999;padding:10px 14px;border-radius:12px;background:#fff4cf;color:#664a00;border:1px solid #e6cc7a;font:600 12px system-ui;box-shadow:0 7px 25px #0002';
   document.body.appendChild(el);
  }
  el.textContent='Sem conexão com o servidor: exibindo uma consulta antiga deste computador. Alterações não serão confirmadas até a conexão voltar.';
 }
 function responseFrom(rows){
  return new Response(JSON.stringify({ok:true,items:rows}),{
   status:200,headers:{'Content-Type':'application/json','X-Coopex-Local':'1'}
  });
 }
 window.fetch=async function(input,init){
  const req=new Request(input,init),u=new URL(req.url),method=req.method.toUpperCase();
  const eligible=method==='GET'&&u.origin===location.origin&&
    u.pathname==='/api/cooperado/entregas';
  if(!eligible){
   const res=await original(input,init);
   if(method!=='GET'&&u.origin===location.origin&&res.ok&&
      /^\/(?:cooperado\/|api\/cooperado\/)/.test(u.pathname)){
    db.clear(uid).catch(()=>{});
   }
   return res;
  }
  // Consultas oficiais são priorizadas; cache local apenas em falha de rede/5xx.
  let result;
  try{
   result=await original(input,init);
   if(result.ok){
    warning(false);
    result.clone().json().then(data=>{
     if(data&&data.ok!==false&&Array.isArray(data.items))
       db.save(uid,'entregas',u.search,data.items).catch(()=>{});
    }).catch(()=>{});
    return result;
   }
   if(result.status<500)return result;
  }catch(err){
   if(err?.name==='AbortError')throw err;
  }
  let saved=null;
  try{saved=await db.read(uid,'entregas',u.search)}catch(_){}
  if(saved){
   warning(true);
   return responseFrom(saved.items);
  }
  if(result)return result;
  throw new TypeError('Sem conexão e sem dados locais para esta consulta');
 };
 document.addEventListener('click',e=>{
  const button=e.target.closest?.('.logout-btn');
  if(!button)return;
  // Não deixar informações pessoais neste navegador após Sair.
  e.preventDefault();e.stopPropagation();
  const match=button.getAttribute('onclick')?.match(/window\.location\.href='([^']+)'/);
  const dest=match?.[1]||'/logout';
  Promise.resolve(db.clear(uid)).catch(()=>{}).finally(()=>location.assign(dest));
 },true);
})();
