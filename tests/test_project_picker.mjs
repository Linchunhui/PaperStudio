// Run the real dialog controller with deterministic DOM and task responses.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const text=fs.readFileSync(new URL('../web/project-picker.js',import.meta.url),'utf8').replace(/^export /gm,'');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeURL=v=>/^https?:\/\//.test(v||'')?v:'';
const timers=new Map();let time=0;
const context=vm.createContext({console,encodeURIComponent,Set,Map,setTimeout(fn){timers.set(++time,fn);return time;},clearTimeout(id){timers.delete(id);}});
vm.runInContext(text,context);
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function surface(){
  const handlers=new Map(),formHandlers=new Map();let restored=0;
  const node=()=>({disabled:false,innerHTML:'',textContent:'',focus(){},addEventListener(){}});
  const select={...node(),value:'',set innerHTML(html){this.html=html;this.value=html.match(/value="([^"]*)"/)?.[1]||'';},get innerHTML(){return this.html||'';}};
  const submit=node(),status=node(),result=node(),url={value:''};
  const form={elements:{project_id:select,url},querySelector:()=>submit,addEventListener:(k,v)=>formHandlers.set(k,v)};
  const dialog={...node(),open:false,setAttribute(){},querySelector(selector){return selector==='form'?form:selector==='#project-picker-status'?status:selector==='#project-picker-result'?result:null;},
    showModal(){this.open=true;},close(){this.open=false;handlers.get('close')?.();},remove(){this.removed=true;},addEventListener:(k,v)=>handlers.set(k,v)};
  const doc={activeElement:{isConnected:true,focus(){restored++;}},body:{append(){}},createElement(){return dialog;},querySelector(){return null;}};
  return {doc,dialog,form,select,submit,status,result,url,restored:()=>restored,
    send:()=>formHandlers.get('submit')({preventDefault(){},stopPropagation(){}})};
}
const calls=[],notices=[];let resolvePost;
const api=async(path,options={})=>{
  calls.push({path,options});
  if(path==='/projects')return [{id:'inbox',name:'收件箱'},{id:'read-only',name:'只读项目',can_edit:false},{id:'team',name:'Team <script>',can_edit:true}];
  if(path==='/imports')return new Promise(resolve=>{resolvePost=resolve;});
  if(path==='/imports/task')return {id:'task',project_id:'team',paper_id:'saved',status:'succeeded',steps:[]};
  throw new Error('Unexpected '+path);
};
const ui=surface();
const picker=context.openProjectPicker({item:{title:'Title <img>',catalog_paper_id:'cat',url:'https://arxiv.org/abs/2601.12345'},api,esc,safeURL,toast:m=>notices.push(m),projectId:'team',document:ui.doc});
await settle();
assert.ok(ui.dialog.open);
assert.match(ui.dialog.innerHTML,/Title &lt;img&gt;/);
assert.doesNotMatch(ui.dialog.innerHTML,/brief_modes|name="guide"|name="translate"/);
assert.doesNotMatch(ui.select.innerHTML,/read-only/);
assert.equal(ui.select.value,'team');
assert.equal(calls.filter(c=>c.options.method).length,0);
const sending=ui.send();await settle();await ui.send();
assert.equal(calls.filter(c=>c.path==='/imports').length,1,'Duplicate clicks cannot enqueue more work');
const body=calls.find(c=>c.path==='/imports').options.body;
assert.equal(body.project_id,'team');assert.equal(body.catalog_paper_id,'cat');
assert.equal(JSON.stringify(body.processing),JSON.stringify({parse:true,translate:false,guide:false,brief_modes:[]}));
resolvePost({id:'task',project_id:'team',status:'queued',steps:[]});await sending;
assert.match(ui.status.textContent,/等待保存/);
const poll=[...timers.values()][0];timers.clear();await poll();
assert.match(ui.status.textContent,/已加入项目/);
assert.match(ui.result.innerHTML,/\/read\/saved\?project=team/);
picker.close();assert.equal(ui.restored(),1);assert.equal(timers.size,0);

// Missing source URLs can be supplied inside this same dialog. An unverified
// title-only entry must not acquire a catalog identity from the supplied URL.
const missing=surface();let added;
context.openProjectPicker({item:{title:'Title only',catalog_paper_id:'unverified',resolve:{action:'search'},url:'https://conference.example/schedule'},
  api:async(path,options)=>path==='/projects'?[{id:'inbox',name:'Inbox'}]:(added=options.body,{id:'done',status:'succeeded',paper_id:'p2',project_id:'inbox'}),esc,safeURL,toast(){},document:missing.doc});
await settle();assert.match(missing.dialog.innerHTML,/name="url"/);
missing.url.value='https://arxiv.org/abs/2601.12346';await missing.send();
assert.equal(added.url,missing.url.value);assert.equal(added.catalog_paper_id,undefined);
assert.match(missing.result.innerHTML,/开始阅读/);

// Validation errors keep the picker and project choice intact for retry.
const failed=surface();let fail=true;
context.openProjectPicker({item:{title:'Retry',url:'https://example.org/paper.pdf'},api:async(path)=>{
  if(path==='/projects')return [{id:'inbox',name:'Inbox'}];
  if(fail)throw new Error('链接暂时无法访问');return {id:'ok',status:'succeeded',paper_id:'ok',project_id:'inbox'};
},esc,safeURL,toast(){},document:failed.doc});await settle();await failed.send();
assert.equal(failed.submit.disabled,false);assert.equal(failed.select.disabled,false);
assert.match(failed.status.textContent,/链接暂时无法访问/);assert.ok(failed.dialog.open);
fail=false;await failed.send();assert.match(failed.result.innerHTML,/\/read\/ok/);

// Closing while the POST is in flight leaves accepted work running in the
// background and prevents stale UI writes into a removed dialog.
const closed=surface();let finish;
const closer=context.openProjectPicker({item:{title:'Background',url:'https://example.org/paper.pdf'},api:async(path)=>path==='/projects'?[{id:'inbox',name:'Inbox'}]:new Promise(resolve=>{finish=resolve;}),esc,safeURL,toast:m=>notices.push(m),document:closed.doc});
await settle();const pending=closed.send();await settle();closer.close();
finish({id:'background',status:'queued'});await pending;
assert.equal(closed.result.innerHTML,'');assert.match(notices.at(-1),/已提交加入项目任务/);
console.log('PASS: inline project selection, read-only projects, exact non-AI plan, duplicate submits, progress, missing URLs, retry, focus return and close during submission');
