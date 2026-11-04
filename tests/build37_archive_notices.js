const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
class N{constructor(tag,a={},...children){this.tag=tag;Object.assign(this,a);this.children=children.flat(Infinity).filter(x=>x!=null)}append(...c){this.children.push(...c.filter(x=>x!=null))}replaceChildren(...c){this.children=c}}
const all=n=>typeof n==='object'&&n?[n,...(n.children||[]).flatMap(all)]:[],el=(...v)=>new N(...v),notices=[{id:'deleted',kind:'archive_outcome',severity:'red',target:'balances',created_utc:'2026-11-03T00:00:00z',text:'Deleted request',dismissible:true},{id:'complete',kind:'archive_outcome',severity:'green',target:'balances',created_utc:'2026-11-03T01:00:00z',text:'Verified archive',dismissible:true}];
let destination,deleted;
const c={window:{},el,noticeData:{notifications:notices},humanDate:x=>x,document:{addEventListener:()=>{},getElementById:()=>area}};vm.createContext(c);
vm.runInContext(fs.readFileSync(path.resolve(process.argv[2],'builder.js'),'utf8'),c);
const page=c.window.WallabyBuilder.notificationsView({el,button:(t,cls,onclick)=>el('button',{class:cls,onclick},t),notifications:notices,navigate:p=>destination=p,remove:id=>deleted=id});
let cards=all(page).filter(n=>n.class?.includes('notification-card'));assert(cards[0].class.includes('overdue'));assert(cards[1].class.includes('notification-success'));all(cards[0]).find(n=>n.class==='notification-open').onclick();assert.equal(destination,'balances');all(cards[1]).find(n=>n.class==='notification-delete').onclick();assert.equal(deleted,'complete');
const app=fs.readFileSync(path.resolve(process.argv[2],'app.js'),'utf8');vm.runInContext(app.slice(app.indexOf('function archiveNoticeRows('),app.indexOf('function balancesView(')),c);
let area=c.walletArchiveNotices();assert.equal(area.children.length,2);assert(area.children[0].class.includes('overdue'));assert(area.children[1].class.includes('notification-success'));assert(!all(area).some(n=>n.tag==='button'));
c.noticeData.notifications=[];c.refreshArchiveNotices();assert.equal(area.children.length,0);
console.log('PASS: deleted red/completed green in both views, outcome links, notification dismissal and isolated Wallets notice updates.');
