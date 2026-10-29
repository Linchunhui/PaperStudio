// Contract checks with a minimal DOM double; these do not replace browser or visual QA.
// Run: node tests/test_workspace.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../web/app.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace(/^export /gm, '');
const elements = new Map();
const handlers = new Map();
const lifecycle = new Map();
let importRows=[],libraryRows=[],slowSearchResolve;
const el = name => {
  if (!elements.has(name)) elements.set(name, {innerHTML:'', textContent:'', hidden:false, attributes:{},setAttribute(name,value){this.attributes[name]=value;},classList:{add(){},remove(){}}, addEventListener(type,fn){handlers.set(type,fn);}});
  return elements.get(name);
};
const requests = [];
class Fields {
  constructor(form){this.data = new Map(Object.entries(form?.data||{}));}
  get(k){return this.data.get(k)??null;}
  set(k,v){this.data.set(k,v);}
}
const payload = path => {
  if(path==='/api/auth/session')return {user:{id:'local',username:'reader',display_name:'Reader',role:'admin',is_admin:true,inbox_id:'inbox'},csrf_token:'test-csrf'};
  if(path==='/api/projects')return [{id:'inbox',name:'收件箱',parent_id:null},{id:'child',name:'文档研究',parent_id:'inbox'}];
  if(path==='/api/capabilities')return {ai_configured:false,translation_configured:false};
  if(path==='/api/skills')return [{id:'paper-brief',name:'粗读'}];
  if(path==='/api/sources')return {sources:[]};
  if(path==='/api/discovery/config')return {web_search:{configured:true,provider:'searxng'}};
  if(path.startsWith('/api/discovery/feed'))return {items:[{id:'unsafe',kind:'article',url:'javascript:alert(1)',title:'<img src=x onerror=alert(1)>',snippet:'<script>bad</script>',source_id:'hf',paper_urls:[]},{id:'real',kind:'paper',url:'https://arxiv.org/abs/2501.12948',title:'Paper',paper_urls:['https://arxiv.org/abs/2501.12948'],source_id:'hf',source_ids:['hf','qbitai'],reasons:['新发布']}],statuses:[{source_id:'hf',status:'ok',fetched_at:'2026-10-23T10:00:00Z'},{source_id:'jiqizhixin',status:'unconfigured',error:'请配置搜索服务'}]};
  if(path.startsWith('/api/discovery/search'))return {items:[],statuses:[]};
  if(path==='/api/imports')return importRows;
  if(path.startsWith('/api/papers'))return libraryRows;
  throw new Error('Unexpected endpoint: '+path);
};
const context=vm.createContext({mountReader:()=>{},mountWelcome:()=>({dispose(){}}),mountModelSettings:()=>({dispose(){}}),mountPortability:()=>({dispose(){}}),mountDataBundle:()=>({dispose(){}}),mountProjectTemplates:()=>({dispose(){}}),feedbackMarkup:()=>'',mountFeedback:()=>({dispose(){}}),console,URL,URLSearchParams,Headers,FormData:Fields,location:{pathname:'/',search:''},history:{replaceState(){}},document:{querySelector:el},window:{addEventListener(type,fn){lifecycle.set(type,fn);}},localStorage:{getItem(){return null;},setItem(){}},setTimeout(){return 1;},clearTimeout(){},fetch:async(path,request)=>{
  requests.push({path,request});
  if(path.startsWith('/api/discovery/search?')&&path.includes('q=slow'))return new Promise(resolve=>{slowSearchResolve=()=>resolve({ok:true,headers:new Headers({'content-type':'application/json'}),json:async()=>({items:[{id:'new',kind:'paper',title:'New search paper',url:'https://arxiv.org/abs/2501.12948',source_id:'arxiv'}],statuses:[]})});});
  const data=request.method==='POST'?{id:'i1',paper_id:'p1',status:'queued'}:payload(path);
  return {ok:true,headers:new Headers({'content-type':'application/json'}),json:async()=>data};
}});
const settle=async()=>{for(let i=0;i<25;i++)await Promise.resolve();};
const click=async dataset=>{await handlers.get('click')({target:{closest:()=>({dataset,isConnected:true,disabled:false})}});await settle();};
(async()=>{
  vm.runInContext(source,context);
  await settle();
  assert.match(el('#app').innerHTML,/id="explore-panel"/,'default view mounts unified search and recommendations');
  assert.doesNotMatch(el('#app').innerHTML,/data-view="recommendations"|data-view="imports"/);
  await click({view:'skills'});
  assert.match(el('#app').innerHTML,/searxng/);
  await click({action:'open-import'});
  assert.match(el('#app').innerHTML,/data-library-section="follows"/);
  assert.match(el('#app').innerHTML,/class="processing-grid"/);
  const submitButton={disabled:false,isConnected:true,textContent:''};
  const form={id:'import-form',data:{url:'https://arxiv.org/abs/2501.12948v1',project_id:'child',title:'Paper'},elements:{parse:{checked:false},translate:{checked:true},guide:{checked:false},abstract:{checked:true},cards:{checked:true},summary:{checked:true}},querySelector:()=>submitButton};
  await handlers.get('submit')({preventDefault(){},target:form});
  const post=requests.find(x=>x.path==='/api/imports'&&x.request.method==='POST');
  assert.ok(post,'URL form must submit the imports API');
  assert.deepEqual(JSON.parse(post.request.body),{url:'https://arxiv.org/abs/2501.12948v1',project_id:'child',title:'Paper',processing:{parse:true,translate:true,guide:false,brief_modes:['abstract','cards','summary']}},'AI selections must require parse and preserve exact API keys');
  const countPosts=()=>requests.filter(x=>x.path==='/api/imports'&&x.request.method==='POST').length;
  const beforeInvalid=countPosts();
  form.data.url='javascript:alert(1)';
  await handlers.get('submit')({preventDefault(){},target:form});
  assert.equal(countPosts(),beforeInvalid,'invalid input URL must not be sent to import API');
  assert.match(el('#import-feedback').textContent,/HTTP\(S\)/,'invalid link must explain accepted schemes');
  await click({librarySection:'papers'});
  const beforeLibraryRefresh=requests.filter(x=>x.path.startsWith('/api/papers')).length;
  importRows=[{id:'i1',paper_id:'new-paper',status:'running',project_id:'inbox',steps:[]}];
  libraryRows=[{id:'new-paper',title:'Background paper',page_count:3,memberships:[{project_id:'inbox',status:'unread'}]}];
  lifecycle.get('pagehide')();
  lifecycle.get('pageshow')({persisted:true});
  await settle();
  assert.ok(requests.filter(x=>x.path.startsWith('/api/papers')).length>beforeLibraryRefresh,'newly downloaded paper must refresh the visible library');
  assert.match(el('#library-results').innerHTML,/Background paper/,'completed background download must appear without navigating away');
  const shared={memberships:[{project_id:'parent',status:'unread'},{project_id:'child',status:'read'}]};
  assert.equal(context.paperMembership(shared,'parent',new Set(['parent','child']),'read').project_id,'child','status filtering must open the matching project context');
  assert.equal(context.paperMembership(shared,'parent',new Set(['parent','child']),'all').project_id,'parent','unfiltered reading should preserve current project context');
  assert.equal(context.safeURL('javascript:alert(1)'), '');
  assert.equal(context.safeURL('https://secret:token@example.com/paper.pdf'), '');
  assert.equal(context.safeURL('https://arxiv.org/pdf/2501.12948'), 'https://arxiv.org/pdf/2501.12948');
  assert.equal(context.processingProgress({kind:'download',progress:{downloaded_bytes:1048576,total_bytes:2097152,source_sha:'secret'}}),'已下载 1.0 / 2.0 MB');
  assert.match(context.processingProgress({kind:'translate',progress:{coverage:{ready_pages:3,total_pages:8,translation_run_id:'secret'}}}),/3 \/ 8 页/);
  assert.doesNotMatch(context.processingProgress({kind:'parse',progress:{text_pages:7,total_pages:8,source_sha:'secret',scope:'internals'}}),/secret|internals/);
  console.log('PASS: unified workspace routes, nested library/import UI, provider config, exact processing payload, invalid URL rejection, background library refresh and safe progress');
})().catch(error=>{console.error(error);process.exitCode=1;});

