/* COOPEX: fila local de rascunhos de entregas.
 * A sincronizacao automatica permanece desabilitada ate existir idempotencia no servidor.
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
  const pending=items.filter(x=>x.status==='salvo-localmente');
  let node=existing;
  if(!node){
   node=document.createElement('div');node.id='cx-offline-entrega-counter';
   node.style.cssText='position:fixed;bottom:8px;right:8px;z-index:9998;background:#f9e5ac;color:#503600;padding:8px 11px;border-radius:10px;font:600 12px system-ui;box-shadow:0 4px 14px #0002';
   document.body.appendChild(node);
  }
  if(!pending.length){node.remove();return;}
  node.textContent=pending.length+' entrega(s) salva(s) neste navegador, ainda não enviadas ao servidor';
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
    announce('Entrega salva somente neste navegador. NÃO foi cadastrada no servidor. Aguarde a sincronização segura, ainda não habilitada.');
    form.reset();
   }catch(error){
    announce('Não foi possível salvar a entrega offline. Mantenha os dados no formulário. '+error.message);
   }
  },true);
 }
 renderCount();
 window.addEventListener('online',renderCount);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
window.CoopexOfflineEntregas=Object.freeze({list});
})();