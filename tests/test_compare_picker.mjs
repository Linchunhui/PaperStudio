import assert from 'node:assert/strict';
import {runtime,load,esc,settle,root} from './ui-harness.mjs';
const ui=root(),events=new Map(),calls=[],notices=[];let focused=0,removed=0,resolvePapers;
const dialog={...ui.root,setAttribute(){},showModal(){},addEventListener(k,fn){events.set(k,fn);},remove(){removed++;},close(){events.get('close')?.();}};
const c=runtime({document:{activeElement:{focus(){focused++;}},createElement(){return dialog;},body:{append(){}}}});load(c,'compare-picker.js');
const open=c.openComparePicker({projectId:'study',api:async path=>{calls.push(path);return new Promise(resolve=>{resolvePapers=resolve;});},esc,toast:v=>notices.push(v),onCompare:async detail=>{calls.push(detail);}});
assert.match(dialog.innerHTML,/正在读取/);assert.equal(calls.length,1);assert.match(calls[0],/project_id=study&include_children=false/);
resolvePapers(Array.from({length:8},(_,i)=>({id:'p'+i,title:'Paper '+i,page_count:i+1})));await open;await settle();
assert.equal(ui.node('[type=submit]').disabled,true);
for(let i=0;i<7;i++)dialog.onchange({target:{type:'checkbox',value:'p'+i,checked:true}});
assert.equal(ui.node('#compare-count').textContent,'已选择 6 / 6 篇');assert.equal(ui.node('[type=submit]').disabled,false);
dialog.oninput({target:{name:'search',value:'Paper 1'}});assert.match(ui.node('#compare-list').innerHTML,/Paper 1/);assert.doesNotMatch(ui.node('#compare-list').innerHTML,/Paper 2/);
await dialog.onsubmit({preventDefault(){},target:{data:{question:'对比实验条件'}}});assert.equal(calls.length,2);assert.deepEqual(JSON.parse(JSON.stringify(calls[1].paper_ids)),['p0','p1','p2','p3','p4','p5']);assert.equal(calls[1].kind,'compare');assert.equal(calls[1].question,'对比实验条件');assert.equal(calls[1].autoGenerate,true);assert.equal(removed,1);assert.equal(focused,1);
// Closing during read must not reopen or submit anything.
const later=c.openComparePicker({projectId:'study',api:async()=>new Promise(resolve=>{resolvePapers=resolve;}),esc,toast(){},onCompare(){assert.fail('Closed dialog cannot submit');}});dialog.close();const previous=dialog.innerHTML;resolvePapers([]);await later;assert.equal(dialog.innerHTML,previous);
console.log('PASS: project-only 2–6 paper choice, bounded selection across search, explicit LLM request, focus and late-close handling');
