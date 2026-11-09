'use strict';
// Exercise the shipped UI logic in a Node VM. This is not browser/layout acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const elements = new Map(), downloads = [], urls = new Map();
class Element {
 constructor(id=''){this.id=id;this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.files=[];this.options=[];this.selectedOptions=[];this.dataset={};this.listeners=new Map();}
 addEventListener(event,fn){if(!this.listeners.has(event))this.listeners.set(event,[]);this.listeners.get(event).push(fn);}
 replaceChildren(){this.options=[];}
 append(...items){this.options.push(...items);}
 click(){if(this.href)downloads.push({filename:this.download,blob:urls.get(this.href)});}
}
const el=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
const document={getElementById:el,querySelectorAll:()=>[...elements.values()],createElement:()=>new Element()};
let snapshot='a'.repeat(64), denied=false, calls=0, hold=false, releaseFetch=null, redlineHold=false, releaseRedline=null;
const markdown='# Отчёт\n\nДословная цитата с кириллицей.\n';
const response=()=>({report:{act_count:1,snapshot_sha256:snapshot,excluded:{future_acts:0,superseded_versions:1},status:'draft'},markdown,filename:'case-report-example.md'});
const context={document,Blob,TextDecoder,Uint8Array,setTimeout:fn=>fn(),
 btoa:s=>Buffer.from(s,'binary').toString('base64'),atob:s=>Buffer.from(s,'base64').toString('binary'),
 crypto:{randomUUID:()=> 'synthetic-ui-test'},
 URL:{createObjectURL:blob=>{const key='blob:'+urls.size;urls.set(key,blob);return key;},revokeObjectURL:()=>{}},
 fetch:async (route,options)=>{if(route==='/documents/redline'){if(redlineHold)await new Promise(resolve=>releaseRedline=resolve);return {ok:true,status:200,json:async()=>({filename:'redlined.docx',document_base64:Buffer.from('TRACKED TEST').toString('base64'),word_track_changes:true,warning:'Проверьте правки.'})};}if(route==='/workspace/cases/report'){calls++;if(hold)await new Promise(resolve=>releaseFetch=resolve);return {ok:!denied,status:denied?403:200,json:async()=>denied?{error:'Нет доступа к делу.'}:response()};}return {ok:true,status:200,json:async()=>({documents:[],matters:[]})};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/workspace.js'),'utf8'),context);
function fire(id,event){for(const listener of el(id).listeners.get(event)||[])listener({preventDefault(){}});}
async function settle(){for(let index=0;index<20;index++){await new Promise(resolve=>setImmediate(resolve));if(!vm.runInContext('busy',context))return;}throw Error('UI did not settle');}
async function emit(id,event){fire(id,event);await settle();}
function scope(){vm.runInContext("activeMatter='synthetic-matter'",context);el('workspace-token').value='PRIVATE_TEST_TOKEN';el('matter-list').value='synthetic-matter';el('report-case-number').value='А00-1/2025';el('report-as-of').value='2025-01-01';}
async function build(){scope();await emit('build-case-report','submit');assert.equal(el('case-report-preview').textContent,markdown);assert.equal(el('download-case-report').disabled,false);}
(async()=>{
 await build();assert.match(el('case-report-summary').textContent,/Полнота истории не подтверждена/);
 await emit('download-case-report','click');assert.equal(calls,2);assert.equal(downloads.length,1);assert.equal(await downloads[0].blob.text(),markdown);assert.equal(downloads[0].filename,'case-report-example.md');assert.ok(downloads[0].blob.type.includes('charset=utf-8'));
 denied=true;await emit('download-case-report','click');assert.equal(downloads.length,1);assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);denied=false;
 await build();snapshot='b'.repeat(64);await emit('download-case-report','click');assert.equal(downloads.length,1);assert.equal(el('case-report-preview').textContent,'');assert.match(el('workspace-status').textContent,/изменились/);
 await build();el('report-as-of').value='2025-02-01';await emit('report-as-of','input');assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);
 await build();await emit('workspace-token','input');assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);
 await build();await emit('matter-list','change');assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);
 await build();el('report-case-number').value='А00-2/2025';await emit('download-case-report','click');assert.equal(downloads.length,1);assert.equal(el('download-case-report').disabled,true);
 await build();hold=true;fire('download-case-report','click');assert.ok(releaseFetch);fire('workspace-token','input');releaseFetch();await settle();hold=false;releaseFetch=null;assert.equal(downloads.length,1);assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);
 scope();hold=true;fire('build-case-report','submit');assert.ok(releaseFetch);fire('workspace-token','input');releaseFetch();await settle();hold=false;releaseFetch=null;assert.equal(el('case-report-preview').textContent,'');assert.equal(el('download-case-report').disabled,true);
 async function prepareDocx(){scope();vm.runInContext('clearDocxRedline()',context);el('redline-docx-file').files=[{name:'original.docx',size:4,arrayBuffer:async()=>Uint8Array.from([80,75,3,4]).buffer}];el('redline-docx-quote').value='10 дней';el('redline-docx-replacement').value='15 дней';await emit('add-docx-change','click');}
 await prepareDocx();await emit('download-docx-redline','click');assert.equal(downloads.length,2);assert.equal(downloads[1].filename,'redlined.docx');assert.equal(await downloads[1].blob.text(),'TRACKED TEST');
 await prepareDocx();redlineHold=true;fire('download-docx-redline','click');await new Promise(resolve=>setImmediate(resolve));assert.ok(releaseRedline);fire('workspace-token','input');releaseRedline();await settle();redlineHold=false;assert.equal(downloads.length,2);assert.equal(el('docx-change-list').options.length,0);assert.match(el('workspace-status').textContent,/изменились/);
 assert.ok(!markdown.includes('PRIVATE_TEST_TOKEN'));
 process.stdout.write(JSON.stringify({status:'passed',scope:'node_vm_workspace_report',checks:12,browser_acceptance:false})+'\n');
})().catch(error=>{process.stderr.write(error.message+'\n');process.exitCode=1;});
