import {api,esc,qs,list,toast,memberships,statuses,jobLabels,jobStatuses,icons} from './app.js';

export async function mountReader({paperId,projectId}) {
  const app=qs('#app');
  const state={paper:null,projects:[],projectId:projectId||'',page:1,zoom:100,sync:true,side:'original',notes:[],jobs:[],capabilities:{},tab:'notes',selection:null,draft:'',color:'yellow',editId:null,selectedJob:null,pages:{},loadSequence:0,pollTimer:null,mode:'abstract',saving:false};
  const pid=encodeURIComponent(paperId);
  let ignoreScrollUntil=0;
  let positionQueue=Promise.resolve(true);
  const finishedProjects=new Set();
  const projectPath=id=>{
    const names=[],seen=new Set();let p=state.projects.find(x=>x.id===id);
    while(p&&!seen.has(p.id)){seen.add(p.id);names.unshift(p.name);p=state.projects.find(x=>x.id===p.parent_id)}
    return names.join(' / ');
  };
  const membership=()=>memberships(state.paper||{}).find(m=>m.project_id===state.projectId)||{};
  const notesUrl=()=>`/papers/${pid}/annotations?project_id=${encodeURIComponent(state.projectId)}`;
  function currentSource(side){return side==='translation'?state.paper?.translated_sha256:state.paper?.sha256}
  function isCurrentAnchor(anchor){return !!anchor&&anchor.is_current!==false&&anchor.source_sha===currentSource(anchor.side)}
  function hasUnsavedDraft(){
    if(state.saving)return true;
    if(!state.selection)return false;
    if(!state.editId)return true;
    const saved=state.notes.find(n=>n.id===state.editId);
    return !saved||state.draft!==(saved.note||'')||state.color!==(saved.color||'yellow');
  }
  function canReplaceDraft(){
    if(!hasUnsavedDraft())return true;
    toast(state.saving?'正在保存批注，请稍候。':'请先保存或取消右侧正在编辑的批注，原稿已保留。');
    state.tab='notes';renderSidebar();qs('#note-draft')?.focus();return false;
  }
  function shell() {
    document.title=`${state.paper.title} · PaperStudio`;
    app.innerHTML=`<div class="reader-app"><header class="reader-header"><a class="reader-back" href="/?project=${encodeURIComponent(state.projectId)}" title="返回论文库" aria-label="返回论文库">←</a><div class="reader-heading"><div class="eyebrow">PAPERSTUDIO / READING</div><h1>${esc(state.paper.title)}</h1><span>${esc(projectPath(state.projectId)||'当前项目')}</span></div><div class="reader-header-actions"><a class="button small subtle" href="/api/papers/${pid}/file?side=original" target="_blank" rel="noopener">原始 PDF ↗</a><button class="button primary small" data-reader-action="finish">${icons.note}读完，整理笔记</button></div></header><div class="reader-toolbar"><div class="reader-page-controls"><button class="icon-button" data-reader-action="prev" aria-label="上一页">‹</button><label>第 <input id="reader-page" aria-label="当前页码" type="number" min="1" max="${state.paper.page_count}" value="${state.page}"> / ${state.paper.page_count} 页</label><button class="icon-button" data-reader-action="next" aria-label="下一页">›</button></div><div class="reader-zoom-controls"><button class="icon-button" data-reader-action="zoom-out" aria-label="缩小页面">−</button><select id="reader-zoom" aria-label="两侧页面缩放">${[75,100,125,150,175,200].map(z=>`<option value="${z}" ${z===state.zoom?'selected':''}>${z}%</option>`).join('')}</select><button class="icon-button" data-reader-action="zoom-in" aria-label="放大页面">＋</button><label class="check-label"><input type="checkbox" id="reader-sync" ${state.sync?'checked':''}>同步滚动</label></div><div class="reader-context"><span id="reading-status" class="pill">${statuses[membership().status]||'待读'}</span><label>项目<select id="reader-project" aria-label="当前阅读项目">${memberships(state.paper).map(m=>`<option value="${esc(m.project_id)}" ${m.project_id===state.projectId?'selected':''}>${esc(projectPath(m.project_id)||m.project_name||m.project_id)}</option>`).join('')}</select></label></div></div><div class="reader-columns"><section class="pdf-column" aria-label="原文 PDF"><header class="pdf-column-header"><strong><span class="column-dot original"></span>原文</strong><span>原始 PDF · 矢量呈现</span></header><div class="pdf-scroll" data-side="original" tabindex="0" aria-label="原文页面滚动区"><div class="pdf-page-host" id="page-original"></div></div></section><section class="pdf-column translated-column" aria-label="译文 PDF"><header class="pdf-column-header"><strong><span class="column-dot translated"></span>译文</strong><div class="actions"><span>PDF → SVG</span><button class="text-button" data-reader-action="upload-translation">导入译文</button></div></header><div class="pdf-scroll" data-side="translation" tabindex="0" aria-label="译文页面滚动区"><div class="pdf-page-host" id="page-translation"></div></div></section><aside class="reading-sidebar" aria-label="AI 与阅读笔记"><div class="reading-tabs"><button data-reader-tab="notes" class="active">${icons.note}阅读笔记 <span id="note-count">${state.notes.length}</span></button><button data-reader-tab="ai">${icons.spark}AI 工作台</button></div><div class="reading-sidebar-body" id="reading-sidebar-body"></div></aside></div><footer class="reader-footer"><span id="reader-hint">在原文或译文中拖选词句，即可创建带页码与位置的笔记。</span><span>当前项目独立保存 · 原文件不改写</span></footer><input id="translation-file" type="file" accept="application/pdf,.pdf" hidden></div>`;
    for(const scroller of app.querySelectorAll('.pdf-scroll'))scroller.addEventListener('scroll',syncScroll,{passive:true});
    renderSidebar(); updateToolbar();
  }
  function updateToolbar(){
    const input=qs('#reader-page');if(input)input.value=state.page;
    qs('[data-reader-action=prev]').disabled=state.page<=1;
    qs('[data-reader-action=next]').disabled=state.page>=state.paper.page_count;
    qs('#reader-zoom').value=String(state.zoom);
    qs('#reading-status').textContent=statuses[membership().status]||'待读';
  }
  function syncScroll(event){
    if(!state.sync||performance.now()<ignoreScrollUntil)return;
    const from=event.currentTarget,to=qs(`.pdf-scroll[data-side="${from.dataset.side==='original'?'translation':'original'}"]`);if(!to)return;
    const fraction=from.scrollTop/Math.max(1,from.scrollHeight-from.clientHeight);
    ignoreScrollUntil=performance.now()+55;
    to.scrollTop=fraction*Math.max(0,to.scrollHeight-to.clientHeight);
  }
  function pagePlaceholder(side,message='正在读取 PDF 页面…'){
    const host=qs('#page-'+side);host.innerHTML=`<div class="pdf-placeholder"><div class="loading-ring"></div><p>${esc(message)}</p></div>`;
  }
  function translationPlaceholder(){
    qs('#page-translation').innerHTML=`<div class="translation-empty"><div class="translation-symbol">文<span>⇄</span>A</div><div class="eyebrow">READ SIDE BY SIDE</div><h2>译文按需准备</h2><p>原文已可阅读。导入对应的单语译文 PDF，即可在这里按原页并排查看。</p><button class="button primary" data-reader-action="upload-translation">导入译文 PDF</button><button class="button subtle" data-reader-action="translate">${icons.spark}生成全文翻译</button><small>${state.capabilities.translation_configured?'BabelDOC 已配置，点击后创建后台任务。':'BabelDOC 尚未配置。已有译文可直接导入。'}<br>第一版要求原文与译文页数一致。</small></div>`;
  }
  function fontFamily(item){
    const name=(item.font_family||'').toLowerCase();
    if(/times|serif|roman/.test(name))return 'Georgia, serif';
    if(/courier|mono/.test(name))return 'monospace';
    return 'Arial, sans-serif';
  }
  const textCanvas=document.createElement('canvas'), textContext=textCanvas.getContext('2d');
  function renderPdf(side,page){
    state.pages[side]=page;
    const host=qs('#page-'+side);
    const width=Number(page.width),height=Number(page.height);
    if(!width||!height){host.innerHTML='<div class="error-panel">PDF 页面尺寸无效。</div>';return;}
    const base=`/api/papers/${pid}/pages/${state.page}.svg?side=${side}`;
    const svgUrl=typeof page.svg_url==='string'&&page.svg_url.startsWith('/api/')?page.svg_url:base;
    const textItems=Array.isArray(page.text)?page.text:[];
    host.innerHTML=`<div class="pdf-page" data-page="${state.page}" data-side="${side}" style="width:${state.zoom}%;aspect-ratio:${width}/${height}"><img class="pdf-vector" src="${esc(svgUrl)}" alt="${side==='original'?'原文':'译文'} PDF 第 ${state.page} 页" draggable="false"><div class="highlight-layer" aria-hidden="true"></div><div class="text-layer" aria-label="可选择的${side==='original'?'原文':'译文'}文本">${textItems.map((item,index)=>{
      const text=String(item.text||'');const fontSize=Math.max(.1,Number(item.font_size)||Number(item.height)||10),family=fontFamily(item);
      textContext.font=`${fontSize}px ${family}`;
      const measured=textContext.measureText(text).width;
      const scale=measured>0?Math.max(.02,Number(item.width)/measured):1;
      const rotation=Number(item.rotation)||0;
      const next=textItems[index+1];const separator=next?(Math.abs(Number(next.y)-Number(item.y))>fontSize*.35?'\n':Number(next.x)>Number(item.x)+Number(item.width)+.7?' ':''):'';
      return `<span style="left:${Number(item.x)/width*100}%;top:${Number(item.y)/height*100}%;font-family:${family};font-size:${fontSize/width*100}cqw;transform:rotate(${rotation}deg) scaleX(${scale});" data-text-span>${esc(text)}</span>${separator}`;
    }).join('')}</div></div>${page.has_text===false?'<div class="page-text-warning">这一页没有可提取文字层。可以清晰查看原页，划词批注需要先做 OCR。</div>':''}`;
    const img=host.querySelector('.pdf-vector');img.addEventListener('error',()=>{toast('PDF 矢量页加载失败，请重新打开当前页。');});
    const element=host.querySelector('.pdf-page');
    element.addEventListener('pointerup',()=>setTimeout(()=>captureSelection(element),0));
    element.addEventListener('keyup',event=>{if(event.key==='Shift'||event.key.startsWith('Arrow'))captureSelection(element);});
    renderHighlights();
  }
  async function loadPage(page,{remember=true}={}){
    const count=Number(state.paper.page_count)||1;state.page=Math.min(count,Math.max(1,Math.floor(Number(page)||1)));
    const generation=++state.loadSequence;state.pages={};updateToolbar();pagePlaceholder('original');
    const tasks=[['original',api(`/papers/${pid}/pages/${state.page}?side=original`)]];
    if(state.paper.translated_filename){pagePlaceholder('translation');tasks.push(['translation',api(`/papers/${pid}/pages/${state.page}?side=translation`)]);}else translationPlaceholder();
    await Promise.all(tasks.map(async([side,promise])=>{try{const result=await promise;if(generation!==state.loadSequence)return;renderPdf(side,result);}catch(e){if(generation!==state.loadSequence)return;qs('#page-'+side).innerHTML=`<div class="error-panel">${esc(e.message)}<button class="button small" data-reader-action="reload-page">重试这一页</button></div>`;}}));
    if(generation!==state.loadSequence)return;
    for(const s of app.querySelectorAll('.pdf-scroll')){s.scrollTop=0;s.scrollLeft=0;}
    if(remember)await persistPosition();
  }
  function persistPosition(status){
    const projectId=state.projectId,page=state.page;
    if(status==='read')finishedProjects.add(projectId);
    const write=async()=>{
      const member=memberships(state.paper).find(m=>m.project_id===projectId)||{};
      const next=finishedProjects.has(projectId)?'read':status||member.status||'reading';
      try{
        const updated=await api(`/papers/${pid}/reading`,{method:'PATCH',body:{project_id:projectId,status:next==='unread'?'reading':next,last_page:page}});
        if(Array.isArray(state.paper.memberships)){const i=state.paper.memberships.findIndex(x=>x.project_id===projectId);if(i>=0)state.paper.memberships[i]={...state.paper.memberships[i],...updated};}
        else Object.assign(member,updated);
        updateToolbar();return true;
      }catch(e){toast('阅读位置未保存：'+e.message);return false;}
    };
    // Finish joins this same queue; a slower page write cannot revert the read status.
    positionQueue=positionQueue.then(write,write);
    return positionQueue;
  }
  function captureSelection(element){
    const selection=window.getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return;
    const range=selection.getRangeAt(0);
    if(!element.querySelector('.text-layer').contains(range.startContainer)||!element.querySelector('.text-layer').contains(range.endContainer))return;
    const quote=selection.toString().trim();if(!quote)return;
    if(quote.length>10000){toast('一次批注请选择不超过 10,000 个字符。');return;}
    if(!canReplaceDraft()){selection.removeAllRanges();return;}
    const pageRect=element.getBoundingClientRect();
    const rects=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>{
      const x=Math.max(0,(r.left-pageRect.left)/pageRect.width),y=Math.max(0,(r.top-pageRect.top)/pageRect.height);
      return {x,y,width:Math.max(0,Math.min(1-x,r.width/pageRect.width)),height:Math.max(0,Math.min(1-y,r.height/pageRect.height))};
    }).filter(r=>r.width>0&&r.height>0&&r.x<1&&r.y<1);
    if(!rects.length)return;
    const side=element.dataset.side,page=Number(element.dataset.page),info=state.pages[side];
    state.selection={project_id:state.projectId,side,page,quote,rects,source_sha:info.source_sha||currentSource(side)};
    state.draft='';state.editId=null;state.tab='notes';renderSidebar();renderHighlights();
  }
  function renderHighlights(){
    for(const pageElement of app.querySelectorAll('.pdf-page')){
      const side=pageElement.dataset.side,page=Number(pageElement.dataset.page),layer=pageElement.querySelector('.highlight-layer');
      const notes=state.notes.filter(n=>n.side===side&&Number(n.page)===page&&isCurrentAnchor(n));
      const selection=state.selection?.side===side&&state.selection.page===page&&isCurrentAnchor(state.selection)?state.selection:null;
      const rects=notes.flatMap(n=>(n.rects||[]).map(r=>({...r,color:n.color||'yellow',pending:false})));
      if(selection&&!state.editId)rects.push(...selection.rects.map(r=>({...r,color:state.color,pending:true})));
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
    return `<div class="ai-intro"><span class="eyebrow">READ WITH EVIDENCE</span><h2>让 AI 帮你读懂，<br>也帮你整理。</h2><p>导读与摘要读取论文材料；读后总结只使用当前项目的笔记快照。</p></div>${!state.capabilities.ai_configured?'<div class="notice compact">AI 模型尚未配置。请在服务端设置 AI_BASE_URL、AI_MODEL，以及服务需要时的 AI_API_KEY。笔记和 PDF 阅读不受影响。</div>':`<div class="ai-model"><span class="status-dot on"></span>${esc(state.capabilities.ai_model||'模型已配置')}</div>`}<div class="ai-actions"><button class="button" data-ai-kind="guide">${icons.book}论文导读</button><label for="brief-mode">摘要与粗读模式</label><div class="brief-action"><select id="brief-mode">${[['abstract','精简摘要'],['cards','卡片粗读'],['summary','连贯总结'],['all','全部层次']].map(([k,v])=>`<option value="${k}" ${state.mode===k?'selected':''}>${v}</option>`).join('')}</select><button class="button primary" data-ai-kind="brief">生成</button></div><button class="button" data-ai-kind="notes-summary">${icons.note}总结当前项目笔记</button></div><div class="notes-section-heading"><h2>任务与成果</h2><button class="text-button" data-reader-action="refresh-jobs">刷新</button></div><div class="job-history">${state.jobs.map(j=>`<button class="job-history-row ${current?.id===j.id?'active':''}" data-job-select="${esc(j.id)}"><span>${esc(jobLabels[j.kind]||j.kind)}</span><small class="job-state ${esc(j.status)}">${esc(jobStatuses[j.status]||j.status)}</small></button>`).join('')||'<p class="empty-notes">选择一个阅读任务，完成的内容会保存在这里。</p>'}</div>${current?`<article class="job-detail"><div class="job-detail-heading"><strong>${esc(jobLabels[current.kind]||current.kind)}</strong><span class="pill">${esc(jobStatuses[current.status]||current.status)}</span></div>${current.stage?`<p class="job-stage">${esc(current.stage)}</p>`:''}${current.error?`<div class="error-panel compact">${esc(current.error)}</div>`:''}${current.result_markdown?`<div class="ai-markdown">${markdown(current.result_markdown)}</div><button class="button small" data-job-download="${esc(current.id)}">导出这份结果 ↓</button>`:current.status==='succeeded'?'<p class="small muted">任务已完成。全文翻译可在中间一栏阅读。</p>':'<p class="small muted">生成内容在任务完成后显示。不会把等待状态当作已经读完。</p>'}<div class="job-detail-actions">${['queued','running'].includes(current.status)?`<button class="text-button danger" data-job-cancel="${esc(current.id)}">取消任务</button>`:''}${['failed','interrupted','cancelled'].includes(current.status)?`<button class="button small" data-job-retry="${esc(current.id)}">重新执行</button>`:''}</div></article>`:''}`;
  }
  function renderSidebar(){
    qs('#reading-sidebar-body').innerHTML=state.tab==='notes'?notesArea():aiArea();
    qs('#note-count').textContent=state.notes.length;
    for(const button of app.querySelectorAll('[data-reader-tab]')){button.classList.toggle('active',button.dataset.readerTab===state.tab);button.setAttribute('aria-pressed',String(button.dataset.readerTab===state.tab));}
  }
  async function refreshNotes(){state.notes=list(await api(notesUrl()));renderHighlights();if(state.tab==='notes')renderSidebar();else qs('#note-count').textContent=state.notes.length;}
  async function saveNote(){
    if(!state.selection)throw new Error('先在 PDF 中选择词语或句子。');
    if(!state.editId&&!isCurrentAnchor(state.selection))throw new Error('选区来自旧版 PDF，草稿已保留。请在新版页面重新选择词句。');
    state.saving=true;renderSidebar();
    try{
      const body={...state.selection,note:state.draft,color:state.color};
      const saved=state.editId?await api('/annotations/'+encodeURIComponent(state.editId),{method:'PATCH',body:{note:state.draft,color:state.color}}):await api(`/papers/${pid}/annotations`,{method:'POST',body});
      state.selection=null;state.draft='';state.editId=null;window.getSelection()?.removeAllRanges();await refreshNotes();toast('笔记已保存，并保留原文位置。');return saved;
    }finally{state.saving=false;renderSidebar();}
  }
  async function refreshJobs(){
    const previous=new Map(state.jobs.map(j=>[j.id,j.status]));state.jobs=list(await api(`/jobs?paper_id=${pid}&project_id=${encodeURIComponent(state.projectId)}`));
    state.jobs.sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''));
    if(state.tab==='ai')renderSidebar();
    const translation=state.jobs.find(j=>j.kind==='translate'&&j.status==='succeeded'&&previous.get(j.id)!=='succeeded');
    if(translation){state.paper=await api('/papers/'+pid);await refreshNotes();await loadPage(state.page,{remember:false});}
    clearTimeout(state.pollTimer);if(state.jobs.some(j=>['queued','running'].includes(j.status)))state.pollTimer=setTimeout(()=>refreshJobs().catch(e=>toast('任务状态读取失败：'+e.message)),1800);
  }
  async function createJob(kind,annotationId){
    if(kind==='translate'&&!state.capabilities.translation_configured)throw new Error('全文翻译服务尚未配置。可先点击“导入译文”使用已有单语译文 PDF。');
    if(kind!=='translate'&&!state.capabilities.ai_configured){state.tab='ai';renderSidebar();throw new Error('AI 模型尚未配置，笔记已保存，可先导出 Markdown。');}
    const body={paper_id:paperId,project_id:state.projectId,kind};if(kind==='brief')body.mode=state.mode;if(annotationId)body.annotation_id=annotationId;
    const job=await api('/jobs',{method:'POST',body});state.selectedJob=job.id;state.tab='ai';await refreshJobs();toast('任务已提交，可以继续阅读。');return job;
  }
  async function jumpToNote(note){
    if(!note)return;
    if(!isCurrentAnchor(note)){toast('这条笔记属于旧版 PDF，原句已保留，不会跳转到当前版本的相同位置。');return;}
    if(state.page!==Number(note.page))await loadPage(note.page);
    const scroller=qs(`.pdf-scroll[data-side="${note.side}"]`),page=qs(`#page-${note.side} .pdf-page`),rect=note.rects?.[0];
    if(page&&rect){scroller.scrollTop=Math.max(0,page.offsetTop+rect.y*page.clientHeight-scroller.clientHeight*.3);}
  }
  app.addEventListener('input',e=>{if(e.target.id==='note-draft')state.draft=e.target.value;});
  app.addEventListener('change',async e=>{
    try{
      if(e.target.id==='reader-page')await loadPage(e.target.value);
      if(e.target.id==='reader-zoom'){state.zoom=Number(e.target.value);for(const p of app.querySelectorAll('.pdf-page'))p.style.width=state.zoom+'%';updateToolbar();}
      if(e.target.id==='reader-sync')state.sync=e.target.checked;
      if(e.target.id==='brief-mode')state.mode=e.target.value;
      if(e.target.id==='reader-project'){if(!canReplaceDraft()){e.target.value=state.projectId;return;}const nextProject=e.target.value;e.target.disabled=true;try{if(!await persistPosition()){e.target.value=state.projectId;return;}location.href=`/read/${pid}?project=${encodeURIComponent(nextProject)}`;}finally{e.target.disabled=false;}}
      if(e.target.id==='translation-file'){
        const file=e.target.files[0];if(!file)return;if(file.size>40*1024*1024)throw new Error('第一版译文 PDF 上限为 40 MB。');const fd=new FormData();fd.set('file',file);toast('正在保存译文 PDF…');state.paper=await api('/papers/'+pid+'/translation',{method:'POST',body:fd});await refreshNotes();await loadPage(state.page,{remember:false});toast('译文已就绪，两侧按页对应。');e.target.value='';
      }
    }catch(err){toast(err.message);}
  });
  app.addEventListener('click',async e=>{
    const button=e.target.closest('button');if(!button)return;
    try{
      if(button.dataset.readerTab){state.tab=button.dataset.readerTab;renderSidebar();return;}
      if(button.dataset.noteColor){state.color=button.dataset.noteColor;renderSidebar();renderHighlights();return;}
      if(button.dataset.aiKind){button.disabled=true;await createJob(button.dataset.aiKind);return;}
      if(button.dataset.jobSelect){state.selectedJob=button.dataset.jobSelect;renderSidebar();return;}
      if(button.dataset.jobCancel){await api('/jobs/'+encodeURIComponent(button.dataset.jobCancel)+'/cancel',{method:'POST'});await refreshJobs();return;}
      if(button.dataset.jobRetry){const result=await api('/jobs/'+encodeURIComponent(button.dataset.jobRetry)+'/retry',{method:'POST'});state.selectedJob=result.id;await refreshJobs();return;}
      if(button.dataset.jobDownload){const job=state.jobs.find(j=>j.id===button.dataset.jobDownload);const blob=new Blob([job?.result_markdown||''],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${job.kind}-${job.id}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
      if(button.dataset.noteJump){await jumpToNote(state.notes.find(n=>n.id===button.dataset.noteJump));return;}
      if(button.dataset.noteEdit){if(state.editId===button.dataset.noteEdit){qs('#note-draft')?.focus();return;}if(!canReplaceDraft())return;const n=state.notes.find(n=>n.id===button.dataset.noteEdit);if(!n)return;state.selection={project_id:state.projectId,side:n.side,page:n.page,quote:n.quote,rects:n.rects,source_sha:n.source_sha};state.draft=n.note||'';state.color=n.color||'yellow';state.editId=n.id;renderSidebar();qs('#note-draft')?.focus();return;}
      if(button.dataset.noteDelete){if(state.editId===button.dataset.noteDelete&&!canReplaceDraft())return;await api('/annotations/'+encodeURIComponent(button.dataset.noteDelete),{method:'DELETE'});if(state.editId===button.dataset.noteDelete){state.selection=null;state.draft='';state.editId=null;}await refreshNotes();toast('批注已删除');return;}
      if(button.dataset.noteSummary){button.disabled=true;await createJob('selection-summary',button.dataset.noteSummary);return;}
      switch(button.dataset.readerAction){
        case'prev':await loadPage(state.page-1);break;
        case'next':await loadPage(state.page+1);break;
        case'reload-page':await loadPage(state.page,{remember:false});break;
        case'zoom-in':case'zoom-out':state.zoom=Math.min(200,Math.max(75,state.zoom+(button.dataset.readerAction==='zoom-in'?25:-25)));for(const p of app.querySelectorAll('.pdf-page'))p.style.width=state.zoom+'%';updateToolbar();break;
        case'upload-translation':qs('#translation-file').click();break;
        case'translate':await createJob('translate');break;
        case'discard-selection':state.selection=null;state.draft='';state.editId=null;window.getSelection()?.removeAllRanges();renderSidebar();renderHighlights();break;
        case'save-note':await saveNote();break;
        case'summarize-selection':{const note=await saveNote();await createJob('selection-summary',note.id);break;}
        case'refresh-jobs':await refreshJobs();break;
        case'finish':button.disabled=true;if(state.selection)await saveNote();if(!await persistPosition('read'))throw new Error('阅读状态保存失败，请重试。笔记仍已保留。');state.tab='ai';renderSidebar();if(state.notes.length)await createJob('notes-summary');else toast('已标记为已读。当前项目还没有笔记，暂未创建笔记总结任务。');break;
      }
    }catch(error){toast(error.message);}finally{if(button.isConnected&&!['prev','next'].includes(button.dataset.readerAction))button.disabled=false;}
  });
  window.addEventListener('beforeunload',event=>{if(hasUnsavedDraft()){event.preventDefault();event.returnValue='';}});
  try{
    const [paper,projects,capabilities]=await Promise.all([api('/papers/'+pid),api('/projects'),api('/capabilities').catch(()=>({}))]);
    state.paper=paper;state.projects=list(projects);state.capabilities=capabilities;
    const ms=memberships(paper);if(!state.projectId||!ms.some(m=>m.project_id===state.projectId))state.projectId=ms[0]?.project_id||'';
    if(!state.projectId)throw new Error('这篇论文还没有关联项目，请先返回论文库关联一个项目。');
    history.replaceState(null,'',`/read/${pid}?project=${encodeURIComponent(state.projectId)}`);
    state.page=Number(membership().last_page)||1;
    state.notes=list(await api(notesUrl()));shell();await loadPage(state.page,{remember:false});await refreshJobs();
  }catch(error){app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1>暂时无法打开论文</h1><p>${esc(error.message)}</p><a class="button primary" href="/">返回论文库</a></main>`;}
}
