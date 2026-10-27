const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[2],'utf8');
const take=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
async function main(){
 const stale=deferred(),upload=deferred();let calls=0;
 const c={console,Intl,setTimeout,clearTimeout,queueMicrotask,performance:{now:()=>0},location:{hash:'#/ownership'},history:{},document:{querySelectorAll:()=>[]},WallabyDescriptors:{canContinue:()=>true},WallabyBuilder:{},offline:false,token:'test',applicationClosed:false,pollBusy:false,jobEpoch:0,lastSequence:0,status:'complete',pending:null,stage:'loading',stageLabel:'',report:null,jobMode:'descriptors',jobLoaded:true,followJob:false,logs:[],activityStarted:true,serverBusy:false,settings:{},family:{loaded:true,user:{id:'example'},descriptors:{ready:true},inventory_ready:false,report_generation:null,ownership_context:null},familyUploadReady:()=>true,uploadInputs:()=>upload.promise,post:async()=>({}),api:async()=>({json:()=>++calls===1?stale.promise:Promise.resolve({status:'complete',events:[],mode:'inventory',user:{id:'example'},job_ready:true,report_generation:null,inventory_ready:true})}),render:()=>{},go:name=>{c.location.hash='#/'+name;},route:()=>c.location.hash.slice(2),toast:()=>{},appendLog:x=>c.logs.push(x),jobActive:()=>['uploading','running','waiting'].includes(c.status),scrollOwnership:id=>c.scrolled=id,loadFamily:async()=>{c.family.inventory_ready=true;c.family.inventory=[{id:'wallet'}];},updateHeader:()=>{}};
 vm.createContext(c);vm.runInContext(take('async function startJob(','function auxiliaryActivityView')+take('async function poll()','function enterView'),c);
 const oldPoll=c.poll();await Promise.resolve();const run=c.startJob('inventory');
 assert.equal(c.route(),'activity','must switch immediately, before uploads finish');assert.equal(c.status,'uploading');
 stale.resolve({status:'complete',events:[],mode:'descriptors',user:{id:'example'},job_ready:true,report_generation:null});await oldPoll;
 assert.equal(c.status,'uploading','old descriptor poll must not replace the new inventory job');assert.equal(c.route(),'activity');assert.equal(c.jobLoaded,false);
 upload.resolve();await run;
 assert.equal(c.route(),'ownership');assert.equal(c.scrolled,'ownership-confirm');assert.equal(c.family.inventory_ready,true);assert.equal(c.jobLoaded,true);
 const attrs=()=>({attributes:{},setAttribute(k,v){this.attributes[k]=v;},append(p){this.path=p;}});const icons={document:{createElementNS:()=>attrs()}};vm.createContext(icons);vm.runInContext(take('const icons=','function el('),icons);
 const fossil=icons.icon('extinct');assert.equal(fossil.attributes.viewBox,'0 0 128 96');assert.equal(fossil.attributes.fill,'#000');assert.equal(fossil.attributes.stroke,'none');assert.equal(fossil.path.attributes['fill-rule'],'evenodd');
 assert.match(source,/historical:\{[^}]+kind:'extinct'/);
 console.log('PASS: delayed prior-job poll ignored; immediate Activity and completed Stage 3; shared filled fossil upload icon.');
}
main().catch(e=>{console.error(e);process.exit(1);});
// Restoring a server-verified descriptor snapshot must not depend on this tab
// having witnessed the original load operation.
{
 const c={window:{}};vm.createContext(c);vm.runInContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[2]),'descriptors.js'),'utf8'),c);
 assert.equal(c.window.WallabyDescriptors.canContinue({ready:true}),true);
 assert.equal(c.window.WallabyDescriptors.canContinue({ready:false}),false);
}
