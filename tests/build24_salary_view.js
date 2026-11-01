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
 async fire(type){for(const f of this.events[type]||[])await f({target:this});}
}
const all=n=>[n,...n.children.filter(c=>c&&typeof c==='object').flatMap(all)],text=n=>typeof n==='object'?n.children.map(text).join(' '):String(n),el=(...a)=>new N(...a);
const flush=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
async function main(){let saved={started:true,revision:0,inputs:clone(fixture.inputs)},writes=[],calculations=[],timers=new Map(),counter=0,delay=null;
const api=async(url,opt)=>{if(!opt)return {json:async()=>clone(url==='/api/salary'?saved:url==='/api/salary/context'?fixture.context:fixture.context.tax_tables)};const body=JSON.parse(opt.body);assert.equal(body.user_id,'a');if(url.endsWith('/save')){assert.equal(body.revision,saved.revision);saved={started:true,revision:saved.revision+1,inputs:body.inputs};writes.push(clone(body.inputs));return {json:async()=>clone(saved)}}if(url.endsWith('/calculate')){calculations.push(clone(body.inputs));return {json:async()=>{if(delay)await delay.promise;return clone(fixture.result)}}}throw Error(url)};
const c={window:{},document:{createElementNS:(_,tag)=>el(tag)},Intl,Date,Number,Promise,console,confirm:()=>false,setTimeout:(f,n)=>{timers.set(++counter,{f,n});return counter},clearTimeout:id=>timers.delete(id)};vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(root,'family.js'),'utf8'),c);vm.runInContext(fs.readFileSync(path.join(root,'salary_diagram.js'),'utf8'),c);c.WallabySalaryDiagram=c.window.WallabySalaryDiagram;vm.runInContext(fs.readFileSync(path.join(root,'salary.js'),'utf8'),c);const page=c.window.WallabySalary.view({el,button:(label,klass,f)=>el('button',{class:klass,onclick:f},label),icon:name=>el('svg',{'data-icon':name}),api,toast:()=>{},user:fixture.context.user});await flush();
assert.equal(calculations.length,1);assert(text(page).includes('Your calculated allocations'));assert(text(page).includes('53 actual paydays'));const field=name=>all(page).find(n=>n.attrs['aria-label']===name);const base=field('Scenario BTC/AUD');base.value='42000';await base.fire('input');assert.equal(field('Scenario BTC/AUD'),base,'editing must preserve the original input node');let t=[...timers.values()].find(t=>t.n===550);await t.f();await flush();assert.equal(writes.at(-1).price_aud,'42000');assert.equal(calculations.at(-1).price_aud,'42000');
const frequency=field('Pay frequency');frequency.value='monthly';await frequency.fire('change');assert.equal(field('Day of the month').closest('label').hidden,false);assert.equal(field('Day of the week').closest('label').hidden,true);assert.equal(field('One known fortnightly payday').closest('label').hidden,true);
// Delay a result across a newer edit: the input remains in place and the latest
// request is sent rather than replaced by stale server values.
let resolve;delay={promise:new Promise(r=>resolve=r)};t=[...timers.values()].at(-1);const request=t.f();await flush();base.value='43000';await base.fire('input');resolve();delay=null;await request;await flush();assert.equal(base.value,'43000');assert.equal(calculations.at(-1).price_aud,'43000');assert.equal(field('Scenario BTC/AUD'),base);
assert(!all(page).some(n=>n.attrs['aria-label']==='Wallet or exchange to add'));assert(all(page).some(n=>n.tag==='svg'&&n.attrs['aria-label']?.includes('Projected remaining')));console.log('PASS: complete salary view renders, scoped saves/calculate complete, schedule controls switch and delayed results preserve current typing.');}
main().catch(e=>{console.error(e);process.exit(1)});
