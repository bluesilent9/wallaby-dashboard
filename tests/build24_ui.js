const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path'),root=path.resolve(process.argv[2]);
const code=fs.readFileSync(path.join(root,'salary.js'),'utf8'),c={window:{}};vm.createContext(c);vm.runInContext(code,c);const s=c.window.WallabySalary;
assert.equal(s.dateLabel('2028-02-29'),'29/02/2028');
for(const [frequency,kind]of [['daily',null],['weekly','weekly'],['fortnightly','fortnightly'],['monthly','monthly']]){const v=s.controlsVisible({frequency,partner_mode:'independent'});for(const k of ['weekly','fortnightly','monthly'])assert.equal(v[k],k===kind);assert(v.independent);assert(!v.share);}
assert(s.controlsVisible({frequency:'daily',partner_mode:'share'}).share);
for(const url of ['/api/transaction','/api/wallet-builder','/api/nodes/rpc','/api/builder/agent-lease'])assert(!code.includes(url),'Salary must not send financial or Agent mutation requests');
assert(code.includes('user_id:c.user.id'));assert(code.includes("post('save',{revision:state.revision,inputs:snapshot})"));assert(code.includes('if(version!==edit){pending=true;continue;}'));assert(!code.includes('WallabyBuilder.calculatorView'),'retired generic diagram must not duplicate salary state');
console.log('PASS: calendar/partner control modes, Australian dates, scoped versioned autosave, stale edit protection and no Agent/transaction endpoints.');
