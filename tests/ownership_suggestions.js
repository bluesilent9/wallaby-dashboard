const fs=require('fs'),vm=require('vm'),assert=require('assert');
function el(tag,attrs={},...children){const n={tag,attrs,children:children.flat().filter(x=>x!==null&&x!==undefined),handlers:{},append(...v){this.children.push(...v.flat());},replaceChildren(...v){this.children=v.flat();},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,f){this.handlers[k]=f;}};Object.assign(n,attrs);return n;}
const context={window:{},document:{createElementNS:()=>el('svg')}};vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const users=[{id:'a',name:'Alice'},{id:'b',name:'Bob'}];
function render(saved=[]){const data={users,user:users[0],revision:saved.length?2:1,ownership:saved,inventory_ready:true,ownership_ready:false,inventory:[{id:'wallet',kind:'descriptor',source:'opaque',source_name:'alice_smsf_watch',name:'Alice SMSF Wallet',suggested_owner_id:'a',smsf_detected:true,watch_only:true}]};return context.window.WallabyFamily.ownership({el,button:(text,cls,onclick)=>el('button',{onclick},text),icon:name=>el('svg',{name}),data,settings:{},descriptorReady:true,descriptorSection:el('div'),uploadControl:()=>el('div'),familyUploadReady:()=>true,startJob:()=>{},action:()=>{},go:()=>{},refresh:()=>{}});}
function nodes(root){return typeof root==='object'&&root!==null?[root,...root.children.flatMap(nodes)]:[];}
let list=nodes(render());let owner=list.find(n=>n.tag==='select'&&n.attrs['aria-label']==='Owner of opaque');assert.equal(owner.value,'a');assert(!owner.disabled);
let smsf=list.find(n=>n.attrs['aria-label']==='SMSF for opaque');assert(smsf.checked&&smsf.disabled);
let input=list.find(n=>n.attrs['aria-label']==='Wallet name for alice_smsf_watch');input.value='Bob savings';input.handlers.input();assert.equal(owner.value,'a');assert(smsf.checked);
let slots=nodes(list.find(n=>n.class==='ownership-icons'));assert.deepEqual(slots.filter(n=>n.tag==='svg').map(n=>n.name),['wallet','eye','smsf']);
list=nodes(render([{id:'wallet',name:'Saved name',owner_id:'b',external:false,smsf:false,display_name_set:true}]));assert.equal(list.find(n=>n.tag==='select'&&n.attrs['aria-label']==='Owner of opaque').value,'b');assert(list.find(n=>n.attrs['aria-label']==='SMSF for opaque').checked);
console.log('PASS: editable owner suggestion, saved owner precedence, forced SMSF, immutable source hints and ordered icons.');
