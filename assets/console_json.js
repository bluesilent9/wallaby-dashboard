(function(root){'use strict';
const privateMaterial=/\b[xtyzuYZUV]prv[1-9A-HJ-NP-Za-km-z]+|[(\s][5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}(?=[/)\s])/;
function extractAll(text,kind='descriptors'){
 if(typeof text!=='string'||!text.trim()||new TextEncoder().encode(text).length>4194304)throw Error('Paste up to 4 MB of public descriptor console output.');
 if(privateMaterial.test(text))throw Error('Private key material rejected. Clear the console and run listdescriptors false.');
 let position=0,attempts=0;const found=[];
 while(true){const begin=text.indexOf('{',position);if(begin<0)break;if(++attempts>4096)throw Error('Too many unrelated JSON fragments. Clear the console first.');
 const stack=[];let quoted=false,escaped=false,end=-1;
 for(let i=begin;i<text.length;i++){const c=text[i];if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;continue;}if(c==='"'){quoted=true;continue;}if(c==='{'||c==='['){stack.push(c);if(stack.length>128)break;}else if(c==='}'||c===']'){if(stack.pop()!==(c==='}'?'{':'['))break;if(!stack.length){end=i+1;break;}}}
 if(end<0){position=begin+1;continue;}let value;try{value=JSON.parse(text.slice(begin,end));}catch{position=begin+1;continue;}position=end;
 if(value&&typeof value==='object'&&!Array.isArray(value)&&(kind==='history'?Array.isArray(value.transactions)&&typeof value.lastblock==='string':Array.isArray(value.descriptors)))found.push(value);
 }
 if(!found.length)throw Error(kind==='history'?'No complete listsinceblock response found. Copy its entire console output.':'No complete listdescriptors response found. Copy the entire console output for each wallet.');return found;
}
const api={extractAll};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.WallabyConsole=api;
})(typeof globalThis!=='undefined'?globalThis:this);
