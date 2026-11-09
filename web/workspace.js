'use strict';
const w = id => document.getElementById(id);
let activeMatter = '', parsedPages = [], currentJob = null, busy = false, pendingOriginal = null;
let primarySources = [];
async function call(path, payload, jobId = null) {
 const headers = {'Content-Type':'application/json', Authorization:'Bearer '+w('workspace-token').value.trim()};
 if(jobId) headers['X-Request-ID'] = jobId;
 const response = await fetch(path,{method:'POST',headers,body:JSON.stringify(payload),cache:'no-store'});
 const data = await response.json();
 if(!response.ok) throw Error(data.error || 'Ошибка '+response.status);
 return data;
}
async function run(action) {
 if(busy)return;busy=true;
 const controls = [...document.querySelectorAll('input,textarea,select,button')].filter(el=>el.id!=='workspace-cancel');
 const previous = controls.map(el=>el.disabled); controls.forEach(el=>el.disabled=true);
 w('workspace-status').textContent='Выполняется…';
 try {await action();w('workspace-status').textContent='Готово. Проверьте результат и ограничения.'}
 catch(error){w('workspace-status').textContent=error.message}
 finally{controls.forEach((el,i)=>el.disabled=previous[i]);currentJob=null;busy=false;w('workspace-cancel').hidden=true}
}
async function compute(path,payload){
 currentJob=crypto.randomUUID();w('workspace-cancel').hidden=false;
 return call(path,payload,currentJob);
}
function matter(){if(!activeMatter)throw Error('Выберите дело.');return activeMatter}
function show(data){
 w('workspace-json').textContent=JSON.stringify(data,null,2);
 if(data.matches)w('workspace-result').textContent=data.matches.map(row=>row.title+'\n'+row.text).join('\n\n')||'Подходящих фрагментов нет.';
 else if(data.findings)w('workspace-result').textContent=[data.summary,...(data.source_warnings||[])].join('\n\n')+'\n\n'+data.findings.map(row=>row.assessment+'\n'+row.citations.map(ref=>ref.quote).join('\n')).join('\n\n');
 else w('workspace-result').textContent=data.warning||data.title||'Действие выполнено. Подробности доступны ниже.';
}
function clearReview(){w('review-note').value='';for(const id of ['attest-source','attest-completeness','attest-period','attest-reasoning'])w(id).checked=false}
function downloadFile(encoded,filename){const raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([raw],{type:'application/octet-stream'}));const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function refreshMatters(selected=null){
 clearTask();pendingOriginal=null;
 w('evidence-list').replaceChildren();clearReview();
 const data=await call('/workspace/matters/list',{});w('matter-list').replaceChildren();
 for(const row of data.matters){const option=document.createElement('option');option.value=row.id;option.textContent=row.title+' · '+row.role;w('matter-list').append(option)}
 if(selected)w('matter-list').value=selected;
 activeMatter=w('matter-list').value;await refreshDocuments();
}
async function refreshDocuments(){
 w('document-list').replaceChildren();if(!activeMatter)return;
 const data=await call('/workspace/documents/list',{matter_id:activeMatter});
 for(const row of data.documents){const li=document.createElement('li');const button=document.createElement('button');button.className='secondary';button.textContent=row.title;
  button.addEventListener('click',()=>run(async()=>{const data=await call('/workspace/documents/read',{document_id:row.id});w('document-title').value=data.title;w('document-text').value=data.text;w('source-url').value=data.source_url;parsedPages=data.pages;pendingOriginal=null;w('document-file').value='';show({title:data.title,sha256:data.sha256})}));li.append(button);if(row.has_original){const original=document.createElement('button');original.className='secondary';original.textContent='Оригинал';original.addEventListener('click',()=>run(async()=>{const file=await call('/workspace/documents/original',{document_id:row.id});downloadFile(file.file_base64,file.filename)}));li.append(original)}w('document-list').append(li)}
}
w('workspace-token').addEventListener('input',()=>{activeMatter='';w('identity').textContent='Подключитесь с новым токеном.';for(const id of ['matter-list','document-list','evidence-list'])w(id).replaceChildren();for(const id of ['workspace-result','workspace-json','new-user-result'])w(id).textContent='';for(const id of ['document-text','document-title','source-url','matter-question','search-query','redact-terms','document-file','matter-title','member-id','user-name'])w(id).value='';clearReview();w('allow-cloud').checked=false;w('matter-demo').checked=true;w('user-admin').hidden=true;parsedPages=[]});
w('connect').addEventListener('click',()=>run(async()=>{const user=await call('/workspace/me',{});w('identity').textContent=user.name+' · '+user.role+' · '+user.id;w('user-admin').hidden=user.role!=='admin';await refreshPrimarySources();await refreshMatters()}));
w('matter-list').addEventListener('change',()=>run(async()=>{activeMatter=w('matter-list').value;w('workspace-result').textContent='';w('workspace-json').textContent='';w('evidence-list').replaceChildren();clearReview();for(const id of ['document-text','document-title','source-url','matter-question'])w(id).value='';parsedPages=[];await refreshDocuments()}));
w('create-matter').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const created=await call('/workspace/matters/create',{title:w('matter-title').value});await refreshMatters(created.matter_id)})});
w('grant-access').addEventListener('submit',event=>{event.preventDefault();run(async()=>show(await call('/workspace/members/set',{matter_id:matter(),user_id:w('member-id').value,role:w('member-role').value}))) });
w('create-user').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const user=await call('/workspace/users/create',{name:w('user-name').value,role:w('user-role').value});w('new-user-result').textContent=JSON.stringify(user,null,2)})});
w('document-text').addEventListener('input',()=>{parsedPages=[];pendingOriginal=null});
w('document-file').addEventListener('change',()=>{parsedPages=[];pendingOriginal=null;w('document-text').value='';w('source-url').value='';w('document-title').value=w('document-file').files[0]?.name||''});
w('parse-file').addEventListener('click',()=>run(async()=>{
 const file=w('document-file').files[0];if(!file)throw Error('Выберите файл.');if(file.size>10*1024*1024)throw Error('Файл больше 10 МиБ.');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 const original={filename:file.name,file_base64:btoa(binary),ocr:w('enable-ocr').checked,ocr_language:w('ocr-language').value};
 const data=await compute('/documents/parse',original);pendingOriginal=original;
 w('document-text').value=data.document;w('document-title').value=file.name;parsedPages=data.pages;show({mode:data.mode,pages:data.pages.length,warning:data.warning});
}));
w('add-document').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const metadata={matter_id:matter(),title:w('document-title').value,source_url:w('source-url').value};show(pendingOriginal?await compute('/workspace/documents/upload',{...metadata,...pendingOriginal}):await call('/workspace/documents/add',{...metadata,document:w('document-text').value,pages:parsedPages}));await refreshDocuments()})});
w('import-primary').addEventListener('click',()=>run(async()=>{if(!w('primary-source').value)throw Error('Выберите доступный первоисточник.');const result=await call('/workspace/sources/import',{matter_id:matter(),source_id:w('primary-source').value});show(result);w('workspace-result').textContent=result.reused?'Этот источник уже есть в деле.':'Источник добавлен. Актуальность и применимость требуют проверки.';await refreshDocuments()}));
w('search-matter').addEventListener('submit',event=>{event.preventDefault();run(async()=>show(await call('/workspace/search',{matter_id:matter(),query:w('search-query').value}))) });
w('ask-matter').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const payload={matter_id:matter(),question:w('matter-question').value,demo:w('matter-demo').checked,allow_cloud:w('allow-cloud').checked,editions:[...w('ask-editions').selectedOptions].map(option=>JSON.parse(option.value))};if(w('matter-as-of').value)payload.as_of=w('matter-as-of').value;show(await compute('/workspace/ask',payload))}) });
w('redact-preview').addEventListener('click',()=>run(async()=>{const result=await compute('/documents/redact',{document:w('document-text').value,terms:w('redact-terms').value.split('\n').map(s=>s.trim()).filter(Boolean)});w('document-text').value=result.document;parsedPages=[];pendingOriginal=null;show({redaction_count:result.redaction_count,review_required:result.review_required,anonymization_verified:false})}));
w('export-docx').addEventListener('click',()=>run(async()=>{const data=await compute('/documents/export',{document:w('document-text').value,title:w('document-title').value||'Черновик'});const raw=Uint8Array.from(atob(data.document_base64),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([raw],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}));const a=document.createElement('a');a.href=url;a.download='draft.docx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}));
w('workspace-cancel').addEventListener('click',async()=>{if(!currentJob)return;try{await call('/requests/cancel',{request_id:currentJob})}catch{w('workspace-status').textContent='Отмена не подтверждена; действует серверный дедлайн.'}});
async function refreshEvidence(){const data=await call('/workspace/evidence/list',{matter_id:matter()});w('evidence-list').replaceChildren();clearReview();for(const row of data.artifacts){const option=document.createElement('option');option.value=row.artifact_id;option.textContent=row.label+' · '+row.review_status;w('evidence-list').append(option)}}
w('evidence-list').addEventListener('change',clearReview);
w('workspace-token').addEventListener('input',()=>{pendingOriginal=null});
w('matter-list').addEventListener('change',()=>{pendingOriginal=null});
function primarySourceNote(){const source=primarySources.find(row=>row.id===w('primary-source').value);w('primary-source-note').textContent=source?source.page_count+' стр. · '+source.edition_label+(source.case_number?' Дело '+source.case_number+'. Полная цепочка инстанций не загружена.':' История изменений полностью не подтверждена.'):'Каталог недоступен; работа с документами дела остаётся доступной.'}
async function refreshPrimarySources(){w('primary-source').replaceChildren();primarySources=[];try{const data=await call('/workspace/sources/list',{});primarySources=data.sources;for(const source of data.sources){const option=document.createElement('option');option.value=source.id;option.textContent=source.title;w('primary-source').append(option)}}catch{}primarySourceNote()}
w('primary-source').addEventListener('change',primarySourceNote);
w('workspace-token').addEventListener('input',()=>{primarySources=[];w('primary-source').replaceChildren();primarySourceNote()});
w('refresh-evidence').addEventListener('click',()=>run(refreshEvidence));
w('read-evidence').addEventListener('click',()=>run(async()=>{if(!w('evidence-list').value)throw Error('Выберите материал.');const data=await call('/workspace/evidence/read',{artifact_id:w('evidence-list').value});show(data);w('workspace-result').textContent=JSON.stringify(data.payload,null,2)}));
w('review-evidence').addEventListener('submit',event=>{event.preventDefault();run(async()=>{if(!w('evidence-list').value)throw Error('Выберите материал.');show(await call('/workspace/evidence/review',{artifact_id:w('evidence-list').value,decision:w('review-decision').value,note:w('review-note').value,attestations:{source_authenticity:w('attest-source').checked,completeness:w('attest-completeness').checked,temporal_applicability:w('attest-period').checked,legal_reasoning:w('attest-reasoning').checked}}));await refreshEvidence()})});

let openTask=null, taskCitations=[];
function clearTask(){openTask=null;taskCitations=[];w('task-facts').textContent='';for(const id of ['task-answer','task-risks','task-forbidden','task-quote','matter-as-of'])w(id).value='';for(const id of ['task-list','task-document','ask-editions'])w(id).replaceChildren();w('task-citation-count').textContent='Цитат: 0'}
for(const [id,event] of [['workspace-token','input'],['matter-list','change'],['connect','click']])w(id).addEventListener(event,clearTask);
async function refreshTasks(){openTask=null;taskCitations=[];w('task-facts').textContent='';for(const id of ['task-answer','task-risks','task-forbidden','task-quote'])w(id).value='';w('task-citation-count').textContent='Цитат: 0';const data=await call('/workspace/benchmarks/list',{matter_id:matter()});w('task-list').replaceChildren();for(const task of data.tasks){const option=document.createElement('option');option.value=task.task_id;option.textContent=task.case_id+' · '+task.status;w('task-list').append(option)}show(data);w('workspace-result').textContent='Заданий: '+data.total+'; ждут ответа: '+data.needs_expert_answer+'; проверка записана: '+data.reviews_recorded+'. Юридическая приёмка отдельно.'}
w('import-tasks').addEventListener('click',()=>run(async()=>{await call('/workspace/benchmarks/import',{matter_id:matter()});await refreshTasks()}));
w('refresh-tasks').addEventListener('click',()=>run(refreshTasks));
w('task-list').addEventListener('change',()=>{openTask=null;taskCitations=[];w('task-facts').textContent='Откройте выбранное задание.';for(const id of ['task-answer','task-risks','task-forbidden','task-quote'])w(id).value='';w('task-citation-count').textContent='Цитат: 0'});
w('open-task').addEventListener('click',()=>run(async()=>{const data=await call('/workspace/benchmarks/read',{task_id:w('task-list').value});openTask=data;const draft=data.current_draft?.payload;w('task-facts').textContent=data.source_case.id+' · '+data.source_case.as_of+'\n'+data.source_case.facts;w('task-answer').value=draft?.expected_answer||'';w('task-risks').value=(draft?.required_risks||[]).join('\n');w('task-forbidden').value=(draft?.forbidden_claims||[]).join('\n');taskCitations=(draft?.citations||[]).map(row=>({document_id:row.document_id,quote:row.quote}));w('task-citation-count').textContent='Цитат: '+taskCitations.length;const docs=await call('/workspace/documents/list',{matter_id:matter()});w('task-document').replaceChildren();for(const doc of docs.documents){const option=document.createElement('option');option.value=doc.id;option.textContent=doc.title;w('task-document').append(option)}}));
w('add-task-citation').addEventListener('click',()=>run(async()=>{if(!openTask)throw Error('Откройте задание.');if(!w('task-document').value||!w('task-quote').value.trim())throw Error('Выберите документ и введите точную цитату.');if(taskCitations.length>=20)throw Error('Допускается до 20 цитат.');taskCitations.push({document_id:w('task-document').value,quote:w('task-quote').value});w('task-citation-count').textContent='Цитат: '+taskCitations.length;w('task-quote').value=''}));
w('clear-task-citations').addEventListener('click',()=>{taskCitations=[];w('task-citation-count').textContent='Цитат: 0'});
w('save-task').addEventListener('submit',event=>{event.preventDefault();run(async()=>{if(!openTask)throw Error('Откройте задание.');const lines=id=>w(id).value.split('\n').map(row=>row.trim()).filter(Boolean);const payload={task_id:openTask.task_id,expected_answer:w('task-answer').value,required_risks:lines('task-risks'),forbidden_claims:lines('task-forbidden'),citations:taskCitations};if(openTask.current_artifact_id)payload.previous_artifact_id=openTask.current_artifact_id;const saved=await call('/workspace/benchmarks/draft',payload);openTask.current_artifact_id=saved.artifact_id;show(saved);w('workspace-result').textContent='Черновик сохранён. Откройте очередь экспертной проверки из другой учётной записи.'})});
w('refresh-editions').addEventListener('click',()=>run(async()=>{const data=await call('/workspace/evidence/list',{matter_id:matter()});w('ask-editions').replaceChildren();const seen=new Set();for(const row of data.artifacts){if(!row.edition||row.review_status!=='approved')continue;const value=JSON.stringify({act_id:row.edition.act_id,provision:row.edition.provision});if(seen.has(value))continue;seen.add(value);const option=document.createElement('option');option.value=value;option.textContent=row.label;w('ask-editions').append(option)}}));

w('draft-primary-decision').addEventListener('click',()=>run(async()=>{const source=primarySources.find(row=>row.id===w('primary-source').value);if(source?.material_type!=='official_court_decision')throw Error('Выберите отдельное определение ВС в каталоге.');const saved=await call('/workspace/sources/decision-draft',{matter_id:matter(),source_id:source.id});show(saved);w('workspace-result').textContent=saved.reused?'Карточка этого документа уже есть в деле.':'Создан черновик карточки судебного акта. Требуется экспертная проверка; полная история дела не загружена.';await refreshDocuments();await refreshEvidence()}));
