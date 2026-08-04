(function(){
  const ORIGIN_IDS=['coleta_bairro','bairro_coleta','origem_bairro'];
  const DEST_IDS=['entrega_bairro','bairro_entrega','destino_bairro'];
  const VALUE_IDS=['valor','valor_entrega'];
  let routeTimer=null;
  let activeController=null;

  function byIds(ids){
    for(const id of ids){const el=document.getElementById(id);if(el)return el}
    return null;
  }
  function esc(text){return String(text??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[ch]))}
  function money(value){return new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(value||0))}
  function messageEl(valueInput){
    let el=document.getElementById('routeMsg')||document.getElementById('bairroRouteMsg');
    if(el)return el;
    el=document.createElement('span');el.id='bairroRouteMsg';el.className='bairro-route-msg';
    if(valueInput)valueInput.insertAdjacentElement('afterend',el);
    return el;
  }
  function injectStyle(){
    if(document.getElementById('coopexBairroAutocompleteStyle'))return;
    const style=document.createElement('style');style.id='coopexBairroAutocompleteStyle';style.textContent=`
      .bairro-ac-wrap{position:relative!important}
      .bairro-ac-list{position:absolute;left:0;right:0;top:calc(100% + 5px);z-index:9000;display:none;max-height:260px;overflow:auto;padding:6px;border:1px solid var(--cx-line-strong,#c4d2ef);border-radius:12px;background:var(--cx-card,#fff);box-shadow:0 18px 42px rgba(12,43,130,.22)}
      .bairro-ac-list.open{display:block}
      .bairro-ac-item{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:10px;border:0;border-radius:9px;background:transparent;color:var(--cx-text,#17223f);text-align:left;cursor:pointer;font:inherit}
      .bairro-ac-item:hover,.bairro-ac-item.active{background:var(--cx-royal-light,#edf3ff)}
      .bairro-ac-item strong{display:block;font-size:.88rem}.bairro-ac-item small{display:block;margin-top:2px;color:var(--cx-muted,#687696);font-size:.73rem}.bairro-ac-mark{color:var(--cx-royal-dark,#0b329f);font-size:.7rem;font-weight:900;white-space:nowrap}
      .bairro-route-msg{display:block;min-height:19px;margin-top:5px;color:var(--cx-muted,#687696);font-size:.78rem}.bairro-route-msg.ok{color:var(--cx-green,#13814e)}.bairro-route-msg.warn{color:var(--cx-warn,#9b6500)}
      body.dark .bairro-ac-list{background:#101d35;border-color:#38527f}.dark .bairro-ac-item{color:#edf3ff}.dark .bairro-ac-item:hover,.dark .bairro-ac-item.active{background:#162846}.dark .bairro-ac-mark{color:#b9ccff}
    `;document.head.appendChild(style);
  }

  function makeAutocomplete(input,onSelected){
    if(!input||input.dataset.bairroAutocomplete==='1')return;
    input.dataset.bairroAutocomplete='1';input.setAttribute('autocomplete','off');
    const parent=input.parentElement;parent.classList.add('bairro-ac-wrap');
    const list=document.createElement('div');list.className='bairro-ac-list';parent.appendChild(list);
    let timer=null,items=[],active=-1,controller=null;

    function hide(){list.classList.remove('open');active=-1}
    function activate(index){
      const nodes=[...list.querySelectorAll('.bairro-ac-item')];nodes.forEach(n=>n.classList.remove('active'));
      if(!nodes.length)return;active=(index+nodes.length)%nodes.length;nodes[active].classList.add('active');nodes[active].scrollIntoView({block:'nearest'});
    }
    function choose(item){input.value=item.label;hide();input.dispatchEvent(new Event('change',{bubbles:true}));onSelected?.(item);schedulePrice(40)}
    function render(found){
      items=found||[];active=-1;list.innerHTML='';
      if(!items.length){hide();return}
      items.forEach(item=>{
        const btn=document.createElement('button');btn.type='button';btn.className='bairro-ac-item';
        btn.innerHTML=`<span><strong>${esc(item.label)}</strong><small>${esc(item.cidade||'')}</small></span><span class="bairro-ac-mark">Selecionar</span>`;
        btn.addEventListener('mousedown',event=>{event.preventDefault();choose(item)});list.appendChild(btn)
      });list.classList.add('open')
    }
    async function search(){
      const q=input.value.trim();if(q.length<1){hide();return}
      if(controller)controller.abort();controller=new AbortController();
      try{
        const response=await fetch(`/api/bairros/sugestoes?q=${encodeURIComponent(q)}&limit=10`,{signal:controller.signal,headers:{Accept:'application/json','X-Requested-With':'fetch'},cache:'no-store'});
        const data=await response.json();render(response.ok&&data.ok?data.items:[])
      }catch(error){if(error.name!=='AbortError')hide()}
    }
    input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,90);schedulePrice(260)});
    input.addEventListener('focus',()=>{if(input.value.trim())search()});
    input.addEventListener('blur',()=>{setTimeout(hide,140);setTimeout(()=>normalizeInput(input),160)});
    input.addEventListener('keydown',event=>{
      const nodes=[...list.querySelectorAll('.bairro-ac-item')];
      if(!nodes.length||!list.classList.contains('open'))return;
      if(event.key==='ArrowDown'){event.preventDefault();activate(active+1)}
      else if(event.key==='ArrowUp'){event.preventDefault();activate(active-1)}
      else if(event.key==='Enter'&&active>=0){event.preventDefault();choose(items[active])}
      else if(event.key==='Escape')hide()
    });
    document.addEventListener('click',event=>{if(!parent.contains(event.target))hide()});
  }

  async function normalizeInput(input){
    if(!input||!input.value.trim())return;
    try{
      const response=await fetch(`/api/bairros/resolver?q=${encodeURIComponent(input.value.trim())}`,{headers:{Accept:'application/json','X-Requested-With':'fetch'},cache:'no-store'});
      const data=await response.json();if(response.ok&&data.ok&&data.label){input.value=data.label;input.dispatchEvent(new Event('change',{bubbles:true}));schedulePrice(30)}
    }catch(error){}
  }

  async function lookupPrice(){
    const origin=byIds(ORIGIN_IDS),destination=byIds(DEST_IDS),valueInput=byIds(VALUE_IDS);
    if(!origin||!destination||!valueInput)return;
    const msg=messageEl(valueInput);const o=origin.value.trim(),d=destination.value.trim();
    if(!o||!d){if(msg){msg.textContent='Informe coleta e entrega.';msg.className='bairro-route-msg'}return}
    if(activeController)activeController.abort();activeController=new AbortController();
    if(msg){msg.textContent='Buscando valor correto...';msg.className='bairro-route-msg'}
    try{
      const response=await fetch(`/api/bairros/preco?origem=${encodeURIComponent(o)}&destino=${encodeURIComponent(d)}`,{signal:activeController.signal,headers:{Accept:'application/json','X-Requested-With':'fetch'},cache:'no-store'});
      const data=await response.json();
      if(!response.ok||!data.ok){if(msg){msg.textContent=data.error||'Rota sem valor cadastrado.';msg.className='bairro-route-msg warn'}return}
      origin.value=data.origem;destination.value=data.destino;valueInput.value=Number(data.valor||0).toFixed(2).replace('.',',');
      const hidden=document.getElementById('bairro');if(hidden)hidden.value=data.destino;
      if(msg){msg.textContent=`Valor encontrado: ${money(data.valor)}`;msg.className='bairro-route-msg ok'}
      if(typeof window.sync==='function')window.sync();
      valueInput.dispatchEvent(new Event('input',{bubbles:true}));
    }catch(error){if(error.name!=='AbortError'&&msg){msg.textContent='Não foi possível consultar o valor agora.';msg.className='bairro-route-msg warn'}}
  }
  function schedulePrice(delay=180){clearTimeout(routeTimer);routeTimer=setTimeout(lookupPrice,delay)}

  function start(){
    injectStyle();
    const origin=byIds(ORIGIN_IDS),destination=byIds(DEST_IDS);
    if(!origin||!destination)return;
    makeAutocomplete(origin);makeAutocomplete(destination);
    origin.oninput=()=>schedulePrice(260);destination.oninput=()=>{const hidden=document.getElementById('bairro');if(hidden)hidden.value=destination.value;schedulePrice(260);if(typeof window.sync==='function')window.sync()};
    origin.addEventListener('change',()=>schedulePrice(40));destination.addEventListener('change',()=>schedulePrice(40));
    window.lookup=lookupPrice;window.lookupSoon=schedulePrice;
    setTimeout(()=>{normalizeInput(origin);normalizeInput(destination)},60);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
