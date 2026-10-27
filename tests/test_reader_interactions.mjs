// Execute production controllers with native-selection and viewport doubles.
// The tests assert observable side effects (including absence of automatic work).
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../web/reader.js',import.meta.url),'utf8');
const between=(a,b)=>{const i=source.indexOf(a),j=source.indexOf(b,i+a.length);assert.ok(i>=0&&j>i);return source.slice(i,j);};
const context=vm.createContext({Promise,Number,Set,Map});
vm.runInContext('(function(){'+fs.readFileSync(new URL('../web/math.js',import.meta.url),'utf8').replace(/^export /gm,'')+';Object.assign(globalThis,{mathText,mathMarkdown,sourceSelection});})()',context);
vm.runInContext(between('// A page','export async function mountReader').replaceAll('export ',''),context);
const exact={x:.2,y:.3,width:.3,height:.04};
const node={};
let sidebarRenders=0,highlights=0,menuShows=0,providerCalls=0,focusCalls=0,hint='';
const native={isCollapsed:false,rangeCount:1,toString:()=> 'selected original words',getRangeAt:()=>({startContainer:node,endContainer:node,cloneRange:()=>({cloneContents:()=>({textContent:'selected original words',querySelectorAll:()=>[]})}),getClientRects:()=>[{left:120,top:230,width:30,height:4}]})};
const layer={contains:value=>value===node};
const pageElement={dataset:{side:'original',page:'2'},querySelector:()=>layer,getBoundingClientRect:()=>({left:100,top:200,width:100,height:100})};
const originalDraft={side:'original',page:1,quote:'existing unsaved words',source_sha:'sha',rects:[exact]};
Object.assign(context,{
 state:{paper:{id:'paper',sha256:'sha',page_count:5},projectId:'project',pages:new Map([['original:2',{source_sha:'sha'}]]),selection:originalDraft,draft:'keep this draft',tab:'ai',pendingSelection:null},
 window:{getSelection:()=>native},pageKey:(side,page)=>`${side}:${page}`,toast:()=>{},
 renderSidebar:()=>sidebarRenders++,renderHighlights:()=>highlights++,focusParagraph:()=>focusCalls++,paragraphHint:value=>hint=value,
 showSelectionMenu:()=>menuShows++,api:()=>{providerCalls++;throw new Error('Selection must not issue writes');},
});
vm.runInContext(between('  function captureSelection(', '  function captureReflowSelection('),context);
assert.equal(context.captureSelection(pageElement),true);
assert.equal(context.state.selection,originalDraft,'dragging new words does not replace existing note composer');
assert.equal(context.state.draft,'keep this draft');assert.equal(context.state.tab,'ai');
assert.equal(context.state.pendingSelection.quote,'selected original words');
assert.equal(JSON.stringify(context.state.pendingSelection.rects),JSON.stringify([exact]));
assert.equal(sidebarRenders,0);assert.equal(menuShows,0);assert.equal(providerCalls,0);
assert.match(hint,/右键/);

// Reflow selection keeps original PDF coordinates, never screenshot coordinates.
const reflowBlock={id:'block-2',text:'selected original words and context',rects:[{x:.1,y:.2,width:.7,height:.2}]};
context.state.readingPages=new Map([[2,{blocks:[reflowBlock]}]]);
const reflow={contains:()=>true,closest:()=>({dataset:{blockPage:'2',blockId:'block-2'}})};
vm.runInContext(between('  function captureReflowSelection(', '  function hideSelectionMenu('),context);
assert.equal(context.captureReflowSelection(reflow),true);
assert.equal(context.state.pendingSelection.block_id,'block-2');
assert.equal(JSON.stringify(context.state.pendingSelection.rects),JSON.stringify(reflowBlock.rects));
assert.equal(context.state.selection,originalDraft);assert.equal(sidebarRenders,0);

// Only the explicit Note action creates a draft. Summary/explanation are separate.
let chosenActions=[],hidden=0,focused=0,allowDraft=true;
Object.assign(context,{
 hideSelectionMenu:()=>hidden++,isCurrentAnchor:s=>s?.source_sha==='sha',canReplaceDraft:()=>allowDraft,
 qs:()=>({focus:()=>focused++}),openResearch:(kind,selection)=>chosenActions.push({kind,selection}),
 translateExactSelection:async selection=>{providerCalls++;chosenActions.push({kind:'translate',selection});},
});
vm.runInContext(between('  async function selectionAction(', '  function clearArea('),context);
await context.selectionAction('summary');
assert.equal(chosenActions[0].kind,'summary');assert.equal(context.state.selection,originalDraft);assert.equal(sidebarRenders,0);
await context.selectionAction('explain');assert.equal(chosenActions[1].kind,'explain');
await context.selectionAction('translate');assert.equal(providerCalls,1);assert.equal(chosenActions[2].selection.quote,'selected original words');
await context.selectionAction('qa');assert.equal(chosenActions[3].kind,'qa');assert.equal(chosenActions[3].selection,context.state.pendingSelection);
allowDraft=false;await context.selectionAction('note');assert.equal(context.state.selection,originalDraft,'explicit note action respects existing unsaved draft');
allowDraft=true;await context.selectionAction('note');assert.equal(context.state.selection.quote,'selected original words');
assert.equal(context.state.visibility,'private');assert.equal(context.state.tab,'notes');assert.equal(sidebarRenders,1);assert.equal(focused,1);
context.state.pendingSelection={...context.state.pendingSelection,side:'translation'};
await assert.rejects(context.selectionAction('explain'),/原文/);
context.state.pendingSelection={...context.state.pendingSelection,source_sha:'old'};
await assert.rejects(context.selectionAction('note'),/失效/);

// Source/version/project mismatch, bad pages, and invalid rectangles never navigate.
const paper={id:'paper',sha256:'sha',page_count:5};
const citation={paper_id:'paper',project_id:'project',source_sha:'sha',page:2,rects:[exact],quote:'selected original words'};
assert.ok(context.readerEvidenceAnchor(citation,paper,'project'));
for(const changes of [{paper_id:'other'},{project_id:'other'},{source_sha:'old'},{page:0},{page:6},{page:2.5},{side:'translation'},{verified:false},{rects:[{...exact,x:NaN}]},{rects:[{...exact,width:2}]}])assert.equal(context.readerEvidenceAnchor({...citation,...changes},paper,'project'),null);
assert.ok(context.readerEvidenceAnchor({...citation,rects:[]},paper,'project'),'page-only evidence remains honest page-only navigation');
let jumps=[],warnings=[];
Object.assign(context,{
 state:{paper,projectId:'project',readingMode:'reflow',translationView:false},
 toast:value=>warnings.push(value),setReadingDisplay:async(mode)=>{context.state.readingMode=mode;},
 goToPage:async(page)=>jumps.push(page),pageHost:()=>null,qs:()=>({}),persistPosition:async()=>{},
});
vm.runInContext(between('  async function jumpToEvidence(', '  function renderHighlights('),context);
assert.equal(await context.jumpToEvidence({...citation,source_sha:'old'}),false);assert.equal(jumps.length,0);
assert.equal(await context.jumpToEvidence(citation),true);assert.deepEqual(jumps,[2]);assert.equal(context.state.readingMode,'pdf');
assert.equal(JSON.stringify(context.state.evidence.rects),JSON.stringify([exact]));

// Figure/formula regions normalize backwards drags and reject click-sized areas.
assert.equal(JSON.stringify(context.readerAreaRect({x:.7,y:.8},{x:.2,y:.3})),JSON.stringify({x:.2,y:.3,width:.49999999999999994,height:.5}));
assert.equal(context.readerAreaRect({x:.2,y:.3},{x:.201,y:.301}),null);
assert.equal(context.readerAreaRect({x:Infinity,y:.3},{x:.4,y:.5}),null);
const shell=between('  function shell()', '  function updateReadingLayout(');
assert.ok(shell.indexOf('id="translation-view-toggle"')<shell.indexOf('</header>'));
assert.ok(shell.indexOf('id="reading-mode-toggle"')<shell.indexOf('</header>'));
assert.match(shell,/<button id="reading-mode-toggle"[^>]*role="switch"[^>]*aria-checked="false"/);
assert.match(shell,/<button id="translation-view-toggle"[^>]*role="switch"[^>]*aria-checked="false"/);
assert.ok(shell.indexOf('id="reader-zoom"')<shell.indexOf('</header>'));
assert.ok(shell.includes('id="reader-research-panel"'));
assert.ok(shell.indexOf('id="reader-page"')<shell.indexOf('</header>'));
assert.ok(shell.indexOf('id="paragraph-link-toggle"')<shell.indexOf('</header>'));
assert.equal((shell.match(/role="switch"/g)||[]).length,3);
assert.doesNotMatch(shell,/id="reader-project"|id="translation-download"|id="translation-progress"|id="translation-file"|class="reader-settings"|>翻译全文<|>导入译文<|>下载译文<|>原始 PDF ↗</);

assert.ok(source.includes("listen(app,'contextmenu'"));
assert.ok(source.includes("event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')"));
console.log('PASS: selection never opens note/model/menu, explicit actions, private draft preservation, reflow PDF anchors, version-safe evidence jumps, area selection and header controls');

// Existing translated PDF text is shown by page with an explicit alignment label;
// replacing the translation never reuses stale text against new coordinates.
let translationHTML='',translationHidden=false,pageClass=false;
const translatedTarget={set innerHTML(v){translationHTML=v;},get innerHTML(){return translationHTML;},set hidden(v){translationHidden=v;}};
const translatedHost={classList:{toggle:(name,value)=>{pageClass=value;}}};
const existing={page:2,original_sha:'sha',source_sha:'translated-sha',translation_run_id:null,status:'ready',text:'旧译文内容。'};
Object.assign(context,{
 state:{paper,translations:{active_run_id:null},readingPages:new Map([[2,{existing_translation:existing}]])},
 qs:selector=>selector.startsWith('#existing-translation-')?translatedTarget:translatedHost,
 translationPage:()=>({status:'ready',source_sha:'translated-sha'}),esc:value=>String(value).replaceAll('<','&lt;'),
});
vm.runInContext(between('  function renderExistingPageTranslation(', '  async function selectReadingBlock('),context);
context.renderExistingPageTranslation(2);assert.equal(translationHidden,false);assert.equal(pageClass,true);assert.match(translationHTML,/按页对应/);assert.match(translationHTML,/旧译文内容/);
context.translationPage=()=>({status:'ready',source_sha:'replacement'});
context.renderExistingPageTranslation(2);assert.equal(translationHidden,true);assert.equal(pageClass,false);
console.log('PASS: existing PDF translation reflow is page-aligned and rejects replaced versions');

// The reader has exactly three tabs and no persistent region-tool buttons.
assert.equal((shell.match(/role="tab"/g)||[]).length,3);
assert.match(shell,/data-reader-tab="qa"/);
assert.doesNotMatch(shell,/data-region-kind=|解读与总结/);
const regionCalls=[];
Object.assign(context,{
 state:{paper:{sha256:'sha'},projectId:'project',areaMode:'region',areaDraft:{pointerId:8,page:2,source_sha:'sha',rect:exact}},
 areaPointerMove(){},clearArea(){context.state.areaDraft=null;context.state.areaMode=null;},
 renderHighlights(){},paragraphHint(){},openResearch(){regionCalls.push('provider');}
});
vm.runInContext(between('  function areaPointerUp(', '  async function jumpToEvidence('),context);
context.areaPointerUp({pointerId:8,preventDefault(){},stopPropagation(){}});
assert.equal(context.state.pendingSelection.region,true);
assert.equal(context.state.pendingSelection.page,2);
assert.equal(regionCalls.length,0,'Releasing a region only selects; right-click action is required to explain');
console.log('PASS: three reader tabs and explicit post-selection region actions');

let openedSheets=0;const researchEvents=[];
Object.assign(context,{
 state:{projectId:'project',narrow:true,tab:'notes',paper:{sha256:'sha'}},paperId:'paper',
 isCurrentAnchor:()=>true,renderSidebar(){},qs:()=>({hidden:true}),toolSheet:{open(){openedSheets++;}},
 window:{dispatchEvent:event=>researchEvents.push(event)},CustomEvent:class{constructor(name,options){this.type=name;this.detail=options.detail;}}
});
vm.runInContext(between('  function openResearch(', '  async function selectionAction('),context);
context.openResearch('knowledge',null,{autoGenerate:false});
assert.equal(openedSheets,0,'Initial saved-insights read must not open the mobile tools sheet');
assert.equal(researchEvents.at(-1).detail.autoGenerate,false);
context.openResearch('qa',{side:'original',source_sha:'sha',page:2,quote:'Selected question',rects:[exact]});
assert.equal(openedSheets,1);assert.equal(context.state.tab,'qa');
assert.equal(researchEvents.at(-1).detail.selection.quote,'Selected question');
console.log('PASS: initial mobile reading stays clear; explicit selected Q&A opens its tab');

// Display state updates the semantic switch and thumb CSS without provider work.
const switchNodes=new Map(['reading-mode-toggle','translation-view-toggle','paragraph-link-toggle'].map(id=>[id,{attrs:{},setAttribute(key,value){this.attrs[key]=value;}}]));
Object.assign(context,{
 state:{readingMode:'pdf',translationView:false,narrowSide:'original',paragraphEnabled:true},
 qs:selector=>selector==='.continuous-reader'?{classList:{toggle(){}}}:switchNodes.get(selector.slice(1)),
 app:{querySelectorAll(){return [];}},
});
vm.runInContext(between('  function updateReadingLayout(', '  function applySidebarWidth('),context);
context.updateReadingLayout();
assert.equal(switchNodes.get('reading-mode-toggle').attrs['aria-checked'],'false');
assert.equal(switchNodes.get('translation-view-toggle').attrs['aria-checked'],'false');
context.state.readingMode='reflow';context.state.translationView=true;context.state.paragraphEnabled=false;
context.updateReadingLayout();
assert.equal(switchNodes.get('reading-mode-toggle').attrs['aria-checked'],'true');
assert.equal(switchNodes.get('translation-view-toggle').attrs['aria-checked'],'true');
assert.equal(switchNodes.get('paragraph-link-toggle').attrs['aria-checked'],'false');
console.log('PASS: removed project/translation section, title-row controls and semantic sliding switches');
