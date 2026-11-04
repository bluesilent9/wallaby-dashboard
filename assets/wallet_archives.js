/* Public archive requests. Physical actions and private backups stay on Agent. */
window.WallabyArchives=(()=>{
const controls=new Set();
function groups(sources){const agents=new Map();for(const s of sources||[]){if(!s.compatible)continue;let agent=agents.get(s.node_id);if(!agent){agent={node_id:s.node_id,hostname:s.hostname,sources:[]};agents.set(s.node_id,agent);}agent.sources.push(s);}return [...agents.values()].sort((a,b)=>a.hostname.localeCompare(b.hostname)||a.node_id.localeCompare(b.node_id));}
function apply(record){const {wallet,request}=record,available=!wallet.extinct&&wallet.kind!=='exchange'&&!wallet.disc_only&&groups(wallet.archive_sources).length>0;request.disabled=record.busy||record.choosing||!available;request.title=available?'Request a locally approved disc archive':wallet.disc_only?'A disc-loaded account cannot be archived again from Dashboard':'Connect an Agent holding this wallet’s private signing keys. Watch-only and extinct wallets cannot be archived.';}
function refresh(){for(const record of controls){if(record.request.isConnected===false){controls.delete(record);continue;}apply(record);}}
function control(c){const {el,button,wallet,api,family,toast}=c;let record;
 const request=button('Archive this Wallet','small primary',async()=>{
  if(record.busy||record.choosing)return;record.busy=true;apply(record);
  try{
   const row=(family.ownership||[]).find(r=>r.kind==='descriptor'&&r.source===wallet.descriptor_id);if(!row)throw Error('Refresh Ownership before requesting archival.');
   const current=await(await api('/api/wallets/archive-options?wallet_id='+encodeURIComponent(row.id))).json();wallet.archive_sources=current.sources||[];const agents=groups(wallet.archive_sources);
   if(!agents.length){toast('No eligible Agent is connected. Connect an Agent holding this wallet’s private signing keys.');return;}
   const send=async chosen=>{if(!chosen.length)throw Error('Select at least one Agent.');await api('/api/wallets/archive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wallet_id:row.id,sources:chosen.flatMap(a=>a.sources.map(s=>s.id))})});toast('Archive requests queued. Review on the Agents.');};
   const dialog=el('dialog',{class:'wallaby-dialog'},el('h2',{},'Archive '+wallet.name),el('p',{},'Choose the Agents to receive this archive request. Approve locally on each selected Agent; private wallet files stay there.'));
   const choices=current.agents.map(agent=>{const source=agents.find(a=>a.node_id===agent.node_id);return {agent:{...agent,sources:source?.sources||[]},input:el('input',{type:'checkbox',checked:false,disabled:!agent.available||!source})};});
   let submitting=false,closed=false;record.choosing=true;
   const close=()=>{if(submitting||closed)return;closed=true;record.choosing=false;dialog.close();dialog.remove();apply(record);};
   const cancel=button('Cancel','outline',close);
   dialog.oncancel=e=>{e.preventDefault();close();};dialog.onclose=close;
   const selected=()=>choices.filter(v=>!v.input.disabled&&v.input.checked);
   const confirm=button('Request archival','primary',async()=>{if(submitting)return;submitting=true;confirm.disabled=true;cancel.disabled=true;try{await send(selected().map(v=>v.agent));submitting=false;close();}catch(e){toast(e.message);submitting=false;cancel.disabled=false;confirm.disabled=!selected().length;}});confirm.disabled=true;
   for(const {agent,input}of choices){input.onchange=()=>{if(!submitting)confirm.disabled=!selected().length;};dialog.append(el('label',{class:'checkbox-row archive-agent-choice'},input,el('span',{},el('strong',{},agent.hostname),el('small',{class:'subtle'},agent.available?agent.sources.map(s=>s.wallet).join(', '):agent.reason))));}
   dialog.append(cancel,confirm);document.body.append(dialog);dialog.showModal();
  }catch(e){toast(e.message);}finally{record.busy=false;apply(record);}
 });record={wallet,request,busy:false};controls.add(record);apply(record);return request;
}
return {control,groups,refresh};})();
