// Run production mode, selection and translation controllers without a browser.
// Actual layout/gesture acceptance is recorded separately.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../web/reader.js',import.meta.url),'utf8');
const between=(a,b)=>{const start=source.indexOf(a),end=source.indexOf(b,start+a.length);assert.ok(start>=0&&end>start);return source.slice(start,end);};
const context=vm.createContext({AbortController,Promise,encodeURIComponent,isAdmin:()=>true});
vm.runInContext(between('// A page','export async function mountReader').replaceAll('export ',''),context);
const box={x:.1,y:.1,width:.3,height:.15};
const blocks=[{id:'first',type:'paragraph',text:'Original <script> text.',rects:[box],translation:null},{id:'second',text:'Another paragraph.',rects:[{...box,y:.5}],translation:null}];
assert.equal(context.readingBlockAt(blocks,{point:{x:.2,y:.2}}).id,'first');
assert.equal(context.readingBlockAt(blocks,{point:{x:.9,y:.9}}),null,'blank space must not pick nearest paragraph');
const exact=[{x:.11,y:.11,width:.04,height:.02}],snapshot=JSON.stringify(exact);
assert.equal(context.readingBlockAt(blocks,{rects:exact}).id,'first');
assert.equal(JSON.stringify(exact),snapshot,'lookup does not change precise note rectangle');
assert.equal(context.readingDisplay('unknown',true).mode,'pdf');
assert.equal(context.readingDisplay('reflow','true').bilingual,false,'translation requires actual explicit boolean');
assert.match(source,/readingMode:'pdf',translationView:false/,'every new reader starts original PDF without translation');
const job={id:'accepted',kind:'paragraph-translate',source_sha:'sha',page:1,block_id:'first',project_id:'project',status:'queued'};
assert.equal(context.readingJobMatches(job,'sha',1,'first','project'),true);
for(const changed of [{source_sha:'old'},{page:2},{block_id:'second'},{project_id:'other'},{kind:'translate'}])assert.equal(context.readingJobMatches({...job,...changed},'sha',1,'first','project'),false);

let loaded=[];
Object.assign(context,{
  state:{readingMode:'pdf',translationView:false},
  loadPageSide:async(side,page)=>loaded.push({side,page}),renderReadingPage:async page=>loaded.push({side:'reflow',page})
});
vm.runInContext(between('  async function loadPair(', '  function captureSelection('),context);
await context.loadPair(1);assert.deepEqual(loaded,[{side:'original',page:1}],'default does not request translated SVG/text');
loaded=[];context.state.translationView=true;await context.loadPair(2);assert.deepEqual(loaded,[{side:'original',page:2},{side:'translation',page:2}]);
loaded=[];context.state.readingMode='reflow';await context.loadPair(3);assert.deepEqual(loaded,[{side:'reflow',page:3}]);

// Switching either display control performs no translation POST.
let displayCalls=[];
Object.assign(context,{
 state:{readingMode:'pdf',translationView:false,page:1,pageOffset:.2,pairs:new Map(),near:new Set([1]),requests:new Map()},
 readPosition:()=>{},clearParagraphFocus:()=>{},updateReadingLayout:()=>{},renderReadingPanel:()=>{},
 requestAnimationFrame:fn=>fn(),goToPage:async(page)=>displayCalls.push(['goto',page]),
 loadPair:async page=>displayCalls.push(['read',page]),api:()=>{throw new Error('Mode toggle cannot create provider work');}
});
vm.runInContext(between('  async function setReadingDisplay(', '  async function loadReadingPage('),context);
await context.setReadingDisplay('reflow',true);assert.equal(context.state.readingMode,'reflow');assert.equal(context.state.translationView,true);
await context.setReadingDisplay('pdf',false);assert.equal(context.state.translationView,false);
assert.deepEqual(displayCalls,[['goto',1],['read',1],['goto',1],['read',1]]);

// Out-of-order source block loads never replace the last explicit selection.
let pending=[],panels=0;
const page={paper_id:'paper',page:1,source_sha:'sha',blocks};
Object.assign(context,{
 state:{paper:{sha256:'sha'},projectId:'project',readingSequence:0,disposed:false,translationView:false,readingFocus:null,selection:{quote:'exact words',rects:exact},draft:'unsaved thought',readingPages:new Map([[1,page]])},
 loadReadingPage:()=>new Promise(resolve=>pending.push(resolve)),renderReadingPanel:()=>panels++,renderParagraphHighlights:()=>{},updateReadingResults:()=>{},paragraphHint:()=>{}
});
vm.runInContext(between('  async function selectReadingBlock(', '  function renderReadingPanel('),context);
const first=context.selectReadingBlock(1,{point:{x:.2,y:.2}}),second=context.selectReadingBlock(1,{point:{x:.2,y:.6}});
pending[1](page);await second;assert.equal(context.state.readingFocus.block_id,'second');
pending[0](page);await first;assert.equal(context.state.readingFocus.block_id,'second');
assert.equal(panels,1);assert.equal(context.state.selection.quote,'exact words');assert.equal(context.state.draft,'unsaved thought');

// The only provider write uses authoritative identity; source text is absent.
let requests=[];
Object.assign(context,{
 pid:'paper',state:{...context.state,capabilities:{ai_configured:true},readingBusy:new Set(),jobs:[]},
 loadReadingPage:async()=>page,api:async(path,options)=>{requests.push({path,options});return job;},refreshJobs:async()=>{},
 blockTranslation:(block,pageNumber)=>context.readingJobMatches(block.translation,'sha',pageNumber,block.id,'project')?block.translation:null
});
vm.runInContext(between('  async function translateReadingBlock(', '  async function translateReadingPage('),context);
await context.translateReadingBlock(1,'first');
assert.equal(requests.length,1);assert.equal(requests[0].path,'/papers/paper/reading/translate');
assert.equal(JSON.stringify(requests[0].options.body),JSON.stringify({project_id:'project',page:1,block_id:'first',source_sha:'sha'}));
assert.equal(requests[0].options.method,'POST');
await context.translateReadingBlock(1,'first');assert.equal(requests.length,1,'pending cache deduplicates repeated clicks');
blocks[0].translation={...job,status:'succeeded',result_markdown:'中文。'};
await context.translateReadingBlock(1,'first');assert.equal(requests.length,1,'successful result is reused without a provider request');
context.state.capabilities.ai_configured=false;
await context.translateReadingBlock(1,'first');assert.equal(requests.length,1,'cached Chinese remains readable without current model configuration');
await assert.rejects(context.translateReadingBlock(1,'second'),/管理员设置/);assert.equal(requests.length,1);

// A generic jobs history must never resurrect a cache rejected by GET reading
// after model, prompt or limits changed. Only an accepted job ID may update.
blocks[0].translation=null;
context.state.jobs=[{...job,status:'succeeded',result_markdown:'Old model output'}];
vm.runInContext(between('  function updateReadingJobs(', '  async function refreshAcceptedReadingJobs('),context);
context.updateReadingJobs();assert.equal(blocks[0].translation,null);
blocks[0].translation={...job,id:'current',status:'queued'};
context.state.jobs=[{...job,status:'succeeded',result_markdown:'Old model output'},{...job,id:'current',status:'succeeded',result_markdown:'Current model output'}];
context.updateReadingJobs();assert.equal(blocks[0].translation.id,'current');assert.equal(blocks[0].translation.result_markdown,'Current model output');

// Escaped plain text prevents model output and PDF text from becoming HTML.
context.esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
vm.runInContext(between('  function readingTranslation(', '  function blockTranslation('),context);
assert.match(context.readingTranslation({status:'succeeded',result_markdown:'<img src=x onerror=alert(1)>'}),/&lt;img/);
assert.doesNotMatch(context.readingTranslation({status:'failed',error:'<script>bad</script>'}),/<script>/);
console.log('PASS: default untranslated modes, source block selection, precise-note isolation, late response protection, explicit paragraph POST, cache reuse, accepted-job identity and escaped results');

// A native sentence uses the verified-selection endpoint, not a paragraph lookup.
requests=[];
const selection={side:'original',source_sha:'sha',page:1,quote:'only these words',rects:exact};
Object.assign(context,{
 state:{paper:{sha256:'sha'},projectId:'project',readingSequence:3,jobs:[],narrow:false},
 isCurrentAnchor:anchor=>anchor.source_sha==='sha',
 api:async(path,options)=>{requests.push({path,options});return {...job,selection_scope:true};}
});
await context.translateExactSelection(selection);
assert.equal(context.state.readingSequence,4,'invalidate outstanding paragraph selection loads');
assert.equal(requests[0].path,'/papers/paper/reading/translate-selection');
assert.equal(requests[0].options.body.selected_text,selection.quote);
assert.equal(requests[0].options.body.selected_rects,exact);
await context.translateExactSelection(selection);assert.equal(requests.length,1);
await assert.rejects(context.translateExactSelection({...selection,source_sha:'old'}),/重新选择/);
assert.equal(requests.length,1);

// A selected sentence still finishes when it falls out of the recent-job list.
vm.runInContext(between('  async function refreshAcceptedReadingJobs(', '  function updateToolbar('),context);
context.state.jobs=[];context.state.readingPages=new Map();context.state.readingPollCursor=0;
context.api=async path=>{assert.equal(path,'/jobs/accepted');return {...job,selection_scope:true,status:'succeeded',result_markdown:'选区译文'};};
await context.refreshAcceptedReadingJobs();context.updateReadingJobs();
assert.equal(context.state.selectionTranslation.job.status,'succeeded');

// Mixed content renders source images and text; external/unsafe assets are rejected.
vm.runInContext(between('  function readingContentMarkup(', '  async function renderReadingPage('),context);
context.readingBlockMarkup=b=>`<p>${context.esc(b.text)}</p>`;
const visual={id:'visual-1',type:'table',image_url:'/api/papers/paper/pages/1.png?width=2000',width:200,height:100};
const markup=context.readingContentMarkup({blocks,content:[{id:'first',type:'text'},visual,{...visual,id:'unsafe',image_url:'javascript:alert(1)'}]},1);
assert.match(markup,/Original &lt;script&gt; text/);
assert.match(markup,/<figure class="reflow-visual"/);
assert.match(markup,/width=4000/);
assert.match(markup,/右键讲解/);
assert.doesNotMatch(markup,/data-reading-visual=/);
assert.doesNotMatch(markup,/javascript:|id="unsafe"/);
console.log('PASS: exact sentence submission, deduplication and stale-load protection; source image/table markup');

