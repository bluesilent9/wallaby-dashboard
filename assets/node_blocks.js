/* AGPL-3.0. Adapted from Mempool's blockchain-blocks.component.ts and
 * component template; copyright 2019-2026 Mempool contributors.
 * Angular StateService/CacheService are replaced by Wallaby node snapshots.
 * Corresponding source and original component: /source.zip. */
(function(root){'use strict';
// Compact display only; the node's full rate remains in data and the hover text.
function displayRate(value){
 const text=String(value),m=text.length<=100&&text.match(/^(\d+)(?:\.(\d+))?$/);if(!m)return 'Unavailable';
 const fraction=m[2]||'',whole=BigInt(m[1]);
 if(whole===0n&&!/[1-9]/.test(fraction.slice(0,3))&&/[1-9]/.test(fraction.slice(3)))return '<0.001';
 let n=whole*1000n+BigInt((fraction+'000').slice(0,3));if(Number(fraction[3]||'0')>=5)n++;
 const d=String(n).padStart(4,'0'),f=d.slice(-3).replace(/0+$/,'');return d.slice(0,-3)+(f?'.'+f:'');
}
function blockStyle(block,index,width=125){
 const padding=.24*width,offset=width+padding;
 // Upstream getStyleForBlock's weight-driven fill and placement.
 const free=100-(Number(block.weight||0)/4000000)*100;
 return {left:offset*index+'px',background:`linear-gradient(var(--secondary) ${Math.max(0,free)}%,var(--mainnet-alt) ${Math.max(0,free)}%,var(--primary) 100%)`};
}
function markerIndex(blocks,height){return blocks.findIndex(b=>b.height===height);}
function view({el,button,data,state,changed}){
 const section=el('section',{class:'node-block-panel','aria-label':'Pending and mined blocks'}),pending=data.projected_blocks||[],mined=data.recent_blocks||[],count=3,start=Math.min(state.offset,Math.max(0,mined.length-count));
 const top=el('div',{class:'node-block-title'},el('h2',{},'Your node’s block view'),el('span',{class:'subtle'},data.source||'Waiting for a connected node'));
 const content=el('div',{class:'node-block-track'}),future=el('div',{class:'node-block-half pending-blocks'}),past=el('div',{class:'node-block-half mined-blocks'});
 const create=(b,i,isPending)=>{
  const style=blockStyle(b,i),rate=b.median_rate,known=state.tracked,marked=known&&(isPending?known.status==='mempool'&&i===0:known.status==='confirmed'&&known.block_height===b.height);
  const cube=el('div',{class:'bitcoin-block '+(isPending?'projected-block':'mined-block'),style:'background:'+style.background,'data-block-height':isPending?'pending-'+i:b.height,tabindex:0},el('strong',{class:'node-block-number'},isPending?'Next '+(i+1):Number(b.height).toLocaleString('en-AU')),el('span',{class:'node-block-main'},state.mode==='size'?(b.vsize?Math.round(b.vsize/1000)+' kvB':b.size?Math.round(b.size/1000)+' kB':'Size unavailable'):isPending?el('span',{class:'node-block-rate'},displayRate(rate)):new Date(b.timestamp*1000).toLocaleTimeString('en-AU',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',hour12:false})+' UTC'),isPending&&state.mode!=='size'?el('span',{class:'node-block-rate-unit'},'sats/vB'):null,el('span',{class:'transaction-count'},b.tx_count!==null&&b.tx_count!==undefined?Number(b.tx_count).toLocaleString('en-AU')+' transactions':''),el('span',{class:'time-difference'},isPending?'~'+b.estimated_minutes+' minutes':new Date(b.timestamp*1000).toLocaleDateString('en-AU',{timeZone:'UTC'})));
  if(marked)cube.append(el('span',{class:'node-track-marker',title:known.txid,'aria-label':'Tracked transaction'},'▼'));
  cube.title=isPending?'Estimated rate: '+rate+' sats/vB. Projected from ancestor packages in the local mempool; confirmation timing is uncertain.':b.hash;
  return cube;
 };
 future.append(el('div',{class:'block-half-label'},'PENDING · ESTIMATED'));
 // The next block is nearest the central dashed separator.
 for(const [i,b]of pending.slice(0,count).entries())future.append(create(b,i,true));
 if(!pending.length)future.append(el('p',{class:'subtle'},data.projection_error||'Waiting for the local mempool…'));
 past.append(el('div',{class:'block-half-label'},'MINED · UTC'));
 for(const [i,b]of mined.slice(start,start+count).entries())past.append(create(b,i,false));
 const previous=button('←','outline',()=>{state.offset=Math.max(0,start-count);changed();}),next=button('→','outline',()=>{state.offset=Math.min(Math.max(0,mined.length-count),start+count);changed();}),mode=button(state.mode==='size'?'Show time / fees':'Show sizes','outline',()=>{state.mode=state.mode==='size'?'fees':'size';changed();});
 previous.disabled=start===0;previous.title='More recent mined blocks';next.disabled=start+count>=mined.length;next.title='Older mined blocks';
 const centre=el('div',{class:'node-block-divider'},el('div',{class:'block-controls'},previous,mode,next));content.append(future,centre,past);section.append(top,content);
 if(state.tracked?.status==='confirmed'&&!mined.slice(start,start+count).some(b=>b.height===state.tracked.block_height))section.append(el('div',{class:'info-banner fee-warning',role:'status'},'This is an older transaction outside the displayed recent blocks. Confirmed in block '+state.tracked.block_height+'.'));
 if(state.tracked?.status==='mempool')section.append(el('p',{class:'subtle'},'Tracked transaction is in the local mempool. The marker indicates the next block; inclusion in that block is not guaranteed.'));
 if(data.projection_error&&pending.length)section.append(el('p',{class:'subtle'},data.projection_error));
 return section;
}
const api={view,blockStyle,markerIndex,displayRate};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.WallabyNodeBlocks=api;
})(typeof globalThis!=='undefined'?globalThis:this);
