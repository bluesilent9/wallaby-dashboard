/* Suggestions use immutable node/source names, never edited display names. */
window.WallabyOwnershipHints=(()=>{
const common=new Set('old new wallet wallets watch watching only smsf pay payment payments bitcoin btc core descriptor descriptors account accounts node vault multisig multi signature single receiving receive change sending send distribution test backup archived archive extinct the and for from of to'.split(' '));
const words=name=>[...new Set((String(name||'').toLocaleLowerCase().match(/\p{L}+/gu)||[]).filter(w=>w.length>2&&!common.has(w)))];
function create(items,users,existing=[],restored={}){
 const state=restored,assigned=new Set(existing.filter(r=>r.owner_id||r.external).map(r=>r.id)),eligible=items.filter(w=>['descriptor','wallet'].includes(w.kind)&&!w.suggested_owner_id&&!users.some(u=>words(u.name).some(n=>words(w.source_name||w.name).includes(n)))),groups=new Map();
 for(const w of eligible)for(const word of words(w.source_name||w.name)){if(!groups.has(word))groups.set(word,[]);groups.get(word).push(w.id);}
 function assign(id,owner,current){const changes=[];for(const [word,ids]of groups){if(ids.length<2||!ids.includes(id))continue;const rule=state[word];if(rule){if(rule.active&&rule.owner!==owner&&(rule.anchor===id||rule.suggested.includes(id)))rule.active=false;continue;}if(assigned.has(id)||!owner||owner==='external')continue;const peers=ids.filter(n=>n!==id&&!assigned.has(n)&&!current(n));state[word]={active:true,owner,anchor:id,suggested:peers};for(const n of peers)if(!changes.some(r=>r.id===n))changes.push({id:n,owner});}return changes;}
 return {assign,state};
}
return {create,words};})();
