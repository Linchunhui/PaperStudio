import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../web/collaboration-ui.js',import.meta.url),'utf8').replace(/^import .*$/m,'').replace(/^export /gm,'');
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
class Fields{constructor(form){this.data=form?.data||{};}get(key){return this.data[key]??null;}}
async function boot(role){
  const requests=[],copies=[],container={isConnected:true,innerHTML:'',scrollTop:0,querySelectorAll(){return [];},contains(){return false;}};
  const group={id:'g1',name:'<script>Research</script>',owner_id:'owner',role,member_count:2,members:[{user_id:'owner',username:'owner',display_name:'Owner',role:'admin'},{user_id:'member',username:'reader',display_name:'Reader',role:'viewer'}],projects:[{id:'p1',name:'Shared project'}]};
  const context=vm.createContext({document:{activeElement:null},console,URL,URLSearchParams,FormData:Fields,esc,list:value=>Array.isArray(value)?value:value.items||[],toast(){},confirm:()=>true,qs(){},location:{origin:'https://paperstudio.example',search:'?invite=invite-token'},navigator:{clipboard:{async writeText(value){copies.push(value);}}},authSession:{user:{id:role==='admin'?'owner':'member'}},api:async(path,options={})=>{
    requests.push({path,options});if(path==='/groups')return [group];if(path==='/groups/g1')return group;if(path==='/groups/g1/invitations')return {token:'new-invite',invite_url:'/?invite=new-invite'};if(path==='/groups/join')return group;if(path==='/groups/g1/members/member')return group;throw new Error('Unexpected API '+path);
  }});
  vm.runInContext(source,context);await context.mountGroups(container);
  const click=async(dataset,attributes=[])=>container.onclick({stopPropagation(){},target:{closest:()=>({dataset,hasAttribute:name=>attributes.includes(name)})}});
  const submit=async(id,data)=>container.onsubmit({preventDefault(){},stopPropagation(){},target:{id,data,setAttribute(){},querySelector:()=>({disabled:false,isConnected:true})}});
  await click({group:'g1'});return {context,container,requests,copies,click,submit};
}
const viewer=await boot('viewer');
assert.match(viewer.container.innerHTML,/&lt;script&gt;Research&lt;\/script&gt;/);
assert.doesNotMatch(viewer.container.innerHTML,/id="invite-group-form"|data-member-role|data-remove-member/,'Viewer has no invitation or role-management controls');
assert.match(viewer.container.innerHTML,/view=library&project=p1/);
const admin=await boot('admin');
assert.match(admin.container.innerHTML,/id="invite-group-form"/);
assert.match(admin.container.innerHTML,/aria-pressed="true"/);
assert.match(admin.container.innerHTML,/aria-label="将 Reader 移出小组"/);
assert.match(admin.container.innerHTML,/data-member-role="member"/);
assert.doesNotMatch(admin.container.innerHTML,/data-member-role="owner"/,'Creator administrator cannot be demoted in the UI');
await admin.submit('invite-group-form',{role:'editor',expires_days:'7'});
const invite=admin.requests.find(row=>row.path==='/groups/g1/invitations');
assert.equal(invite.options.method,'POST');assert.deepEqual(JSON.parse(JSON.stringify(invite.options.body)),{role:'editor',expires_days:7});
assert.match(admin.container.innerHTML,/https:\/\/paperstudio.example\/\?invite=new-invite/,'Invitation is a usable absolute URL');
await admin.click({},['data-copy-invite']);assert.equal(admin.copies[0],'https://paperstudio.example/?invite=new-invite');
await viewer.submit('join-group-form',{token:'https://paperstudio.example/?invite=token-from-url'});
const join=viewer.requests.find(row=>row.path==='/groups/join');assert.equal(join.options.body.token,'token-from-url','Pasted invitation URL resolves to the token field');
console.log('PASS: group-role UI boundaries, safe group names, owner immutability, absolute invite links, explicit invitation and join payloads');

const owner=admin.context;
const makeField=value=>({tagName:'INPUT',id:'',name:'name',form:{id:'create-group-form'},dataset:{},value,selectionStart:1,selectionEnd:3,selectionDirection:'forward',getAttribute(name){return name==='name'?this.name:null;},focus(){owner.document.activeElement=this;},setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}});
const original=makeField('未提交的小组名称');let fields=[original];const formRoot={scrollTop:0,contains:el=>fields.includes(el),querySelectorAll:selector=>selector==='details'?[]:fields};owner.document.activeElement=original;const restore=owner.retainInteraction(formRoot,true);const updated=makeField('');fields=[updated];restore();assert.equal(updated.value,'未提交的小组名称');assert.equal(owner.document.activeElement,updated);assert.deepEqual([updated.selectionStart,updated.selectionEnd],[1,3]);

// Exercise the dialog lifecycle without requiring an installed browser package.
let lastDialog=null;const opener={isConnected:true,focus(){owner.document.activeElement=this;}};
owner.document.activeElement=opener;owner.document.body={append(dialog){dialog.isConnected=true;}};
owner.document.createElement=tag=>{
  assert.equal(tag,'dialog');const listeners={},attrs={};let markup='';
  const dialog={open:false,isConnected:false,scrollTop:0,controls:[],setAttribute(name,value){attrs[name]=value;},getAttribute(name){return attrs[name];},querySelectorAll(selector){return selector==='details'?[]:this.controls;},contains(el){return this.controls.includes(el);},querySelector(selector){return selector==='[data-close-sharing]'?this.controls[0]:null;},addEventListener(name,fn){listeners[name]=fn;},showModal(){this.open=true;this.controls[0].focus();},close(){this.open=false;listeners.close?.();},remove(){this.isConnected=false;},cancel(){let prevented=false;listeners.cancel({preventDefault(){prevented=true;}});return prevented;},get innerHTML(){return markup;},set innerHTML(value){markup=value;const close={id:'',tagName:'BUTTON',dataset:{closeSharing:''},textContent:'×',getAttribute(){return null;},focus(){owner.document.activeElement=this;},hasAttribute(name){return name==='data-close-sharing';}};this.controls=[close];}};
  lastDialog=dialog;return dialog;
};
owner.api=async path=>path==='/groups'?[{id:'g1',name:'Research',role:'admin'}]:[];
await owner.openProjectSharing({id:'p1',name:'Project'});assert.equal(lastDialog.open,true);assert.match(lastDialog.innerHTML,new RegExp(`<h2 id="${lastDialog.getAttribute('aria-labelledby')}">共享项目 · Project</h2>`));assert.match(lastDialog.innerHTML,new RegExp(`<p id="${lastDialog.getAttribute('aria-describedby')}">`));assert.equal(owner.document.activeElement,lastDialog.controls[0],'Initial async load retains dialog focus');assert.match(lastDialog.innerHTML,/aria-busy="false"/);assert.equal(lastDialog.cancel(),true);assert.equal(lastDialog.isConnected,false);assert.equal(owner.document.activeElement,opener,'Escape returns focus to the opener');
await owner.openProjectSharing({id:'p1',name:'Project'});await lastDialog.onclick({target:{closest:()=>lastDialog.controls[0]}});assert.equal(lastDialog.isConnected,false);assert.equal(owner.document.activeElement,opener,'Close button returns focus to the opener');
console.log('PASS: group form drafts and cursor preservation; named/described dialog, async-load focus, Escape and close return focus');
