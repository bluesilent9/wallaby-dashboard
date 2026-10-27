/* Exact satoshi/pipe input conversion. Fee rates remain draft rates, not coin amounts. */
(function(root){'use strict';
const MAX_SATS=21000000n*100000000n;
function parseAmount(head,tail){
 head=String(head).trim()||'0';tail=String(tail).trim()||'0';
 if(!/^\d{1,8}(?:\.\d{1,2})?$/.test(head))throw Error('Enter the Bitcoin portion with no more than two decimal places.');
 if(!/^(?:\d{1,6}|\d{1,3},\d{3})$/.test(tail))throw Error('Enter 0–999,999 in the sats field.');
 const [whole,cents='']=head.split('.');const sats=BigInt(whole)*100000000n+BigInt(cents.padEnd(2,'0'))*1000000n+BigInt(tail.replace(',',''));
 if(BigInt(tail.replace(',',''))>999999n||sats>MAX_SATS)throw Error('The Bitcoin amount is outside the valid range.');
 return sats.toString();
}
function parseFee(value){value=String(value).trim()||'0';if(!/^\d{1,12}(?:\.\d{1,8})?$/.test(value))throw Error('Enter a non-negative fee rate with up to eight decimal places.');let [a,b='']=value.split('.');a=BigInt(a).toString();b=b.replace(/0+$/,'');return a+(b?'.'+b:'');}
function positiveFee(rate){return BigInt(String(rate||'0').replace('.',''))>0n;}
function hasData(edge){return edge.max===true||BigInt(edge.amount_sats||'0')>0n||positiveFee(edge.fee_rate);}
function amountParts(value){const sats=BigInt(value||'0');return {head:(sats/100000000n)+'.'+String(sats/1000000n%100n).padStart(2,'0'),tail:(sats%1000000n).toLocaleString('en-AU')};}
function btcValue(value){const n=BigInt(value||'0'),sats=n<0n?-n:n;return (n<0n?'-':'')+(sats/100000000n)+'.'+String(sats%100000000n).padStart(8,'0');}
function balanceSats(value){const m=String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);if(!m)throw Error('Balance unavailable');let digits=m[2]+(m[3]||''),shift=8+Number(m[4]||0)-(m[3]||'').length;if(Math.abs(shift)>100)throw Error('Balance unavailable');if(shift<0){const cut=-shift;if(/[^0]/.test(digits.slice(-cut)))throw Error('Balance below satoshi precision');digits=digits.slice(0,-cut)||'0';shift=0;}return BigInt(digits)*10n**BigInt(shift)*(m[1]==='-'?-1n:1n);}

function projection(id,balance,edges,fees){let n=balanceSats(balance);for(const e of edges){const amount=BigInt(e.amount_sats||'0');if(e.from===id)n-=amount;if(e.to===id)n+=amount;}if(fees)n-=BigInt(fees[id]||'0');return n.toString();}
const api={balanceSats,projection,parseAmount,parseFee,positiveFee,hasData,amountParts,btcValue};root.WallabyBuilderValues=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
