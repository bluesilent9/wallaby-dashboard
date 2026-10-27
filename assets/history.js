/* Presentation-only wallet graph and transaction ports. Tax parcels stay intact. */
(function(root){
'use strict';
function decimalSum(values){const parts=values.map(value=>{const m=String(value||'0').match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);if(!m)throw Error('Invalid parcel amount');return {digits:BigInt((m[1]==='-'?'-':'')+m[2]+(m[3]||'')),scale:(m[3]||'').length-Number(m[4]||0)};});const scale=Math.max(0,...parts.map(p=>p.scale)),total=parts.reduce((sum,p)=>sum+p.digits*10n**BigInt(scale-p.scale),0n),negative=total<0n;let s=String(negative?-total:total).padStart(scale+1,'0');if(scale)s=s.slice(0,-scale)+'.'+s.slice(-scale);return (negative?'-':'')+s;}
function txid(record){const value=record?.blockchain_txid||record?.transaction_id||'';return /^[0-9a-f]{64}$/i.test(value)?value.toLowerCase():null;}
function address(value){return typeof value==='string'&&(/^[13][1-9A-HJ-NP-Za-km-z]{25,34}$/.test(value)||/^(bc1)[ac-hj-np-z02-9]{8,87}$/.test(value))?value:null;}
function txURL(record){const id=txid(record);return id?'https://blockstream.info/tx/'+id:null;}
function addressURL(value){const a=address(value);return a?'https://blockstream.info/address/'+a:null;}
function recordIdentity(record){const id=txid(record);if(id)return JSON.stringify(['onchain',id]);if(record?.transaction_id)return JSON.stringify(['record',record.transaction_id]);return JSON.stringify([record?.utc,record?.source_file,record?.source_row,record?.source_page,record?.record_type,record?.amount,record?.btc]);}
function day(value){const m=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})/);return m?m[3]+'/'+m[2]+'/'+m[1]:null;}
// Precision is chosen only for box summaries; transaction details retain the raw UTC.
function dateLabel(first,last){
 const a=String(first||last||''),b=String(last||first||'');if(!day(a))return 'UTC time unavailable';
 const time=value=>{const m=value.match(/T(\d{2}:\d{2}:\d{2})/);return m?.[1]||null;};
 if(a===b||Number.isFinite(Date.parse(a))&&Date.parse(a)===Date.parse(b))return day(a)+(time(a)?' '+time(a):'')+' UTC';
 if(day(a)===day(b)&&time(a)&&time(b))return day(a)+' '+time(a).slice(0,5)+' - '+time(b).slice(0,5)+' UTC';
 return day(a)+' - '+day(b)+' UTC';
}
function build(event){
const nodes=[],edges=[],intern=new Map(),edgeIndex=new Map();if(!event.paths?.length)return {nodes,edges,width:0,height:0};
const terminal=event.disposal||event.paths[0].nodes.at(-1);
function make(original,key){const n={id:'n'+nodes.length,node:original,key,members:[],incoming:[],outgoing:[],records:[],parents:[],children:[]};nodes.push(n);return n;}
const sink=make(terminal,'disposal');
function member(list,i){if(!list.includes(i))list.push(i);}
function port(n,side,record,i){if(!record)return null;const key=recordIdentity(record);let p=n[side].find(p=>p.key===key);if(!p){p={id:n.id+'-'+side+'-'+n[side].length,key,data:{...record},records:[],members:[],side};n[side].push(p);}member(p.members,i);const raw={...record};delete raw.source_lot_btc;const signature=JSON.stringify(raw);if(!p.records.some(r=>JSON.stringify(r)===signature))p.records.push(raw);return p;}
for(const [i,path]of event.paths.entries()){
 const chain=[];
 for(const [j,original]of path.nodes.entries()){
  const end=j===path.nodes.length-1;
  // Wallet identity is shared across receipt visits; exchange purchases keep
  // their acquisition identity, and the disposal remains one event endpoint.
  const key=(original.kind==='wallet'||original.kind==='exchange'&&original.stage==='receipt')&&original.stage!=='acquisition'?JSON.stringify(['account',original.kind,original.label]):JSON.stringify([original.kind,original.label,original.stage,original.utc,recordIdentity(original.incoming||original.details)]);
  let n=end?sink:intern.get(key);if(!n){n=make(original,key);intern.set(key,n);}member(n.members,i);n.records.push({node:original,path:i});chain.push(n);
 }
 for(let j=1;j<chain.length;j++){
  const a=chain[j-1],b=chain[j],from=port(a,'outgoing',path.nodes[j-1].outgoing||path.nodes[j-1].details,i),to=port(b,'incoming',path.nodes[j].incoming||path.nodes[j].details,i),key=JSON.stringify([a.id,from.id,b.id,to.id]);
  let edge=edgeIndex.get(key);if(!edge){edge={from:a.id,to:b.id,fromPort:from.id,toPort:to.id,members:[]};edgeIndex.set(key,edge);edges.push(edge);if(a!==b){if(!a.children.includes(b))a.children.push(b);if(!b.parents.includes(a))b.parents.push(a);}}member(edge.members,i);
 }
}
// Order transaction ports chronologically; their data stays independent even
// when multiple acquisitions contribute to the same transaction.
for(const n of nodes){
 for(const side of ['incoming','outgoing']){for(const p of n[side])p.records.sort((a,b)=>String(a.utc||'').localeCompare(String(b.utc||''))||Number(a.source_row||0)-Number(b.source_row||0));n[side].sort((a,b)=>String(a.data.utc||'').localeCompare(String(b.data.utc||''))||a.key.localeCompare(b.key));for(const p of n[side]){p.data={...p.data,allocated_btc:decimalSum(p.members.map(i=>event.paths[i].allocated_btc)),allocated_cost_base_aud:decimalSum(p.members.map(i=>event.paths[i].allocated_cost_aud)),acquisition_path_count:p.members.length};delete p.data.source_lot_btc;}}
 const ins=n.incoming.flatMap(p=>p.records.map(r=>r.utc)).filter(Boolean).sort(),outs=n.outgoing.flatMap(p=>p.records.map(r=>r.utc)).filter(Boolean).sort();n.first_utc=ins[0]||n.node.utc;n.last_utc=outs.at(-1)||ins.at(-1)||n.node.utc;
 n.date_label=dateLabel(n.first_utc,n.last_utc);
 const addresses=[];function add(a){if(address(a)&&!addresses.includes(a))addresses.push(a);}
 const ownedRecords=n.records.flatMap(item=>item.node.wallet_address_records||[]).sort((a,b)=>String(a.utc||'').localeCompare(String(b.utc||'')));
 if(ownedRecords.length){for(const r of ownedRecords)if(r.direction==='incoming')for(const a of r.addresses)add(a);}
 else for(const p of n.incoming)for(const r of p.records)if(!r.location||r.location===n.node.label){for(const a of r.wallet_addresses||[])add(a);add(r.address);}
 if(!ownedRecords.length&&!n.incoming.length)for(const item of n.records){const record=item.node.incoming||{};for(const a of record.wallet_addresses||[])add(a);add(record.address);}
 const firstAddress=addresses[0];if(ownedRecords.length){for(const r of ownedRecords)for(const a of r.addresses)add(a);}else for(const p of n.outgoing)for(const r of p.records)if(!r.location||r.location===n.node.label)for(const a of r.wallet_addresses||[])add(a);
 n.addresses=addresses;n.default_address=firstAddress||addresses[0]||null;
 n.parcel_btc=decimalSum(n.members.map(i=>event.paths[i].allocated_btc));n.parcel_cost=decimalSum(n.members.map(i=>event.paths[i].allocated_cost_aud));
 n.width=190;n.height=Math.max(110,36+Math.max(n.incoming.length,n.outgoing.length)*25);
}
// Condense cycles before assigning columns. A wallet visited again remains a
// single box; return transfers are drawn as arcs rather than duplicated boxes.
let count=0;const stack=[],components=[];function visit(n){n._index=n._low=count++;stack.push(n);n._stack=true;for(const c of n.children){if(c._index===undefined){visit(c);n._low=Math.min(n._low,c._low);}else if(c._stack)n._low=Math.min(n._low,c._index);}if(n._low===n._index){const component=[];let c;do{c=stack.pop();c._stack=false;c.component=components.length;component.push(c);}while(c!==n);components.push(component);}}
for(const n of nodes)if(n._index===undefined)visit(n);
// A strongly connected component is a return visit, not a reason to stack all
// of its accounts in the same column. Keep first-visit order, then reduce the
// number of backward transfers without reversing equally good chronological order.
const order=new Map(nodes.map((n,i)=>[n,i]));
for(const component of components){
 component.sort((a,b)=>order.get(a)-order.get(b));
 const internal=edges.filter(e=>component.some(n=>n.id===e.from)&&component.some(n=>n.id===e.to));
 const score=()=>{const positions=new Map(component.map((n,i)=>[n.id,i]));return internal.reduce((s,e)=>s+(positions.get(e.from)>=positions.get(e.to)?1:0),0);};
 for(let pass=0;pass<component.length;pass++){let changed=false;for(let i=1;i<component.length;i++){const before=score();[component[i-1],component[i]]=[component[i],component[i-1]];if(score()<before)changed=true;else [component[i-1],component[i]]=[component[i],component[i-1]];}if(!changed)break;}
}
const ranks=new Map();function distance(i){if(ranks.has(i))return ranks.get(i);const children=new Set(components[i].flatMap(n=>n.children).map(n=>n.component).filter(j=>j!==i));const value=components[i].length+(children.size?Math.max(...[...children].map(distance)):0);ranks.set(i,value);return value;}
const maxDistance=Math.max(...components.map((_,i)=>distance(i)));for(const n of nodes)n.column=maxDistance-distance(n.component)+components[n.component].indexOf(n);
// Invisible routing lanes split long transfers at each intervening column.
// They participate in ordering just like accounts, but never become wallet boxes.
const byId=new Map(nodes.map(n=>[n.id,n])),vertices=[...nodes],links=[],dummyIndex=new Map();
for(const e of edges){const a=byId.get(e.from),b=byId.get(e.to);e.via=[];if(b.column<=a.column)continue;let previous=a;
 for(let c=a.column+1;c<b.column;c++){const key=JSON.stringify([e.toPort,c]);let v=dummyIndex.get(key);if(!v){v={id:'lane'+dummyIndex.size,column:c,width:190,height:16,members:e.members.slice(),incoming:[],outgoing:[],dummy:true};dummyIndex.set(key,v);vertices.push(v);}for(const endpoint of [a])if(components[endpoint.component].length>1&&a.component!==b.component){v.below||=new Set();for(const n of components[endpoint.component])if(n.column===c)v.below.add(n);}e.via.push(v);links.push({from:previous,to:v,edge:e});previous=v;}
 links.push({from:previous,to:b,edge:e});
}
const cols=[];for(const n of vertices)(cols[n.column]||=[]).push(n);for(const col of cols)col.sort((a,b)=>Math.min(...a.members)-Math.min(...b.members)||Number(!!a.dummy)-Number(!!b.dummy)||a.id.localeCompare(b.id));
function constrain(col){for(const v of col.filter(n=>n.below?.size)){const last=Math.max(...[...v.below].map(n=>col.indexOf(n)));const at=col.indexOf(v);if(at<last){col.splice(at,1);col.splice(last,0,v);}}}
for(const col of cols)constrain(col);
const loopMargin=140,padding=80,gapY=48,stepX=300;let graphHeight=180;for(const col of cols)graphHeight=Math.max(graphHeight,padding*2+col.reduce((s,n)=>s+n.height+gapY,0)-gapY);
function place(){for(const col of cols){const used=col.reduce((s,n)=>s+n.height+gapY,0)-gapY;let y=(graphHeight-used)/2;for(const n of col){n.x=loopMargin+n.column*stepX;n.y=y;n.cy=y+n.height/2;y+=n.height+gapY;for(const side of ['incoming','outgoing'])n[side].forEach((p,i)=>{p.x=side==='incoming'?n.x:n.x+n.width;p.y=n.y+n.height*(i+1)/(n[side].length+1);p.offset=100*(i+1)/(n[side].length+1);});}}}
function endY(link,side){const v=link[side];if(v.dummy)return v.cy;const list=side==='from'?v.outgoing:v.incoming,id=side==='from'?link.edge.fromPort:link.edge.toPort;return list.find(p=>p.id===id).y;}
function arrangePorts(){for(const n of nodes)for(const side of ['incoming','outgoing']){
 const neighbours=p=>edges.filter(e=>side==='incoming'?e.toPort===p.id:e.fromPort===p.id).map(e=>{const other=side==='incoming'?(e.via.at(-1)||byId.get(e.from)):(e.via[0]||byId.get(e.to));const back=byId.get(e.from).column>=byId.get(e.to).column;const opposite=other.dummy?null:(side==='incoming'?other.outgoing:other.incoming).find(q=>q.id===(side==='incoming'?e.fromPort:e.toPort));return {other,back,y:opposite?.y??other.cy};});
 const placement=p=>{const adjacent=neighbours(p);return [adjacent.some(a=>a.back)?0:1,adjacent.reduce((s,a)=>s+a.y,0)/(adjacent.length||1)];};
 n[side].sort((a,b)=>{const x=placement(a),y=placement(b);return x[0]-y[0]||x[1]-y[1]||String(a.data.utc||'').localeCompare(String(b.data.utc||''))||a.key.localeCompare(b.key);});
 n[side].forEach((p,i)=>{p.y=n.y+n.height*(i+1)/(n[side].length+1);p.offset=100*(i+1)/(n[side].length+1);});
}}
function crossings(){let count=0;for(let i=0;i<links.length;i++)for(let j=i+1;j<links.length;j++){const a=links[i],b=links[j];if(a.from.column!==b.from.column)continue;if((endY(a,'from')-endY(b,'from'))*(endY(a,'to')-endY(b,'to'))<-.01)count++;}return count;}
place();for(let i=0;i<4;i++)arrangePorts();let best=crossings(),snapshot=()=>({cols:cols.map(c=>c.slice()),ports:nodes.map(n=>[n.incoming.slice(),n.outgoing.slice()])}),saved=snapshot();
for(let pass=0;pass<12;pass++){
 for(const reverse of [true,false]){for(let i=0;i<cols.length;i++){const c=reverse?cols.length-1-i:i;
 const target=n=>{const ns=links.filter(l=>reverse?l.from===n:l.to===n);return ns.length?ns.reduce((s,l)=>s+endY(l,reverse?'to':'from'),0)/ns.length:n.cy;};
 cols[c].sort((a,b)=>target(a)-target(b)||Math.min(...a.members)-Math.min(...b.members));constrain(cols[c]);place();for(let k=0;k<4;k++)arrangePorts();
 }const score=crossings();if(score<best){best=score;saved=snapshot();}}
 if(best===0)break;
}
for(let c=0;c<cols.length;c++)cols[c]=saved.cols[c];nodes.forEach((n,i)=>{[n.incoming,n.outgoing]=saved.ports[i];});place();
const ports=new Map(nodes.flatMap(n=>[...n.incoming,...n.outgoing]).map(p=>[p.id,p]));let minY=0,maxY=graphHeight;
for(const e of edges){const a=ports.get(e.fromPort),b=ports.get(e.toPort);e.x1=a.x;e.y1=a.y;e.x2=b.x;e.y2=b.y;e.btc=decimalSum(e.members.map(i=>event.paths[i].allocated_btc));
 const blockers=nodes.filter(n=>n.id!==e.from&&n.id!==e.to&&n.x<e.x2&&n.x+n.width>e.x1);
 e.points=[a.x,a.y,a.x+(b.x-a.x)/2,a.y,b.x-(b.x-a.x)/2,b.y,b.x,b.y];
 if(b.x<=a.x){const top=Math.min(...nodes.map(n=>n.y)),others=nodes.filter(n=>n.id!==e.from&&n.id!==e.to),middle=(a.x+b.x)/2;for(let extra=100;extra<=51200;extra*=2){const lane=top-extra;e.points=[[a.x,a.y,a.x+100,a.y,a.x+100,lane,middle,lane],[middle,lane,b.x-100,lane,b.x-100,b.y,b.x,b.y]];if(!hits(e.points,others))break;}}
 else if(blockers.length&&hits(e.points,blockers)){
  const top=Math.min(...blockers.map(n=>n.y)),bottom=Math.max(...blockers.map(n=>n.y+n.height));
  let found=false;for(let extra=120;extra<=960&&!found;extra+=120){const lanes=[top-extra,bottom+extra].sort((x,y)=>Math.abs(x-(a.y+b.y)/2)-Math.abs(y-(a.y+b.y)/2));for(const lane of lanes){const points=[a.x,a.y,a.x+(b.x-a.x)/3,lane,b.x-(b.x-a.x)/3,lane,b.x,b.y];if(!hits(points,blockers)){e.points=points;found=true;break;}}}
 }
 if(e.via.length){const segments=[];let previous=[a.x,a.y];for(const v of e.via){const left=[v.x,v.cy],right=[v.x+v.width,v.cy];segments.push(curve(previous,left),curve(left,right));previous=right;}segments.push(curve(previous,[b.x,b.y]));if(!hits(segments,nodes.filter(n=>n.id!==e.from&&n.id!==e.to)))e.points=segments;}
 for(let i=0;i<=160;i++){const point=bezier(e.points,i/160);minY=Math.min(minY,point[1]);maxY=Math.max(maxY,point[1]);}
}
const offset=Math.max(0,30-minY);if(offset){for(const n of vertices){n.y+=offset;n.cy+=offset;for(const p of [...n.incoming,...n.outgoing])p.y+=offset;}for(const e of edges){e.y1+=offset;e.y2+=offset;for(const p of pieces(e.points))for(let i=1;i<p.length;i+=2)p[i]+=offset;}}
for(const e of edges){e.d=pieces(e.points).map((p,i)=>(i?'':`M${p[0]} ${p[1]} `)+(Math.abs(p[1]-p[7])<.01&&p[3]===p[1]&&p[5]===p[7]?`L${p[6]} ${p[7]}`:`C${p[2]} ${p[3]} ${p[4]} ${p[5]} ${p[6]} ${p[7]}`)).join(' ');}
graphHeight=maxY+offset;

return {nodes,edges,sink:sink.id,width:loopMargin*2+(cols.length-1)*stepX+190,height:graphHeight+40};
}
function pieces(p){return Array.isArray(p[0])?p:[p];}
function curve(a,b){return [a[0],a[1],(a[0]+b[0])/2,a[1],(a[0]+b[0])/2,b[1],b[0],b[1]];}
function bezier(p,t){if(Array.isArray(p[0])){const f=Math.min(p.length-1,Math.floor(t*p.length));return bezier(p[f],t===1?1:t*p.length-f);}const a=1-t;return [a*a*a*p[0]+3*a*a*t*p[2]+3*a*t*t*p[4]+t*t*t*p[6],a*a*a*p[1]+3*a*a*t*p[3]+3*a*t*t*p[5]+t*t*t*p[7]];}
function hits(points,boxes){if(Array.isArray(points[0]))return points.some(p=>hits(p,boxes));for(let i=1;i<80;i++){const [x,y]=bezier(points,i/80);if(boxes.some(n=>x>n.x-6&&x<n.x+n.width+6&&y>n.y-6&&y<n.y+n.height+6))return true;}return false;}
function connector(x1,y1,x2,y2){if(x2<=x1){const arc=Math.min(y1,y2)-90;return `M${x1} ${y1} C${x1+120} ${arc} ${x2-120} ${arc} ${x2} ${y2}`;}if(Math.abs(y1-y2)<.01)return `M${x1} ${y1} L${x2} ${y2}`;const handle=(x2-x1)/2;return `M${x1} ${y1} C${x1+handle} ${y1} ${x2-handle} ${y2} ${x2} ${y2}`;}
const api={build,dateLabel,connector,decimalSum,recordIdentity,txid,txURL,address,addressURL,bezier,hits};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.WallabyHistory=api;
})(typeof globalThis!=='undefined'?globalThis:this);
