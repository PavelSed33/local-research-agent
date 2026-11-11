'use strict';
// Node VM tests of the actual input function; no browser/layout acceptance claim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const start = source.indexOf('async function documentFields(');
const end = source.indexOf('\nfunction show(', start);
assert.ok(start >= 0 && end > start, 'Document input function boundaries changed');
const elements = {file: {files: []}, text: {value: 'Условный документ'}};
const context = {$: id => elements[id], TextDecoder, Uint8Array,
                 btoa: value => Buffer.from(value, 'binary').toString('base64')};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const read = () => context.documentFields('text', 'file');
const file = (name, bytes) => ({name, size: bytes.length,
                              arrayBuffer: async () => Uint8Array.from(bytes).buffer});
(async () => {
  assert.equal((await read()).document, 'Условный документ');
  elements.file.files = [file('example.txt', Buffer.from('\ufeffДоговор', 'utf8'))];
  assert.equal((await read()).document, 'Договор');
  elements.file.files = [file('broken.txt', [0xc4, 0xee, 0xe3])];
  await assert.rejects(read, /UTF-8/);
  elements.file.files = [{name: 'large.txt', size: 10 * 1024 * 1024 + 1}];
  await assert.rejects(read, /10 МиБ/);
  elements.file.files = [file('example.pdf', [1, 2, 3])];
  await assert.rejects(read, /TXT и DOCX/);
  const bytes = Buffer.alloc(20000, 255); bytes[0] = 80; bytes[1] = 75;
  elements.file.files = [file('example.docx', bytes)];
  assert.equal((await read()).document_base64, bytes.toString('base64'));
  elements.file.files = []; elements.text.value = '   ';
  await assert.rejects(read, /Добавьте текст/);
  console.log(JSON.stringify({status: 'passed', checks: 7, scope: 'node_vm_document_input', browser_acceptance: false}));
})().catch(() => {process.stderr.write('Document input regression failed\n'); process.exitCode = 1});
