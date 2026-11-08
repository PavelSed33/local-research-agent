/* Optional real Chromium QA. Install Playwright separately; no production dependency.
   Run against a disposable local API: BROWSER_SMOKE_URL=http://127.0.0.1:8080 node scripts/browser_smoke.cjs
   Optional API token: BROWSER_SMOKE_TOKEN. Only synthetic documents and demo API work.
*/
'use strict';
const {chromium}=require('playwright');
const {mkdtemp,readFile,rm,writeFile}=require('node:fs/promises');
const {tmpdir}=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const base=new URL(process.env.BROWSER_SMOKE_URL||'http://127.0.0.1:8080');
if(!['127.0.0.1','localhost','[::1]'].includes(base.hostname))throw Error('Use a disposable loopback API.');
const token=process.env.BROWSER_SMOKE_TOKEN||'';
const text='Условный договор: ООО «Тест-А» оказывает ООО «Тест-Б» услуги. Стоимость 1 000 рублей.';
async function main(){
 const directory=await mkdtemp(path.join(tmpdir(),'legal-browser-'));
 let browser;
 try{
  const docx=path.join(directory,'synthetic.docx');
  execFileSync(process.env.PYTHON||'python',['-c',`
import sys,zipfile
from xml.sax.saxutils import escape
with zipfile.ZipFile(sys.argv[1],'w') as z:
 z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
 z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
 z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+escape(sys.argv[2])+'</w:t></w:r></w:p></w:body></w:document>')
`,docx,text]);
  const bad=path.join(directory,'broken.docx');await writeFile(bad,'not a ZIP');
  const executablePath=process.env.BROWSER_SMOKE_EXECUTABLE_PATH;
  browser=await chromium.launch({headless:true,...(executablePath?{executablePath}:{})});
  const context=await browser.newContext({acceptDownloads:true,viewport:{width:1280,height:900}});
  const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base.href);await page.locator('#health').filter({hasText:'API доступен'}).waitFor();
  if(token)await page.locator('#token').fill(token);
  await page.locator('#readiness').click();
  await page.locator('#readiness-status').filter({hasText:'Проверка настроек завершена'}).waitFor();
  assert.ok(await page.locator('#readiness-items li').count()>0);
  await page.locator('#run').click();await page.locator('#status').filter({hasText:'Введите вопрос'}).waitFor();
  await page.locator('#question').fill('Технический вопрос без персональных данных');
  await page.locator('#run').click();await page.locator('#result').waitFor({state:'visible'});
  assert.match(await page.locator('#verification').innerText(),/Демонстрация/);
  const downloadEvent=page.waitForEvent('download');await page.locator('#download').click();
  const downloaded=await downloadEvent;const exported=JSON.parse(await readFile(await downloaded.path(),'utf8'));
  assert.equal(exported.demo,true);assert.equal(exported.verified,false);
  await page.locator('[data-mode="extract"]').click();
  await page.locator('#file').setInputFiles(docx);await page.locator('#run').click();
  await page.locator('#result').waitFor({state:'visible'});
  assert.equal(JSON.parse(await page.locator('#json').textContent()).demo,true);
  await page.locator('#file').setInputFiles(bad);await page.locator('#run').click();
  await page.locator('#status').filter({hasText:/повреждён|формат/}).waitFor();
  assert.equal(await page.locator('#result').isVisible(),false);
  await page.locator('#file').setInputFiles([]);await page.locator('#document').fill(text);
  // Hold only this browser's demo request before dispatch, then test early cancellation.
  // Running-worker interruption is covered separately by test_request_jobs.py.
  let release;const gate=new Promise(resolve=>release=resolve);let saw;
  const intercepted=new Promise(resolve=>saw=resolve);
  await page.route('**/extract',async route=>{saw();await gate;await route.abort().catch(()=>{});});
  await page.locator('#run').click();await intercepted;
  await page.locator('#cancel').click();
  await page.locator('#status').filter({hasText:'Запрос отменён.'}).waitFor();release();
  assert.equal(await page.locator('#run').isEnabled(),true);
  assert.equal(await page.locator('#result').isVisible(),false);
  await page.unroute('**/extract');
  await page.locator('#run').click();await page.locator('#result').waitFor({state:'visible'});
  await page.locator('[data-mode="local-extract"]').click();
  await page.locator('#file').setInputFiles(docx);await page.locator('#run').click();
  await page.locator('#status').filter({hasText:'Готово.'}).waitFor();
  const localResult=JSON.parse(await page.locator('#json').textContent());
  assert.equal(localResult.mode,'local_patterns');assert.equal(localResult.demo,false);
  assert.equal(localResult.external_requests_attempted,false);assert.ok(localResult.matches.length>0);
  assert.match(await page.locator('#verification').innerText(),/Локальные совпадения/);
  await page.locator('#file').setInputFiles([]);await page.locator('#document').fill(text);
  await page.locator('[data-mode="skills"]').click();
  await page.locator('#skill-id option').nth(2).waitFor({state:'attached'});
  await page.locator('#skill-id').selectOption('contract-playbook');
  await page.locator('#run').click();await page.locator('#result').waitFor({state:'visible'});
  assert.equal(JSON.parse(await page.locator('#json').textContent()).status,'needs_facts');
  await page.locator('#skill-position').fill('Заказчик');
  await page.locator('#skill-playbook').fill('Оплата производится после подписания акта.');
  for(const skill of ['contract-playbook','contract-risk','claim-audit']){
   await page.locator('#skill-id').selectOption(skill);
   if(skill==='claim-audit')await page.locator('#skill-query').fill('ГК РФ статья 310');
   assert.equal(await page.locator('#skill-playbook').isVisible(),skill==='contract-playbook');
   assert.equal(await page.locator('#skill-query').isVisible(),skill==='claim-audit');
   await page.locator('#run').click();
   await page.locator('#status').filter({hasText:'Готово.'}).waitFor();
   const result=JSON.parse(await page.locator('#json').textContent());
   assert.equal(result.skill_id,skill);assert.equal(result.demo,true);assert.equal(result.verified,false);
   assert.equal(result.status,'draft');assert.ok(result.findings.length>0);
  }
  await page.locator('[data-mode="research"]').click();
  await page.locator('#research-scenario option').nth(34).waitFor({state:'attached'});
  await page.locator('#research-scenario').selectOption('civil-limitation');
  assert.ok(await page.locator('[data-research-fact]').count()>0,'Scenario facts did not render');
  await page.locator('#run').click();await page.locator('#status').filter({hasText:'Готово.'}).waitFor();
  assert.equal(JSON.parse(await page.locator('#json').textContent()).status,'needs_facts');
  await page.locator('[data-research-fact]').first().selectOption('true');
  await page.locator('#research-scenario').selectOption('procedure-costs');
  assert.ok((await page.locator('[data-research-fact]').evaluateAll(fields=>fields.map(f=>f.value))).every(value=>value===''));
  await page.setViewportSize({width:390,height:844});
  const dimensions=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
  assert.ok(dimensions.scroll<=dimensions.width,'Mobile page overflows horizontally');
  await page.locator('[data-mode="legal"]').click();await page.locator('#profile').selectOption('employment');
  assert.equal(await page.locator('#contract-type').isEnabled(),false);
  assert.equal(errors.length,0,JSON.stringify(errors));
  console.log(JSON.stringify({status:'passed',real_browser:true,synthetic_demo_only:true,
   checks:['blank input','question result','JSON download content','DOCX upload','broken DOCX error',
    'stale result hidden','early cancellation','recovery after cancellation','three skills','missing skill facts',
    '35 research scenarios','scenario facts reset','390px layout','profile controls','no page errors']}));
 } finally {if(browser)await browser.close();await rm(directory,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
