/* Node RPC supplies public wallet metadata; existing blockchain tracing follows. */
window.WallabyDescriptors=(()=>{'use strict';
let error='',busy=false,scope='';
function resetScope(value){if(scope!==value){scope=value;error='';busy=false;}}
function canContinue(data){return !!data?.ready&&!busy;}
function view(c){const {el,button,note,post,onReady,toast}=c;resetScope(c.scope);
const status=el('div',{class:error?'error-banner':'',role:'status'},error);
const run=button('Load Node Wallets','primary',async()=>{if(busy)return;busy=true;run.disabled=true;error='';try{await c.onStart?.();await post('/api/descriptors/nodes',{});toast('Collecting public wallets from every connected node.');await onReady();}catch(e){error=e.message;status.textContent=error;status.className='error-banner';c.onError?.(error);}finally{busy=false;run.disabled=false;}},'wallet');run.id='load-node-wallets';run.disabled=busy;
return el('section',{id:'ownership-descriptors',class:'ownership-descriptors'},el('div',{class:'builder-stage ownership-stage'},el('strong',{role:'heading','aria-level':'2'},'Stage 1 of 3 - Node Wallets')),el('section',{class:'card node-wallet-form'},run,status));
}return {view,canContinue,resetScope};})();
