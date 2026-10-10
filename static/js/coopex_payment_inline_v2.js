/* COOPEX: edicao robusta da forma de pagamento na propria tabela. */
(function(){
  if(window.__coopexPaymentInlineV2)return;
  window.__coopexPaymentInlineV2=true;
  const options=['Pix','Pix (Cooperativa)','Dinheiro','Comanda','CREDITO_AUTO'];
  document.addEventListener('click',async function(ev){
    const btn=ev.target.closest('.cx-forma-pagamento');
    if(!btn)return;
    ev.preventDefault();ev.stopImmediatePropagation();
    if(btn.dataset.editing==='1')return;
    btn.dataset.editing='1';
    const cell=btn.parentElement, old=btn.dataset.forma||btn.textContent.trim();
    const wrapper=document.createElement('span');wrapper.className='cx-pg-edit';
    const select=document.createElement('select');select.setAttribute('aria-label','Forma de pagamento');
    const values=[...new Set([...options,old].filter(Boolean))];
    values.forEach(v=>{const opt=document.createElement('option');opt.value=v;opt.textContent=v;select.appendChild(opt)});
    select.value=old;
    const save=document.createElement('button');save.type='button';save.textContent='✓';save.title='Salvar';save.setAttribute('aria-label','Salvar forma de pagamento');
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='✕';cancel.title='Cancelar';cancel.setAttribute('aria-label','Cancelar');
    wrapper.append(select,save,cancel);btn.replaceWith(wrapper);
    const finish=()=>{delete btn.dataset.editing;wrapper.replaceWith(btn)};
    cancel.addEventListener('click',finish);
    save.addEventListener('click',async()=>{
      if(select.value===old){finish();return}
      save.disabled=true;cancel.disabled=true;
      try{
        const response=await fetch('/coopex-connect/admin/entrega/'+encodeURIComponent(btn.dataset.entregaId)+'/forma-pagamento',{
          method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json','Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
          body:JSON.stringify({pagamento:select.value})
        });
        const data=await response.json().catch(()=>({}));
        if(!response.ok || !data.ok)throw new Error(data.error || 'Não foi possível salvar ('+response.status+').');
        btn.dataset.forma=data.pagamento;btn.textContent=data.pagamento;finish();
        const toast=document.createElement('div');toast.textContent='Forma de pagamento atualizada';toast.setAttribute('role','status');
        Object.assign(toast.style,{position:'fixed',bottom:'20px',right:'20px',zIndex:'99999',padding:'12px 16px',borderRadius:'10px',background:'#1457e5',color:'#fff'});
        document.body.appendChild(toast);setTimeout(()=>toast.remove(),2500);
      }catch(err){alert(err.message);save.disabled=false;cancel.disabled=false}
    });
    select.addEventListener('keydown',e=>{if(e.key==='Escape')finish();if(e.key==='Enter'){e.preventDefault();save.click()}});
    select.focus();
  },true);
})();
