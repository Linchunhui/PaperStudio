// Exercise the real sheet controller and document-switch contract, without
// claiming browser layout or touch verification from these DOM doubles.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../web/reader.js',import.meta.url),'utf8');
const between=(a,b)=>{const i=source.indexOf(a),j=source.indexOf(b,i+a.length);assert.ok(i>=0&&j>i);return source.slice(i,j);};
const context=vm.createContext({Promise,Number,Set,Map});
vm.runInContext(between('// A page','export async function mountReader').replaceAll('export ',''),context);
let active=null;
function element(parent=null){
  const attributes=new Map(),classes=new Set();
  return {parent,attributes,classes,inert:false,hidden:false,disabled:false,isConnected:true,tabIndex:0,
    focus(){active=this;},getClientRects(){return this.hidden||this.isConnected===false?[]:[{}];},
    closest(){for(let item=this;item;item=item.parent)if(item.hidden||item.inert)return item;return null;},
    contains(target){for(let item=target;item;item=item.parent)if(item===this)return true;return false;},
    setAttribute(key,value){attributes.set(key,value);},removeAttribute(key){attributes.delete(key);},
    classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);}},
  };
}
const header=element(),documentPane=element(),footer=element(),panel=element(),opener=element(header),backdrop=element();
const close=element(panel),setting=element(panel),inactiveTab=element(panel),draft=element(panel),finish=element(panel),hidden=element(panel),disabled=element(panel);
inactiveTab.tabIndex=-1;hidden.hidden=true;disabled.disabled=true;
panel.querySelectorAll=()=>[close,setting,inactiveTab,draft,finish,hidden,disabled];
const sheet=context.createReaderToolSheet({panel,opener,backdrop,background:[header,documentPane,footer],activeElement:()=>active});
assert.equal(panel.inert,false);assert.equal(backdrop.hidden,true);
sheet.setNarrow(true);assert.equal(panel.inert,true);assert.equal(panel.attributes.get('aria-hidden'),'true');
assert.equal(panel.attributes.get('role'),'dialog');assert.equal(opener.attributes.get('aria-expanded'),'false');
opener.focus();sheet.open();assert.equal(active,close);assert.equal(panel.inert,false);assert.equal(panel.attributes.get('aria-modal'),'true');
assert.equal(header.inert,true);assert.equal(documentPane.inert,true);assert.equal(footer.inert,true);assert.equal(backdrop.hidden,false);
const key=(name,shift=false)=>({key:name,shiftKey:shift,prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
finish.focus();const forward=key('Tab');assert.equal(sheet.keydown(forward),true);assert.equal(active,close);assert.equal(forward.prevented,true);
const backward=key('Tab',true);sheet.keydown(backward);assert.equal(active,finish);
setting.focus();const middle=key('Tab');assert.equal(sheet.keydown(middle),false);assert.equal(middle.prevented,false,'native navigation between visible controls remains intact');
sheet.containFocus({target:opener});assert.equal(active,close,'programmatic focus cannot escape an open modal sheet');
draft.value='unsaved note';draft.focus();const escape=key('Escape');sheet.keydown(escape);assert.equal(escape.stopped,true);assert.equal(active,opener);
assert.equal(draft.value,'unsaved note');assert.equal(panel.inert,true);assert.equal(documentPane.inert,false);assert.equal(backdrop.hidden,true);
assert.equal(panel.attributes.has('aria-modal'),false);
// A removed/hidden selection-menu trigger falls back to the durable opener.
const transient=element();transient.focus();sheet.open();transient.isConnected=false;sheet.close();assert.equal(active,opener);
sheet.open();sheet.setNarrow(false);assert.equal(panel.inert,false);assert.equal(panel.attributes.get('role'),'complementary');assert.equal(header.inert,false);
const desktopEscape=key('Escape');assert.equal(sheet.keydown(desktopEscape),false,'desktop sidebar does not trap focus');
draft.focus();sheet.setNarrow(true);assert.equal(active,opener,'shrinking the viewport never strands focus in the closed tools');
sheet.open();sheet.dispose();assert.equal(documentPane.inert,false);assert.equal(panel.inert,false);assert.equal(backdrop.hidden,true);
console.log('PASS: tools sheet inert background, open/close focus, Tab wrap, Escape, removed trigger fallback, breakpoint cleanup and retained drafts');

// A narrow document switch performs display reads only and preserves exact anchors.
let display=[],closed=0,documentFocus=0,posts=0;
const rect={x:.2,y:.3,width:.1,height:.04},selection={source_sha:'sha',page:2,rects:[rect]};
Object.assign(context,{
 state:{narrow:true,narrowSide:'original',translationView:false,readingMode:'pdf',selection},
 setReadingDisplay:async(mode,bilingual)=>{display.push({mode,bilingual});context.state.readingMode=mode;context.state.translationView=bilingual;},
 toolSheet:{close:options=>{assert.equal(options.restoreFocus,false);closed++;}},qs:()=>({focus:()=>documentFocus++}),
 api:()=>{posts++;throw new Error('Display switching cannot run a model');}
});
vm.runInContext(between('  async function setNarrowDocument(', '  async function resizeReader('),context);
await context.setNarrowDocument('translation');assert.equal(context.state.narrowSide,'translation');assert.equal(context.state.translationView,true);
await context.setNarrowDocument('original');assert.equal(context.state.narrowSide,'original');assert.equal(context.state.selection,selection);assert.equal(posts,0);assert.equal(closed,2);assert.equal(documentFocus,2);
context.state.narrow=false;await context.setNarrowDocument('translation');assert.equal(display.length,2,'desktop keeps the existing side-by-side controls');

// Verified citations reveal original content; rejected versions cannot change view.
let reveals=[],jumps=[];
Object.assign(context,{
 state:{paper:{id:'paper',sha256:'sha',page_count:5},projectId:'project',narrow:true,narrowSide:'translation',readingMode:'pdf',translationView:true},
 setNarrowDocument:async side=>reveals.push(side),toast:()=>{},goToPage:async page=>jumps.push(page),pageHost:()=>null,
 renderHighlights:()=>{},paragraphHint:()=>{},persistPosition:async()=>{},
});
vm.runInContext(between('  async function jumpToEvidence(', '  function renderHighlights('),context);
const citation={paper_id:'paper',project_id:'project',source_sha:'sha',page:2,rects:[rect]};
assert.equal(await context.jumpToEvidence({...citation,source_sha:'stale'}),false);assert.equal(reveals.length,0);assert.equal(jumps.length,0);
assert.equal(await context.jumpToEvidence(citation),true);assert.deepEqual(reveals,['original']);assert.deepEqual(jumps,[2]);
assert.equal(JSON.stringify(context.state.evidence.rects),JSON.stringify([rect]));

// The production sidebar renderer exposes the selected tab to assistive tech.
const body=element(),noteCount={},noteTab=element(),aiTab=element();noteTab.dataset={readerTab:'notes'};aiTab.dataset={readerTab:'ai'};
Object.assign(context,{
 state:{tab:'ai',notes:[]},document:{activeElement:null},notesArea:()=>'<p>notes</p>',aiArea:()=>'<p>AI</p>',
 qs:selector=>selector==='#reading-sidebar-body'?body:noteCount,app:{querySelectorAll:()=>[noteTab,aiTab]},
});
vm.runInContext(between('  function renderSidebar(', '  async function refreshNotes('),context);
context.renderSidebar();assert.equal(aiTab.attributes.get('aria-selected'),'true');assert.equal(aiTab.tabIndex,0);assert.equal(noteTab.tabIndex,-1);assert.equal(body.attributes.get('aria-labelledby'),'reader-tab-ai');
console.log('PASS: narrow view switching creates no provider work, keeps exact selections, safely reveals evidence and exposes tab semantics');

// Execute the actual splitter handlers: drag bounds, persistence, keyboard,
// cancellation, position retention, and mobile/browser-resize transitions.
const resize=vm.createContext({Promise,Number,Math,Map,Set});
vm.runInContext(between('// A page','export async function mountReader').replaceAll('export ',''),resize);
const handlers=new Map(),frames=new Map(),stored=new Map(),attrs=new Map(),classes=new Set();
let frameId=0,available=1200,cssWidth=0,capture=null,scroll={scrollTop:0},destinations=[];
const win={innerWidth:1200},handle={
 setAttribute:(k,v)=>attrs.set(k,v),getAttribute:k=>attrs.get(k),
 setPointerCapture:id=>{capture=id;},releasePointerCapture:id=>{if(capture===id)capture=null;}
};
const columns={getBoundingClientRect:()=>({width:available,right:available}),style:{setProperty:(k,v)=>{assert.equal(k,'--reader-sidebar-width');cssWidth=parseFloat(v);}},classList:{add:k=>classes.add(k),remove:k=>classes.delete(k)}};
Object.assign(resize,{
 state:{disposed:false,narrow:false,paper:{},page:3,pageOffset:.4,pairs:new Map([[3,{get offsetHeight(){return 1000+cssWidth;}}]])},window:win,
 qs:id=>({'#reader-resizer':handle,'.continuous-columns':columns,'#document-scroll':scroll}[id]),
 listen:(target,type,handler)=>{handlers.set((target===win?'window:':'')+type,handler);},
 requestAnimationFrame:fn=>{const id=++frameId;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
 localStorage:{setItem:(k,v)=>stored.set(k,v)},readPosition(){},hideSelectionMenu(){},rowTop:()=>500,
 toolSheet:{setNarrow(){}},updateReadingLayout(){},goToPage:async(page,options)=>destinations.push({page,...options})
});
vm.runInContext('let sidebarDrag=null,sidebarRatio=.27,sidebarFrame;',resize);
vm.runInContext(between('  function applySidebarWidth(', '  async function setNarrowDocument('),resize);
vm.runInContext(between('  async function resizeReader(', '  async function setReadingDisplay('),resize);
const flush=()=>{const work=[...frames.values()];frames.clear();for(const fn of work)fn();};
const fire=(type,details={})=>handlers.get(type)({button:0,pointerId:1,preventDefault(){},...details});
resize.setupSidebarResize();assert.equal(cssWidth,324);
fire('pointerdown');assert.equal(capture,1);assert.ok(classes.has('is-resizing'));
fire('pointermove',{clientX:400});flush();assert.equal(cssWidth,780,'sidebar cannot exceed 65%');
assert.equal(scroll.scrollTop,500+.4*1780,'same page-relative position survives reflow');
fire('pointerup');flush();assert.equal(capture,null);assert.ok(!classes.has('is-resizing'));
assert.equal(Number(stored.get('paperstudio.reader.sidebar-ratio.v1')),.65);
fire('keydown',{key:'ArrowRight'});flush();assert.equal(cssWidth,764);
fire('keydown',{key:'Home'});flush();assert.equal(cssWidth,280);
fire('keydown',{key:'End'});flush();assert.equal(cssWidth,780);
fire('dblclick');flush();assert.equal(cssWidth,324);
fire('pointerdown');fire('pointermove',{clientX:1199});fire('pointercancel');flush();assert.equal(cssWidth,280);assert.equal(capture,null);
fire('pointerdown');fire('pointermove',{clientX:500});resize.stopSidebarResize();assert.equal(frames.size,0);assert.equal(capture,null);assert.ok(!classes.has('is-resizing'));
// A viewport change on the same side of the breakpoint still restores position.
available=1100;win.innerWidth=1100;const desktopResize=resize.resizeReader();flush();await desktopResize;
assert.equal(destinations.at(-1).page,3);assert.equal(destinations.at(-1).offset,.4);
available=600;win.innerWidth=600;const mobileResize=resize.resizeReader();flush();await mobileResize;
assert.equal(resize.state.narrow,true);fire('pointerdown');assert.equal(capture,null);
assert.equal(destinations.at(-1).remember,false);
console.log('PASS: sidebar drag/keyboard bounds, width persistence, page position, cancellation, cleanup and mobile breakpoint');
