/* Registro PWA progressivo: não modifica interface, login ou rotas. */
(function(){
 'use strict';
 if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
 window.addEventListener('load',function(){
   navigator.serviceWorker.register('/coopex-sw.js',{scope:'/'})
     .catch(function(error){console.warn('COOPEX PWA indisponível:',error);});
 },{once:true});
})();
