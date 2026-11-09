'use strict';
// Actual workspace UI functions, synthetic DOM/fetch. Not browser acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
 const elements=new Map(),requests=[];let hold='',release;
 class Element{constructor(){this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.files=[];this.options=[];this.selectedOptions=[];this.listeners=new Map();this.dataset={};}addEventListener(event,fn){if(!this.listeners.has(event))this.listeners.set(event,[]);this.listeners.get(event).push(fn);}replaceChildren(){this.options=[]}append(...items){this.options.push(...items)}}
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
 const statistics={human_evaluations:2,distinct_benchmarks:1,mean_correctness_0_to_4:3,missed_required_risk_fraction:.25,unsupported_claim_count:1};
 const metrics={...statistics,excluded_due_to_review_change:0,excluded_without_review_binding:0,by_run_label:[{run_label:'v1',...statistics}],shared_benchmark_count:1,shared_benchmark_comparison:[{run_label:'v1',...statistics}],limitation:'Оценки людей; не юридическая приёмка.'};
 const context={document:{getElementById:el,querySelectorAll:()=>[...elements.values()],createElement:()=>new Element()},Blob,Uint8Array,setTimeout:fn=>fn(),crypto:{randomUUID:()=> 'test-job'},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}},
  fetch:async(route,options)=>{requests.push({route,payload:JSON.parse(options.body)});if(route===hold)await new Promise(resolve=>release=resolve);return {ok:true,status:200,json:async()=>route.endsWith('/metrics')?metrics:{evaluation_id:'evaluation',legal_acceptance:false}}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/workspace.js'),'utf8'),context);
 vm.runInContext("activeMatter='matter-a'",context);el('workspace-token').value='synthetic-token';
 const fire=(id,event)=>{for(const fn of el(id).listeners.get(event)||[])fn({preventDefault(){}})};
 const settle=async()=>{for(let i=0;i<30;i++){await new Promise(resolve=>setImmediate(resolve));if(!vm.runInContext('busy',context))return}throw Error('UI did not settle')};
 function prepare(approved=true){context.task={task_id:'task-a',current_artifact_id:'benchmark-a',current_draft:{review_status:approved?'approved':'draft',payload:{required_risks:['Риск А','Риск Б']}}};vm.runInContext('openTask=task;renderScoring(task)',context);el('score-run-label').value='v1';el('score-response').value='СИНТЕТИЧЕСКИЙ ответ';el('score-correctness').value='3';el('score-unsupported').value='1';el('score-note').value='СИНТЕТИЧЕСКАЯ заметка';}
 return {el,requests,fire,settle,prepare,context,setHold:route=>hold=route,release:()=>release()};
}
(async()=>{
 {const h=setup();h.prepare();assert.equal(h.el('score-panel').hidden,false);assert.equal(h.el('score-risks').options.length,2);vm.runInContext('scoreRiskInputs[1].checked=true',h.context);h.fire('score-answer','submit');await h.settle();assert.deepEqual(h.requests[0].payload.missed_risks,[1]);assert.equal(h.requests[0].payload.run_label,'v1');assert.equal(h.requests[0].payload.correctness,3);assert.equal(h.requests[1].route,'/workspace/evaluations/metrics');assert.match(h.el('quality-summary').textContent,/Общих эталонов/);}
 {const h=setup();h.prepare(false);h.fire('score-answer','submit');await h.settle();assert.equal(h.el('score-panel').hidden,true);assert.equal(h.requests.length,0);}
 {const h=setup();h.prepare();h.el('score-unsupported').value='';h.fire('score-answer','submit');await h.settle();assert.equal(h.requests.length,0);assert.match(h.el('workspace-status').textContent,/Заполните/);}
 {const h=setup();h.prepare();h.el('quality-summary').textContent='PRIVATE METRICS';h.fire('workspace-token','input');assert.equal(h.el('score-response').value,'');assert.equal(h.el('quality-summary').textContent,'');assert.equal(h.el('score-panel').hidden,true);}
 {const h=setup();h.prepare();h.setHold('/workspace/evaluations/record');h.fire('score-answer','submit');h.fire('task-list','change');h.release();await h.settle();assert.equal(h.requests.length,1);assert.match(h.el('workspace-status').textContent,/Задание изменилось/);assert.equal(h.el('quality-summary').textContent,'');}
 {const h=setup();h.setHold('/workspace/evaluations/metrics');h.fire('refresh-quality','click');h.fire('workspace-token','input');h.release();await h.settle();assert.equal(h.el('quality-summary').textContent,'');}
 process.stdout.write(JSON.stringify({status:'passed',scope:'node_vm_quality_scoring',checks:6,browser_acceptance:false})+'\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1});
