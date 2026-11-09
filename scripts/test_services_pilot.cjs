'use strict';
// Production UI handlers in a VM; this is not a browser acceptance result.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
 const elements=new Map(),downloads=[],requests=[],urls=new Map();let hold='',release=null,denied=false;
 class Element{
  constructor(){this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.files=[];this.options=[];this.selectedOptions=[];this.listeners=new Map();this.dataset={};}
  addEventListener(event,fn){if(!this.listeners.has(event))this.listeners.set(event,[]);this.listeners.get(event).push(fn);}
  replaceChildren(){this.options=[]}append(...items){this.options.push(...items)}
  click(){if(this.href)downloads.push({name:this.download,blob:urls.get(this.href)})}
 }
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
 const report={analysis_mode:'services_pilot',matter_id:'matter-a',as_of:'2025-06-30',markdown:'# Черновик\n\nПроверка цитат.\n',filename:'services-pilot-2025-06-30.md',selected_editions:[],sources:[{document_id:'contract-a',document_sha256:'a'.repeat(64)}]};
 const context={document:{getElementById:el,querySelectorAll:()=>[...elements.values()],createElement:()=>new Element()},Blob,Uint8Array,setTimeout:fn=>fn(),crypto:{randomUUID:()=> 'ui-test-job'},
  URL:{createObjectURL:blob=>{const key='blob:'+urls.size;urls.set(key,blob);return key},revokeObjectURL:()=>{}},
  fetch:async(route,options)=>{requests.push({route,payload:JSON.parse(options.body)});if(route===hold)await new Promise(resolve=>release=resolve);const fail=denied&&route==='/workspace/analysis/validate';return {ok:!fail,status:fail?403:200,json:async()=>fail?{error:'Нет доступа.'}:route==='/workspace/ask'?report:route==='/workspace/benchmarks/list'?{tasks:[],total:0,needs_expert_answer:0,reviews_recorded:0}:{context_current:true}}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/workspace.js'),'utf8'),context);
 vm.runInContext("activeMatter='matter-a'",context);el('workspace-token').value='test-token';el('matter-list').value='matter-a';el('services-pilot').checked=true;el('pilot-contract').value='contract-a';el('matter-question').value='Оплата услуг';el('matter-as-of').value='2025-06-30';el('matter-demo').checked=true;
 for(const key of ['both_businesses','paid_activity'])el('pilot-'+key).value='true';for(const key of ['mixed_contract','special_regime'])el('pilot-'+key).value='false';
 const fire=(id,event)=>{for(const fn of el(id).listeners.get(event)||[])fn({preventDefault(){}})};
 const settle=async()=>{for(let i=0;i<25;i++){await new Promise(resolve=>setImmediate(resolve));if(!vm.runInContext('busy',context))return}throw Error('UI did not settle')};
 return {el,fire,settle,requests,downloads,report,setHold:route=>hold=route,release:()=>release(),deny:()=>denied=true};
}
(async()=>{
 {const h=setup();h.fire('ask-matter','submit');await h.settle();const p=h.requests[0].payload;assert.equal(p.analysis_mode,'services_pilot');assert.equal(p.contract_document_id,'contract-a');assert.deepEqual(p.facts,{both_businesses:true,paid_activity:true,mixed_contract:false,special_regime:false});assert.equal(h.el('workspace-result').textContent,h.report.markdown);assert.equal(h.el('download-pilot-analysis').disabled,false);h.fire('download-pilot-analysis','click');await h.settle();assert.equal(h.requests[1].route,'/workspace/analysis/validate');assert.equal(h.downloads.length,1);assert.equal(await h.downloads[0].blob.text(),h.report.markdown);}
 {const h=setup();h.el('pilot-paid_activity').value='';h.fire('ask-matter','submit');await h.settle();assert.equal(h.requests[0].payload.facts.paid_activity,null);h.el('pilot-paid_activity').value='true';h.fire('pilot-paid_activity','input');assert.equal(h.el('download-pilot-analysis').disabled,true);}
 {const h=setup();h.setHold('/workspace/ask');h.fire('ask-matter','submit');h.fire('pilot-contract','input');h.release();await h.settle();assert.equal(h.el('download-pilot-analysis').disabled,true);assert.equal(h.el('workspace-result').textContent,'');}
 {const h=setup();h.fire('ask-matter','submit');await h.settle();h.deny();h.fire('download-pilot-analysis','click');await h.settle();assert.equal(h.downloads.length,0);assert.equal(h.el('download-pilot-analysis').disabled,true);}
 {const h=setup();h.fire('ask-matter','submit');await h.settle();h.setHold('/workspace/analysis/validate');h.fire('download-pilot-analysis','click');h.fire('workspace-token','input');h.release();await h.settle();assert.equal(h.downloads.length,0);assert.equal(h.el('workspace-result').textContent,'');assert.equal(h.el('services-pilot').checked,false);}
 {const h=setup();h.fire('import-services-tasks','click');await h.settle();assert.equal(h.requests[0].route,'/workspace/benchmarks/import');assert.equal(h.requests[0].payload.case_ids.length,25);assert.equal(h.requests[0].payload.case_ids[24],'SERV-P25');}
 process.stdout.write(JSON.stringify({status:'passed',scope:'node_vm_services_pilot',checks:6,browser_acceptance:false})+'\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1});
