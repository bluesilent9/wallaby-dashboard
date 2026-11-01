const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path'),root=path.resolve(process.argv[2]);
const fixture=JSON.parse(fs.readFileSync(path.join(root,'../tests/fixtures/salary-scenario.json'))),clone=x=>JSON.parse(JSON.stringify(x));
class N{
 constructor(tag,attrs={},...children){this.tag=tag;this.style={};this.attrs={};this.children=[];this.events={};this.dataset={};this.isConnected=true;for(const[k,v]of Object.entries(attrs)){if(k.startsWith('on'))this.addEventListener(k.slice(2),v);else this.setAttribute(k,v);}this.append(...children);}
 setAttribute(k,v){if(v===undefined||v===null)return;this.attrs[k]=String(v);if(k==='value')this._value=String(v);else if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v);else if(k!=='style')this[k]=v;}
 getAttribute(k){return this.attrs[k]??null}removeAttribute(k){delete this.attrs[k];if(k==='hidden')this.hidden=false;}
 toggleAttribute(k,force){if(force){this.setAttribute(k,'');this[k]=true;}else this.removeAttribute(k)}
 append(...children){for(const c of children.flat(Infinity)){if(c!==null&&c!==undefined){if(typeof c==='object')c.parent=this;this.children.push(c)}}}
 replaceChildren(...children){for(const c of this.children)if(c&&typeof c==='object')c.isConnected=false;this.children=[];this.append(...children)}
 addEventListener(k,f){(this.events[k]??=[]).push(f)}
 get value(){if(this.tag==='select')return this._value??this.children.find(x=>x.tag==='option')?.value??'';return this._value??'';}set value(v){this._value=String(v)}
 closest(selector){for(let n=this;n;n=n.parent)if(n.tag===selector)return n;return null;}
 querySelector(selector){return all(this).find(n=>selector.startsWith('[data-')?Object.hasOwn(n.attrs,selector.slice(1,-1)):false)||null;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.isConnected=false;}showModal(){this.open=true}close(){this.open=false}
 async fire(type,extra={}){for(const f of this.events[type]||[])await f({target:this,preventDefault(){},...extra});}
}
const all=n=>[n,...n.children.filter(c=>c&&typeof c==='object').flatMap(all)],text=n=>typeof n==='object'?n.children.map(text).join(' '):String(n),el=(...a)=>new N(...a);
const flush=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
async function main(){let saved={started:true,revision:0,inputs:clone(fixture.inputs)},timers=new Map(),serial=0,requests=[];const api=async(url,opt)=>({json:async()=>{if(!opt)return clone(url==='/api/salary'?saved:url==='/api/salary/context'?fixture.context:fixture.context.tax_tables);const body=JSON.parse(opt.body);requests.push({url,body});assert.equal(body.user_id,'a');if(url.endsWith('/save')){saved={started:true,revision:saved.revision+1,inputs:body.inputs};return clone(saved);}if(url.endsWith('/calculate'))return clone(fixture.result);if(url.endsWith('/template'))return {id:'saved-template',name:body.name};throw Error(url);}});
const c={window:{},document:{createElementNS:(_,tag)=>el(tag)},Intl,Date,Number,Promise,console,confirm:()=>false,setTimeout:(f,n)=>{timers.set(++serial,{f,n});return serial},clearTimeout:id=>timers.delete(id)};vm.createContext(c);for(const file of ['family.js','salary_diagram.js','salary.js']){vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),c);c.WallabySalaryDiagram=c.window.WallabySalaryDiagram;}
const page=c.window.WallabySalary.view({el,button:(label,klass,f)=>el('button',{class:klass,onclick:f},label),icon:n=>el('svg',{'data-icon':n}),api,toast:()=>{},user:fixture.context.user});await flush();const nodes=()=>all(page),findLabel=s=>nodes().find(n=>n.attrs['aria-label']===s),cards=()=>nodes().filter(n=>String(n.attrs.class).includes('salary-person-card')),buttonText=s=>nodes().find(n=>n.tag==='button'&&text(n)===s);assert.equal(cards().length,3);assert(text(page).includes('Stage 1 - Where will the money come from?'));assert(text(page).includes('Stage 2 - What would you like to pay?'));assert(!text(page).includes('Partner allocation mode'));
const a=cards()[0];await a.fire('click');let dialog=nodes().find(n=>n.tag==='dialog');assert(dialog.open);let target=findLabel('Annual salary target · AUD');target.value='90000';await nodes().find(n=>n.tag==='form'&&n.parent===dialog).fire('submit');assert(!nodes().some(n=>n.tag==='dialog'));assert(text(page).includes('$90,000.00 annual target'));
const save=async()=>{await [...timers.values()].filter(t=>t.n===550).at(-1).f();await flush();};await save();assert.equal(requests.filter(r=>r.url.endsWith('/save')).at(-1).body.inputs.people[0].target,'90000');
// Chained recipient edits share, never a second salary target.
await cards()[1].fire('click');dialog=nodes().find(n=>n.tag==='dialog');assert(findLabel('Allocation share · %'));assert(!findLabel('Annual salary target · AUD'));findLabel('Allocation share · %').value='15';await nodes().find(n=>n.tag==='form'&&n.parent===dialog).fire('submit');await save();assert.equal(saved.inputs.allocations[0].share,'15');
// Persist exact card placement after dragging, without changing identities.
const card=cards()[0];await card.fire('pointerdown',{button:0,clientX:10,clientY:10,target:card});await card.fire('pointermove',{clientX:50,clientY:70});await card.fire('pointerup');await save();assert.deepEqual(saved.inputs.layout.a,{x:65,y:85});
// Naming cancellation does not save a transaction template.
assert.equal(buttonText('Save Transaction Template').disabled,false);await buttonText('Save Transaction Template').fire('click');assert(findLabel('Transaction Template name'));await buttonText('Cancel').fire('click');assert(!requests.some(r=>r.url.endsWith('/template')));
await buttonText('Save Transaction Template').fire('click');dialog=nodes().find(n=>n.tag==='dialog');findLabel('Transaction Template name').value='First payday';await nodes().find(n=>n.tag==='form'&&n.parent===dialog).fire('submit');assert.equal(requests.filter(r=>r.url.endsWith('/template')).length,1);assert.equal(requests.at(-1).body.name,'First payday');assert(!requests.some(r=>/\/api\/(nodes|transaction|builder)\//.test(r.url)));
console.log('PASS: actual funding/user canvases, target/share modal controls, chain edits, drag persistence, cancelled naming and user-scoped template save.');}
main().catch(e=>{console.error(e);process.exit(1)});
