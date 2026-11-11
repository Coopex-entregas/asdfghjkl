/* Armazenamento local para desenvolvimento.
 * Não enviar operações automaticamente sem endpoint idempotente no Flask.
 */
(function(global){
 'use strict';
 const DB='coopex-local-v1',VERSION=1;
 function database(){
   return new Promise((resolve,reject)=>{
     const req=indexedDB.open(DB,VERSION);
     req.onupgradeneeded=()=>{
       const db=req.result;
       if(!db.objectStoreNames.contains('pendencias'))
         db.createObjectStore('pendencias',{keyPath:'id'});
     };
     req.onsuccess=()=>resolve(req.result);
     req.onerror=()=>reject(req.error);
   });
 }
 async function listPending(){
   const db=await database();
   return new Promise((resolve,reject)=>{
     const tx=db.transaction('pendencias','readonly');
     const req=tx.objectStore('pendencias').getAll();
     req.onsuccess=()=>resolve(req.result);
     req.onerror=()=>reject(req.error);
     tx.oncomplete=()=>db.close();
   });
 }
 // Somente testes internos: nenhuma operação financeira, fila ou entrega
 // está habilitada para gravação offline nesta fase.
 global.CoopexOffline=Object.freeze({
   fase:'infraestrutura-sem-gravacoes-de-negocio',
   listPending,
   suportado:typeof indexedDB!=='undefined'
 });
})(window);
