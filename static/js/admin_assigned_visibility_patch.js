(function(){
  'use strict';

  function syncVisibleState(){
    document.body.classList.remove('hide-assigned');

    const button=document.getElementById('btnToggleAssigned');
    if(button){
      button.setAttribute('aria-expanded','true');
      button.textContent='Ocultar atribuídas';
    }

    const chip=document.getElementById('chipAssignedInfo');
    if(chip){
      chip.textContent='Atribuídas visíveis';
      chip.classList.add('active');
    }

    document.querySelectorAll('.row-assigned').forEach(function(row){
      row.style.removeProperty('display');
      row.removeAttribute('hidden');
      row.setAttribute('aria-hidden','false');
    });
  }

  function start(){
    syncVisibleState();
    window.setTimeout(syncVisibleState,50);
    window.setTimeout(syncVisibleState,250);

    const form=document.querySelector('form.filters, form[action*="admin"]');
    if(form){
      form.addEventListener('submit',function(){
        try{sessionStorage.setItem('coopex_admin_show_assigned','1')}catch(error){}
      });
    }
  }

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',start,{once:true});
  }else{
    start();
  }

  window.addEventListener('load',syncVisibleState,{once:true});
  window.addEventListener('pageshow',syncVisibleState);
})();
