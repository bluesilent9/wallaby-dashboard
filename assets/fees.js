/* Exact decimal fee-rate conversions. Bitcoin Core's kvB is 1,000 vB. */
(function(root){'use strict';
function decimal(value){const s=String(value),m=s.match(/^(\d+)(?:\.(\d+))?$/);if(!m||s.length>100)throw Error('Enter a non-negative decimal rate');return {n:BigInt(m[1]+(m[2]||'')),scale:(m[2]||'').length};}
function plain(n,scale,min=0){let digits=String(n).padStart(scale+1,'0');let whole=scale?digits.slice(0,-scale):digits,frac=scale?digits.slice(-scale):'';frac=frac.replace(/0+$/,'').padEnd(min,'0');return whole+(frac?'.'+frac:'');}
function grouped(value){const [whole,fraction]=value.split('.');return whole+(fraction?'.'+fraction.match(/.{1,4}/g).join(' '):'');}
function btcTooSmall(value){const d=decimal(value);return d.n*100000000n<10n**BigInt(d.scale);}
function rates(value){const d=decimal(value);return {sat_vb:plain(d.n,d.scale),sat_kb:plain(d.n*1000n,d.scale),btc_vb:plain(d.n,d.scale+8,8),btc_kb:plain(d.n*1000n,d.scale+8,8)};}
function audCost(feeSats,quote){const d=decimal(quote),numerator=feeSats*d.n*100n,denominator=10n**BigInt(d.scale+8),cents=(numerator+denominator/2n)/denominator;const s=String(cents).padStart(3,'0');return 'AUD$'+s.slice(0,-2).replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+s.slice(-2);}
function estimate(rate,size,quote){if(!/^\d+$/.test(String(size))||BigInt(size)<1n||BigInt(size)>1000000n)throw Error('Enter a whole transaction size from 1 to 1,000,000 vB.');const d=decimal(rate),denominator=10n**BigInt(d.scale),feeSats=(d.n*BigInt(size)+denominator-1n)/denominator;return {sats:String(feeSats),btc:plain(feeSats,8,8),aud:quote===null||quote===undefined?null:audCost(feeSats,quote)};}
const api={rates,grouped,estimate,btcTooSmall};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.WallabyFees=api;
})(typeof globalThis!=='undefined'?globalThis:this);
