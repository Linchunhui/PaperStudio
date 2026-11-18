import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../web/reader-accounts.js',import.meta.url),'utf8').replace(/^export /gm,'');
class FormData {
  constructor(values){this.values=values;}
  get(key){return this.values[key]??null;}
  getAll(key){return this.values[key]??[];}
}
const context=vm.createContext({FormData,Error});
vm.runInContext(source,context);
const valid={name:' Reader ',persona:'Researcher',goals:'Read\nEvaluate',topic:['document.ocr'],custom:'archives, history',level:'beginner'};
const payload=context.readerInput(valid);
assert.equal(payload.name,'Reader');
assert.equal(payload.goals.length,2);
assert.equal(payload.interests.length,3);
assert.equal(payload.interests[0].topic_id,'document.ocr');
assert.equal(payload.interests[1].custom,'archives');
assert.throws(()=>context.readerInput({...valid,topic:[],custom:''}),/至少/);
assert.throws(()=>context.readerInput({...valid,topic:Array(21).fill('x'),custom:''}),/20/);
const handlers=new Map(),status={textContent:''};
const root={innerHTML:'',addEventListener:(k,v)=>handlers.set(k,v),removeEventListener:k=>handlers.delete(k),
 querySelector:()=>status,querySelectorAll:()=>[]};
let resolve;
const request=new Promise(done=>resolve=done);
const mounted=context.mountReaderAccounts(root,{api:()=>request,esc:String,onSwitched:()=>{}});
const initial=root.innerHTML;
mounted.dispose();
resolve({owner:{id:'owner',display_name:'Owner'},items:[],taxonomy:{categories:[]}});
await Promise.resolve();await Promise.resolve();
assert.equal(root.innerHTML,initial);
assert.equal(handlers.size,0);
console.log('reader accounts UI input and disposal contracts passed');

// A successful delayed switch must reconcile global identity after panel disposal.
const lateHandlers=new Map();
const lateRoot={innerHTML:'',addEventListener:(k,v)=>lateHandlers.set(k,v),
  removeEventListener:k=>lateHandlers.delete(k),querySelector:()=>status,querySelectorAll:()=>[]};
let finishSwitch, switched=0, received;
const pendingSwitch=new Promise(resolve=>finishSwitch=resolve);
const lateMount=context.mountReaderAccounts(lateRoot,{
  api:path=>path.endsWith('/switch')?pendingSwitch:Promise.resolve({
    owner:{id:'owner',display_name:'Owner'},active_user_id:'owner',items:[],taxonomy:{categories:[]}}),
  esc:String,onSwitched:result=>{switched++;received=result;}
});
await Promise.resolve();await Promise.resolve();
const switchClick=lateHandlers.get('click');
const switching=switchClick({target:{closest:()=>({dataset:{readerSwitch:'child'}})}});
lateMount.dispose();
assert.equal(switched,0);
finishSwitch({active_user_id:'child',csrf_token:'rotated-token'});
await switching;
assert.equal(switched,1);
assert.equal(received.active_user_id,'child');
assert.equal(received.csrf_token,'rotated-token');
assert.equal(lateHandlers.size,0);
console.log('disposed panel still reconciles successful account switch');
