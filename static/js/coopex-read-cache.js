/* COOPEX: snapshots locais SOMENTE de consultas, sem gravacoes offline. */
(function(w){
'use strict';
const NAME='coopex-leitura-v1',TABLE='consultas',MAX=20,TTL=7*86400000;
function db(){
 return new Promise((yes,no)=>{
  const r=indexedDB.open(NAME,1);
  r.onupgradeneeded=()=>r.result.createObjectStore(TABLE,{keyPath:'id'});
  r.onsuccess=()=>yes(r.result);r.onerror=()=>no(r.error);
 });
}
async function execute(mode,action){
 const d=await db();
 return new Promise((yes,no)=>{
  const t=d.transaction(TABLE,mode),s=t.objectStore(TABLE);
  let value=null;
  t.oncomplete=()=>{d.close();yes(value)};
  t.onerror=()=>{d.close();no(t.error)};
  action(s,v=>value=v);
 });
}
const name=(u,k,q)=>String(u)+'|'+String(k)+'|'+String(q);
async function read(u,k,q){
 return execute('readonly',(s,ret)=>{
  const r=s.get(name(u,k,q));
  r.onsuccess=()=>{
   const x=r.result;
   ret(x&&Date.now()-x.time<TTL?x:null);
  };
 });
}
async function save(u,k,q,items){
 if(!Array.isArray(items))return;
 return execute('readwrite',(s,ret)=>{
  const id=name(u,k,q),time=Date.now(),all=[];
  const cur=s.openCursor();
  cur.onsuccess=()=>{
   const c=cur.result;
   if(!c){
    all.sort((a,b)=>b.time-a.time);
    all.slice(MAX-1).forEach(x=>s.delete(x.id));
    s.put({id,user:String(u),time,items});ret(true);return;
   }
   const x=c.value;
   if(x.user===String(u)){
    if(x.id===id||time-x.time>TTL)s.delete(x.id);
    else all.push(x);
   }
   c.continue();
  };
 });
}
async function clear(u){
 return execute('readwrite',(s,ret)=>{
  const cur=s.openCursor();
  cur.onsuccess=()=>{
   const c=cur.result;
   if(!c){ret(true);return;}
   if(c.value.user===String(u))c.delete();
   c.continue();
  };
 });
}
w.CoopexReadCache=Object.freeze({read,save,clear});
})(window);
