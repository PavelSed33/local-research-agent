'use strict';
// Execute production request handlers; no claim of browser or layout acceptance.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function harness(){
 const elements=new Map(), requests=[], downloads=[];
 class Element{
  constructor(){this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.files=[];this.options=[];this.listeners=new Map();this.selectedOptions=[];}
  addEventListener(event,fn){if(!this.listeners.has(event))this.listeners.set(event,[]);this.listeners.get(event).push(fn);}
  replaceChildren(){this.options=[];}
  append(...items){this.options.push(...items);}
  click(){if(this.href)downloads.push(this.download);}
 }
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
 let release;
 const context={document:{getElementById:el,querySelectorAll:()=>[...elements.values()],createElement:()=>new Element()},
  Blob,Uint8Array,crypto:{randomUUID:()=> 'test-job'},setTimeout:fn=>fn(),
  atob:s=>Buffer.from(s,'base64').toString('binary'),btoa:s=>Buffer.from(s,'binary').toString('base64'),
  URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},
  fetch:async(route,options)=>{requests.push({route,options});return new Promise(resolve=>{release=data=>resolve({ok:true,status:200,json:async()=>data})})}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/workspace.js'),'utf8'),context);
 el('workspace-token').value='old-token';el('matter-list').value='old-matter';vm.runInContext("activeMatter='old-matter'",context);
 const fire=(id,event)=>{for(const fn of el(id).listeners.get(event)||[])fn({preventDefault(){}})};
 const settle=async()=>{for(let i=0;i<30;i++){await new Promise(resolve=>setImmediate(resolve));if(!vm.runInContext('busy',context))return}throw Error('UI did not settle')};
 return {el,fire,settle,context,requests,downloads,release:data=>release(data)};
}
const privateSearch={matches:[{title:'PRIVATE TITLE',text:'PRIVATE DOCUMENT'}]};
(async()=>{
 // A reset and return to the same token/scope still invalidates the request.
 for(const reset of ['token','matter','reconnect']){
  const h=harness();h.el('search-query').value='query';h.fire('search-matter','submit');assert.equal(h.requests.length,1);
  if(reset==='token'){h.fire('workspace-token','input');}
  if(reset==='matter'){h.el('matter-list').value='other';h.fire('matter-list','change');h.el('matter-list').value='old-matter';h.fire('matter-list','change');}
  if(reset==='reconnect'){h.fire('connect','click');}
  h.release(privateSearch);await h.settle();assert.equal(h.el('workspace-result').textContent,'');assert.equal(h.el('workspace-json').textContent,'');assert.match(h.el('workspace-status').textContent,/изменились/);
 }
 // Ordinary requests still display the returned data.
 {const h=harness();h.fire('search-matter','submit');h.release(privateSearch);await h.settle();assert.match(h.el('workspace-result').textContent,/PRIVATE DOCUMENT/);}
 // Response decoding is asynchronous too; invalidate during JSON parsing.
 {const h=harness();let decode;h.context.fetch=async()=>({ok:true,status:200,json:()=>new Promise(resolve=>decode=resolve)});h.fire('search-matter','submit');await new Promise(resolve=>setImmediate(resolve));h.fire('workspace-token','input');decode(privateSearch);await h.settle();assert.equal(h.el('workspace-json').textContent,'');}
 // A document original returned late must never trigger a download.
 {const h=harness();const action=vm.runInContext("run(async()=>{const file=await call('/workspace/documents/original',{document_id:'old-document'});downloadFile(file.file_base64,file.filename)})",h.context);h.fire('workspace-token','input');h.release({filename:'private.txt',file_base64:Buffer.from('PRIVATE').toString('base64')});await action;assert.equal(h.downloads.length,0);}
 // A local file read cannot continue with a different account's credentials.
 {const h=harness();let finish;h.el('document-file').files=[{name:'private.txt',size:7,arrayBuffer:()=>new Promise(resolve=>finish=resolve)}];h.fire('parse-file','click');h.el('workspace-token').value='new-token';h.fire('workspace-token','input');finish(Buffer.from('PRIVATE'));await h.settle();assert.equal(h.requests.length,0);assert.equal(h.el('document-text').value,'');assert.equal(vm.runInContext('pendingOriginal',h.context),null);}
 // Matter reset clears previous fields immediately even while an action is busy.
 {const h=harness();h.fire('search-matter','submit');h.el('document-text').value='PRIVATE';h.el('redact-terms').value='PRIVATE';h.el('document-list').append({});h.el('matter-list').value='new-matter';h.fire('matter-list','change');assert.equal(h.el('document-text').value,'');assert.equal(h.el('redact-terms').value,'');assert.equal(h.el('document-list').options.length,0);h.release(privateSearch);await h.settle();assert.equal(h.el('workspace-result').textContent,'');}
 // A new action works after rejecting an old response.
 {const h=harness();h.fire('search-matter','submit');h.fire('workspace-token','input');h.release(privateSearch);await h.settle();h.el('workspace-token').value='new-token';vm.runInContext("activeMatter='new-matter'",h.context);h.fire('search-matter','submit');assert.equal(h.requests[1].options.headers.Authorization,'Bearer new-token');h.release({matches:[]});await h.settle();assert.match(h.el('workspace-status').textContent,/Готово/);}
 process.stdout.write(JSON.stringify({status:'passed',scope:'node_vm_workspace_context',checks:9,browser_acceptance:false})+'\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1});
