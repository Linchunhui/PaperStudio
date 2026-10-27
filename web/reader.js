import {api,esc,qs,list,toast,memberships,statuses,jobLabels,jobStatuses,icons,authSession} from './app.js';
import {insightMarkup} from './paper-insights.js';

// A page's translation identity is independent of the other pages in its run.
export function translationAnchorCurrent(anchor, originalSha, manifest) {
  if (!anchor || anchor.is_current === false) return false;
  if (anchor.side === 'original') return anchor.source_sha === originalSha;
  const page = manifest?.pages?.find(p => Number(p.page) === Number(anchor.page));
  if (!page || page.status !== 'ready' || page.source_sha !== anchor.source_sha) return false;
  return (anchor.translation_run_id || null) === (page.translation_run_id || manifest.active_run_id || null);
}
export function clampPage(value, count) {
  return Math.min(count, Math.max(1, Math.floor(Number(value) || 1)));
}
const clampOffset = value => Math.min(1, Math.max(0, Number(value) || 0));

// A cached document is paused, not destroyed. Resume once per cache round trip.
export function createReaderLifecycle(pause, resume) {
  let suspended = false;
  return {
    hide(event) { suspended = Boolean(event.persisted); pause(); },
    show(event) {
      if (!event.persisted || !suspended) return;
      suspended = false;
      resume();
    }
  };
}

// Paragraph focus is a viewing aid, independent from the exact note selection.
export function paragraphVersionKey(originalSha, manifest, page) {
  const translated = manifest?.pages?.find(p => Number(p.page) === Number(page));
  return JSON.stringify([originalSha, Number(page), manifest?.mode || 'none',
    manifest?.active_run_id || null, translated?.status || 'none', translated?.source_sha || null]);
}
export function alignmentMatchesVersion(alignment, originalSha, manifest, page) {
  const translated = manifest?.pages?.find(p => Number(p.page) === Number(page));
  return !!alignment && Number(alignment.page) === Number(page) && alignment.original_sha === originalSha &&
    (alignment.translation_sha || null) === (translated?.status === 'ready' ? translated.source_sha || null : null) &&
    (alignment.translation_run_id || null) === (manifest?.active_run_id || null);
}
export function validParagraphRects(rects) {
  return (Array.isArray(rects) ? rects : []).filter(r => r &&
    [r.x,r.y,r.width,r.height].every(Number.isFinite) && r.x >= 0 && r.y >= 0 &&
    r.width > 0 && r.height > 0 && r.x + r.width <= 1.000001 && r.y + r.height <= 1.000001);
}
export function matchingParagraphs(paragraphs, side, probe) {
  if (!['original','translation'].includes(side) || !probe) return [];
  const selected = validParagraphRects(probe.rects);
  const point = probe.point;
  const atPoint = point && Number.isFinite(point.x) && Number.isFinite(point.y);
  return (Array.isArray(paragraphs) ? paragraphs : []).filter(p => {
    if (!p?.id || !validParagraphRects(p.original?.rects).length || !validParagraphRects(p.translation?.rects).length) return false;
    return validParagraphRects(p[side]?.rects).some(r => selected.length
      ? selected.some(s => Math.min(r.x+r.width,s.x+s.width)-Math.max(r.x,s.x) > 0.000001 &&
          Math.min(r.y+r.height,s.y+s.height)-Math.max(r.y,s.y) > 0.000001)
      : atPoint && point.x >= r.x && point.x <= r.x+r.width && point.y >= r.y && point.y <= r.y+r.height);
  });
}
export function paragraphScrollDelta(start, end, viewportStart, viewportEnd, margin=24) {
  if (![start,end,viewportStart,viewportEnd].every(Number.isFinite) || end <= start || viewportEnd <= viewportStart) return 0;
  const inset = Math.min(margin, (viewportEnd-viewportStart)/4);
  const low = viewportStart+inset, high = viewportEnd-inset;
  if (start >= low && end <= high) return 0;
  // An oversized paragraph which already spans the viewport should stay put.
  if (start <= low && end >= high) return 0;
  if (start < low) return start-low;
  return end-start > high-low ? start-low : end-high;
}

// Reading layout is independent from translation work. A display toggle never
// authorizes a provider request or broadens a precise PDF note selection.
export function readingBlockAt(blocks, probe) {
  const selected=validParagraphRects(probe?.rects),point=probe?.point;
  return (Array.isArray(blocks)?blocks:[]).filter(block=>block?.id&&typeof block.text==='string').find(block=>
    validParagraphRects(block.rects).some(r=>selected.length
      ? selected.some(s=>Math.min(r.x+r.width,s.x+s.width)>Math.max(r.x,s.x)&&Math.min(r.y+r.height,s.y+s.height)>Math.max(r.y,s.y))
      : point&&Number.isFinite(point.x)&&Number.isFinite(point.y)&&point.x>=r.x&&point.x<=r.x+r.width&&point.y>=r.y&&point.y<=r.y+r.height))||null;
}
export function readingJobMatches(job,sourceSha,page,blockId,projectId) {
  return !!job&&job.kind==='paragraph-translate'&&job.source_sha===sourceSha&&Number(job.page)===Number(page)&&job.block_id===blockId&&job.project_id===projectId;
}
export function readingDisplay(mode,bilingual) {
  return {mode:mode==='reflow'?'reflow':'pdf',bilingual:bilingual===true};
}

export function readerSidebarWidth(value, available) {
  const maximum=Math.max(280,Math.min(available*.65,available-369));
  return Math.round(Math.max(280,Math.min(maximum,Number(value)||350)));
}

// Only immutable, in-scope original-paper evidence may navigate this reader.
// Invalid coordinates are rejected instead of being silently clamped to a page.
export function readerEvidenceAnchor(citation, paper, projectId) {
  if (!citation || !paper || citation.paper_id !== paper.id ||
      citation.project_id !== projectId || citation.source_sha !== paper.sha256 ||
      citation.verified === false || citation.side === 'translation') return null;
  const page = Number(citation.page);
  if (!Number.isInteger(page) || page < 1 || page > paper.page_count) return null;
  const sourceRects = citation.rects ?? [];
  if (!Array.isArray(sourceRects) || sourceRects.length > 1000) return null;
  const rects = validParagraphRects(sourceRects);
  if (rects.length !== sourceRects.length) return null;
  return {side:'original', page, source_sha:paper.sha256, rects, block_id:citation.block_id || null};
}
export function readerAreaRect(start, end) {
  if (![start?.x,start?.y,end?.x,end?.y].every(Number.isFinite)) return null;
  const x=Math.max(0,Math.min(start.x,end.x)), y=Math.max(0,Math.min(start.y,end.y));
  const right=Math.min(1,Math.max(start.x,end.x)), bottom=Math.min(1,Math.max(start.y,end.y));
  const rect={x,y,width:right-x,height:bottom-y};
  return validParagraphRects([rect]).length && rect.width>=.005 && rect.height>=.005 ? rect : null;
}

// The narrow-screen tools are a modal sheet; desktop keeps a normal sidebar.
// This controller owns focus and inert state only, never PDF page geometry.
export function createReaderToolSheet({panel,opener,backdrop,background,activeElement=()=>document.activeElement}) {
  let narrow=false,opened=false,returnFocus=null;
  const visible=element=>element&&element.isConnected!==false&&!element.disabled&&!element.closest?.('[hidden],[inert]')&&element.getClientRects().length>0;
  const focusables=()=>[...panel.querySelectorAll('button,a[href],input,select,textarea,summary,[tabindex]')].filter(element=>element.tabIndex>=0&&visible(element));
  function paint(){
    panel.inert=narrow&&!opened;
    for(const element of background)if(element)element.inert=narrow&&opened;
    panel.classList.toggle('is-sheet-open',narrow&&opened);
    panel.setAttribute('role',narrow?'dialog':'complementary');
    if(narrow&&opened)panel.setAttribute('aria-modal','true');else panel.removeAttribute('aria-modal');
    if(narrow&&!opened)panel.setAttribute('aria-hidden','true');else panel.removeAttribute('aria-hidden');
    opener.setAttribute('aria-expanded',String(narrow&&opened));
    backdrop.hidden=!(narrow&&opened);
  }
  function close({restoreFocus=true}={}){
    const wasOpen=opened;opened=false;paint();
    if(wasOpen&&restoreFocus)(visible(returnFocus)?returnFocus:opener).focus({preventScroll:true});
    returnFocus=null;
  }
  paint();
  return {
    setNarrow(value){
      const next=Boolean(value);if(next===narrow)return;
      const focusedInside=panel.contains(activeElement());
      narrow=next;opened=false;returnFocus=null;paint();
      if(narrow&&focusedInside)opener.focus({preventScroll:true});
      if(!narrow&&focusedInside&&!visible(activeElement()))(focusables()[0]||panel).focus({preventScroll:true});
    },
    open(){
      if(!narrow)return;
      if(!opened)returnFocus=activeElement();
      opened=true;paint();(focusables()[0]||panel).focus({preventScroll:true});
    },
    close,
    keydown(event){
      if(!narrow||!opened)return false;
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return true;}
      if(event.key!=='Tab')return false;
      const elements=focusables(),first=elements[0],last=elements.at(-1),focused=activeElement();
      if(!elements.length){event.preventDefault();panel.focus();return true;}
      if(!panel.contains(focused)||(event.shiftKey&&focused===first)||(!event.shiftKey&&focused===last)){
        event.preventDefault();(event.shiftKey?last:first).focus();return true;
      }
      return false;
    },
    containFocus(event){if(narrow&&opened&&!panel.contains(event.target))(focusables()[0]||panel).focus({preventScroll:true});},
    dispose(){opened=false;narrow=false;returnFocus=null;paint();},
  };
}

export async function mountReader({paperId,projectId}) {
  const app = qs('#app');
  const pid = encodeURIComponent(paperId);
  const state = {
    paper:null, projects:[], projectId:projectId || '', page:1, pageOffset:0, zoom:100,
    notes:[], jobs:[], capabilities:{}, tab:'ai', panelTab:null, interpretationKind:'knowledge', insights:null, insightsError:'', insightsMarkup:null, selection:null, pendingSelection:null, draft:'', color:'yellow', visibility:'private',
    readingPanelOpen:false, selectionTranslation:null, contextAnchor:null, evidence:null, areaMode:null, areaDraft:null, noteVersions:new Map(),
    narrow:false,narrowSide:'original',
    editId:null, selectedJob:null, mode:'abstract', saving:false, disposed:false,
    manifest:[], translations:{active_run_id:null,mode:'none',status:'idle',pages:[]},
    pages:new Map(), requests:new Map(), near:new Set(), pairs:new Map(), pollTimer:null,
    paragraphEnabled:true, paragraphFocus:null, paragraphMatches:[], paragraphResolvedKey:null,
    paragraphCache:new Map(), paragraphRequests:new Map(), paragraphSequence:0,
    readingMode:'pdf',translationView:false,readingPages:new Map(),readingRequests:new Map(),
    readingFocus:null,readingSequence:0,readingBusy:new Set(),readingBatch:false,readingPollCursor:0
  };
  let observer, scrollFrame, saveTimer, toolSheet, sidebarDrag=null, sidebarRatio=.27, sidebarFrame, positionQueue = Promise.resolve(true), refreshing = false, refreshSequence = 0;
  try{const saved=Number(localStorage.getItem('paperstudio.reader.sidebar-ratio.v1'));if(saved>0&&saved<=.65)sidebarRatio=saved;}catch{}
  const finishedProjects = new Set(), listeners=[];
  const isAdmin=()=>authSession?.user?.role==='admin'||authSession?.user?.is_admin===true;
  const listen=(target,type,handler,options)=>{target.addEventListener(type,handler,options);listeners.push(()=>target.removeEventListener(type,handler,options));};
  const removeListeners=()=>{while(listeners.length)listeners.pop()();};

  const projectPath = id => {
    const names=[], seen=new Set(); let p=state.projects.find(x=>x.id===id);
    while(p && !seen.has(p.id)){seen.add(p.id);names.unshift(p.name);p=state.projects.find(x=>x.id===p.parent_id);}
    return names.join(' / ');
  };
  const membership = () => memberships(state.paper || {}).find(m=>m.project_id===state.projectId) || {};
  const notesUrl = () => `/papers/${pid}/annotations?project_id=${encodeURIComponent(state.projectId)}`;
  const pageKey = (side, page) => `${side}:${page}`;
  const pageHost = (side, page) => qs(`#page-${side}-${page}`);
  const translationPage = page => state.translations.pages?.find(p=>Number(p.page)===Number(page));
  const isCurrentAnchor = anchor => translationAnchorCurrent(anchor,state.paper?.sha256,state.translations);
  function hasUnsavedDraft() {
    if(state.saving)return true;
    if(!state.selection)return false;
    if(!state.editId)return true;
    const saved=state.notes.find(n=>n.id===state.editId);
    return !saved || state.draft!==(saved.note||'') || state.color!==(saved.color||'yellow') || state.visibility!==(saved.visibility||'private');
  }
  function canReplaceDraft({focus=true}={}) {
    if(!hasUnsavedDraft())return true;
    toast(state.saving?'正在保存批注，请稍候。':'请先保存或取消右侧正在编辑的批注，原稿已保留。');
    state.tab='notes'; renderSidebar(); if(focus)qs('#note-draft')?.focus(); return false;
  }
  function shell() {
    document.title=`${state.paper.title} · PaperStudio`;
    app.innerHTML=`<div class="reader-app continuous-reader reading-pdf translation-off">
      <header class="reader-header"><a class="reader-back" href="/?view=library&project=${encodeURIComponent(state.projectId)}" title="返回论文库" aria-label="返回论文库"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6M8 12h12"/></svg></a><div class="reader-heading"><h1>${esc(state.paper.title)}</h1><span>${esc(projectPath(state.projectId)||'当前项目')}</span></div><span id="reading-status" class="pill">${statuses[membership().status]||'待读'}</span><button id="reader-tools-open" class="button reader-tools-open" data-reader-action="open-tools" aria-controls="reader-tools-panel" aria-expanded="false">阅读工具</button></header>
      <div class="reader-columns continuous-columns"><section class="document-pane" aria-label="论文连续阅读"><div id="document-scroll" class="document-scroll" tabindex="0" aria-label="连续 PDF 页面滚动区"><div id="document-pages" class="document-pages"></div></div></section>
      <div id="reader-resizer" class="reader-resizer" role="separator" aria-orientation="vertical" aria-label="调整阅读区与侧栏宽度，左右方向键微调，双击复位" aria-controls="reader-tools-panel" tabindex="0"></div><div class="reader-tools-backdrop" id="reader-tools-backdrop" hidden aria-hidden="true"></div><aside class="reading-sidebar" id="reader-tools-panel" tabindex="-1" aria-label="阅读设置、问答与笔记"><div class="reader-tools-heading"><h2>阅读工具</h2><button class="icon-button" data-reader-action="close-tools" aria-label="关闭阅读工具"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>
        <details class="reader-settings"><summary>阅读设置 <span>模式、翻译与页面</span></summary><div class="reader-toolbar">
          <div class="reader-display-buttons" role="group" aria-label="阅读显示开关"><button id="reading-mode-toggle" class="button reader-toggle" data-reader-action="toggle-reading" aria-pressed="false"><span>重排阅读</span><strong>已关闭</strong></button><button id="translation-view-toggle" class="button reader-toggle" data-reader-action="toggle-translation" aria-pressed="false"><span>对照翻译</span><strong>已关闭</strong></button></div>
          <div class="reader-narrow-document-controls pdf-only bilingual-only" role="group" aria-label="阅读文档"><button class="button" data-reader-side="original" aria-pressed="true">原文</button><button class="button" data-reader-side="translation" aria-pressed="false">译文</button></div>
          <div class="reader-page-controls"><button class="icon-button" data-reader-action="prev" aria-label="跳到上一页"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg></button><label>第 <input id="reader-page" aria-label="定位页码" type="number" min="1" max="${state.paper.page_count}" value="${state.page}"> / ${state.paper.page_count} 页</label><button class="icon-button" data-reader-action="next" aria-label="跳到下一页"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg></button></div>
          <div class="reader-zoom-controls"><button class="icon-button" data-reader-action="zoom-out" aria-label="缩小页面"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button><select id="reader-zoom" aria-label="页面缩放">${[75,100,125,150,175,200].map(z=>`<option value="${z}" ${z===state.zoom?'selected':''}>${z}%</option>`).join('')}</select><button class="icon-button" data-reader-action="zoom-in" aria-label="放大页面"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg></button><span class="continuous-mode-label">连续滚动</span></div>
          <button id="paragraph-link-toggle" class="button reader-toggle paragraph-link-toggle pdf-bilingual-only" data-reader-action="toggle-link" aria-pressed="true"><span>段落联动</span><strong>已开启</strong></button>
          <div class="reader-context"><label>当前项目<select id="reader-project" aria-label="当前阅读项目">${memberships(state.paper).map(m=>`<option value="${esc(m.project_id)}" ${m.project_id===state.projectId?'selected':''}>${esc(projectPath(m.project_id)||m.project_name||m.project_id)}</option>`).join('')}</select></label></div>
          <div class="reader-settings-actions"><button class="button small" data-reader-action="translate">翻译全文</button><button class="button small" data-reader-action="upload-translation">导入译文</button><button class="button small reflow-only" data-reader-action="translate-reading-page">翻译本页段落</button><button id="paragraph-retranslate" class="button small" data-reader-action="retranslate" hidden>重新翻译全文</button><a id="translation-download" class="button small" href="/api/papers/${pid}/file?side=translation" download hidden>下载译文</a><a class="button small" href="/api/papers/${pid}/file?side=original" target="_blank" rel="noopener">原始 PDF ↗</a></div><div class="reader-settings-status"><span id="translation-progress"></span><span id="reading-batch-status" role="status"></span></div>
        </div></details>
        <div class="reading-tabs" role="tablist" aria-label="阅读助手"><button id="reader-tab-ai" role="tab" aria-controls="reading-sidebar-body" aria-selected="true" tabindex="0" data-reader-tab="ai" class="active">解读</button><button id="reader-tab-qa" role="tab" aria-controls="reading-sidebar-body" aria-selected="false" tabindex="-1" data-reader-tab="qa">问答</button><button id="reader-tab-notes" role="tab" aria-controls="reading-sidebar-body" aria-selected="false" tabindex="-1" data-reader-tab="notes">笔记 <span id="note-count">${state.notes.length}</span></button></div>
        <div class="reading-sidebar-body" id="reading-sidebar-body" role="tabpanel" aria-labelledby="reader-tab-ai" tabindex="0"><div id="paragraph-translation-panel" class="paragraph-translation-panel" hidden aria-live="polite"></div><div id="reader-dialogue"><div id="reader-interpretation"><div id="reader-insights"></div><div class="reader-interpretation-actions"><button class="button small" data-research-view="knowledge">结构化解读</button><button class="button small" data-research-view="lineage">研究脉络</button></div></div><div id="reader-research-panel" class="reader-research-panel" hidden></div><div id="reader-archive"></div></div><div id="reader-notes" hidden></div></div><button class="reader-finish" data-reader-action="finish">读完，整理笔记</button>
      </aside></div><div id="selection-context-menu" class="selection-context-menu" role="menu" aria-label="选段操作" hidden><button role="menuitem" data-selection-action="translate">翻译选区</button><button role="menuitem" data-selection-action="qa">针对选区提问</button><button role="menuitem" data-selection-action="summary">总结</button><button role="menuitem" data-selection-action="explain">解释</button><button role="menuitem" data-selection-action="note">笔记</button><button role="menuitem" data-selection-action="figure">图表讲解</button><button role="menuitem" data-selection-action="formula">公式讲解</button><button role="menuitem" data-selection-action="region">选择图表 / 公式区域</button></div>
      <footer class="reader-footer"><span id="reader-hint" role="status" aria-live="polite">拖选句子或段落后右键，选择翻译、问答、总结、解释或笔记。也可按 Shift+F10。</span><span>默认不翻译 · 笔记默认私有</span></footer><input id="translation-file" type="file" accept="application/pdf,.pdf" hidden></div>`;
    qs('#document-pages').innerHTML=state.manifest.map(p=>`<section class="page-pair" data-pair-page="${p.page}" aria-label="第 ${p.page} 页"><article class="page-slot"><div class="pair-page-label">原文 · ${p.page}</div><div id="page-original-${p.page}" class="pdf-frame" style="aspect-ratio:${p.width}/${p.height}"></div></article><article class="page-slot"><div class="pair-page-label">译文 · ${p.page}</div><div id="page-translation-${p.page}" class="pdf-frame" style="aspect-ratio:${p.width}/${p.height}"></div></article><article id="reading-page-${p.page}" class="reflow-page reflow-only" data-reading-page="${p.page}"><div class="reflow-page-heading">第 ${p.page} 页</div><p class="muted">正在重排原文…</p></article></section>`).join('');
    for(const element of app.querySelectorAll('.page-pair'))state.pairs.set(Number(element.dataset.pairPage),element);
    const scroll=qs('#document-scroll'); scroll.addEventListener('scroll',handleScroll,{passive:true});
    observer=new IntersectionObserver(entries=>{
      for(const entry of entries){const page=Number(entry.target.dataset.pairPage);if(entry.isIntersecting){state.near.add(page);void loadPair(page);}else{state.near.delete(page);unloadPair(page);}}
    },{root:scroll,rootMargin:'1000px 0px',threshold:0});
    for(const element of state.pairs.values())observer.observe(element);
    toolSheet=createReaderToolSheet({panel:qs('#reader-tools-panel'),opener:qs('#reader-tools-open'),backdrop:qs('#reader-tools-backdrop'),background:[qs('.reader-header'),qs('.document-pane'),qs('.reader-footer')]});
    state.narrow=window.innerWidth<=900;toolSheet.setNarrow(state.narrow);setupSidebarResize();
    renderSidebar(); updateToolbar(); updateTranslationProgress(); updateReadingLayout();
    if(state.paper.can_edit===false)for(const button of app.querySelectorAll('[data-reader-action=translate],[data-reader-action=upload-translation],[data-reader-action=retranslate]')){button.disabled=true;button.title='当前论文为只读权限';}
  }
  function updateReadingLayout() {
    const root=qs('.continuous-reader');if(!root)return;
    root.classList.toggle('reading-reflow',state.readingMode==='reflow');
    root.classList.toggle('reading-pdf',state.readingMode==='pdf');
    root.classList.toggle('translation-off',!state.translationView);
    root.classList.toggle('translation-on',state.translationView);
    root.classList.toggle('narrow-translation',state.narrowSide==='translation'&&state.translationView);
    for(const button of app.querySelectorAll('[data-reader-side]'))button.setAttribute('aria-pressed',String(button.dataset.readerSide===(state.translationView?state.narrowSide:'original')));
    for(const button of app.querySelectorAll('[data-reader-mode]')){
      const active=button.dataset.readerMode===state.readingMode;
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    }
    for(const [id,on] of [['reading-mode-toggle',state.readingMode==='reflow'],['translation-view-toggle',state.translationView],['paragraph-link-toggle',state.paragraphEnabled]]){
      const button=qs('#'+id);if(button){button.setAttribute('aria-pressed',String(on));const status=button.querySelector('strong');if(status)status.textContent=on?'已开启':'已关闭';}
    }
    const label=qs('.continuous-mode-label');if(label)label.textContent=state.readingMode==='reflow'?'连续滚动 · 文字重排':state.translationView?'连续滚动 · 左右对照':'连续滚动 · 原始 PDF';
    const retry=qs('#paragraph-retranslate');if(retry&&(state.readingMode!=='pdf'||!state.translationView))retry.hidden=true;
  }
  function applySidebarWidth(value) {
    const columns=qs('.continuous-columns'),handle=qs('#reader-resizer');if(!columns||!handle)return;
    const available=columns.getBoundingClientRect().width;
    if(!available)return;
    const width=readerSidebarWidth(value??available*sidebarRatio,available);
    columns.style.setProperty('--reader-sidebar-width',width+'px');
    handle.setAttribute('aria-valuemin','280');handle.setAttribute('aria-valuemax',String(readerSidebarWidth(Infinity,available)));
    handle.setAttribute('aria-valuenow',String(width));handle.setAttribute('aria-valuetext',`侧栏占 ${Math.round(width/available*100)}%`);
    return width/available;
  }
  function stopSidebarResize() {
    const drag=sidebarDrag;sidebarDrag=null;
    cancelAnimationFrame(sidebarFrame);sidebarFrame=null;
    qs('.continuous-columns')?.classList.remove('is-resizing');
    if(drag)try{qs('#reader-resizer')?.releasePointerCapture(drag.pointerId);}catch{}
  }
  function setupSidebarResize() {
    const handle=qs('#reader-resizer'),columns=qs('.continuous-columns');
    const keepPosition=anchor=>{cancelAnimationFrame(sidebarFrame);sidebarFrame=requestAnimationFrame(()=>{
      if(state.disposed)return;const scroll=qs('#document-scroll'),row=state.pairs.get(anchor.page);
      if(scroll&&row){scroll.scrollTop=rowTop(anchor.page)+anchor.offset*row.offsetHeight;state.page=anchor.page;state.pageOffset=anchor.offset;}
    });};
    const save=()=>{try{localStorage.setItem('paperstudio.reader.sidebar-ratio.v1',String(sidebarRatio));}catch{}};
    const finish=event=>{if(!sidebarDrag||event.pointerId!==sidebarDrag.pointerId)return;
      const anchor=sidebarDrag;sidebarDrag=null;columns.classList.remove('is-resizing');
      try{handle.releasePointerCapture(event.pointerId);}catch{}keepPosition(anchor);save();
    };
    listen(handle,'pointerdown',event=>{if(event.button!==0||state.narrow)return;event.preventDefault();readPosition();hideSelectionMenu();
      sidebarDrag={pointerId:event.pointerId,page:state.page,offset:state.pageOffset};handle.setPointerCapture(event.pointerId);columns.classList.add('is-resizing');});
    listen(handle,'pointermove',event=>{if(!sidebarDrag||event.pointerId!==sidebarDrag.pointerId)return;event.preventDefault();
      sidebarRatio=applySidebarWidth(columns.getBoundingClientRect().right-event.clientX)||sidebarRatio;keepPosition(sidebarDrag);});
    listen(handle,'pointerup',finish);listen(handle,'pointercancel',finish);
    listen(handle,'lostpointercapture',event=>{if(sidebarDrag)finish(event);});
    listen(window,'blur',()=>{if(sidebarDrag)finish({pointerId:sidebarDrag.pointerId});});
    listen(handle,'keydown',event=>{if(state.narrow||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();readPosition();
      const anchor={page:state.page,offset:state.pageOffset},current=Number(handle.getAttribute('aria-valuenow'));
      const width=event.key==='Home'?280:event.key==='End'?Infinity:current+(event.key==='ArrowLeft'?1:-1)*(event.shiftKey?40:16);
      sidebarRatio=applySidebarWidth(width)||sidebarRatio;keepPosition(anchor);save();});
    listen(handle,'dblclick',()=>{readPosition();const anchor={page:state.page,offset:state.pageOffset};sidebarRatio=.27;applySidebarWidth();keepPosition(anchor);save();});
    applySidebarWidth();
  }
  async function setNarrowDocument(side) {
    if(!state.narrow)return;
    state.narrowSide=side==='translation'?'translation':'original';
    await setReadingDisplay('pdf',state.narrowSide==='translation'||state.translationView);
    toolSheet?.close({restoreFocus:false});qs('#document-scroll')?.focus({preventScroll:true});
  }
  async function resizeReader() {
    hideSelectionMenu();applySidebarWidth();const narrow=window.innerWidth<=900;
    if(state.disposed||!state.paper)return;
    // Resize CSS has already changed row heights; keep the last known reading
    // position rather than interpreting the old scrollTop in the new layout.
    const page=state.page,offset=state.pageOffset;
    if(narrow!==state.narrow){stopSidebarResize();state.narrow=narrow;toolSheet?.setNarrow(narrow);updateReadingLayout();}
    await new Promise(resolve=>requestAnimationFrame(resolve));
    if(!state.disposed)await goToPage(page,{offset,remember:false});
  }
  async function setReadingDisplay(mode,bilingual) {
    readPosition();const page=state.page,offset=state.pageOffset;
    const next=readingDisplay(mode,bilingual);
    state.readingMode=next.mode;state.translationView=next.bilingual;
    if(!next.bilingual)state.narrowSide='original';
    clearParagraphFocus('单击原文段落后可在右侧翻译；切换显示模式不会自动调用翻译。');
    for(const row of state.pairs.values())row.style.minHeight='';
    if(!state.translationView||state.readingMode!=='pdf'){
      for(const [key,controller] of state.requests)if(key.startsWith('translation:')){controller.abort();state.requests.delete(key);}
    }
    updateReadingLayout();renderReadingPanel();
    await new Promise(resolve=>requestAnimationFrame(resolve));
    await goToPage(page,{offset,remember:false});
    await Promise.all([...state.near].map(loadPair));
  }
  async function loadReadingPage(page,{force=false}={}) {
    const cached=state.readingPages.get(page);
    if(!force&&cached?.source_sha===state.paper.sha256)return cached;
    if(state.readingRequests.has(page))return state.readingRequests.get(page).pending;
    const sha=state.paper.sha256,project=state.projectId,controller=new AbortController();
    const pending=(async()=>{
      try{
        const data=await api(`/papers/${pid}/pages/${page}/reading?project_id=${encodeURIComponent(project)}`,{signal:controller.signal});
        if(state.disposed||controller.signal.aborted||state.paper.sha256!==sha||state.projectId!==project)return null;
        if(data.source_sha!==sha||Number(data.page)!==Number(page)||!Array.isArray(data.blocks))throw new Error('原文版本已变化，请重新打开论文。');
        state.readingPages.set(page,data);
        while(state.readingPages.size>24){
          const candidate=[...state.readingPages.keys()].find(p=>!state.near.has(p)&&p!==state.readingFocus?.page&&p!==page);
          if(candidate===undefined)break;state.readingPages.delete(candidate);
        }
        return data;
      }finally{if(state.readingRequests.get(page)===controller)state.readingRequests.delete(page);}
    })();controller.pending=pending;state.readingRequests.set(page,controller);return pending;
  }
  function readingTranslation(job) {
    if(!job)return '<span class="muted">尚未翻译。可按需翻译此段或本页。</span>';
    if(job.status==='succeeded')return `<div class="paragraph-result" lang="zh-CN">${esc(job.result_markdown||'译文为空，请重试。')}</div>`;
    if(['queued','running'].includes(job.status))return `<span class="paragraph-working">${job.status==='queued'?'等待翻译…':'正在翻译此段…'}</span>`;
    return `<span class="paragraph-error">${esc(job.error||'此段翻译未完成，可重新提交。')}</span>`;
  }
  function blockTranslation(block,page) {
    return readingJobMatches(block?.translation,state.paper.sha256,page,block?.id,state.projectId)?block.translation:null;
  }
  function readingBlockMarkup(block,page) {
    const job=blockTranslation(block,page),active=state.readingFocus?.page===page&&state.readingFocus?.block_id===block.id;
    return `<section class="reflow-block ${block.type==='heading'?'reflow-heading':''} ${active?'selected':''}" data-block-id="${esc(block.id)}" data-block-page="${page}"><div class="reflow-source" lang="en" tabindex="0" role="button" aria-label="选中段落，在右侧按需翻译">${esc(block.text)}</div><div class="reflow-block-actions"><button class="text-button" data-reading-block="${esc(block.id)}" data-reading-page="${page}">选中段落</button><button class="text-button" data-reading-pdf="${page}">原 PDF ↗</button><button class="text-button bilingual-only" data-reading-translate="${esc(block.id)}" data-reading-page="${page}">${job?.status==='succeeded'?'重新查看译文':'翻译此段'}</button></div><div class="reflow-translation bilingual-only ${job?'':'no-block-translation'}" data-translation-key="${esc(job?.id||'')}:${esc(job?.status||'')}">${readingTranslation(job)}</div></section>`;
  }
  function readingContentMarkup(data,page) {
    if(!Array.isArray(data.content))return data.blocks.map(block=>readingBlockMarkup(block,page)).join('');
    return data.content.map(item=>{
      if(item.type==='text'){const block=data.blocks.find(b=>b.id===item.id);return block?readingBlockMarkup(block,page):'';}
      if(!item.image_url?.startsWith(`/api/papers/${pid}/pages/`))return '';
      const label={table:'表格',formula:'公式',figure:'图表',page:'原页内容'}[item.type]||'原页内容';
      return `<figure class="reflow-visual" data-visual-id="${esc(item.id)}" data-visual-page="${page}"><a href="${esc(item.image_url.replace('width=2000','width=4000'))}" target="_blank" rel="noopener" aria-label="放大第 ${page} 页${label}"><img src="${esc(item.image_url)}" width="${Number(item.width)}" height="${Number(item.height)}" alt="第 ${page} 页${label}，保留原始内容" loading="lazy" draggable="false"></a><figcaption><span>${label} · 点击放大，右键讲解</span><button class="button small" data-reading-pdf="${page}">定位原页</button></figcaption></figure>`;
    }).join('');
  }
  async function renderReadingPage(page) {
    const host=qs(`#reading-page-${page}`);if(!host||state.disposed)return;
    try{
      const data=await loadReadingPage(page);if(!data||state.disposed)return;
      if(host.dataset.identity===data.source_sha){updateReadingResults(page);return;}
      const row=state.pairs.get(page),oldHeight=row?.getBoundingClientRect().height||0;
      host.innerHTML=`<div class="reflow-page-heading"><span>第 ${page} 页</span><button class="button small" data-reading-pdf="${page}">查看原 PDF ↗</button></div>${readingContentMarkup(data,page)}${data.page_image_url?.startsWith('/api/')?`<details class="reflow-original-page"><summary>核对本页完整版式</summary><a href="${esc(data.page_image_url)}" target="_blank" rel="noopener"><img src="${esc(data.page_image_url)}" alt="第 ${page} 页完整原始版式" loading="lazy"></a></details>`:''}<section id="existing-translation-${page}" class="existing-page-translation bilingual-only" hidden></section>`;
      host.dataset.identity=data.source_sha;renderExistingPageTranslation(page);
      if(state.readingMode==='reflow'&&page<state.page&&row)qs('#document-scroll').scrollTop+=row.getBoundingClientRect().height-oldHeight;
      host.querySelectorAll('.reflow-source').forEach(element=>{
        const choose=()=>{const section=element.closest('[data-block-id]');void selectReadingBlock(page,{blockId:section.dataset.blockId});};
        element.addEventListener('click',()=>{const selected=window.getSelection();if(!selected||selected.isCollapsed)choose();});
        element.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});
      });
    }catch(error){if(state.disposed||error.name==='AbortError')return;host.innerHTML=`<div class="reflow-empty">${esc(error.message)} <button class="text-button" data-reading-reload="${page}">重新加载</button></div>`;}
  }
  function updateReadingResults(page) {
    const data=state.readingPages.get(page),host=qs(`#reading-page-${page}`);if(!data||!host)return;
    for(const element of host.querySelectorAll('[data-block-id]')){
      const block=data.blocks.find(b=>b.id===element.dataset.blockId);if(!block)continue;
      const job=blockTranslation(block,page),target=element.querySelector('.reflow-translation'),key=`${job?.id||''}:${job?.status||''}`;
      if(target&&target.dataset.translationKey!==key){target.innerHTML=readingTranslation(job);target.dataset.translationKey=key;}
      if(target)target.classList.toggle('no-block-translation',!job);
      element.classList.toggle('selected',state.readingFocus?.page===page&&state.readingFocus?.block_id===block.id);
    }
    renderExistingPageTranslation(page);
  }
  function renderExistingPageTranslation(page) {
    const data=state.readingPages.get(page),host=qs(`#reading-page-${page}`),target=qs(`#existing-translation-${page}`);
    if(!target||!host)return;
    const existing=data?.existing_translation,version=translationPage(page);
    const valid=existing?.original_sha===state.paper.sha256&&Number(existing.page)===Number(page)&&version?.status==='ready'&&
      existing.source_sha===version.source_sha&&(existing.translation_run_id||null)===(version.translation_run_id||state.translations.active_run_id||null);
    target.hidden=!valid;host.classList.toggle('has-page-translation',valid&&existing.status==='ready');
    if(!valid)return;
    target.innerHTML=`<div class="existing-translation-heading"><strong>已有译文 · 第 ${page} 页</strong><span>按页对应，尚无可靠的逐段对齐</span></div>${existing.status==='ready'?`<div class="existing-translation-text" lang="zh-CN">${esc(existing.text||'')}</div>`:'<p class="muted">已有译文 PDF 的这一页没有可提取文字，可切回 PDF 阅读。</p>'}`;
  }
  async function selectReadingBlock(page,probe) {
    const sequence=++state.readingSequence,sha=state.paper.sha256;
    try{
      const data=await loadReadingPage(page);
      if(!data||state.disposed||sequence!==state.readingSequence||state.paper.sha256!==sha)return;
      const block=probe.blockId?data.blocks.find(b=>b.id===probe.blockId):readingBlockAt(data.blocks,probe);
      if(!block){paragraphHint('这里没有可定位的文字段落。图表与扫描文字请在原 PDF 中查看。');return;}
      const old=state.readingFocus?.page;
      state.readingFocus={page,block_id:block.id,source_sha:sha};
      state.selectionTranslation=null;
      renderReadingPanel();renderParagraphHighlights();if(old)updateReadingResults(old);updateReadingResults(page);
      if(!state.translationView)paragraphHint('已选中段落。右键选择需要的操作。');
    }catch(error){if(!state.disposed&&sequence===state.readingSequence&&error.name!=='AbortError')paragraphHint('段落读取失败：'+error.message);}
  }
  function renderReadingPanel() {
    const panel=qs('#paragraph-translation-panel');if(!panel)return;
    const selected=state.selectionTranslation;
    if(state.readingPanelOpen&&selected&&selected.anchor.source_sha===state.paper.sha256){
      panel.hidden=false;const job=selected.job;
      const key=JSON.stringify([selected.anchor.quote,job?.id,job?.status,job?.result_markdown,job?.error,selected.busy,selected.error]);
      if(panel.dataset.renderKey===key)return;panel.dataset.renderKey=key;
      panel.innerHTML=`<div class="paragraph-panel-heading"><strong>选区翻译 <span>第 ${selected.anchor.page} 页</span></strong><button class="button small" data-reader-action="close-reading-panel" aria-label="关闭选区翻译">收起</button></div><blockquote>${esc(selected.anchor.quote)}</blockquote><div class="paragraph-panel-result">${selected.error?`<span class="paragraph-error">${esc(selected.error)}</span>`:selected.busy?'正在提交选区…':readingTranslation(job)}</div><div class="paragraph-panel-actions"><button class="button small" data-reader-action="translate-selection" ${selected.busy||['queued','running'].includes(job?.status)?'disabled':''}>${job?.status==='succeeded'?'查看已缓存译文':'翻译选区'}</button><button class="button small" data-reader-action="ask-selection">针对选区提问</button>${['queued','running'].includes(job?.status)?`<button class="button small" data-job-cancel="${esc(job.id)}">取消</button>`:''}</div><p class="paragraph-panel-note">仅翻译上方选中的原文，不扩大到整段或整页。</p>`;
      return;
    }
    const focus=state.readingFocus,block=focus&&state.readingPages.get(focus.page)?.blocks.find(b=>b.id===focus.block_id);
    if(!state.readingPanelOpen||!block||focus.source_sha!==state.paper.sha256){panel.hidden=true;delete panel.dataset.renderKey;return;}
    const job=blockTranslation(block,focus.page),busy=state.readingBusy.has(`${focus.page}:${block.id}`)||['queued','running'].includes(job?.status);
    panel.hidden=false;
    const key=JSON.stringify([focus.source_sha,focus.page,block.id,job?.id,job?.status,job?.result_markdown,job?.error,busy]);
    if(panel.dataset.renderKey===key)return;panel.dataset.renderKey=key;
    const scroll=panel.scrollTop;
    panel.innerHTML=`<div class="paragraph-panel-heading"><strong>段落翻译 <span>第 ${focus.page} 页</span></strong><button class="text-button" data-reader-action="close-reading-panel" aria-label="关闭段落翻译">×</button></div><blockquote>${esc(block.text)}</blockquote><div class="paragraph-panel-result">${readingTranslation(job)}</div><div class="paragraph-panel-actions"><button class="button small" data-reading-translate="${esc(block.id)}" data-reading-page="${focus.page}" ${busy?'disabled':''}>${busy?'翻译处理中…':job?.status==='succeeded'?'查看已缓存译文':'翻译此段'}</button><button class="text-button" data-reading-pdf="${focus.page}">原 PDF ↗</button>${['queued','running'].includes(job?.status)?`<button class="text-button" data-job-cancel="${esc(job.id)}">取消</button>`:''}</div><p class="paragraph-panel-note">只处理这一段；全文 PDF 翻译需单独开启。</p>`;
    panel.scrollTop=scroll;
  }
  async function translateReadingBlock(page,blockId) {
    const data=await loadReadingPage(page),block=data?.blocks.find(b=>b.id===blockId);
    if(!block||data.source_sha!==state.paper.sha256)throw new Error('段落已失效，请重新选择。');
    const existing=blockTranslation(block,page),key=`${page}:${blockId}`;
    if(existing&&['queued','running','succeeded'].includes(existing.status)){renderReadingPanel();return existing;}
    if(isAdmin()&&state.capabilities.ai_configured===false)throw new Error('翻译服务尚未就绪，请在管理员设置中完成配置。');
    if(state.readingBusy.has(key))return null;
    state.readingBusy.add(key);renderReadingPanel();
    try{
      const job=await api(`/papers/${pid}/reading/translate`,{method:'POST',body:{project_id:state.projectId,page,block_id:blockId,source_sha:data.source_sha}});
      if(!readingJobMatches(job,data.source_sha,page,blockId,state.projectId))throw new Error('翻译任务与当前段落不一致，请刷新后重试。');
      block.translation=job;
      state.jobs=[job,...state.jobs.filter(j=>j.id!==job.id)];
      if(!state.disposed){updateReadingResults(page);renderReadingPanel();void refreshJobs();}
      return job;
    }finally{state.readingBusy.delete(key);if(!state.disposed)renderReadingPanel();}
  }
  async function translateExactSelection(anchor) {
    if(!anchor||anchor.side!=='original'||!isCurrentAnchor(anchor))throw new Error('请重新选择当前版本的原文。');
    state.readingSequence++;
    const previous=state.selectionTranslation;
    if(previous?.anchor.quote===anchor.quote&&previous.anchor.page===anchor.page&&previous.anchor.source_sha===anchor.source_sha&&(previous.busy||['queued','running','succeeded'].includes(previous.job?.status))){state.readingPanelOpen=true;renderReadingPanel();return;}
    const selected={anchor:{...anchor},job:null,busy:true,error:''};state.selectionTranslation=selected;state.readingPanelOpen=true;
    if(state.narrow)toolSheet?.open();renderReadingPanel();
    try{
      const job=await api(`/papers/${pid}/reading/translate-selection`,{method:'POST',body:{project_id:state.projectId,page:anchor.page,source_sha:anchor.source_sha,selected_text:anchor.quote,selected_rects:anchor.rects}});
      if(job.source_sha!==anchor.source_sha||Number(job.page)!==anchor.page||job.project_id!==state.projectId||!job.selection_scope)throw new Error('翻译任务与选区不一致，请重新选择。');
      selected.job=job;state.jobs=[job,...state.jobs.filter(j=>j.id!==job.id)];void refreshJobs();
    }catch(error){selected.error=error.message;throw error;}finally{selected.busy=false;if(!state.disposed)renderReadingPanel();}
  }
  async function translateReadingPage() {
    if(state.readingBatch)return;
    const page=state.page,data=await loadReadingPage(page);if(!data?.blocks.length)throw new Error('本页没有可翻译文字。');
    if(data.blocks.length>80)throw new Error('本页段落较多，请逐段选择翻译。');
    state.readingBatch=true;const status=qs('#reading-batch-status');
    try{
      let done=0;
      for(const block of data.blocks){
        if(state.disposed)break;
        await translateReadingBlock(page,block.id);done++;
        if(status)status.textContent=`已提交 ${done} / ${data.blocks.length} 段，结果逐段显示`;
      }
      if(!state.disposed)toast('本页段落已提交，译文会逐段出现。');
    }finally{state.readingBatch=false;}
  }
  function updateReadingJobs() {
    const selected=state.selectionTranslation;
    if(selected?.job){const job=state.jobs.find(j=>j.id===selected.job.id&&j.source_sha===selected.anchor.source_sha&&j.project_id===state.projectId);if(job)selected.job=job;}
    for(const [page,data] of state.readingPages){
      for(const block of data.blocks){
        // Only refresh an ID accepted by the model-aware reading endpoint or
        // this page's explicit POST. Generic history can include older models.
        const job=block.translation&&state.jobs.find(j=>j.id===block.translation.id&&readingJobMatches(j,data.source_sha,page,block.id,state.projectId));
        if(job)block.translation=job;
      }
      updateReadingResults(page);
    }
    renderReadingPanel();
  }
  async function refreshAcceptedReadingJobs() {
    const known=new Set(state.jobs.map(j=>j.id)),missing=[];
    const selected=state.selectionTranslation?.job;
    if(selected&&['queued','running'].includes(selected.status)&&!known.has(selected.id))missing.push(selected.id);
    for(const [page,data] of state.readingPages)for(const block of data.blocks){
      const job=blockTranslation(block,page);
      if(job&&['queued','running'].includes(job.status)&&!known.has(job.id)&&!missing.includes(job.id))missing.push(job.id);
    }
    if(!missing.length)return;
    const start=state.readingPollCursor%missing.length;
    const batch=[...missing.slice(start),...missing.slice(0,start)].slice(0,12);
    state.readingPollCursor=(start+batch.length)%missing.length;
    const results=await Promise.allSettled(batch.map(id=>api('/jobs/'+encodeURIComponent(id))));
    if(state.disposed)return;
    for(let i=0;i<results.length;i++)if(results[i].status==='fulfilled'&&results[i].value.id===batch[i])state.jobs.push(results[i].value);
  }
  function updateToolbar() {
    const input=qs('#reader-page'); if(input && document.activeElement!==input)input.value=state.page;
    const previous=qs('[data-reader-action=prev]'),next=qs('[data-reader-action=next]');
    if(previous)previous.disabled=state.page<=1; if(next)next.disabled=state.page>=state.paper.page_count;
    if(qs('#reader-zoom'))qs('#reader-zoom').value=String(state.zoom);
    if(qs('#reading-status'))qs('#reading-status').textContent=statuses[membership().status]||'待读';
  }
  function updateTranslationProgress() {
    const ready=(state.translations.pages||[]).filter(p=>p.status==='ready').length;
    const failed=(state.translations.pages||[]).filter(p=>p.status==='failed').length;
    qs('#translation-progress').textContent=state.translations.mode==='none'?'尚未生成':`${ready} / ${state.paper.page_count} 页就绪${failed?' · '+failed+' 页失败':''}`;
    qs('#translation-download').hidden=!state.translations.download_ready;
  }
  function rowTop(page) {
    const row=state.pairs.get(page),scroll=qs('#document-scroll');
    return row ? row.getBoundingClientRect().top-scroll.getBoundingClientRect().top+scroll.scrollTop : 0;
  }
  function readPosition() {
    const scroll=qs('#document-scroll'); if(!scroll || !state.pairs.size)return;
    let low=1,high=state.manifest.length,found=1;
    const target=scroll.scrollTop+10;
    while(low<=high){const mid=Math.floor((low+high)/2);if(rowTop(mid)<=target){found=mid;low=mid+1;}else high=mid-1;}
    state.page=found;
    state.pageOffset=clampOffset((scroll.scrollTop-rowTop(found))/Math.max(1,state.pairs.get(found).offsetHeight));
    updateToolbar();
  }
  function handleScroll() {
    hideSelectionMenu();
    if(scrollFrame)return;
    scrollFrame=requestAnimationFrame(()=>{
      scrollFrame=null;readPosition();clearTimeout(saveTimer);
      saveTimer=setTimeout(()=>void persistPosition(),650);
    });
  }
  async function goToPage(value,{offset=0,remember=true}={}) {
    const page=clampPage(value,state.paper.page_count),scroll=qs('#document-scroll');
    state.page=page;state.pageOffset=clampOffset(offset);updateToolbar();
    scroll.scrollTop=rowTop(page)+state.pageOffset*state.pairs.get(page).offsetHeight;
    await loadPair(page);
    if(remember)await persistPosition();
  }
  async function setZoom(value) {
    readPosition(); const page=state.page,offset=state.pageOffset;
    state.zoom=Math.min(200,Math.max(75,Number(value)||100));
    qs('#document-pages').style.width=state.zoom+'%';
    updateToolbar(); await new Promise(resolve=>requestAnimationFrame(resolve));
    await goToPage(page,{offset,remember:false});
  }
  function persistPosition(status) {
    clearTimeout(saveTimer);
    const projectId=state.projectId,page=state.page,pageOffset=state.pageOffset;
    if(status==='read')finishedProjects.add(projectId);
    const write=async()=>{
      const member=memberships(state.paper).find(m=>m.project_id===projectId)||{};
      const next=finishedProjects.has(projectId)?'read':status||member.status||'reading';
      try{
        const updated=await api(`/papers/${pid}/reading`,{method:'PATCH',keepalive:true,body:{project_id:projectId,status:next==='unread'?'reading':next,last_page:page,page_offset:pageOffset}});
        const i=state.paper.memberships?.findIndex(x=>x.project_id===projectId);
        if(i>=0)state.paper.memberships[i]={...state.paper.memberships[i],...updated};
        updateToolbar();return true;
      }catch(e){if(!state.disposed)toast('阅读位置未保存：'+e.message);return false;}
    };
    positionQueue=positionQueue.then(write,write);return positionQueue;
  }
  function pageIdentity(side,page) {
    if(side==='original')return state.paper.sha256;
    const info=translationPage(page);
    return `${state.translations.mode}:${state.translations.active_run_id||''}:${info?.status||'queued'}:${info?.source_sha||''}`;
  }
  const paragraphKey = page => paragraphVersionKey(state.paper.sha256,state.translations,page);
  function paragraphHint(message,{retranslate=false}={}) {
    const hint=qs('#reader-hint');if(hint)hint.textContent=message;
    const button=qs('#paragraph-retranslate');if(button)button.hidden=!retranslate;
  }
  function paragraphFocusCurrent(focus=state.paragraphFocus) {
    return !!focus && isCurrentAnchor(focus);
  }
  function clearParagraphFocus(message='单击段落可定位对应译文，拖选词句可记录精确笔记。') {
    state.paragraphSequence++;state.paragraphFocus=null;state.paragraphMatches=[];state.paragraphResolvedKey=null;
    for(const controller of state.paragraphRequests.values())controller.abort();
    state.paragraphRequests.clear();renderParagraphHighlights();paragraphHint(message);
  }
  function invalidateParagraphPage(page) {
    for(const [key,value] of state.paragraphCache)if(Number(value.page)===page)state.paragraphCache.delete(key);
    for(const [key,controller] of state.paragraphRequests)if(controller.page===page){controller.abort();state.paragraphRequests.delete(key);}
    if(state.paragraphFocus?.page===page){state.paragraphMatches=[];state.paragraphResolvedKey=null;}
  }
  function renderParagraphHighlights() {
    const focus=state.paragraphEnabled&&paragraphFocusCurrent()?state.paragraphFocus:null;
    const matched=focus&&state.paragraphResolvedKey===paragraphKey(focus.page)?state.paragraphMatches:[];
    for(const element of app.querySelectorAll('.pdf-page')){
      const layer=element.querySelector('.paragraph-focus-layer');if(!layer)continue;
      const side=element.dataset.side,page=Number(element.dataset.page);
      let rects=[];
      if(focus?.page===page){
        rects=matched.flatMap(pair=>validParagraphRects(pair[side]?.rects));
        if(!matched.length&&side===focus.side)rects=validParagraphRects(focus.rects);
      }
      if(!rects.length&&side==='original'&&state.readingFocus?.page===page&&state.readingFocus.source_sha===state.paper.sha256){
        const block=state.readingPages.get(page)?.blocks.find(b=>b.id===state.readingFocus.block_id);
        rects=validParagraphRects(block?.rects);
      }
      layer.innerHTML=rects.map(r=>`<span class="paragraph-focus ${side===focus?.side?'source':'counterpart'}" style="left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%"></span>`).join('');
      if(focus?.page===page&&side===focus.side&&focus.point&&!matched.length){
        layer.insertAdjacentHTML('beforeend',`<span class="paragraph-focus-point" style="left:${focus.point.x*100}%;top:${focus.point.y*100}%"></span>`);
      }
    }
  }
  function focusVisible(focus) {
    const element=pageHost(focus.side,focus.page)?.querySelector('.pdf-page'),scroll=qs('#document-scroll');
    if(!element||!scroll)return false;
    const page=element.getBoundingClientRect(),viewport=scroll.getBoundingClientRect();
    const rects=focus.point?[{x:focus.point.x,y:focus.point.y,width:0,height:0}]:validParagraphRects(focus.rects);
    return rects.some(r=>{
      const x=page.left+(r.x+r.width/2)*page.width,y=page.top+(r.y+r.height/2)*page.height;
      return x>=viewport.left&&x<=viewport.right&&y>=viewport.top&&y<=viewport.bottom;
    });
  }
  async function loadParagraphAlignment(page) {
    const key=paragraphKey(page);
    if(state.paragraphCache.has(key))return state.paragraphCache.get(key);
    if(state.paragraphRequests.has(key))return state.paragraphRequests.get(key).pending;
    const controller=new AbortController();controller.page=page;
    const pending=(async()=>{
      try{
        const data=await api(`/papers/${pid}/pages/${page}/alignment`,{signal:controller.signal});
        if(controller.signal.aborted||state.disposed||paragraphKey(page)!==key)return null;
        if(!alignmentMatchesVersion(data,state.paper.sha256,state.translations,page))throw new Error('段落对应信息正在更新，请稍后重新选择。');
        state.paragraphCache.set(key,data);
        // Keep only a few page maps, independent of long-document page count.
        while(state.paragraphCache.size>12)state.paragraphCache.delete(state.paragraphCache.keys().next().value);
        return data;
      }finally{if(state.paragraphRequests.get(key)===controller)state.paragraphRequests.delete(key);}
    })();
    controller.pending=pending;state.paragraphRequests.set(key,controller);return pending;
  }
  async function revealParagraphCounterpart(focus,token) {
    if(state.narrow||!focusVisible(focus))return;
    const side=focus.side==='original'?'translation':'original';
    await loadPageSide(side,focus.page);
    if(state.disposed||token!==state.paragraphSequence||!focusVisible(focus)||!paragraphFocusCurrent(focus))return;
    const element=pageHost(side,focus.page)?.querySelector('.pdf-page'),scroll=qs('#document-scroll');
    if(!element||!scroll)return;
    const rects=state.paragraphMatches.flatMap(pair=>validParagraphRects(pair[side]?.rects));if(!rects.length)return;
    const box=element.getBoundingClientRect(),viewport=scroll.getBoundingClientRect();
    const top=Math.min(...rects.map(r=>box.top+r.y*box.height)),bottom=Math.max(...rects.map(r=>box.top+(r.y+r.height)*box.height));
    const left=Math.min(...rects.map(r=>box.left+r.x*box.width)),right=Math.max(...rects.map(r=>box.left+(r.x+r.width)*box.width));
    const dy=paragraphScrollDelta(top,bottom,viewport.top,viewport.bottom),dx=paragraphScrollDelta(left,right,viewport.left,viewport.right,16);
    if(dy||dx){scroll.scrollTop+=dy;scroll.scrollLeft+=dx;readPosition();void persistPosition();}
  }
  async function refreshParagraphFocus({scroll=false}={}) {
    const focus=state.paragraphFocus,token=state.paragraphSequence;
    if(state.disposed||state.narrow||!state.paragraphEnabled||!focus||state.translationView===false||state.readingMode==='reflow')return;
    if(!paragraphFocusCurrent(focus)){clearParagraphFocus('译文版本已更新，请重新选择段落。笔记草稿仍然保留。');return;}
    const key=paragraphKey(focus.page);
    try{
      const data=await loadParagraphAlignment(focus.page);
      if(!data||state.disposed||!state.paragraphEnabled||token!==state.paragraphSequence||key!==paragraphKey(focus.page))return;
      state.paragraphMatches=['ready','partial'].includes(data.status)?matchingParagraphs(data.paragraphs,focus.side,focus):[];
      state.paragraphResolvedKey=key;renderParagraphHighlights();
      if(state.paragraphMatches.length){
        paragraphHint(`已联动 ${state.paragraphMatches.length} 个段落 · ${focus.side==='original'?'原文 → 译文':'译文 → 原文'}；划词选区和笔记保持独立。`);
        if(scroll)await revealParagraphCounterpart(focus,token);
      }else if(data.status==='pending'||data.reason==='translation_pending'){
        paragraphHint('已记住选中位置，本页译文完成后会自动定位对应段落。');
      }else if(data.reason==='no_tracking'){
        paragraphHint('当前译文没有段落对应信息；可点击“重新翻译”，重新生成译文与段落对应记录。',{retranslate:true});
      }else if(data.reason==='no_text'){
        paragraphHint('这一页缺少可用文字，暂时无法定位对应段落。');
      }else{
        paragraphHint('这处内容没有可信的段落对应信息，未自动选择另一侧。');
      }
    }catch(error){
      if(!state.disposed&&state.paragraphEnabled&&token===state.paragraphSequence&&error.name!=='AbortError')paragraphHint('段落联动暂不可用：'+error.message);
    }
  }
  function focusParagraph(side,page,probe,{scroll=true}={}) {
    if(state.narrow||!state.paragraphEnabled||state.translationView===false||state.readingMode==='reflow')return;
    const info=state.pages.get(pageKey(side,page));if(!info)return;
    state.paragraphSequence++;
    for(const controller of state.paragraphRequests.values())controller.abort();state.paragraphRequests.clear();
    state.paragraphFocus={side,page,source_sha:info.source_sha,translation_run_id:info.translation_run_id||null,...probe};
    state.paragraphMatches=[];state.paragraphResolvedKey=null;
    renderParagraphHighlights();paragraphHint('正在定位对应段落…');void refreshParagraphFocus({scroll});
  }
  function pageProtected(page) {
    if(Number(state.selection?.page)===page||Number(state.pendingSelection?.page)===page)return true;
    const selection=window.getSelection();const row=state.pairs.get(page);
    return !!(row && selection && !selection.isCollapsed && (row.contains(selection.anchorNode)||row.contains(selection.focusNode)));
  }
  function unloadPair(page) {
    if(pageProtected(page))return;
    const row=state.pairs.get(page),reflow=qs(`#reading-page-${page}`);
    if(state.readingMode==='reflow'&&row&&reflow?.dataset.identity){
      row.style.minHeight=`${row.getBoundingClientRect().height}px`;
      reflow.innerHTML=`<div class="reflow-page-heading">第 ${page} 页</div><p class="muted">滚动到此页时载入原文…</p>`;delete reflow.dataset.identity;
    }
    for(const side of ['original','translation']){
      const key=pageKey(side,page);state.requests.get(key)?.abort();state.requests.delete(key);state.pages.delete(key);
      const host=pageHost(side,page);if(host){host.replaceChildren();delete host.dataset.identity;}
    }
  }
  function prunePages() { for(const page of state.pairs.keys())if(!state.near.has(page))unloadPair(page); }
  function placeholder(side,page) {
    const host=pageHost(side,page);if(!host)return;
    if(side==='original'){host.innerHTML='<div class="page-state"><div class="loading-ring"></div><p>正在读取原始 PDF…</p></div>';return;}
    const item=translationPage(page),status=item?.status||'queued';
    if(state.translations.mode==='none'){
      host.innerHTML=`<div class="page-state translation-state"><span class="translation-symbol">文 ⇄ A</span><strong>第 ${page} 页译文待生成</strong><p>原文已可阅读。翻译完成一页后会直接显示在这里。</p><button class="button small" data-reader-action="translate">开始逐页翻译</button><button class="text-button" data-reader-action="upload-translation">导入已有译文 PDF</button></div>`;
    }else if(status==='failed'){
      const retryJob=state.jobs.find(j=>j.kind==='translate'&&j.translation_run_id===state.translations.active_run_id&&['failed','interrupted','cancelled'].includes(j.status));
      host.innerHTML=`<div class="page-state translation-state failed"><strong>第 ${page} 页翻译失败</strong><p>${esc(item?.error||'这一页未生成，其他已完成页面仍可阅读。')}</p>${retryJob?`<button class="button small" data-job-retry="${esc(retryJob.id)}">重试未完成页面</button>`:'<p>其他页面处理结束后，可在解读页签的已保存任务中重试。</p>'}</div>`;
    }else{
      const label={running:'正在翻译这一页',queued:'等待逐页翻译',cancelled:'翻译已取消',interrupted:'翻译已中断',ready:'正在载入译文'}[status]||'等待译文';
      host.innerHTML=`<div class="page-state translation-state"><span class="page-status-dot ${status==='running'?'working':''}"></span><strong>第 ${page} 页 · ${label}</strong><p>已完成的页面可以直接阅读，无需等待整篇。</p></div>`;
    }
  }
  function fontFamily(item) {
    const name=(item.font_family||'').toLowerCase();
    if(/times|serif|roman/.test(name))return 'Georgia, serif';
    if(/courier|mono/.test(name))return 'monospace';return 'Arial, sans-serif';
  }
  const textContext=document.createElement('canvas').getContext('2d');
  function renderPdf(side,page,info) {
    const host=pageHost(side,page),layout=state.manifest[page-1];
    const width=Number(info.width),height=Number(info.height);
    if(!host || !width || !height)throw new Error('PDF 页面尺寸无效');
    const imageUrl=typeof info.image_url==='string'&&info.image_url.startsWith('/api/')?info.image_url+'&width=2400':typeof info.svg_url==='string'&&info.svg_url.startsWith('/api/')?info.svg_url:`/api/papers/${pid}/pages/${page}.svg?side=${side}`;
    const fitWidth=Math.min(1,(layout.height/layout.width)/(height/width))*100;
    state.pages.set(pageKey(side,page),info);
    const textItems=Array.isArray(info.text)?info.text:[];
    host.innerHTML=`<div class="pdf-page" data-page="${page}" data-side="${side}" style="width:${fitWidth}%;aspect-ratio:${width}/${height}"><img class="pdf-vector" src="${esc(imageUrl)}" alt="${side==='original'?'原文':'译文'} PDF 第 ${page} 页" draggable="false"><div class="paragraph-focus-layer" aria-hidden="true"></div><div class="highlight-layer" aria-hidden="true"></div><div class="text-layer" tabindex="0" aria-label="第 ${page} 页可选择的${side==='original'?'原文':'译文'}文本">${textItems.map((item,index)=>{
      const text=String(item.text||''),fontSize=Math.max(.1,Number(item.font_size)||Number(item.height)||10),family=fontFamily(item);
      textContext.font=`${fontSize}px ${family}`;const measured=textContext.measureText(text).width;
      const scale=measured>0?Math.max(.02,Number(item.width)/measured):1,rotation=Number(item.rotation)||0;
      const next=textItems[index+1],separator=next?(Math.abs(Number(next.y)-Number(item.y))>fontSize*.35?'\n':Number(next.x)>Number(item.x)+Number(item.width)+.7?' ':''):'';
      return `<span style="left:${Number(item.x)/width*100}%;top:${Number(item.y)/height*100}%;height:${Number(item.height)/width*100}cqw;font-family:${family};font-size:${fontSize/width*100}cqw;transform:rotate(${rotation}deg) scaleX(${scale});" data-text-span>${esc(text)}</span>${separator}`;
    }).join('')}</div></div>${info.has_text===false?'<span class="page-ocr-notice">此页无文字层，划词需要 OCR</span>':''}`;
    const element=host.querySelector('.pdf-page');
    let pointerStart=null;
    element.addEventListener('pointerdown',event=>{pointerStart=event.button===0?{x:event.clientX,y:event.clientY,id:event.pointerId}:null;});
    element.addEventListener('pointerup',event=>{
      const start=pointerStart;pointerStart=null;
      const clicked=start&&start.id===event.pointerId&&Math.hypot(event.clientX-start.x,event.clientY-start.y)<6;
      const rect=element.getBoundingClientRect();
      const point=clicked?{x:(event.clientX-rect.left)/rect.width,y:(event.clientY-rect.top)/rect.height}:null;
      setTimeout(()=>{
        if(!element.isConnected||state.disposed)return;
        const selected=captureSelection(element);
        if(!selected&&point&&point.x>=0&&point.x<=1&&point.y>=0&&point.y<=1){
          if(side==='original')void selectReadingBlock(page,{point});
          focusParagraph(side,page,{point});
        }
      },0);
    });
    element.addEventListener('keyup',event=>{if(event.key==='Shift'||(event.key.startsWith('Arrow')&&!event.shiftKey))captureSelection(element);});
    host.querySelector('.pdf-vector').addEventListener('error',()=>{
      if(element.isConnected&&host.contains(element))host.insertAdjacentHTML('beforeend',`<div class="page-render-error">页面图像加载失败。<button class="text-button" data-page-reload="${page}" data-page-side="${side}">重新加载</button></div>`);
    },{once:true});
    renderHighlights();renderParagraphHighlights();
  }
  async function loadPageSide(side,page,{force=false}={}) {
    const host=pageHost(side,page),key=pageKey(side,page),identity=pageIdentity(side,page);
    if(!host || state.disposed||state.readingMode==='reflow'||(side==='translation'&&!state.translationView))return;
    if(!force && host.dataset.identity===identity)return;
    if(!force && state.requests.has(key))return state.requests.get(key).pending;
    if(force){state.requests.get(key)?.abort();state.requests.delete(key);state.pages.delete(key);}
    if(side==='translation' && translationPage(page)?.status!=='ready'){
      placeholder(side,page);host.dataset.identity=identity;return;
    }
    const controller=new AbortController();let finishRequest;
    controller.pending=new Promise(resolve=>{finishRequest=resolve;});
    state.requests.set(key,controller);placeholder(side,page);
    try{
      const info=await api(`/papers/${pid}/pages/${page}?side=${side}`,{signal:controller.signal});
      if(state.disposed || controller.signal.aborted || state.requests.get(key)!==controller || pageIdentity(side,page)!==identity)return;
      renderPdf(side,page,info);host.dataset.identity=identity;
    }catch(error){
      if(controller.signal.aborted || state.disposed)return;
      host.innerHTML=`<div class="page-state failed"><strong>第 ${page} 页加载失败</strong><p>${esc(error.message)}</p><button class="button small" data-page-reload="${page}" data-page-side="${side}">重试页面</button></div>`;
    }finally{if(state.requests.get(key)===controller)state.requests.delete(key);finishRequest();}
  }
  async function loadPair(page) {
    if(state.readingMode==='reflow'){await renderReadingPage(page);return;}
    await Promise.all((state.translationView?['original','translation']:['original']).map(side=>loadPageSide(side,page)));
  }
  function captureSelection(element) {
    const selection=window.getSelection();if(!selection || selection.isCollapsed || !selection.rangeCount)return false;
    state.pendingSelection=null;
    const range=selection.getRangeAt(0),layer=element.querySelector('.text-layer');
    if(!layer.contains(range.startContainer)||!layer.contains(range.endContainer)){toast('请在同一页内选择词句；跨页内容可分别建立笔记。');return true;}
    const quote=selection.toString().trim();if(!quote)return false;
    if(quote.length>10000){toast('一次批注请选择不超过 10,000 个字符。');return true;}
    const pageRect=element.getBoundingClientRect();
    const rects=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>{
      const x=Math.max(0,(r.left-pageRect.left)/pageRect.width),y=Math.max(0,(r.top-pageRect.top)/pageRect.height);
      return {x,y,width:Math.max(0,Math.min(1-x,r.width/pageRect.width)),height:Math.max(0,Math.min(1-y,r.height/pageRect.height))};
    }).filter(r=>r.width>0&&r.height>0&&r.x<1&&r.y<1);
    if(!rects.length)return true;
    const side=element.dataset.side,page=Number(element.dataset.page),info=state.pages.get(pageKey(side,page));if(!info)return true;
    state.pendingSelection={project_id:state.projectId,side,page,quote,rects,source_sha:info.source_sha,translation_run_id:info.translation_run_id||null};
    focusParagraph(side,page,{rects},{scroll:false});
    paragraphHint('选区已保留。右键或 Shift+F10 选择翻译、问答、总结、解释、笔记。');
    return true;
  }
  function captureReflowSelection(element) {
    const native=window.getSelection();if(!native||native.isCollapsed||!native.rangeCount)return false;
    const range=native.getRangeAt(0);
    if(!element.contains(range.startContainer)||!element.contains(range.endContainer)){
      paragraphHint('请在同一段落内选择文字；跨段内容可分别操作。');return true;
    }
    const section=element.closest('[data-block-id]'),page=Number(section?.dataset.blockPage);
    const block=state.readingPages.get(page)?.blocks.find(b=>b.id===section?.dataset.blockId);
    const quote=native.toString().trim();
    if(!block||!quote)return false;
    if(quote.length>10000){paragraphHint('一次请选择不超过 10,000 个字符。');return true;}
    const rects=validParagraphRects(block.rects);
    if(!rects.length){paragraphHint('此段没有可靠的原文位置，请切回原 PDF 选择。');return true;}
    state.pendingSelection={project_id:state.projectId,side:'original',page,quote,rects,
      source_sha:state.paper.sha256,block_id:block.id,translation_run_id:null};
    paragraphHint('选区已保留。右键或 Shift+F10 选择需要的操作。');
    return true;
  }
  function hideSelectionMenu({focus=false}={}) {
    const menu=qs('#selection-context-menu');if(!menu)return;
    menu.hidden=true;
    if(focus)qs('#document-scroll')?.focus({preventScroll:true});
  }
  function showSelectionMenu(point) {
    if(!state.pendingSelection||!isCurrentAnchor(state.pendingSelection))return;
    const menu=qs('#selection-context-menu');if(!menu)return;
    for(const button of menu.querySelectorAll('[data-selection-action]'))button.hidden=!!state.pendingSelection.region&&!['figure','formula','region'].includes(button.dataset.selectionAction);
    menu.hidden=false;
    const bounds=menu.getBoundingClientRect(),margin=10;
    menu.style.left=Math.max(margin,Math.min(Number(point?.x)||margin,window.innerWidth-bounds.width-margin))+'px';
    menu.style.top=Math.max(margin,Math.min(Number(point?.y)||margin,window.innerHeight-bounds.height-margin))+'px';
    menu.querySelector('[role=menuitem]:not([hidden])')?.focus({preventScroll:true});
  }
  async function contextSelection(event) {
    if(state.disposed||state.areaMode)return;
    const visual=event.target.closest?.('.reflow-visual');
    if(visual){
      const page=Number(visual.dataset.visualPage),item=state.readingPages.get(page)?.content?.find(v=>v.id===visual.dataset.visualId);
      if(item&&validParagraphRects(item.rects).length){event.preventDefault();state.pendingSelection={project_id:state.projectId,side:'original',page,source_sha:state.paper.sha256,rects:item.rects,quote:'',region:true};const box=visual.getBoundingClientRect();showSelectionMenu({x:event.clientX||box.left,y:event.clientY||box.top});}return;
    }
    const pdfElement=event.target.closest?.('.pdf-page'),reflowElement=event.target.closest?.('.reflow-source');
    if(!pdfElement&&!reflowElement)return;
    event.preventDefault();
    const saved=state.contextAnchor;state.contextAnchor=null;
    if(pdfElement&&state.pendingSelection?.region&&isCurrentAnchor(state.pendingSelection)&&state.pendingSelection.page===Number(pdfElement.dataset.page)&&pdfElement.dataset.side==='original'){showSelectionMenu({x:event.clientX,y:event.clientY});return;}
    state.pendingSelection=null;
    if(pdfElement)captureSelection(pdfElement);else captureReflowSelection(reflowElement);
    const native=window.getSelection();
    const keyboard=event.type==='keydown';
    let x=event.clientX,y=event.clientY;
    if(keyboard){
      const rect=native?.rangeCount?native.getRangeAt(0).getBoundingClientRect():(pdfElement||reflowElement).getBoundingClientRect();
      x=rect.left+Math.min(24,rect.width);y=rect.bottom+5;
    }
    if(!state.pendingSelection&&(native?.isCollapsed??true)&&pdfElement&&saved&&isCurrentAnchor(saved)&&saved.page===Number(pdfElement.dataset.page)&&saved.side===pdfElement.dataset.side){
      const box=pdfElement.getBoundingClientRect(),point={x:(x-box.left)/box.width,y:(y-box.top)/box.height};
      if(saved.rects.some(r=>point.x>=r.x&&point.x<=r.x+r.width&&point.y>=r.y&&point.y<=r.y+r.height))state.pendingSelection=saved;
    }
    if(!state.pendingSelection&&(native?.isCollapsed??true)){
      // Right-clicking an unselected paragraph selects that original block only.
      const section=reflowElement?.closest('[data-block-id]');
      const page=Number(section?.dataset.blockPage||pdfElement?.dataset.page);
      if(pdfElement?.dataset.side==='translation'){
        paragraphHint('请先选中译文文字再右键；需要原文证据的操作请回到原文。');return;
      }
      const data=await loadReadingPage(page);if(state.disposed)return;
      const box=pdfElement?.getBoundingClientRect();
      if(box){const point={x:(x-box.left)/box.width,y:(y-box.top)/box.height};const visual=data?.content?.find(v=>['figure','table','formula'].includes(v.type)&&validParagraphRects(v.rects).some(r=>point.x>=r.x&&point.x<=r.x+r.width&&point.y>=r.y&&point.y<=r.y+r.height));if(visual){state.pendingSelection={project_id:state.projectId,side:'original',page,source_sha:state.paper.sha256,rects:visual.rects,quote:'',region:true};showSelectionMenu({x,y});return;}}
      const block=section?data?.blocks.find(b=>b.id===section.dataset.blockId):readingBlockAt(data?.blocks,{point:{x:(x-box.left)/box.width,y:(y-box.top)/box.height}});
      if(!block)return;
      state.pendingSelection={project_id:state.projectId,side:'original',page,quote:block.text,
        rects:validParagraphRects(block.rects),source_sha:state.paper.sha256,block_id:block.id,translation_run_id:null};
    }
    showSelectionMenu({x,y});
  }
  function openResearch(kind,selection=null,{autoGenerate=true,resume=false}={}) {
    if(selection?.side==='translation')throw new Error('这项操作需要原文证据。请在原文对应位置选择后重试。');
    if(selection&&!isCurrentAnchor(selection))throw new Error('选区版本已变化，请重新选择原文。');
    state.tab=kind==='qa'?'qa':'ai';state.panelTab=state.tab;
    if(kind!=='qa')state.interpretationKind=kind;
    renderSidebar();
    const panel=qs('#reader-research-panel');if(panel)panel.hidden=false;
    if(state.narrow&&autoGenerate)toolSheet?.open();
    window.dispatchEvent(new CustomEvent('paperstudio:research',{detail:{kind,paper_id:paperId,project_id:state.projectId,embedded:true,sessionTab:state.tab,autoGenerate,resume,
      selection:selection?{source_sha:selection.source_sha,page:selection.page,block_id:selection.block_id,
        quote:selection.quote||'',rects:selection.rects}:undefined}}));
  }
  async function selectionAction(action) {
    const chosen=state.pendingSelection;
    hideSelectionMenu();
    if(!chosen||!isCurrentAnchor(chosen))throw new Error('选区已失效，请重新选择。');
    if(action==='note'){
      if(!canReplaceDraft())return;
      state.selection={...chosen};state.draft='';state.editId=null;state.visibility='private';state.tab='notes';
      renderSidebar();renderHighlights();if(state.narrow)toolSheet?.open();qs('#note-draft')?.focus();return;
    }
    if(chosen.side!=='original')throw new Error('请在原文对应段落选择后执行翻译或基于原文的分析。');
    if(action==='translate'){
      await translateExactSelection(chosen);return;
    }
    if(action==='region'){await beginArea('region');return;}
    if(['qa','summary','explain','figure','formula'].includes(action))openResearch(action,chosen);
  }
  function clearArea() {
    if(state.areaDraft?.element){
      state.areaDraft.element.querySelector('.region-selection')?.remove();
      try{state.areaDraft.element.releasePointerCapture(state.areaDraft.pointerId);}catch{}
    }
    state.areaDraft=null;state.areaMode=null;
    qs('.continuous-reader')?.classList.remove('selecting-region');
  }
  async function beginArea(kind) {
    clearArea();hideSelectionMenu();
    if(state.narrow)await setNarrowDocument('original');
    if(state.readingMode!=='pdf')await setReadingDisplay('pdf',state.translationView);
    state.areaMode=kind;qs('.continuous-reader')?.classList.add('selecting-region');
    paragraphHint('在原文 PDF 中拖拽选择图表或公式区域，再右键选择讲解。按 Esc 取消。');
  }
  function areaPointerDown(event) {
    if(!state.areaMode||event.button!==0)return;
    const element=event.target.closest?.('.pdf-page');
    if(!element||element.dataset.side!=='original')return;
    event.preventDefault();event.stopPropagation();window.getSelection()?.removeAllRanges();
    const box=element.getBoundingClientRect();
    state.areaDraft={element,pointerId:event.pointerId,page:Number(element.dataset.page),
      source_sha:state.paper.sha256,start:{x:(event.clientX-box.left)/box.width,y:(event.clientY-box.top)/box.height}};
    try{element.setPointerCapture(event.pointerId);}catch{}
  }
  function areaPointerMove(event) {
    const draft=state.areaDraft;if(!draft||event.pointerId!==draft.pointerId)return;
    event.preventDefault();event.stopPropagation();
    const box=draft.element.getBoundingClientRect();
    draft.rect=readerAreaRect(draft.start,{x:(event.clientX-box.left)/box.width,y:(event.clientY-box.top)/box.height});
    let overlay=draft.element.querySelector('.region-selection');
    if(!overlay){overlay=document.createElement('div');overlay.className='region-selection';draft.element.append(overlay);}
    if(draft.rect){const r=draft.rect;overlay.style.cssText=`left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%`;}
  }
  function areaPointerUp(event) {
    const draft=state.areaDraft;if(!draft||event.pointerId!==draft.pointerId)return;
    areaPointerMove(event);event.preventDefault();event.stopPropagation();
    const rect=draft.rect;
    clearArea();
    if(!rect){paragraphHint('框选范围太小，请重新选择图表或公式区域。');return;}
    state.pendingSelection={project_id:state.projectId,side:'original',page:draft.page,source_sha:draft.source_sha,rects:[rect],quote:'',region:true};
    renderHighlights();paragraphHint('区域已选中，请右键选择图表讲解或公式讲解。');
  }
  async function jumpToEvidence(citation) {
    const anchor=readerEvidenceAnchor(citation,state.paper,state.projectId);
    if(!anchor){toast('引用与当前论文版本或项目不一致，未跳转。请从引用来源打开对应版本。');return false;}
    state.evidence=anchor;
    if(state.narrow)await setNarrowDocument('original');
    if(state.readingMode!=='pdf')await setReadingDisplay('pdf',state.translationView);
    await goToPage(anchor.page,{remember:false});
    if(state.disposed)return false;
    const element=pageHost('original',anchor.page)?.querySelector('.pdf-page'),scroll=qs('#document-scroll');
    const first=anchor.rects[0];
    if(element&&first){scroll.scrollTop+=element.getBoundingClientRect().top-scroll.getBoundingClientRect().top+first.y*element.clientHeight-scroll.clientHeight*.3;readPosition();}
    renderHighlights();
    paragraphHint(first?'已定位并高亮引用原文。':'已定位引用所在页；此条证据没有精确坐标。');
    await persistPosition();return true;
  }
  function renderHighlights() {
    for(const element of app.querySelectorAll('.pdf-page')){
      const side=element.dataset.side,page=Number(element.dataset.page),layer=element.querySelector('.highlight-layer');
      const notes=state.notes.filter(n=>n.side===side&&Number(n.page)===page&&isCurrentAnchor(n));
      const pending=state.selection?.side===side&&state.selection.page===page&&isCurrentAnchor(state.selection)?state.selection:null;
      const rects=notes.flatMap(n=>(n.rects||[]).map(r=>({...r,color:n.color||'yellow',pending:false})));
      if(state.pendingSelection?.region&&state.pendingSelection.page===page&&side==='original'&&isCurrentAnchor(state.pendingSelection))rects.push(...state.pendingSelection.rects.map(r=>({...r,color:'blue',pending:true})));
      if(pending&&!state.editId)rects.push(...pending.rects.map(r=>({...r,color:state.color,pending:true})));
      const evidence=state.evidence?.page===page&&side==='original'&&state.evidence.source_sha===state.paper.sha256?state.evidence.rects:[];
      layer.innerHTML=evidence.map(r=>`<span class="evidence-highlight" style="left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%"></span>`).join('')+rects.map(r=>`<span class="highlight ${['yellow','green','blue','pink'].includes(r.color)?r.color:'yellow'} ${r.pending?'pending':''}" style="left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%"></span>`).join('');
    }
  }
  function composer(){
    if(!state.selection)return `<div class="note-instruction"><span>${icons.note}</span><strong>读到有启发的地方，留下笔记。</strong><p>选中文字后右键，点击“笔记”再开始记录。笔记默认仅自己可见，并保留原句、页码和位置。</p></div>`;
    const s=state.selection;
    const outdated=!isCurrentAnchor(s),cannotSave=state.saving||(outdated&&!state.editId);
    return `<section class="note-composer"><div class="composer-heading"><strong>${state.editId?'编辑批注':'新的划线笔记'}</strong><span>${s.side==='original'?'原文':'译文'} · 第 ${s.page} 页</span></div>${outdated?'<div class="notice compact">这段选区来自旧版 PDF，原句与草稿仍保留。'+(state.editId?'可以继续编辑这条历史笔记。':'请复制草稿并取消选区，在新版页面重新选择后保存。')+'</div>':''}<blockquote>${esc(s.quote)}</blockquote><label class="note-label" for="note-draft">你的理解、疑问或一句话摘要</label><textarea id="note-draft" rows="5" maxlength="20000" placeholder="这句话说明了什么？与我的研究有什么关系？">${esc(state.draft)}</textarea><div class="color-choices" role="group" aria-label="高亮颜色">${[['yellow','黄色'],['green','绿色'],['blue','蓝色'],['pink','粉色']].map(([key,name])=>`<button class="color-choice ${key} ${state.color===key?'selected':''}" aria-label="${name}高亮" aria-pressed="${state.color===key}" data-note-color="${key}"></button>`).join('')}<span class="grow"></span><button class="text-button" data-reader-action="discard-selection">取消</button></div><label class="note-visibility-label">可见范围<select id="note-visibility" ${state.editId&&state.notes.find(n=>n.id===state.editId)?.owner_id!==authSession?.user?.id?'disabled':''}><option value="private" ${state.visibility==='private'?'selected':''}>仅自己</option>${state.projects.find(p=>p.id===state.projectId)?.can_edit!==false?`<option value="shared" ${state.visibility==='shared'?'selected':''}>当前项目成员</option>`:''}</select></label><div class="composer-actions"><button class="button primary" data-reader-action="save-note" ${cannotSave?'disabled':''}>${state.saving?'正在保存…':'保存笔记'}</button><button class="button subtle" data-reader-action="summarize-selection" ${cannotSave?'disabled':''}>${icons.spark}保存并提炼</button></div></section>`;
  }
  function notesArea(){
    const sorted=[...state.notes].sort((a,b)=>Number(a.page)-Number(b.page)||(a.created_at||'').localeCompare(b.created_at||''));
    return `${composer()}<div class="notes-section-heading"><h2>项目笔记 <span>${state.notes.length}</span></h2><a class="text-button" href="/api/papers/${pid}/notes.md?project_id=${encodeURIComponent(state.projectId)}" download>导出 Markdown ↓</a></div><div class="note-list">${sorted.map(n=>{
      const editable=n.can_edit!==false,versions=state.noteVersions.get(n.id),author=typeof n.author==='object'?n.author?.display_name||n.author?.username:n.author;
      return `<article class="note-card"><div class="note-card-meta"><button class="quote-location" data-note-jump="${esc(n.id)}">${n.side==='translation'?'译文':'原文'} · 第 ${n.page} 页 ↗</button><span class="small muted">${isCurrentAnchor(n)?'已保存':'旧版本批注'}</span></div><div class="note-authorship">${esc(author||'我')} · ${n.visibility==='shared'?'项目共享':'仅自己'} · v${Number(n.version)||1}</div><button class="note-quote ${esc(n.color||'yellow')}" data-note-jump="${esc(n.id)}">${esc(n.quote)}</button>${n.note?`<p class="note-body">${esc(n.note)}</p>`:'<p class="note-body muted">仅高亮，还没有补充个人笔记。</p>'}<div class="note-actions">${editable?`<button class="text-button" data-note-edit="${esc(n.id)}">编辑</button><button class="text-button" data-note-summary="${esc(n.id)}">提炼</button>`:''}<button class="text-button" data-note-versions="${esc(n.id)}">修改记录</button>${editable?`<button class="text-button danger" data-note-delete="${esc(n.id)}">删除</button>`:''}</div>${versions?`<details class="note-version-history" open><summary>历史版本 (${versions.length})</summary>${versions.map(v=>`<article><span>v${Number(v.version)||1} · ${esc(v.updated_at||v.created_at||'')} · ${esc(v.editor||'')}</span><p>${esc(v.content?.note||v.note||'仅高亮')}</p><small>${v.visibility==='shared'?'项目共享':'仅自己'}</small></article>`).join('')}</details>`:''}</article>`;
    }).join('')||'<p class="empty-notes">还没有笔记。从一处划线开始。</p>'}</div>`;
  }
  function markdown(text){
    return String(text||'').split('\n').map(line=>{
      const safe=esc(line).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
      if(/^###\s/.test(line))return `<h4>${safe.replace(/^###\s/,'')}</h4>`;
      if(/^##\s/.test(line))return `<h3>${safe.replace(/^##\s/,'')}</h3>`;
      if(/^#\s/.test(line))return `<h2>${safe.replace(/^#\s/,'')}</h2>`;
      if(/^[-*]\s/.test(line))return `<p class="markdown-bullet">${safe.replace(/^[-*]\s/,'')}</p>`;
      return line.trim()?`<p>${safe}</p>`:'<div class="markdown-space"></div>';
    }).join('');
  }
  function aiArea(){
    const current=state.jobs.find(j=>j.id===state.selectedJob)||state.jobs[0];
    return `<details class="reader-legacy-results"><summary>已保存的阅读成果与任务</summary><div class="notes-section-heading"><h2>任务与成果</h2><button class="text-button" data-reader-action="refresh-jobs">刷新</button></div><div class="job-history">${state.jobs.map(j=>`<button class="job-history-row ${current?.id===j.id?'active':''}" data-job-select="${esc(j.id)}"><span>${esc(jobLabels[j.kind]||j.kind)}</span><small class="job-state ${esc(j.status)}">${esc(jobStatuses[j.status]||j.status)}</small></button>`).join('')||'<p class="empty-notes">选择一个阅读任务，完成的内容会保存在这里。</p>'}</div>${current?`<article class="job-detail"><div class="job-detail-heading"><strong>${esc(jobLabels[current.kind]||current.kind)}</strong><span class="pill">${esc(jobStatuses[current.status]||current.status)}</span></div>${current.stage?`<p class="job-stage">${esc(current.stage)}</p>`:''}${current.error?`<div class="error-panel compact">${esc(current.error)}</div>`:''}${current.result_markdown?`<div class="ai-markdown">${markdown(current.result_markdown)}</div><button class="button small" data-job-download="${esc(current.id)}">导出这份结果 ↓</button>`:current.status==='succeeded'?'<p class="small muted">任务已完成。全文翻译可在中间一栏阅读。</p>':'<p class="small muted">摘要在任务完成后显示；译文会在每页完成后直接填入左侧阅读区。</p>'}<div class="job-detail-actions">${(current.can_share===true||(current.visibility==='shared'&&current.can_edit===true))&&current.status==='succeeded'?`<button class="text-button" data-job-share="${esc(current.id)}" data-job-visibility="${current.visibility==='shared'?'private':'shared'}">${current.visibility==='shared'?'设为仅自己':'共享到当前项目'}</button>`:''}${current.can_edit!==false&&['queued','running'].includes(current.status)?`<button class="text-button danger" data-job-cancel="${esc(current.id)}">取消任务</button>`:''}${current.can_edit!==false&&['failed','interrupted','cancelled'].includes(current.status)?`<button class="button small" data-job-retry="${esc(current.id)}">重新执行</button>`:''}</div></article>`:''}</details>`;
  }
  function renderSidebar() {
    const body=qs('#reading-sidebar-body');if(!body)return;
    const oldScroll=body.scrollTop,focused=document.activeElement;
    const focusId=body.contains(focused)?focused.id:null;
    const caret=focusId==='note-draft'?[focused.selectionStart,focused.selectionEnd]:null;
    qs('#reader-dialogue').hidden=state.tab==='notes';
    qs('#reader-notes').hidden=state.tab!=='notes';
    qs('#reader-interpretation').hidden=state.tab!=='ai';
    qs('#reader-archive').hidden=state.tab!=='ai';
    if(state.tab==='notes')qs('#reader-notes').innerHTML=notesArea();
    else if(state.tab==='ai'){
      const html=state.insightsError?`<p class="task-error">${esc(state.insightsError)}</p><button class="text-button" data-reader-action="reload-insights">重试读取解读</button>`:state.insights?insightMarkup(state.insights):'<p class="small muted">正在读取已保存的解读…</p>';
      if(html!==state.insightsMarkup){qs('#reader-insights').innerHTML=html;state.insightsMarkup=html;}
      const archive=qs('#reader-archive'),markup=aiArea();
      if(archive.dataset.markup!==markup){const open=archive.querySelector('details')?.open;archive.innerHTML=markup;archive.dataset.markup=markup;if(open)archive.querySelector('details').open=true;}
    }
    body.scrollTop=oldScroll;
    qs('#note-count').textContent=state.notes.length;
    for(const button of app.querySelectorAll('[data-reader-tab]')){const active=button.dataset.readerTab===state.tab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
    body.setAttribute('aria-labelledby','reader-tab-'+state.tab);
    if(focusId){const field=document.getElementById(focusId);if(field){field.focus({preventScroll:true});if(caret)field.setSelectionRange(...caret);}}
  }
  function activateReaderTab(tab) {
    state.tab=tab;renderSidebar();
    if(tab!=='notes'&&state.panelTab!==tab)openResearch(tab==='qa'?'qa':state.interpretationKind,null,{autoGenerate:false,resume:true});
  }
  async function loadInsights() {
    try{state.insights=await api(`/papers/${pid}/insights?project_id=${encodeURIComponent(state.projectId)}`);state.insightsError='';}
    catch(error){state.insightsError='已保存的解读暂时无法读取：'+error.message;}
    if(!state.disposed)renderSidebar();
  }
  async function refreshNotes() {
    state.notes=list(await api(notesUrl()));renderHighlights();
    if(state.tab==='notes')renderSidebar();else qs('#note-count').textContent=state.notes.length;
  }
  async function saveNote() {
    if(!state.selection)throw new Error('先在 PDF 中选择词语或句子。');
    if(!state.editId&&!isCurrentAnchor(state.selection))throw new Error('选区来自旧版 PDF，草稿已保留。请在新版页面重新选择词句。');
    state.saving=true;renderSidebar();
    try{
      const body={...state.selection,note:state.draft,color:state.color,visibility:state.visibility};
      const saved=state.editId?await api('/annotations/'+encodeURIComponent(state.editId),{method:'PATCH',body:{note:state.draft,color:state.color,visibility:state.visibility,expected_version:state.notes.find(n=>n.id===state.editId)?.version}}):await api(`/papers/${pid}/annotations`,{method:'POST',body});
      state.selection=null;state.pendingSelection=null;state.draft='';state.editId=null;state.visibility='private';window.getSelection()?.removeAllRanges();await refreshNotes();prunePages();toast('笔记已保存，并保留原文位置。');return saved;
    }finally{state.saving=false;renderSidebar();}
  }
  function manifestFingerprint(manifest) {
    return JSON.stringify([manifest.mode,manifest.active_run_id,(manifest.pages||[]).map(p=>[p.page,p.source_sha,p.translation_run_id])]);
  }
  async function refreshTranslationPages(manifest) {
    const previous=state.translations;
    const oldIdentities=new Map(state.manifest.map(p=>[p.page,pageIdentity('translation',p.page)]));
    state.translations=manifest || await api(`/papers/${pid}/translation-pages`);
    const versionChanged=manifestFingerprint(previous)!==manifestFingerprint(state.translations);
    const work=[];let focusedPageChanged=false;
    for(const page of state.pairs.keys()){
      if(oldIdentities.get(page)===pageIdentity('translation',page))continue;
      invalidateParagraphPage(page);
      if(state.paragraphFocus?.page===page)focusedPageChanged=true;
      const key=pageKey('translation',page);state.requests.get(key)?.abort();state.requests.delete(key);state.pages.delete(key);
      const host=pageHost('translation',page);if(host)delete host.dataset.identity;
      // A manual replacement or a newly completed translated page invalidates
      // the page-level reflow text, but keeps the original text selection intact.
      renderExistingPageTranslation(page);
      if(state.readingPages.has(page)){
        state.readingRequests.get(page)?.abort();state.readingRequests.delete(page);
        if(state.readingMode==='reflow'&&(state.near.has(page)||pageProtected(page)))
          work.push(loadReadingPage(page,{force:true}).then(()=>updateReadingResults(page)));
        else state.readingPages.delete(page);
      }

      if(state.readingMode==='pdf'&&state.translationView&&(state.near.has(page)||pageProtected(page)))work.push(loadPageSide('translation',page,{force:true}));
    }
    updateTranslationProgress();renderParagraphHighlights();
    // Page frames never change dimensions and the original column stays mounted.
    // Completed translation pages cannot reset scrolling or invalidate other pages.
    await Promise.all(work);
    if(focusedPageChanged)await refreshParagraphFocus({scroll:true});
    if(versionChanged)await refreshNotes();
    return versionChanged;
  }
  async function refreshJobs() {
    if(refreshing||state.disposed)return;
    const sequence=++refreshSequence;
    refreshing=true;clearTimeout(state.pollTimer);
    try{
      const [jobs,manifest]=await Promise.all([
        api(`/jobs?paper_id=${pid}&project_id=${encodeURIComponent(state.projectId)}`),
        api(`/papers/${pid}/translation-pages`)
      ]);
      if(state.disposed||sequence!==refreshSequence)return;
      state.jobs=list(jobs).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''));
      await refreshAcceptedReadingJobs();
      if(state.disposed||sequence!==refreshSequence)return;
      updateReadingJobs();
      await refreshTranslationPages(manifest);
      if(state.tab==='ai')renderSidebar();
      // A job can become retryable without changing the failed page's status.
      for(const page of state.near){if(state.readingMode==='pdf'&&state.translationView&&translationPage(page)?.status==='failed')placeholder('translation',page);}
    }catch(error){
      if(!state.disposed&&sequence===refreshSequence){const badge=qs('#translation-progress');if(badge)badge.textContent='状态更新失败，稍后重试';}
    }finally{
      if(sequence===refreshSequence){
        refreshing=false;
        if(!state.disposed){const active=state.jobs.some(j=>['queued','running'].includes(j.status))||['queued','running'].includes(state.translations.status);state.pollTimer=setTimeout(()=>void refreshJobs(),active?1800:8000);}
      }
    }
  }
  async function createJob(kind,annotationId,force=false) {
    if(isAdmin()&&kind==='translate'&&state.capabilities.translation_configured===false)throw new Error('翻译服务尚未配置。可先点击“导入译文”使用已有单语译文 PDF。');
    if(isAdmin()&&kind!=='translate'&&state.capabilities.ai_configured===false){state.tab='ai';renderSidebar();throw new Error('AI 模型尚未配置，笔记已保存，可先导出 Markdown。');}
    const body={paper_id:paperId,project_id:state.projectId,kind};if(kind==='brief')body.mode=state.mode;if(annotationId)body.annotation_id=annotationId;
    if(kind==='translate'&&state.paper?.can_edit===false)throw new Error('当前论文为只读权限，可阅读现有译文或按段翻译。');
    if(kind==='translate'&&force)body.force=true;
    const job=await api('/jobs',{method:'POST',body});
    if(kind==='translate'&&force)paragraphHint(job.reused?'翻译已在进行中，完成一页后会更新译文与段落对应信息。':'已重新提交全文翻译，完成一页后会更新译文与段落对应信息。');
    state.selectedJob=job.id;activateReaderTab('ai');await refreshJobs();renderSidebar();toast(job.reused&&kind==='translate'&&force?'翻译已在进行中，可以继续阅读。':'任务已提交，可以继续阅读。');return job;
  }
  async function jumpToNote(note) {
    if(!note)return;
    if(!isCurrentAnchor(note)){toast('这条笔记属于旧版 PDF，原句已保留，不会跳转到当前版本的相同位置。');return;}
    if(state.narrow)await setNarrowDocument(note.side);
    if(state.readingMode!=='pdf'||(note.side==='translation'&&!state.translationView))await setReadingDisplay('pdf',note.side==='translation'||state.translationView);
    await goToPage(note.page,{remember:false});
    const scroll=qs('#document-scroll'),element=pageHost(note.side,note.page)?.querySelector('.pdf-page'),rect=note.rects?.[0];
    if(element&&rect){scroll.scrollTop+=element.getBoundingClientRect().top-scroll.getBoundingClientRect().top+rect.y*element.clientHeight-scroll.clientHeight*.3;readPosition();}
    await persistPosition();
  }
  listen(app,'contextmenu',event=>{void contextSelection(event).catch(error=>toast(error.message));});
  listen(app,'pointerdown',event=>{
    if(event.button===0&&!state.areaMode&&state.pendingSelection?.region&&event.target.closest?.('.pdf-page')){state.pendingSelection=null;renderHighlights();}
    if(event.button!==2||state.areaMode)return;
    state.contextAnchor=null;
    const element=event.target.closest?.('.pdf-page');if(!element)return;
    if(captureSelection(element)&&state.pendingSelection)state.contextAnchor={...state.pendingSelection};
  });
  listen(app,'pointerdown',areaPointerDown,true);
  listen(app,'pointermove',areaPointerMove,true);
  listen(app,'pointerup',areaPointerUp,true);
  listen(app,'pointercancel',()=>clearArea(),true);
  listen(app,'pointerup',event=>{
    if(state.disposed||state.areaMode)return;
    const source=event.target.closest?.('.reflow-source');
    if(source)setTimeout(()=>{if(source.isConnected&&!state.disposed)captureReflowSelection(source);},0);
    else if(!event.target.closest?.('.pdf-page'))setTimeout(()=>{const selected=window.getSelection(),element=selected?.anchorNode?.parentElement?.closest('.pdf-page');if(element?.isConnected&&!state.disposed)captureSelection(element);},0);
  });
  listen(document,'pointerdown',event=>{
    const menu=qs('#selection-context-menu');
    if(menu&&!menu.hidden&&!menu.contains(event.target))hideSelectionMenu();
  });
  listen(app,'keydown',event=>{
    if(toolSheet?.keydown(event))return;
    if(event.key==='Escape'){
      if(qs('#selection-context-menu')?.hidden===false||state.areaMode){event.preventDefault();hideSelectionMenu({focus:true});clearArea();}
      return;
    }
    const tab=event.target.closest?.('[data-reader-tab]');
    if(tab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();const tabs=[...app.querySelectorAll('[data-reader-tab]')],index=tabs.indexOf(tab);
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      activateReaderTab(tabs[next].dataset.readerTab);tabs[next].focus();return;
    }
    if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){
      void contextSelection(event).catch(error=>toast(error.message));return;
    }
    const menu=event.target.closest?.('#selection-context-menu');
    if(menu&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
      event.preventDefault();const buttons=[...menu.querySelectorAll('[role=menuitem]:not([hidden])')],index=buttons.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
      buttons[next]?.focus();
    }
  });
  listen(window,'resize',()=>{void resizeReader().catch(error=>toast(error.message));});
  listen(window,'blur',()=>clearArea());
  listen(document,'focusin',event=>toolSheet?.containFocus(event));
  listen(window,'paperstudio:evidence',event=>{
    if(!state.disposed&&event.detail?.paper_id===paperId)void jumpToEvidence(event.detail).catch(error=>toast(error.message));
  });
  listen(app,'input',event=>{if(event.target.id==='note-draft')state.draft=event.target.value;});
  listen(app,'change',async event=>{
    try{
      if(event.target.id==='reader-page')await goToPage(event.target.value);
      if(event.target.id==='reader-zoom')await setZoom(event.target.value);
      if(event.target.id==='brief-mode')state.mode=event.target.value;
      if(event.target.id==='note-visibility')state.visibility=event.target.value==='shared'?'shared':'private';
      if(event.target.id==='reader-project'){
        if(!canReplaceDraft()){event.target.value=state.projectId;return;}
        const nextProject=event.target.value;event.target.disabled=true;
        try{readPosition();if(!await persistPosition()){event.target.value=state.projectId;return;}location.href=`/read/${pid}?project=${encodeURIComponent(nextProject)}`;}finally{event.target.disabled=false;}
      }
      if(event.target.id==='translation-file'){
        if(state.paper.can_edit===false)throw new Error('当前论文为只读权限，无法替换译文。');
        const file=event.target.files[0];if(!file)return;
        if(file.size>50*1024*1024)throw new Error('译文 PDF 上限为 50 MB。');
        const data=new FormData();data.set('file',file);toast('正在保存译文 PDF…');
        state.paper=await api('/papers/'+pid+'/translation',{method:'POST',body:data});
        await refreshTranslationPages();toast('译文已就绪，原文位置和笔记草稿均保留。');event.target.value='';
      }
    }catch(error){toast(error.message);}
  });
  listen(app,'click',async event=>{
    if(event.target.id==='reader-tools-backdrop'){toolSheet?.close();return;}
    const link=event.target.closest('a.reader-back');
    if(link){event.preventDefault();if(!canReplaceDraft())return;readPosition();if(await persistPosition())location.href=link.href;return;}
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.selectionAction){await selectionAction(button.dataset.selectionAction);return;}
      if(button.dataset.researchView){openResearch(button.dataset.researchView,null,{autoGenerate:false});return;}
      if(button.dataset.researchKind){openResearch(button.dataset.researchKind);return;}
      if(button.dataset.readerSide){await setNarrowDocument(button.dataset.readerSide);return;}
      if(button.dataset.readerAction==='toggle-reading'){await setReadingDisplay(state.readingMode==='pdf'?'reflow':'pdf',state.translationView);return;}
      if(button.dataset.readerAction==='toggle-translation'){await setReadingDisplay(state.readingMode,!state.translationView);return;}
      if(button.dataset.readerAction==='toggle-link'){state.paragraphEnabled=!state.paragraphEnabled;updateReadingLayout();clearParagraphFocus(state.paragraphEnabled?'段落联动已开启。':'段落联动已关闭，划词与问答仍可使用。');return;}
      if(button.dataset.readerAction==='translate-selection'){await translateExactSelection(state.selectionTranslation?.anchor);return;}
      if(button.dataset.readerAction==='ask-selection'){openResearch('qa',state.selectionTranslation?.anchor);return;}
      if(button.dataset.readerMode){await setReadingDisplay(button.dataset.readerMode,state.narrow&&button.dataset.readerMode==='reflow'?false:state.translationView);return;}
      if(button.dataset.readingBlock){await selectReadingBlock(Number(button.dataset.readingPage),{blockId:button.dataset.readingBlock});return;}
      if(button.dataset.readingTranslate){
        button.disabled=true;state.readingPanelOpen=true;const page=Number(button.dataset.readingPage);
        if(state.narrow)toolSheet?.open();
        await selectReadingBlock(page,{blockId:button.dataset.readingTranslate});
        await translateReadingBlock(page,button.dataset.readingTranslate);return;
      }
      if(button.dataset.readingPdf){
        const page=Number(button.dataset.readingPdf);
        if(state.narrow)await setNarrowDocument('original');
        await setReadingDisplay('pdf',state.translationView);await goToPage(page);return;
      }
      if(button.dataset.readingReload){const page=Number(button.dataset.readingReload);state.readingPages.delete(page);await renderReadingPage(page);return;}
      if(button.dataset.readerTab){activateReaderTab(button.dataset.readerTab);return;}
      if(button.dataset.noteColor){state.color=button.dataset.noteColor;renderSidebar();renderHighlights();return;}
      if(button.dataset.aiKind){button.disabled=true;if(button.dataset.aiKind==='translate')await setReadingDisplay('pdf',true);await createJob(button.dataset.aiKind);return;}
      if(button.dataset.jobSelect){state.selectedJob=button.dataset.jobSelect;renderSidebar();return;}
      if(button.dataset.jobShare){await api('/jobs/'+encodeURIComponent(button.dataset.jobShare)+'/visibility',{method:'PATCH',body:{visibility:button.dataset.jobVisibility}});await refreshJobs();toast(button.dataset.jobVisibility==='shared'?'已共享到当前项目':'已设为仅自己');return;}
      if(button.dataset.jobCancel){await api('/jobs/'+encodeURIComponent(button.dataset.jobCancel)+'/cancel',{method:'POST'});await refreshJobs();return;}
      if(button.dataset.jobRetry){button.disabled=true;const result=await api('/jobs/'+encodeURIComponent(button.dataset.jobRetry)+'/retry',{method:'POST'});state.selectedJob=result.id;await refreshJobs();return;}
      if(button.dataset.jobDownload){const job=state.jobs.find(j=>j.id===button.dataset.jobDownload);if(!job)return;const blob=new Blob([job.result_markdown||''],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${job.kind}-${job.id}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
      if(button.dataset.noteJump){await jumpToNote(state.notes.find(n=>n.id===button.dataset.noteJump));return;}
      if(button.dataset.noteEdit){
        if(state.editId===button.dataset.noteEdit){qs('#note-draft')?.focus();return;}if(!canReplaceDraft())return;
        const note=state.notes.find(n=>n.id===button.dataset.noteEdit);if(!note)return;
        state.selection={project_id:state.projectId,side:note.side,page:note.page,quote:note.quote,rects:note.rects,source_sha:note.source_sha,translation_run_id:note.translation_run_id||null};
        state.draft=note.note||'';state.color=note.color||'yellow';state.visibility=note.visibility||'private';state.editId=note.id;state.tab='notes';renderSidebar();qs('#note-draft')?.focus();return;
      }
      if(button.dataset.noteVersions){
        const id=button.dataset.noteVersions;
        if(state.noteVersions.has(id))state.noteVersions.delete(id);
        else state.noteVersions.set(id,list(await api('/annotations/'+encodeURIComponent(id)+'/versions')));
        renderSidebar();return;
      }
      if(button.dataset.noteDelete){
        if(state.editId===button.dataset.noteDelete&&!canReplaceDraft())return;
        await api('/annotations/'+encodeURIComponent(button.dataset.noteDelete),{method:'DELETE'});
        if(state.editId===button.dataset.noteDelete){state.selection=null;state.draft='';state.editId=null;}
        await refreshNotes();prunePages();toast('批注已删除');return;
      }
      if(button.dataset.noteSummary){button.disabled=true;await createJob('selection-summary',button.dataset.noteSummary);return;}
      if(button.dataset.pageReload){await loadPageSide(button.dataset.pageSide,Number(button.dataset.pageReload),{force:true});return;}
      switch(button.dataset.readerAction){
        case'open-tools':toolSheet?.open();break;
        case'close-tools':toolSheet?.close();break;
        case'close-reading-panel':{state.readingPanelOpen=false;const page=state.readingFocus?.page;state.readingSequence++;state.readingFocus=null;renderReadingPanel();renderParagraphHighlights();if(page)updateReadingResults(page);break;}
        case'translate-reading-page':button.disabled=true;await translateReadingPage();break;
        case'prev':await goToPage(state.page-1);break;
        case'next':await goToPage(state.page+1);break;
        case'zoom-in':case'zoom-out':await setZoom(state.zoom+(button.dataset.readerAction==='zoom-in'?25:-25));break;
        case'upload-translation':qs('#translation-file').click();break;
        case'translate':button.disabled=true;await createJob('translate');break;
        case'retranslate':button.disabled=true;await createJob('translate',undefined,true);break;
        case'discard-selection':state.selection=null;state.pendingSelection=null;state.draft='';state.editId=null;window.getSelection()?.removeAllRanges();renderSidebar();renderHighlights();prunePages();break;
        case'save-note':await saveNote();break;
        case'summarize-selection':{const note=await saveNote();await createJob('selection-summary',note.id);break;}
        case'reload-insights':await loadInsights();break;
        case'refresh-jobs':await refreshJobs();break;
        case'finish':
          button.disabled=true;if(state.selection)await saveNote();readPosition();
          if(!await persistPosition('read'))throw new Error('阅读状态保存失败，请重试。笔记仍已保留。');
          activateReaderTab('ai');
          if(state.notes.length)await createJob('notes-summary');else toast('已标记为已读。当前项目还没有笔记，暂未创建笔记总结任务。');break;
      }
    }catch(error){toast(error.message);}finally{if(button.isConnected&&!['prev','next'].includes(button.dataset.readerAction))button.disabled=false;}
  });
  listen(window,'beforeunload',event=>{if(hasUnsavedDraft()){event.preventDefault();event.returnValue='';}});
  const lifecycle=createReaderLifecycle(()=>{
    state.disposed=true;refreshSequence++;refreshing=false;hideSelectionMenu();clearArea();stopSidebarResize();toolSheet?.dispose();
    window.dispatchEvent(new CustomEvent('paperstudio:reader-paused',{detail:{paper_id:paperId}}));
    observer?.disconnect();clearTimeout(saveTimer);clearTimeout(state.pollTimer);
    if(scrollFrame)cancelAnimationFrame(scrollFrame);scrollFrame=null;
    for(const controller of state.requests.values())controller.abort();
    state.requests.clear();
    for(const controller of state.paragraphRequests.values())controller.abort();
    state.paragraphRequests.clear();
    state.readingSequence++;
    for(const controller of state.readingRequests.values())controller.abort();
    state.readingRequests.clear();
  },()=>{
    state.disposed=false;
    state.narrow=window.innerWidth<=900;toolSheet?.setNarrow(state.narrow);applySidebarWidth();updateReadingLayout();
    if(!observer||!state.pairs.size)return;
    state.near.clear();
    for(const element of state.pairs.values())observer.observe(element);
    readPosition();void loadPair(state.page);void refreshJobs();void refreshParagraphFocus();state.panelTab=null;activateReaderTab(state.tab);
    window.dispatchEvent(new CustomEvent('paperstudio:reader-ready',{detail:{paper_id:paperId,project_id:state.projectId,target:'#reader-research-panel'}}));
  });
  listen(window,'pagehide',event=>{lifecycle.hide(event);if(!event.persisted){removeListeners();window.dispatchEvent(new CustomEvent('paperstudio:reader-disposed',{detail:{paper_id:paperId}}));}});
  listen(window,'pageshow',lifecycle.show);
  listen(document,'visibilitychange',()=>{if(document.visibilityState==='hidden'&&!state.disposed){readPosition();void persistPosition();}});
  try{
    const [paper,projects,capabilities,manifest,translations]=await Promise.all([
      api('/papers/'+pid),api('/projects'),isAdmin()?api('/capabilities').catch(()=>({})):Promise.resolve({}),
      api(`/papers/${pid}/page-manifest`),api(`/papers/${pid}/translation-pages`)
    ]);
    state.paper=paper;state.projects=list(projects);state.capabilities=capabilities;
    state.manifest=manifest.pages;state.translations=translations;
    if(!Array.isArray(state.manifest)||state.manifest.length!==paper.page_count)throw new Error('PDF 页清单不完整，请重新打开论文。');
    const ms=memberships(paper);if(!state.projectId||!ms.some(m=>m.project_id===state.projectId))state.projectId=ms[0]?.project_id||'';
    if(!state.projectId)throw new Error('这篇论文还没有关联项目，请先返回论文库关联一个项目。');
    const initialQuery=new URLSearchParams(location.search);
    history.replaceState(null,'',`/read/${pid}?project=${encodeURIComponent(state.projectId)}`);
    state.page=clampPage(membership().last_page,paper.page_count);state.pageOffset=clampOffset(membership().page_offset);
    state.notes=list(await api(notesUrl()));shell();activateReaderTab('ai');void loadInsights();
    await goToPage(state.page,{offset:state.pageOffset,remember:false});await refreshJobs();
    if(initialQuery.has('page')&&initialQuery.has('source_sha')){
      let rects=[];try{rects=JSON.parse(initialQuery.get('rects')||'[]');}catch{rects=null;}
      await jumpToEvidence({paper_id:paperId,project_id:state.projectId,source_sha:initialQuery.get('source_sha'),page:Number(initialQuery.get('page')),rects});
    }
    window.dispatchEvent(new CustomEvent('paperstudio:reader-ready',{detail:{paper_id:paperId,project_id:state.projectId,target:'#reader-research-panel'}}));

  }catch(error){
    state.disposed=true;observer?.disconnect();stopSidebarResize();toolSheet?.dispose();removeListeners();
    app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1>暂时无法打开论文</h1><p>${esc(error.message)}</p><a class="button primary" href="/?view=library">返回论文库</a></main>`;
  }
  return {paperId, jumpToEvidence, openResearch, dispose(){
    lifecycle.hide({persisted:false});removeListeners();
    window.dispatchEvent(new CustomEvent('paperstudio:reader-disposed',{detail:{paper_id:paperId}}));
  }};

}

