// Exercise actual workspace events and rendering with controlled network/DOM.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../web/app.js',import.meta.url),'utf8').replace(/^import .*$/m,'const mountReader=()=>{};').replace(/^export /gm,'');
const elements=new Map(),handlers=new Map(),requests=[];
const el=name=>{if(!elements.has(name))elements.set(name,{innerHTML:'',outerHTML:'',textContent:'',hidden:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;},focus(){},classList:{add(){},remove(){}},addEventListener(type,fn){handlers.set(type,fn);}});return elements.get(name);};
el('#catalog-search-form').elements={direction:el('#category-control'),topic:el('#topic-control')};
const taxonomy={categories:[
 {id:'document',name:'OCR / 文档智能',count:1,match_count:1,topics:[{id:'document.ocr',name:'文字识别',count:1,match_count:1},{id:'document.layout',name:'版面分析',count:0,match_count:0}]},
 {id:'multimodal',name:'多模态',count:0,match_count:1,topics:[{id:'multimodal.vqa',name:'视觉问答',count:0,match_count:1}]},
 {id:'unsafe',name:'<script>unsafe</script>',count:0,topics:[]}
]};
const item={id:'paper',title:'OCR paper',kind:'paper',url:'https://arxiv.org/abs/2601.12345',classification:{primary:{category_id:'document',category_name:'OCR / 文档智能',topic_id:'document.ocr',topic_name:'文字识别'},secondary:[{category_id:'multimodal',category_name:'多模态',topic_id:'multimodal.vqa',topic_name:'视觉问答'}]},directions:['Old label']};
class Fields{constructor(form){this.values=form.data||{};}get(k){return this.values[k]??null;}}
let emptyDiscovery=false;
const context=vm.createContext({console,URL,URLSearchParams,Headers,AbortController,FormData:Fields,location:{pathname:'/',search:'?view=catalog'},history:{replaceState(){}},document:{querySelector:el},window:{addEventListener(){}},localStorage:{getItem(){return null;},setItem(){}},setTimeout(){return 1;},clearTimeout(){},fetch:async(path,request={})=>{
 requests.push({path,request});let result;
 if(path==='/api/auth/session')result={user:{id:'user',role:'user',inbox_id:'inbox'},csrf_token:'csrf'};
 else if(path==='/api/projects')result=[{id:'inbox',name:'Inbox'}];
 else if(path==='/api/imports')result=[];
 else if(path==='/api/catalog/stats')result={total:1,direction_taxonomy:taxonomy};
 else if(path.startsWith('/api/catalog?'))result={items:[item],total:1,page:1,pages:1};
 else if(path.startsWith('/api/discovery/'))result={items:emptyDiscovery?[]:[item],direction_taxonomy:taxonomy,direction_filter_scope:'当前返回页',has_more:true};
 else throw Error('Unexpected endpoint '+path);
 return {ok:true,headers:new Headers({'content-type':'application/json'}),json:async()=>result};
}});
const settle=async()=>{for(let i=0;i<50;i++)await Promise.resolve();};
const click=async dataset=>{await handlers.get('click')({target:{closest:()=>({dataset,isConnected:true,disabled:false})}});await settle();};
const change=async target=>{await handlers.get('change')({target:{dataset:{},...target}});await settle();};
const query=prefix=>new URL('https://local'+requests.filter(r=>r.path.startsWith(prefix)).at(-1).path).searchParams;
vm.runInContext(source,context);await settle();
assert.match(el('#app').innerHTML,/name="topic"/);
assert.match(el('#catalog-results').innerHTML,/data-topic="document.ocr"/);
assert.match(el('#catalog-results').innerHTML,/主方向/);assert.match(el('#catalog-results').innerHTML,/关联/);
assert.match(el('#category-control').innerHTML,/&lt;script&gt;/);
await click({catalogConference:'ICLR'});
await click({direction:'document',topic:'document.ocr',directionScope:'catalog'});
let params=query('/api/catalog?');assert.equal(params.get('direction'),'document');assert.equal(params.get('topic'),'document.ocr');assert.equal(params.get('conference'),'ICLR');
assert.match(el('#catalog-active-filters').innerHTML,/二级方向：文字识别/);
await change({form:{id:'catalog-search-form'},name:'direction',value:'multimodal'});
params=query('/api/catalog?');assert.equal(params.get('topic'),null);assert.equal(params.get('conference'),'ICLR');
assert.doesNotMatch(el('#topic-control').innerHTML,/document.layout/);
await change({form:{id:'catalog-search-form'},name:'topic',value:'multimodal.vqa'});
await click({clearCatalog:'topic'});params=query('/api/catalog?');assert.equal(params.get('direction'),'multimodal');assert.equal(params.get('topic'),null);
await click({direction:'document',topic:'document.ocr',directionScope:'catalog'});
await click({clearCatalog:'direction'});params=query('/api/catalog?');assert.equal(params.get('direction'),null);assert.equal(params.get('topic'),null);
await click({direction:'document',topic:'document.ocr',directionScope:'catalog'});
await click({followFilter:'institution:Example Lab'});params=query('/api/catalog?');assert.equal(params.get('institution'),'Example Lab');assert.equal(params.get('topic'),null);
await click({view:'discover'});
assert.match(el('.direction-filter').outerHTML,/id="discovery-topic"/);
await click({direction:'document',topic:'document.layout',directionScope:'discover'});
params=query('/api/discovery/');assert.equal(params.get('direction'),'document');assert.equal(params.get('topic'),'document.layout');
await change({id:'discovery-direction',value:'multimodal'});assert.equal(query('/api/discovery/').get('topic'),null);
await change({id:'discovery-topic',value:'multimodal.vqa'});assert.equal(query('/api/discovery/').get('topic'),'multimodal.vqa');
emptyDiscovery=true;
await handlers.get('submit')({preventDefault(){},target:{id:'discovery-search-form',data:{q:'VQA',scope:'hf'},querySelector:()=>null}});await settle();
assert.match(el('#discovery-results').innerHTML,/data-search-page="2"\s*>下一页/,'Filtering an empty page must not disable further search pages');
await click({searchPage:'2'});assert.equal(query('/api/discovery/').get('page'),'2');
await change({id:'discovery-topic',value:'multimodal.vqa'});assert.equal(query('/api/discovery/').get('page'),'1');
await click({action:'clear-discovery-direction'});params=query('/api/discovery/');assert.equal(params.get('direction'),null);assert.equal(params.get('topic'),null);
assert.ok(requests.every(r=>!r.request.method||r.request.method==='GET'),'Taxonomy browsing never triggers generation or imports');
console.log('PASS: two-level catalog/discovery controls, linked filters, tag paths, escaping, clearing, category navigation and filtered-empty pagination');
