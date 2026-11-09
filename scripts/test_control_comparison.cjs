'use strict';
// Real UI handlers in a synthetic DOM; no browser or legal-quality claim.
const assert=require('node:assert/strict');
const {setup}=require('./test_quality_scoring.cjs');
const comparison={gate:'regression',task_count:2,paired_task_count:2,averages:{baseline:{correctness:2},candidate:{correctness:3}},limitation:'Требуется экспертная проверка.'};
function harness(extra={}){return setup({responses:{
 '/workspace/quality/sets/list':{sets:[{set_id:'set-a',title:'Контроль',benchmark_count:2}]},
 '/workspace/evidence/list':{artifacts:[{artifact_id:'approved-a',kind:'benchmark',review_status:'approved',label:'Принят'}, {artifact_id:'draft-b',kind:'benchmark',review_status:'draft',label:'Черновик'}]},
 '/workspace/quality/sets/create':{set_id:'set-a'},
 '/workspace/quality/compare':comparison,...extra}})}
function parameters(h){h.el('control-set').value='set-a';h.el('control-baseline').value='v1';h.el('control-candidate').value='v2'}
(async()=>{
 {const h=harness();h.fire('refresh-control-sets','click');await h.settle();assert.equal(h.el('control-set').options.length,1);assert.equal(h.el('control-benchmarks').options.length,1);assert.equal(h.el('control-benchmarks').options[0].value,'approved-a');}
 {const h=harness();h.el('control-title').value='Контроль';h.el('control-benchmarks').selectedOptions=[{value:'approved-a'}];h.fire('create-control-set','submit');await h.settle();assert.equal(h.requests[0].route,'/workspace/quality/sets/create');assert.deepEqual(h.requests[0].payload.benchmark_ids,['approved-a']);assert.equal(h.el('control-set').value,'set-a');}
 {const h=harness();parameters(h);h.fire('compare-control','submit');await h.settle();assert.match(h.el('control-comparison').textContent,/Обнаружена регрессия/);assert.equal(h.requests[0].payload.candidate_label,'v2');}
 {const h=harness({'/workspace/quality/compare':{...comparison,gate:'incomplete',paired_task_count:1}});parameters(h);h.fire('compare-control','submit');await h.settle();assert.match(h.el('control-comparison').textContent,/Не хватает/);}
 {const h=harness();parameters(h);h.setHold('/workspace/quality/compare');h.fire('compare-control','submit');h.fire('workspace-token','input');h.release();await h.settle();assert.equal(h.el('control-comparison').textContent,'');assert.equal(h.el('control-baseline').value,'');}
 {const h=harness();parameters(h);h.setHold('/workspace/quality/compare');h.fire('compare-control','submit');h.el('control-candidate').value='v3';h.fire('control-candidate','input');h.release();await h.settle();assert.equal(h.el('control-comparison').textContent,'');assert.match(h.el('workspace-status').textContent,/Параметры сравнения изменились/);}
 {const h=harness();parameters(h);h.fire('register-same-answer','submit');await h.settle();assert.equal(h.requests.length,0);assert.match(h.el('workspace-status').textContent,/Сначала запишите/);}
 {const h=harness();h.prepare();h.fire('score-answer','submit');await h.settle();parameters(h);h.el('control-same-label').value='v2';h.el('control-same-confirm').checked=true;h.fire('register-same-answer','submit');await h.settle();const last=h.requests.at(-1);assert.equal(last.route,'/workspace/quality/runs/register');assert.equal(last.payload.evaluation_id,'evaluation');assert.equal(last.payload.same_answer_declared,true);assert.equal(h.el('control-same-confirm').checked,false);}
 process.stdout.write(JSON.stringify({status:'passed',scope:'node_vm_control_comparison',checks:8,browser_acceptance:false})+'\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1});
