const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const s=fs.readFileSync(path.join(path.resolve(process.argv[2]),'app.js'),'utf8');
async function scenario(route,status,follow,pending=null){
 let page=route;const data={status,events:[],mode:'inventory',user:{id:'u'},job_ready:status==='complete',report_generation:null,node_ready:true,descriptors_ready:true,ownership_context:null,pending};
 const c={console,applicationClosed:false,pollBusy:false,offline:false,token:'test',status:'running',jobEpoch:0,lastSequence:0,pending:null,stage:'loading',stageLabel:'',report:null,jobMode:'inventory',jobLoaded:false,followJob:follow,logs:[],activityStarted:true,serverBusy:true,discoveryActivity:{},feeActivity:{},family:{loaded:true,user:{id:'u'},node_ready:true,ownership_context:null,report_generation:null},settings:{},api:async()=>({json:async()=>data}),loadFamily:async()=>{},route:()=>page,go:p=>page=p,render:()=>{},renderLive:()=>{},updateHeader:()=>{},toast:()=>{},appendLog:()=>{},scrollOwnership:()=>{},jobActive:()=>['running','waiting'].includes(c.status),queueMicrotask:()=>{},setInterval:()=>{},WallabyWorkspace:{},requestAnimationFrame:()=>{}};
 vm.createContext(c);vm.runInContext(s.slice(s.indexOf('async function poll()'),s.indexOf('function enterView')),c);await c.poll();return page;
}
(async()=>{
 assert.equal(await scenario('nodes','complete',true),'nodes');
 assert.equal(await scenario('nodes','error',true),'nodes');
 assert.equal(await scenario('nodes','waiting',false,{sequence:1}),'nodes');
 assert.equal(await scenario('activity','complete',true),'ownership');
 const api=s.slice(s.indexOf('async function api('),s.indexOf('async function requestApi('));assert(!api.includes("go('ownership')"));
 console.log('PASS: background/other-tab jobs and completed requests do not steal navigation; explicitly followed Ownership jobs return normally.');
})().catch(e=>{console.error(e);process.exit(1)});
