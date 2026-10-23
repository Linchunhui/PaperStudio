// Pure regression tests of the production paragraph-link helpers. No browser
// or fabricated counterpart selections: actual gesture/layout QA is separate.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/reader.js', import.meta.url), 'utf8');
const start = source.indexOf('// A page');
const end = source.indexOf('export async function mountReader');
assert.ok(start >= 0 && end > start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end).replaceAll('export ', ''), context);
const {matchingParagraphs: match, alignmentMatchesVersion: current, paragraphVersionKey: key,
  paragraphScrollDelta: delta, validParagraphRects: valid} = context;
const box = (x,y,width=.35,height=.10) => ({x,y,width,height});
const paragraphs = [
  {id:'left-1',original:{rects:[box(.08,.10)]},translation:{rects:[box(.08,.15),box(.08,.26,.35,.03)]}},
  {id:'left-2',original:{rects:[box(.08,.30)]},translation:{rects:[box(.08,.40)]}},
  {id:'right-1',original:{rects:[box(.56,.10)]},translation:{rects:[box(.56,.13)]}},
];
const ids = rows => Array.from(rows, row => row.id);
assert.deepEqual(ids(match(paragraphs,'original',{point:{x:.2,y:.15}})), ['left-1']);
assert.deepEqual(ids(match(paragraphs,'translation',{point:{x:.2,y:.27}})), ['left-1'], 'reverse linkage works with a multi-rect translated paragraph');
assert.deepEqual(ids(match(paragraphs,'original',{point:{x:.7,y:.15}})), ['right-1']);
assert.deepEqual(ids(match(paragraphs,'original',{point:{x:.5,y:.15}})), [], 'column gutter does not select a nearest guessed paragraph');
assert.deepEqual(ids(match(paragraphs,'original',{point:{x:.2,y:.25}})), [], 'paragraph gap is unmatched');

const exact = [{x:.1,y:.11,width:.08,height:.02}];
const before = JSON.stringify(exact);
assert.deepEqual(ids(match(paragraphs,'original',{rects:exact})), ['left-1']);
assert.equal(JSON.stringify(exact), before, 'linked paragraph lookup must not broaden a precise note selection');
assert.deepEqual(ids(match(paragraphs,'original',{rects:[box(.1,.11,.08,.02),box(.1,.31,.08,.02)]})), ['left-1','left-2'], 'a multi-paragraph selection links each real correspondence');
assert.deepEqual(ids(match(paragraphs,'original',{rects:[box(.1,.11,.08,.02),box(.6,.11,.08,.02)]})), ['left-1','right-1'], 'use individual line rects rather than an oversized union box');
assert.deepEqual(ids(match(paragraphs,'original',{rects:[box(.08,.2,.35,.1)]})), [], 'touching a boundary is not intersection');
assert.deepEqual(ids(match(paragraphs,'translation',{rects:[box(.1,.41,.1,.02)]})), ['left-2']);
assert.deepEqual(ids(match(paragraphs,'other',{point:{x:.2,y:.15}})), []);
assert.deepEqual(ids(match([{id:'missing',original:{rects:[box(.1,.1)]}}],'original',{point:{x:.2,y:.15}})), [], 'missing counterpart cannot be presented as successful alignment');
assert.deepEqual(ids(match(paragraphs,'original',{point:{x:NaN,y:.15}})), []);
assert.equal(valid([box(0,0),box(-.1,0),box(.9,.9),box(0,0,0),box(0,0,.1,Infinity)]).length, 1);

const manifest = {mode:'progressive',active_run_id:'run-A',pages:[
  {page:1,status:'ready',source_sha:'translated-page-1'},
  {page:2,status:'running'},
]};
const map = {page:1,original_sha:'original',translation_sha:'translated-page-1',translation_run_id:'run-A'};
assert.equal(current(map,'original',manifest,1), true);
assert.equal(current({...map,translation_run_id:'run-old'},'original',manifest,1), false);
assert.equal(current({...map,translation_sha:'old-page'},'original',manifest,1), false);
assert.equal(current({...map,original_sha:'other-original'},'original',manifest,1), false);
assert.equal(current({...map,page:2},'original',manifest,1), false);
const originalKey = key('original',manifest,1);
manifest.pages[1] = {page:2,status:'ready',source_sha:'translated-page-2'};
assert.equal(key('original',manifest,1), originalKey, 'completion of another page must not invalidate the active mapping');
assert.equal(current(map,'original',manifest,1), true);
assert.notEqual(key('original',{...manifest,active_run_id:'run-B'},1), originalKey);
const manual = {mode:'manual',active_run_id:null,pages:[{page:1,status:'ready',source_sha:'manual-pdf'}]};
assert.equal(current({...map,translation_sha:'manual-pdf',translation_run_id:null},'original',manual,1), true);
assert.equal(current(map,'original',manual,1), false, 'manual replacement invalidates a stale progressive mapping');
assert.notEqual(key('original',manual,1), originalKey);
const pending = {mode:'progressive',active_run_id:'run-A',pages:[{page:1,status:'running'}]};
assert.equal(current({...map,translation_sha:null},'original',pending,1), true);
assert.equal(current(map,'original',pending,1), false);
assert.notEqual(key('original',pending,1), originalKey, 'page becoming ready must re-fetch a pending mapping');
assert.equal(current(null,'original',manifest,1), false);

assert.equal(delta(120,180,100,500), -4, 'move by only the missing inset');
assert.equal(delta(130,180,100,500), 0, 'already visible counterpart does not jump');
assert.equal(delta(480,520,100,500), 44);
assert.equal(delta(0,600,100,500), 0, 'large paragraph already spanning the viewport stays put');
assert.equal(delta(600,1200,100,500), 476, 'oversized offscreen paragraph reveals its beginning');
assert.equal(delta(NaN,200,100,500), 0);
assert.equal(delta(120,100,100,500), 0);
console.log('PASS: paragraph bidirectionality, multi-rect/multi-column selection, exact-note isolation, pending readiness, run/hash/manual invalidation and minimal scrolling');

// Exercise the production asynchronous controller without a browser. Fake DOM
// painting/scrolling only; HTTP promises and cancellation/version races are real.
const functionBetween = (start, end) => {
  const a=source.indexOf(start),b=source.indexOf(end,a+start.length);
  assert.ok(a>=0&&b>a, 'production controller function boundary exists');
  return source.slice(a,b);
};
const pendingReplies=[];
let paintCount=0,scrollCount=0,lastHint='',lastHintOptions={};
Object.assign(context,{
  AbortController,
  state:{paper:{sha256:'original'},translations:manifest,paragraphEnabled:true,paragraphSequence:0,
    paragraphFocus:null,paragraphMatches:[],paragraphResolvedKey:null,paragraphCache:new Map(),paragraphRequests:new Map(),
    pages:new Map([['original:1',{source_sha:'original'}],['translation:1',{source_sha:'translated-page-1',translation_run_id:'run-A'}]]),
    disposed:false,selection:{quote:'precise original phrase',rects:exact},draft:'Do not overwrite my note'},
  pid:'paper',pageKey:(side,page)=>`${side}:${page}`,
  paragraphHint:(value,options={})=>{lastHint=value;lastHintOptions=options;},renderParagraphHighlights:()=>{paintCount++;},
  revealParagraphCounterpart:async()=>{scrollCount++;},
  window:{getSelection(){throw new Error('Paragraph controller must not replace native selections');}},
  api:(path,options)=>new Promise((resolve,reject)=>pendingReplies.push({path,options,resolve,reject})),
});
vm.runInContext(`
  const paragraphKey = page => paragraphVersionKey(state.paper.sha256,state.translations,page);
  const paragraphFocusCurrent = (focus=state.paragraphFocus) => !!focus && translationAnchorCurrent(focus,state.paper.sha256,state.translations);
`,context);
vm.runInContext(functionBetween('  function clearParagraphFocus(', '  function invalidateParagraphPage('),context);
vm.runInContext(functionBetween('  async function loadParagraphAlignment(', '  async function revealParagraphCounterpart('),context);
vm.runInContext(functionBetween('  async function refreshParagraphFocus(', '  function focusParagraph('),context);
vm.runInContext(functionBetween('  function focusParagraph(', '  function pageProtected('),context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const originalSelection=context.state.selection;
context.focusParagraph('original',1,{point:{x:.2,y:.15}});
assert.equal(pendingReplies.length,1);
assert.equal(pendingReplies[0].path,'/papers/paper/pages/1/alignment');
context.focusParagraph('original',1,{point:{x:.7,y:.15}});
assert.equal(pendingReplies[0].options.signal.aborted,true,'new focus aborts obsolete alignment work');
assert.equal(pendingReplies.length,2);
pendingReplies[0].resolve({...map,status:'ready',paragraphs});
await settle();
assert.equal(context.state.paragraphMatches.length,0,'late first click cannot overwrite the second click');
pendingReplies[1].resolve({...map,status:'ready',paragraphs});
await settle();
assert.deepEqual(ids(context.state.paragraphMatches),['right-1']);
assert.equal(context.state.selection,originalSelection);
assert.equal(context.state.draft,'Do not overwrite my note');
assert.equal(scrollCount,1);
assert.ok(paintCount>0&&lastHint.includes('已联动'));

context.focusParagraph('translation',1,{point:{x:.2,y:.27}});
await settle();
assert.equal(pendingReplies.length,2,'reverse selection reuses the same immutable page map');
assert.deepEqual(ids(context.state.paragraphMatches),['left-1']);
assert.equal(context.state.selection,originalSelection);

context.clearParagraphFocus();
context.state.paragraphCache.clear();
context.state.translations=pending;
context.focusParagraph('original',1,{point:{x:.2,y:.15}});
pendingReplies[2].resolve({...map,translation_sha:null,status:'pending',reason:'translation_pending',paragraphs:[]});
await settle();
assert.ok(lastHint.includes('完成后'));
assert.equal(context.state.paragraphFocus.source_sha,'original');
assert.equal(context.state.paragraphMatches.length,0);
context.state.translations=manifest;
const resumed=context.refreshParagraphFocus({scroll:true});
assert.equal(pendingReplies.length,4,'a ready-page transition fetches real correspondence');
pendingReplies[3].resolve({...map,status:'partial',paragraphs});
await resumed;
assert.deepEqual(ids(context.state.paragraphMatches),['left-1']);
assert.equal(context.state.draft,'Do not overwrite my note');

context.clearParagraphFocus();
context.state.paragraphCache.clear();
context.focusParagraph('original',1,{point:{x:.2,y:.15}});
context.state.translations=manual;
pendingReplies[4].resolve({...map,status:'ready',paragraphs});
await settle();
assert.equal(context.state.paragraphMatches.length,0,'manual replacement rejects an in-flight old run response');
const manualRequest=context.refreshParagraphFocus();
pendingReplies[5].resolve({...map,translation_sha:'manual-pdf',translation_run_id:null,status:'unavailable',reason:'no_tracking',paragraphs:[]});
await manualRequest;
assert.ok(lastHint.includes('没有段落对应信息'));
assert.equal(lastHintOptions.retranslate,true,'only a missing-tracking result offers explicit retranslation');
assert.equal(context.state.paragraphMatches.length,0);
assert.equal(context.state.selection,originalSelection);

context.state.translations=manifest;
context.state.paragraphCache.clear();
context.focusParagraph('translation',1,{point:{x:.2,y:.27}});
context.state.translations=manual;
await context.refreshParagraphFocus();
assert.equal(context.state.paragraphFocus,null,'stale translation-side focus must be cleared');
assert.equal(context.state.selection,originalSelection,'clearing viewing focus must not clear a note draft');
assert.equal(pendingReplies[6].options.signal.aborted,true);
pendingReplies[6].resolve({...map,status:'ready',paragraphs});
await settle();
assert.equal(context.state.paragraphMatches.length,0);
console.log('PASS: live controller late-response rejection, bidirectional cache reuse, waiting-page resume, no-tracking fallback and exact-note/native-selection isolation');

// The recovery action explicitly starts translation, never silently reruns a
// paid/provider task when a paragraph is merely clicked or selected.
const jobCalls=[];
Object.assign(context,{
  paperId:'paper',
  api:async(path,options)=>{jobCalls.push({path,options});return {id:'translation-job',status:'queued'};},
  refreshJobs:async()=>{},renderSidebar:()=>{},toast:()=>{},
});
context.state.projectId='inbox';
context.state.mode='abstract';
context.state.capabilities={translation_configured:true,ai_configured:true};
vm.runInContext(functionBetween('  async function createJob(', '  async function jumpToNote('),context);
await context.createJob('translate');
assert.equal(jobCalls[0].path,'/jobs');
assert.equal(jobCalls[0].options.body.kind,'translate');
assert.equal(Object.hasOwn(jobCalls[0].options.body,'force'),false,'ordinary translation retains idempotent behavior');
await context.createJob('translate',undefined,true);
assert.equal(jobCalls[1].options.body.force,true,'explicit retranslation creates a fresh completed-result replacement');
await context.createJob('brief',undefined,true);
assert.equal(Object.hasOwn(jobCalls[2].options.body,'force'),false,'force cannot leak into another task type');
const hintElement={textContent:''},retranslateButton={hidden:true};
context.qs=selector=>selector==='#reader-hint'?hintElement:selector==='#paragraph-retranslate'?retranslateButton:null;
vm.runInContext(functionBetween('  function paragraphHint(', '  function paragraphFocusCurrent('),context);
context.paragraphHint('missing tracking',{retranslate:true});
assert.equal(retranslateButton.hidden,false);
context.paragraphHint('normal matched paragraph');
assert.equal(retranslateButton.hidden,true);
assert.equal(hintElement.textContent,'normal matched paragraph');
console.log('PASS: explicit retranslation force request and missing-tracking-only action visibility');
