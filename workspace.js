'use strict';
const el = id => document.getElementById(id);
async function request(path, data) {
  const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+el('token').value},body:JSON.stringify(data)});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Ошибка HTTP '+response.status);
  return payload;
}
async function action(path, data) {
  el('output').textContent='Выполняется запрос…';
  try { el('output').textContent=JSON.stringify(await request(path,data),null,2); }
  catch (error) { el('output').textContent=String(error.message); }
}
el('connect').addEventListener('click',()=>action('/workspace/me',{}));
el('ask').addEventListener('click',()=>action('/workspace/ask',{matter_id:el('matter').value,question:el('question').value,demo:true}));