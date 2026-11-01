/* Calculator-only adapter around the shared public diagram editor. */
window.WallabySalary=(()=>{
function view(c){const {el,button,api,toast}=c,root=el('section',{class:'salary-page'},el('p',{class:'subtle'},'Loading Salary Builder…'));let state=null,pending=null,saving=false;
const post=async(action,value={})=>(await api('/api/salary/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),keepalive:true})).json();
async function save(value){pending={nodes:value.nodes,edges:value.edges};if(saving)return;saving=true;try{while(pending){const diagram=pending;pending=null;state=await post('save',{revision:state.revision,diagram});}}catch(e){toast(e.message);}finally{saving=false;}}
function paint(){root.replaceChildren(el('p',{class:'eyebrow'},'CALCULATIONS'),el('h1',{},'Salary Builder'));if(!state.started){root.append(button('Start Building Your Salary','primary',async()=>{try{state=await post('start');paint();}catch(e){toast(e.message);}}));return;}
root.append(el('p',{class:'intro'},'A visual calculation workspace. Arrange wallets and exchanges and enter planned Bitcoin amounts. This tool never creates, signs or broadcasts a transaction. Fee rates are planning values; projected balances exclude miner fees until a transaction size is defined.'),WallabyBuilder.calculatorView({...c,scope:'salary:'+c.user.id,workspace:{save},calculationOnly:true},state.diagram),button('Reset salary diagram','outline',async()=>{if(!confirm('Reset this salary diagram?'))return;try{while(saving)await new Promise(resolve=>setTimeout(resolve,50));state=await post('reset');paint();}catch(e){toast(e.message);}}));}
api('/api/salary').then(r=>r.json()).then(data=>{if(!root.isConnected)return;state=data;paint();}).catch(e=>root.replaceChildren(el('p',{class:'error-banner'},e.message)));return root;
}
return {view};})();
