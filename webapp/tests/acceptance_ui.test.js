/* Isolated UI regressions. Run with node; jsdom is a test-only dependency. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'), {runScripts:'outside-only'});
const w = dom.window;
w.setInterval = () => 0;
w.fetch = async () => ({ok:true,json:async()=>({groups:[]})});
w.eval(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);/g,'\n') +
  '\nwindow.auditUndo = { push: value => grpUndoStack.push(value), count: () => grpUndoStack.length };');
(async () => {
  w.auditUndo.push({account:'probe',from:'old',to:'new'});
  await w.loadGroups();
  assert.strictEqual(w.auditUndo.count(),1,'refresh must preserve undo');
  const input = w.document.querySelector('input');
  input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true,cancelable:true}));
  assert.strictEqual(w.auditUndo.count(),1,'editing undo must not move accounts');
  const ev=new w.KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true,cancelable:true});
  w.document.body.dispatchEvent(ev);
  assert.strictEqual(w.auditUndo.count(),0,'group undo consumes the step');
  assert.strictEqual(ev.defaultPrevented,true);
  w.eval(`Date = class extends Date {
    static now() { return 1792168200000; }
    getFullYear() { return 2026; }
    getMonth() { return 9; }
    getDate() { return 17; }
    toISOString() { return '2026-10-16T16:30:00.000Z'; }
  }`);
  assert.strictEqual(w.today(),'2026-10-17','date must use local fields');
  console.log('PASS: group undo refresh, editing shortcut, group shortcut, local date');
  dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
