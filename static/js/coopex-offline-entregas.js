/* COOPEX: fila local de rascunhos de entregas.
 * Sincroniza apenas cadastros sem atribuicao ou credito; confirma pelo ID no banco.
 */
(function(){
'use strict';
if(!/^\/(admin|cadastrar_entrega)(?:\/|$)/.test(location.pathname)||!window.indexedDB)return;
const DB='coopex-entregas-offline-v1',STORE='rascunhos';
function openDB(){
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open(DB,1);
  request.onupgradeneeded=()=>{
   if(!request.result.objectStoreNames.contains(STORE))
    request.result.createObjectStore(STORE,{keyPath:'id'});
  };
  request.onsuccess=()=>resolve(request.result);
  request.onerror=()=>reject(request.error);
 });
}
async function transact(mode,action){
 const db=await openDB();
 try{
  return await new Promise((resolve,reject)=>{
   const tx=db.transaction(STORE,mode),store=tx.objectStore(STORE);
   let result;
   tx.oncomplete=()=>resolve(result);
   tx.onerror=()=>reject(tx.error);
   tx.onabort=()=>reject(tx.error||new Error('Armazenamento interrompido'));
   action(store,value=>{result=value});
  });
 }finally{db.close()}
}
async function save(form){
 const data=new FormData(form),fields={};
 for(const [key,value] of data.entries()){
  if(typeof value!=='string')throw new Error('Anexos não são compatíveis com a fila offline');
  if(Object.hasOwn(fields,key)){
   if(Array.isArray(fields[key]))fields[key].push(value);
   else fields[key]=[fields[key],value];
  }else fields[key]=value;
 }
 const item={
  id:crypto.randomUUID(),createdAt:new Date().toISOString(),
  status:'salvo-localmente',form:fields,
  action:new URL(form.action,location.href).pathname,
  mode:new URL(location.href).searchParams.get('modo')==='completo'?'completo':'rapida'
 };
 await transact('readwrite',(store,set)=>{
  const request=store.put(item);
  request.onsuccess=()=>set(item);
 });
 return item;
}
async function list(){
 return transact('readonly',(store,set)=>{
  const request=store.getAll();
  request.onsuccess=()=>set(request.result||[]);
 });
}
async function update(item){
 return transact('readwrite',(store,set)=>{
  const request=store.put(item);
  request.onsuccess=()=>set(item);
 });
}
let sending=false;
function needsReview(item){
 const f=item.form||{};
 const coop=String(f.cooperado_id||'').trim();
 const payment=String(f.pagamento||'').toLowerCase();
 return Boolean(coop&&coop!=='__fora_escala__') ||
   payment.includes('credito') || payment.includes('crédito') ||
   payment.includes('parceria');
}
async function synchronize(){
 if(sending||navigator.onLine===false)return;
 sending=true;
 try{
  const items=(await list()).sort((a,b)=>String(a.createdAt).localeCompare(String(b.createdAt)));
  for(const item of items){
   if(navigator.onLine===false)break;
   if(!['salvo-localmente','enviando','erro-rede'].includes(item.status))continue;
   if(item.action!=='/cadastrar_entrega'||needsReview(item)){
    item.status='revisao';
    item.message='Atribuição ou crédito precisa de conferência antes de enviar';
    await update(item);
    continue;
   }
   const controller=new AbortController();
   const timeout=setTimeout(()=>controller.abort(),20000);
   try{
    item.status='enviando';await update(item);
    const body=new URLSearchParams();
    for(const [key,value] of Object.entries(item.form||{})){
     if(Array.isArray(value))value.forEach(v=>body.append(key,v));
     else body.append(key,value);
    }
    body.set('__coopex_offline_id',item.id);
    const response=await fetch('/cadastrar_entrega?format=json',{
     method:'POST',credentials:'same-origin',cache:'no-store',
     headers:{'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},
     body:body.toString(),signal:controller.signal
    });
    if(response.status===401||response.status===403||response.redirected){
     item.status='salvo-localmente';item.message='Entre novamente no administrador para sincronizar';
     await update(item);break;
    }
    const payload=await response.json().catch(()=>({}));
    if(response.ok && payload.ok===true && Number.isInteger(Number(payload.entrega_id))){
     item.status='confirmado';item.entrega_id=Number(payload.entrega_id);
     item.confirmedAt=new Date().toISOString();item.message='';
     await update(item);
     announce('Entrega local sincronizada e confirmada no servidor: nº '+item.entrega_id);
    }else if(response.status>=500||response.status===429){
     item.status='erro-rede';item.message='Servidor indisponível; será tentado novamente';
     await update(item);break;
    }else{
     item.status='revisao';item.message=String(payload.error||payload.message||'Verificar cadastro antes do reenvio');
     await update(item);
    }
   }catch(error){
    // Não sabemos se o servidor recebeu. A mesma chave torna a tentativa segura.
    item.status='erro-rede';item.message='Conexão interrompida; confirmação pendente';
    await update(item);
    break;
   }finally{clearTimeout(timeout)}
  }
 }catch(error){console.warn('COOPEX: sincronização pendente',error)}
 finally{sending=false;renderCount()}
}
function announce(message){
 let box=document.getElementById('cx-offline-entrega-status');
 if(!box){
  box=document.createElement('div');box.id='cx-offline-entrega-status';
  box.setAttribute('role','status');
  box.style.cssText='position:fixed;z-index:2147483000;bottom:12px;left:12px;right:12px;max-width:580px;margin:auto;border-radius:12px;background:#fff7da;color:#583c00;border:1px solid #e7cb77;padding:13px 17px;font:600 13px system-ui;box-shadow:0 8px 30px #0003';
  document.body.appendChild(box);
 }
 box.textContent=message;
}
async function renderCount(){
 const existing=document.getElementById('cx-offline-entrega-counter');
 if(!existing && location.pathname!=='/admin')return;
 try{
  const items=await list();
  const pending=items.filter(x=>x.status!=='confirmado');
  let node=existing;
  if(!node){
   node=document.createElement('div');node.id='cx-offline-entrega-counter';
   node.style.cssText='position:fixed;bottom:8px;right:8px;z-index:9998;background:#f9e5ac;color:#503600;padding:8px 11px;border-radius:10px;font:600 12px system-ui;box-shadow:0 4px 14px #0002';
   document.body.appendChild(node);
  }
  if(!pending.length){node.remove();return;}
  const review=pending.filter(x=>x.status==='revisao').length;
  node.textContent=pending.length+' entrega(s) local(is) pendente(s)'+
   (review?' · '+review+' requer(em) revisão':'')+
   ' · Não feche/limpe os dados do navegador';
 }catch(error){console.warn('COOPEX: fila local indisponível',error)}
}
function init(){
 const form=document.querySelector('form[action$="/cadastrar_entrega"][method="POST" i]');
 if(form){
  form.addEventListener('submit',async event=>{
   // Capturar SOMENTE falha de conectividade confirmada, não timeout nem HTTP:
   // o servidor pode ter concluído o cadastro e o reenvio criaria duplicação.
   if(navigator.onLine!==false)return;
   if(!form.reportValidity())return;
   event.preventDefault();
   event.stopImmediatePropagation();
   try{
    await save(form);
    announce('Entrega salva neste navegador. NÃO foi cadastrada no servidor. A sincronização ocorrerá quando houver conexão, se não exigir revisão.');
    form.reset();
   }catch(error){
    announce('Não foi possível salvar a entrega offline. Mantenha os dados no formulário. '+error.message);
   }
  },true);
 }
 renderCount();
 if(navigator.onLine!==false)synchronize();
 window.addEventListener('online',synchronize);
 document.addEventListener('visibilitychange',()=>{
  if(!document.hidden&&navigator.onLine!==false)synchronize();
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
window.CoopexOfflineEntregas=Object.freeze({list,synchronize});
})();