import {api,esc,qs,list,toast,memberships,statuses,jobLabels,jobStatuses,icons} from './app.js';

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

export async function mountReader({paperId,projectId}) {
  const app = qs('#app');
  const pid = encodeURIComponent(paperId);
  const state = {
    paper:null, projects:[], projectId:projectId || '', page:1, pageOffset:0, zoom:100,
    notes:[], jobs:[], capabilities:{}, tab:'notes', selection:null, draft:'', color:'yellow',
    editId:null, selectedJob:null, mode:'abstract', saving:false, disposed:false,
    manifest:[], translations:{active_run_id:null,mode:'none',status:'idle',pages:[]},
    pages:new Map(), requests:new Map(), near:new Set(), pairs:new Map(), pollTimer:null,
    paragraphEnabled:true, paragraphFocus:null, paragraphMatches:[], paragraphResolvedKey:null,
    paragraphCache:new Map(), paragraphRequests:new Map(), paragraphSequence:0
  };
  let observer, scrollFrame, saveTimer, positionQueue = Promise.resolve(true), refreshing = false, refreshSequence = 0;
  const finishedProjects = new Set();
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
    return !saved || state.draft!==(saved.note||'') || state.color!==(saved.color||'yellow');
  }
  function canReplaceDraft({focus=true}={}) {
    if(!hasUnsavedDraft())return true;
    toast(state.saving?'正在保存批注，请稍候。':'请先保存或取消右侧正在编辑的批注，原稿已保留。');
    state.tab='notes'; renderSidebar(); if(focus)qs('#note-draft')?.focus(); return false;
  }
  function shell() {
    document.title=`${state.paper.title} · PaperStudio`;
    app.innerHTML=`<div class="reader-app continuous-reader">
      <header class="reader-header"><a class="reader-back" href="/?view=library&project=${encodeURIComponent(state.projectId)}" title="返回论文库" aria-label="返回论文库">←</a><div class="reader-heading"><div class="eyebrow">PAPERSTUDIO / READING</div><h1>${esc(state.paper.title)}</h1><span>${esc(projectPath(state.projectId)||'当前项目')}</span></div><div class="reader-header-actions"><a class="button small subtle" href="/api/papers/${pid}/file?side=original" target="_blank" rel="noopener">原始 PDF ↗</a><button class="button primary small" data-reader-action="finish">${icons.note}读完，整理笔记</button></div></header>
      <div class="reader-toolbar"><div class="reader-page-controls"><button class="icon-button" data-reader-action="prev" aria-label="跳到上一页">‹</button><label>定位到 <input id="reader-page" aria-label="定位页码" type="number" min="1" max="${state.paper.page_count}" value="${state.page}"> / ${state.paper.page_count} 页</label><button class="icon-button" data-reader-action="next" aria-label="跳到下一页">›</button></div><div class="reader-zoom-controls"><button class="icon-button" data-reader-action="zoom-out" aria-label="缩小页面">−</button><select id="reader-zoom" aria-label="两侧页面缩放">${[75,100,125,150,175,200].map(z=>`<option value="${z}" ${z===state.zoom?'selected':''}>${z}%</option>`).join('')}</select><button class="icon-button" data-reader-action="zoom-in" aria-label="放大页面">＋</button><span class="continuous-mode-label">连续滚动 · 左右对齐</span><label class="paragraph-link-toggle"><input id="paragraph-link-toggle" type="checkbox" checked>段落联动</label><button id="paragraph-retranslate" class="text-button paragraph-retranslate" data-reader-action="retranslate" title="重新翻译全文并生成段落对应信息" hidden>重新翻译</button></div><div class="reader-context"><span id="reading-status" class="pill">${statuses[membership().status]||'待读'}</span><label>项目<select id="reader-project" aria-label="当前阅读项目">${memberships(state.paper).map(m=>`<option value="${esc(m.project_id)}" ${m.project_id===state.projectId?'selected':''}>${esc(projectPath(m.project_id)||m.project_name||m.project_id)}</option>`).join('')}</select></label></div></div>
      <div class="reader-columns continuous-columns"><section class="document-pane" aria-label="原文与译文连续阅读"><div class="document-column-labels"><div><strong><span class="column-dot original"></span>原文</strong><span>原始 PDF · SVG</span></div><div><strong><span class="column-dot translated"></span>译文</strong><span id="translation-progress"></span><button class="text-button" data-reader-action="translate">逐页翻译</button><button class="text-button" data-reader-action="upload-translation">导入译文</button><a id="translation-download" class="text-button" href="/api/papers/${pid}/file?side=translation" download hidden>下载完整译文 ↓</a></div></div><div id="document-scroll" class="document-scroll" tabindex="0" aria-label="连续 PDF 页面滚动区"><div id="document-pages" class="document-pages"></div></div></section>
      <aside class="reading-sidebar" aria-label="AI 与阅读笔记"><div class="reading-tabs"><button data-reader-tab="notes" class="active">${icons.note}阅读笔记 <span id="note-count">${state.notes.length}</span></button><button data-reader-tab="ai">${icons.spark}AI 工作台</button></div><div class="reading-sidebar-body" id="reading-sidebar-body"></div></aside></div>
      <footer class="reader-footer"><span id="reader-hint" role="status" aria-live="polite">单击段落可定位对应译文，拖选词句可记录精确笔记。</span><span>译文逐页就绪 · 当前项目独立保存</span></footer><input id="translation-file" type="file" accept="application/pdf,.pdf" hidden></div>`;
    qs('#document-pages').innerHTML=state.manifest.map(p=>`<section class="page-pair" data-pair-page="${p.page}" aria-label="第 ${p.page} 页"><article class="page-slot"><div class="pair-page-label">原文 · ${p.page}</div><div id="page-original-${p.page}" class="pdf-frame" style="aspect-ratio:${p.width}/${p.height}"></div></article><article class="page-slot"><div class="pair-page-label">译文 · ${p.page}</div><div id="page-translation-${p.page}" class="pdf-frame" style="aspect-ratio:${p.width}/${p.height}"></div></article></section>`).join('');
    for(const element of app.querySelectorAll('.page-pair'))state.pairs.set(Number(element.dataset.pairPage),element);
    const scroll=qs('#document-scroll'); scroll.addEventListener('scroll',handleScroll,{passive:true});
    observer=new IntersectionObserver(entries=>{
      for(const entry of entries){const page=Number(entry.target.dataset.pairPage);if(entry.isIntersecting){state.near.add(page);void loadPair(page);}else{state.near.delete(page);unloadPair(page);}}
    },{root:scroll,rootMargin:'1000px 0px',threshold:0});
    for(const element of state.pairs.values())observer.observe(element);
    renderSidebar(); updateToolbar(); updateTranslationProgress();
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
    if(!focusVisible(focus))return;
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
    if(state.disposed||!state.paragraphEnabled||!focus)return;
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
    if(!state.paragraphEnabled)return;
    const info=state.pages.get(pageKey(side,page));if(!info)return;
    state.paragraphSequence++;
    for(const controller of state.paragraphRequests.values())controller.abort();state.paragraphRequests.clear();
    state.paragraphFocus={side,page,source_sha:info.source_sha,translation_run_id:info.translation_run_id||null,...probe};
    state.paragraphMatches=[];state.paragraphResolvedKey=null;
    renderParagraphHighlights();paragraphHint('正在定位对应段落…');void refreshParagraphFocus({scroll});
  }
  function pageProtected(page) {
    if(Number(state.selection?.page)===page)return true;
    const selection=window.getSelection();const row=state.pairs.get(page);
    return !!(row && selection && !selection.isCollapsed && (row.contains(selection.anchorNode)||row.contains(selection.focusNode)));
  }
  function unloadPair(page) {
    if(pageProtected(page))return;
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
      host.innerHTML=`<div class="page-state translation-state failed"><strong>第 ${page} 页翻译失败</strong><p>${esc(item?.error||'这一页未生成，其他已完成页面仍可阅读。')}</p>${retryJob?`<button class="button small" data-job-retry="${esc(retryJob.id)}">重试未完成页面</button>`:'<p>其他页面处理结束后，可在 AI 工作台重试失败任务。</p>'}</div>`;
    }else{
      const label={running:'正在翻译这一页',queued:'等待逐页翻译',cancelled:'翻译已取消',interrupted:'翻译已中断',ready:'正在载入矢量译文'}[status]||'等待译文';
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
    const svgUrl=typeof info.svg_url==='string'&&info.svg_url.startsWith('/api/')?info.svg_url:`/api/papers/${pid}/pages/${page}.svg?side=${side}`;
    const fitWidth=Math.min(1,(layout.height/layout.width)/(height/width))*100;
    state.pages.set(pageKey(side,page),info);
    const textItems=Array.isArray(info.text)?info.text:[];
    host.innerHTML=`<div class="pdf-page" data-page="${page}" data-side="${side}" style="width:${fitWidth}%;aspect-ratio:${width}/${height}"><img class="pdf-vector" src="${esc(svgUrl)}" alt="${side==='original'?'原文':'译文'} PDF 第 ${page} 页" draggable="false"><div class="paragraph-focus-layer" aria-hidden="true"></div><div class="highlight-layer" aria-hidden="true"></div><div class="text-layer" tabindex="0" aria-label="第 ${page} 页可选择的${side==='original'?'原文':'译文'}文本">${textItems.map((item,index)=>{
      const text=String(item.text||''),fontSize=Math.max(.1,Number(item.font_size)||Number(item.height)||10),family=fontFamily(item);
      textContext.font=`${fontSize}px ${family}`;const measured=textContext.measureText(text).width;
      const scale=measured>0?Math.max(.02,Number(item.width)/measured):1,rotation=Number(item.rotation)||0;
      const next=textItems[index+1],separator=next?(Math.abs(Number(next.y)-Number(item.y))>fontSize*.35?'\n':Number(next.x)>Number(item.x)+Number(item.width)+.7?' ':''):'';
      return `<span style="left:${Number(item.x)/width*100}%;top:${Number(item.y)/height*100}%;font-family:${family};font-size:${fontSize/width*100}cqw;transform:rotate(${rotation}deg) scaleX(${scale});" data-text-span>${esc(text)}</span>${separator}`;
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
        if(!selected&&point&&point.x>=0&&point.x<=1&&point.y>=0&&point.y<=1)focusParagraph(side,page,{point});
      },0);
    });
    element.addEventListener('keyup',event=>{if(event.key==='Shift'||(event.key.startsWith('Arrow')&&!event.shiftKey))captureSelection(element);});
    host.querySelector('.pdf-vector').addEventListener('error',()=>{
      if(element.isConnected&&host.contains(element))host.insertAdjacentHTML('beforeend',`<div class="page-render-error">矢量页加载失败。<button class="text-button" data-page-reload="${page}" data-page-side="${side}">重新加载</button></div>`);
    },{once:true});
    renderHighlights();renderParagraphHighlights();
  }
  async function loadPageSide(side,page,{force=false}={}) {
    const host=pageHost(side,page),key=pageKey(side,page),identity=pageIdentity(side,page);
    if(!host || state.disposed)return;
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
  async function loadPair(page) { await Promise.all(['original','translation'].map(side=>loadPageSide(side,page))); }
  function captureSelection(element) {
    const selection=window.getSelection();if(!selection || selection.isCollapsed || !selection.rangeCount)return false;
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
    focusParagraph(side,page,{rects});
    // Viewing another linked paragraph must not replace an unsaved note or the
    // user's native text selection. The existing composer retains its draft.
    if(!canReplaceDraft({focus:false}))return true;
    state.selection={project_id:state.projectId,side,page,quote,rects,source_sha:info.source_sha,translation_run_id:info.translation_run_id||null};
    state.draft='';state.editId=null;state.tab='notes';renderSidebar();renderHighlights();
    return true;
  }
  function renderHighlights() {
    for(const element of app.querySelectorAll('.pdf-page')){
      const side=element.dataset.side,page=Number(element.dataset.page),layer=element.querySelector('.highlight-layer');
      const notes=state.notes.filter(n=>n.side===side&&Number(n.page)===page&&isCurrentAnchor(n));
      const pending=state.selection?.side===side&&state.selection.page===page&&isCurrentAnchor(state.selection)?state.selection:null;
      const rects=notes.flatMap(n=>(n.rects||[]).map(r=>({...r,color:n.color||'yellow',pending:false})));
      if(pending&&!state.editId)rects.push(...pending.rects.map(r=>({...r,color:state.color,pending:true})));
      layer.innerHTML=rects.map(r=>`<span class="highlight ${['yellow','green','blue','pink'].includes(r.color)?r.color:'yellow'} ${r.pending?'pending':''}" style="left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%"></span>`).join('');
    }
  }
  function composer(){
    if(!state.selection)return `<div class="note-instruction"><span>${icons.note}</span><strong>读到有启发的地方，留下笔记。</strong><p>在左侧任一 PDF 中拖选词语或句子。批注会记录原句、页码和位置，刷新后仍可回看。</p></div>`;
    const s=state.selection;
    const outdated=!isCurrentAnchor(s),cannotSave=state.saving||(outdated&&!state.editId);
    return `<section class="note-composer"><div class="composer-heading"><strong>${state.editId?'编辑批注':'新的划线笔记'}</strong><span>${s.side==='original'?'原文':'译文'} · 第 ${s.page} 页</span></div>${outdated?'<div class="notice compact">这段选区来自旧版 PDF，原句与草稿仍保留。'+(state.editId?'可以继续编辑这条历史笔记。':'请复制草稿并取消选区，在新版页面重新选择后保存。')+'</div>':''}<blockquote>${esc(s.quote)}</blockquote><label class="note-label" for="note-draft">你的理解、疑问或一句话摘要</label><textarea id="note-draft" rows="5" maxlength="20000" placeholder="这句话说明了什么？与我的研究有什么关系？">${esc(state.draft)}</textarea><div class="color-choices" role="group" aria-label="高亮颜色">${[['yellow','黄色'],['green','绿色'],['blue','蓝色'],['pink','粉色']].map(([key,name])=>`<button class="color-choice ${key} ${state.color===key?'selected':''}" aria-label="${name}高亮" aria-pressed="${state.color===key}" data-note-color="${key}"></button>`).join('')}<span class="grow"></span><button class="text-button" data-reader-action="discard-selection">取消</button></div><div class="composer-actions"><button class="button primary" data-reader-action="save-note" ${cannotSave?'disabled':''}>${state.saving?'正在保存…':'保存笔记'}</button><button class="button subtle" data-reader-action="summarize-selection" ${cannotSave?'disabled':''}>${icons.spark}保存并提炼</button></div></section>`;
  }
  function notesArea(){
    const sorted=[...state.notes].sort((a,b)=>Number(a.page)-Number(b.page)||(a.created_at||'').localeCompare(b.created_at||''));
    return `${composer()}<div class="notes-section-heading"><h2>项目笔记 <span>${state.notes.length}</span></h2><a class="text-button" href="/api/papers/${pid}/notes.md?project_id=${encodeURIComponent(state.projectId)}" download>导出 Markdown ↓</a></div><div class="note-list">${sorted.map(n=>`<article class="note-card"><div class="note-card-meta"><button class="quote-location" data-note-jump="${esc(n.id)}">${n.side==='translation'?'译文':'原文'} · 第 ${n.page} 页 ↗</button><span class="small muted">${isCurrentAnchor(n)?'已保存':'旧版本批注'}</span></div><button class="note-quote ${esc(n.color||'yellow')}" data-note-jump="${esc(n.id)}">${esc(n.quote)}</button>${n.note?`<p class="note-body">${esc(n.note)}</p>`:'<p class="note-body muted">仅高亮，还没有补充个人笔记。</p>'}<div class="note-actions"><button class="text-button" data-note-edit="${esc(n.id)}">编辑</button><button class="text-button" data-note-summary="${esc(n.id)}">AI 提炼</button><button class="text-button danger" data-note-delete="${esc(n.id)}">删除</button></div></article>`).join('')||'<p class="empty-notes">还没有笔记。从一处划线开始。</p>'}</div>`;
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
    return `<div class="ai-intro"><span class="eyebrow">READ WITH EVIDENCE</span><h2>让 AI 帮你读懂，<br>也帮你整理。</h2><p>导读与摘要读取论文材料；读后总结只使用当前项目的笔记快照。</p></div>${!state.capabilities.ai_configured?'<div class="notice compact">AI 模型尚未配置。请在服务端设置 AI_BASE_URL、AI_MODEL，以及服务需要时的 AI_API_KEY。笔记和 PDF 阅读不受影响。</div>':`<div class="ai-model"><span class="status-dot on"></span>${esc(state.capabilities.ai_model||'模型已配置')}</div>`}<div class="ai-actions"><button class="button" data-ai-kind="guide">${icons.book}论文导读</button><label for="brief-mode">摘要与粗读模式</label><div class="brief-action"><select id="brief-mode">${[['abstract','精简摘要'],['cards','卡片粗读'],['summary','全文总结式粗读'],['all','全部层次']].map(([k,v])=>`<option value="${k}" ${state.mode===k?'selected':''}>${v}</option>`).join('')}</select><button class="button primary" data-ai-kind="brief">生成</button></div><button class="button" data-ai-kind="translate">${icons.spark}逐页翻译全文</button><button class="button" data-ai-kind="notes-summary">${icons.note}总结当前项目笔记</button></div><div class="notes-section-heading"><h2>任务与成果</h2><button class="text-button" data-reader-action="refresh-jobs">刷新</button></div><div class="job-history">${state.jobs.map(j=>`<button class="job-history-row ${current?.id===j.id?'active':''}" data-job-select="${esc(j.id)}"><span>${esc(jobLabels[j.kind]||j.kind)}</span><small class="job-state ${esc(j.status)}">${esc(jobStatuses[j.status]||j.status)}</small></button>`).join('')||'<p class="empty-notes">选择一个阅读任务，完成的内容会保存在这里。</p>'}</div>${current?`<article class="job-detail"><div class="job-detail-heading"><strong>${esc(jobLabels[current.kind]||current.kind)}</strong><span class="pill">${esc(jobStatuses[current.status]||current.status)}</span></div>${current.stage?`<p class="job-stage">${esc(current.stage)}</p>`:''}${current.error?`<div class="error-panel compact">${esc(current.error)}</div>`:''}${current.result_markdown?`<div class="ai-markdown">${markdown(current.result_markdown)}</div><button class="button small" data-job-download="${esc(current.id)}">导出这份结果 ↓</button>`:current.status==='succeeded'?'<p class="small muted">任务已完成。全文翻译可在中间一栏阅读。</p>':'<p class="small muted">摘要在任务完成后显示；译文会在每页完成后直接填入左侧阅读区。</p>'}<div class="job-detail-actions">${['queued','running'].includes(current.status)?`<button class="text-button danger" data-job-cancel="${esc(current.id)}">取消任务</button>`:''}${['failed','interrupted','cancelled'].includes(current.status)?`<button class="button small" data-job-retry="${esc(current.id)}">重新执行</button>`:''}</div></article>`:''}`;
  }
  function renderSidebar() {
    const body=qs('#reading-sidebar-body');if(!body)return;
    const oldScroll=body.scrollTop,focused=document.activeElement;
    const focusId=body.contains(focused)?focused.id:null;
    const caret=focusId==='note-draft'?[focused.selectionStart,focused.selectionEnd]:null;
    body.innerHTML=state.tab==='notes'?notesArea():aiArea();
    body.scrollTop=oldScroll;
    qs('#note-count').textContent=state.notes.length;
    for(const button of app.querySelectorAll('[data-reader-tab]')){button.classList.toggle('active',button.dataset.readerTab===state.tab);button.setAttribute('aria-pressed',String(button.dataset.readerTab===state.tab));}
    if(focusId){const field=document.getElementById(focusId);if(field){field.focus({preventScroll:true});if(caret)field.setSelectionRange(...caret);}}
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
      const body={...state.selection,note:state.draft,color:state.color};
      const saved=state.editId?await api('/annotations/'+encodeURIComponent(state.editId),{method:'PATCH',body:{note:state.draft,color:state.color}}):await api(`/papers/${pid}/annotations`,{method:'POST',body});
      state.selection=null;state.draft='';state.editId=null;window.getSelection()?.removeAllRanges();await refreshNotes();prunePages();toast('笔记已保存，并保留原文位置。');return saved;
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
      if(state.near.has(page)||pageProtected(page))work.push(loadPageSide('translation',page,{force:true}));
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
      await refreshTranslationPages(manifest);
      if(state.tab==='ai')renderSidebar();
      // A job can become retryable without changing the failed page's status.
      for(const page of state.near){if(translationPage(page)?.status==='failed')placeholder('translation',page);}
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
    if(kind==='translate'&&!state.capabilities.translation_configured)throw new Error('翻译服务尚未配置。可先点击“导入译文”使用已有单语译文 PDF。');
    if(kind!=='translate'&&!state.capabilities.ai_configured){state.tab='ai';renderSidebar();throw new Error('AI 模型尚未配置，笔记已保存，可先导出 Markdown。');}
    const body={paper_id:paperId,project_id:state.projectId,kind};if(kind==='brief')body.mode=state.mode;if(annotationId)body.annotation_id=annotationId;
    if(kind==='translate'&&force)body.force=true;
    const job=await api('/jobs',{method:'POST',body});
    if(kind==='translate'&&force)paragraphHint(job.reused?'翻译已在进行中，完成一页后会更新译文与段落对应信息。':'已重新提交全文翻译，完成一页后会更新译文与段落对应信息。');
    state.selectedJob=job.id;state.tab='ai';await refreshJobs();renderSidebar();toast(job.reused&&kind==='translate'&&force?'翻译已在进行中，可以继续阅读。':'任务已提交，可以继续阅读。');return job;
  }
  async function jumpToNote(note) {
    if(!note)return;
    if(!isCurrentAnchor(note)){toast('这条笔记属于旧版 PDF，原句已保留，不会跳转到当前版本的相同位置。');return;}
    await goToPage(note.page,{remember:false});
    const scroll=qs('#document-scroll'),element=pageHost(note.side,note.page)?.querySelector('.pdf-page'),rect=note.rects?.[0];
    if(element&&rect){scroll.scrollTop+=element.getBoundingClientRect().top-scroll.getBoundingClientRect().top+rect.y*element.clientHeight-scroll.clientHeight*.3;readPosition();}
    await persistPosition();
  }
  app.addEventListener('input',event=>{if(event.target.id==='note-draft')state.draft=event.target.value;});
  app.addEventListener('change',async event=>{
    try{
      if(event.target.id==='paragraph-link-toggle'){
        state.paragraphEnabled=event.target.checked;
        clearParagraphFocus(state.paragraphEnabled?'段落联动已开启。单击段落可定位另一侧，划词仍保留精确笔记。':'段落联动已关闭，划词与笔记仍可使用。');
      }
      if(event.target.id==='reader-page')await goToPage(event.target.value);
      if(event.target.id==='reader-zoom')await setZoom(event.target.value);
      if(event.target.id==='brief-mode')state.mode=event.target.value;
      if(event.target.id==='reader-project'){
        if(!canReplaceDraft()){event.target.value=state.projectId;return;}
        const nextProject=event.target.value;event.target.disabled=true;
        try{readPosition();if(!await persistPosition()){event.target.value=state.projectId;return;}location.href=`/read/${pid}?project=${encodeURIComponent(nextProject)}`;}finally{event.target.disabled=false;}
      }
      if(event.target.id==='translation-file'){
        const file=event.target.files[0];if(!file)return;
        if(file.size>50*1024*1024)throw new Error('译文 PDF 上限为 50 MB。');
        const data=new FormData();data.set('file',file);toast('正在保存译文 PDF…');
        state.paper=await api('/papers/'+pid+'/translation',{method:'POST',body:data});
        await refreshTranslationPages();toast('译文已就绪，原文位置和笔记草稿均保留。');event.target.value='';
      }
    }catch(error){toast(error.message);}
  });
  app.addEventListener('click',async event=>{
    const link=event.target.closest('a.reader-back');
    if(link){event.preventDefault();if(!canReplaceDraft())return;readPosition();if(await persistPosition())location.href=link.href;return;}
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.readerTab){state.tab=button.dataset.readerTab;renderSidebar();return;}
      if(button.dataset.noteColor){state.color=button.dataset.noteColor;renderSidebar();renderHighlights();return;}
      if(button.dataset.aiKind){button.disabled=true;await createJob(button.dataset.aiKind);return;}
      if(button.dataset.jobSelect){state.selectedJob=button.dataset.jobSelect;renderSidebar();return;}
      if(button.dataset.jobCancel){await api('/jobs/'+encodeURIComponent(button.dataset.jobCancel)+'/cancel',{method:'POST'});await refreshJobs();return;}
      if(button.dataset.jobRetry){button.disabled=true;const result=await api('/jobs/'+encodeURIComponent(button.dataset.jobRetry)+'/retry',{method:'POST'});state.selectedJob=result.id;await refreshJobs();return;}
      if(button.dataset.jobDownload){const job=state.jobs.find(j=>j.id===button.dataset.jobDownload);if(!job)return;const blob=new Blob([job.result_markdown||''],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${job.kind}-${job.id}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
      if(button.dataset.noteJump){await jumpToNote(state.notes.find(n=>n.id===button.dataset.noteJump));return;}
      if(button.dataset.noteEdit){
        if(state.editId===button.dataset.noteEdit){qs('#note-draft')?.focus();return;}if(!canReplaceDraft())return;
        const note=state.notes.find(n=>n.id===button.dataset.noteEdit);if(!note)return;
        state.selection={project_id:state.projectId,side:note.side,page:note.page,quote:note.quote,rects:note.rects,source_sha:note.source_sha,translation_run_id:note.translation_run_id||null};
        state.draft=note.note||'';state.color=note.color||'yellow';state.editId=note.id;state.tab='notes';renderSidebar();qs('#note-draft')?.focus();return;
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
        case'prev':await goToPage(state.page-1);break;
        case'next':await goToPage(state.page+1);break;
        case'zoom-in':case'zoom-out':await setZoom(state.zoom+(button.dataset.readerAction==='zoom-in'?25:-25));break;
        case'upload-translation':qs('#translation-file').click();break;
        case'translate':button.disabled=true;await createJob('translate');break;
        case'retranslate':button.disabled=true;await createJob('translate',undefined,true);break;
        case'discard-selection':state.selection=null;state.draft='';state.editId=null;window.getSelection()?.removeAllRanges();renderSidebar();renderHighlights();prunePages();break;
        case'save-note':await saveNote();break;
        case'summarize-selection':{const note=await saveNote();await createJob('selection-summary',note.id);break;}
        case'refresh-jobs':await refreshJobs();break;
        case'finish':
          button.disabled=true;if(state.selection)await saveNote();readPosition();
          if(!await persistPosition('read'))throw new Error('阅读状态保存失败，请重试。笔记仍已保留。');
          state.tab='ai';renderSidebar();
          if(state.notes.length)await createJob('notes-summary');else toast('已标记为已读。当前项目还没有笔记，暂未创建笔记总结任务。');break;
      }
    }catch(error){toast(error.message);}finally{if(button.isConnected&&!['prev','next'].includes(button.dataset.readerAction))button.disabled=false;}
  });
  window.addEventListener('beforeunload',event=>{if(hasUnsavedDraft()){event.preventDefault();event.returnValue='';}});
  const lifecycle=createReaderLifecycle(()=>{
    state.disposed=true;refreshSequence++;refreshing=false;
    observer?.disconnect();clearTimeout(saveTimer);clearTimeout(state.pollTimer);
    if(scrollFrame)cancelAnimationFrame(scrollFrame);scrollFrame=null;
    for(const controller of state.requests.values())controller.abort();
    state.requests.clear();
    for(const controller of state.paragraphRequests.values())controller.abort();
    state.paragraphRequests.clear();
  },()=>{
    state.disposed=false;
    if(!observer||!state.pairs.size)return;
    state.near.clear();
    for(const element of state.pairs.values())observer.observe(element);
    readPosition();void loadPair(state.page);void refreshJobs();void refreshParagraphFocus();
  });
  window.addEventListener('pagehide',lifecycle.hide);
  window.addEventListener('pageshow',lifecycle.show);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&!state.disposed){readPosition();void persistPosition();}});
  try{
    const [paper,projects,capabilities,manifest,translations]=await Promise.all([
      api('/papers/'+pid),api('/projects'),api('/capabilities').catch(()=>({})),
      api(`/papers/${pid}/page-manifest`),api(`/papers/${pid}/translation-pages`)
    ]);
    state.paper=paper;state.projects=list(projects);state.capabilities=capabilities;
    state.manifest=manifest.pages;state.translations=translations;
    if(!Array.isArray(state.manifest)||state.manifest.length!==paper.page_count)throw new Error('PDF 页清单不完整，请重新打开论文。');
    const ms=memberships(paper);if(!state.projectId||!ms.some(m=>m.project_id===state.projectId))state.projectId=ms[0]?.project_id||'';
    if(!state.projectId)throw new Error('这篇论文还没有关联项目，请先返回论文库关联一个项目。');
    history.replaceState(null,'',`/read/${pid}?project=${encodeURIComponent(state.projectId)}`);
    state.page=clampPage(membership().last_page,paper.page_count);state.pageOffset=clampOffset(membership().page_offset);
    state.notes=list(await api(notesUrl()));shell();
    await goToPage(state.page,{offset:state.pageOffset,remember:false});await refreshJobs();
  }catch(error){
    state.disposed=true;observer?.disconnect();
    app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1>暂时无法打开论文</h1><p>${esc(error.message)}</p><a class="button primary" href="/?view=library">返回论文库</a></main>`;
  }
}
