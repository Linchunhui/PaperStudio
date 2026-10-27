import {mathText} from './math.js';
import {api, esc, qs, toast} from './app.js';

const names={compare:'论文对比',qa:'有据问答',explain:'解释选段',summary:'总结选段',figure:'图表讲解',formula:'公式讲解',lineage:'研究脉络',knowledge:'结构化解读',reference:'前序工作解读'};
const categories={problem:'研究问题',method:'方法组件',experiment:'实验',dataset:'数据集',limitation:'局限'};
const states={queued:'等待执行',running:'正在整理证据',succeeded:'已完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};

// Replacing a panel must not discard keyboard position or an open disclosure.
function retainInteraction(root, preserveFields=false){
  const controls=()=>Array.from(root.querySelectorAll('input, textarea, select, button, summary, a'));
  const key=el=>el.id||[el.tagName,el.getAttribute('name')||'',el.form?.id||'',JSON.stringify(el.dataset),el.getAttribute('href')||'',el.tagName==='SUMMARY'?el.parentElement.className:el.tagName==='BUTTON'&&!Object.keys(el.dataset).length?el.textContent:''].join('|');
  const before=controls(),active=root.contains(document.activeElement)?document.activeElement:null;
  const activeKey=active?key(active):null,activeIndex=active?before.filter(el=>key(el)===activeKey).indexOf(active):0;
  const selection=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
  const fields=preserveFields?before.filter(el=>el.name&&!el.readOnly).map(el=>[key(el),el.value]):[];
  const disclosures=Array.from(root.querySelectorAll('details')).map(el=>[el.className,el.querySelector('summary')?.textContent.replace(/\(\d+\)/g,'')||'',el.open]);
  const scrollTop=root.scrollTop,readerScroll=root.closest?.('.reading-sidebar-body'),readerScrollTop=readerScroll?.scrollTop;
  return ()=>{
    const after=controls();
    for(const [fieldKey,value] of fields){const field=after.find(el=>key(el)===fieldKey);if(field&&(field.tagName!=='SELECT'||Array.from(field.options).some(option=>option.value===value)))field.value=value;}
    for(const el of root.querySelectorAll('details')){const index=disclosures.findIndex(([name,label])=>name===el.className&&label===(el.querySelector('summary')?.textContent.replace(/\(\d+\)/g,'')||''));if(index>=0){el.open=disclosures[index][2];disclosures.splice(index,1);}}
    const replacement=activeKey===null?null:after.filter(el=>key(el)===activeKey)[activeIndex];
    const focusTarget=replacement&&!replacement.disabled?replacement:activeKey===null?null:after.find(el=>!el.disabled&&el.form?.id===active.form?.id&&el.tagName!=='BUTTON')||after.find(el=>!el.disabled);
    if(focusTarget){focusTarget.focus({preventScroll:true});if(focusTarget===replacement&&selection&&replacement.setSelectionRange)replacement.setSelectionRange(...selection);}
    root.scrollTop=scrollTop;if(readerScroll&&Number.isFinite(readerScrollTop))readerScroll.scrollTop=readerScrollTop;
  };
}

let current=null, timer=null, generation=0;
const readerSessions=new Map();
export function disposeResearchPanel(){clearTimeout(timer);timer=null;generation++;current=null;readerSessions.clear();}

function internalHref(citation){
  if(!citation?.paper_id||!citation?.project_id)return '';
  const query=new URLSearchParams({project:citation.project_id});
  if(['paper','figure'].includes(citation.type)&&citation.page&&citation.source_sha){query.set('page',String(citation.page));query.set('source_sha',citation.source_sha);if(Array.isArray(citation.rects))query.set('rects',JSON.stringify(citation.rects));}
  return `/read/${encodeURIComponent(citation.paper_id)}?${query}`;
}
export function citationMarkup(citation,index=0){
  const type={paper:'论文原文',note:'个人笔记',generated:'生成解读',figure:'原文图像'}[citation.type]||'来源材料';
  const referenceHref=citation.reference_asset_id?`/api/research/reference-assets/${encodeURIComponent(citation.reference_asset_id)}/file#page=${Number.isInteger(Number(citation.page))&&Number(citation.page)>0?Number(citation.page):1}`:'';
  const image=typeof citation.image_url==='string'&&/^\/api\/(?:papers|research)\/[^\s<>]+$/.test(citation.image_url)?citation.image_url:'';
  return `<details class="evidence-card"><summary><span class="evidence-number">${index+1}</span>${esc(type)} · ${esc(citation.paper_title||'项目材料')}${citation.page?' · 第 '+esc(citation.page)+' 页':''}</summary><blockquote>${esc(citation.quote||(image?'原页图像，未提取到文字':'未提供原文摘录'))}</blockquote>${image?`<img class="evidence-image" src="${esc(image)}" alt="引用的原页图表或公式" loading="lazy">`:''}<div class="evidence-foot"><span>${citation.source_sha?'已绑定来源版本':'来源版本未提供'}</span>${internalHref(citation)?`<button class="text-button" data-evidence="${esc(encodeURIComponent(JSON.stringify(citation)))}">${['paper','figure'].includes(citation.type)?'跳转原文':'打开所属论文'} ↗</button>`:referenceHref?`<a class="text-link" href="${esc(referenceHref)}" target="_blank" rel="noopener noreferrer">打开前序论文原文 ↗</a>`:''}</div></details>`;
}
function evidence(items){return (items||[]).map(citationMarkup).join('');}
export function resultMarkup(result={},jobId=''){
  const claims=Array.isArray(result.claims)?result.claims:[], knowledge=Array.isArray(result.knowledge)?result.knowledge:[], lineage=Array.isArray(result.lineage)?result.lineage:[];
  return `${claims.map(claim=>`<section class="answer-claim"><p>${mathText(claim.text)}</p>${evidence(claim.citations)}</section>`).join('')}${knowledge.length?`<div class="knowledge-cards">${knowledge.map(item=>`<details class="knowledge-card" data-knowledge-category="${esc(item.category)}"><summary><span class="pill">${esc(categories[item.category]||item.category)}</span>${mathText(item.label)}</summary><p>${mathText(item.description)}</p>${evidence(item.citations)}${(item.related_labels||[]).length?`<div class="direction-tags">${item.related_labels.map(label=>`<button data-related-label="${esc(label)}">${esc(label)}</button>`).join('')}</div>`:''}</details>`).join('')}</div>`:''}${lineage.length?`<ol class="lineage-list">${lineage.map((item,index)=>`<li><details><summary>${esc(item.label||item.reference)}</summary><p>${esc(item.reference)}</p><p>${mathText(item.relationship)}</p><span class="pill">${item.status==='local_fulltext'?'已结合本地原文':'仅有本文引用语境'}</span>${evidence(item.citations)}${jobId?`<button class="button small" data-expand-reference="${index}">了解这项前序工作</button>`:''}${item.lookup_query?`<a class="text-link" href="/?view=catalog&q=${encodeURIComponent(item.lookup_query)}">在论文索引中查找 ↗</a>`:''}</details></li>`).join('')}</ol>`:''}${(result.unanswered||[]).length?`<div class="notice compact"><strong>证据边界</strong><ul>${result.unanswered.map(item=>`<li>${esc(typeof item==='string'?item:item.reason||item.text||JSON.stringify(item))}</li>`).join('')}</ul></div>`:''}${!claims.length&&!knowledge.length&&!lineage.length&&!result.unanswered?.length?'<p class="muted">尚无可展示的证据结果。</p>':''}`;
}
function coverageMarkup(coverage={}){
  const labels={total_papers:'项目论文',scanned_papers:'检索论文',total_pages:'总页数',scanned_pages:'检索页数',available_chunks:'可检索段落',selected_papers:'采用论文',papers_scanned:'检索论文',papers_total:'项目论文',documents_scanned:'扫描材料',total_documents:'全部材料',chunks_scanned:'扫描段落',chunks_selected:'采用段落',selected_chunks:'采用段落',total_chunks:'总段落',source_count:'来源数',input_chars:'输入字数',retrieved_chars:'检索字数',evidence_count:'证据数'};
  if(coverage.scope==='comparison')labels.total_papers='所选论文';
  const parts=Object.entries(labels).filter(([key])=>coverage[key]!==undefined).map(([key,label])=>`${label} ${coverage[key]}`);
  return parts.length?`<details class="research-coverage"><summary>本次参考范围</summary><p>${parts.map(esc).join(' · ')}</p>${coverage.omitted_paper_ids?.length?`<p>本次回答未采用 ${coverage.omitted_paper_ids.length} 篇论文的段落；它们仍在项目检索范围内。</p>`:''}${coverage.failed_pages?.length?`<p>${coverage.failed_pages.length} 页解析失败，未作为原文证据使用。</p>`:''}${coverage.empty_text_pages?.length?`<p>${coverage.empty_text_pages.length} 页未提取到文字。</p>`:''}${coverage.all_chunks_fit===false?'<p>材料超出单次上下文容量，已从本次参考范围检索与问题相关的证据。</p>':''}${coverage.note?`<p>${esc(coverage.note)}</p>`:''}${(coverage.missing_papers||[]).length?`<p>尚缺全文：${coverage.missing_papers.length} 篇</p>`:''}</details>`:'';
}
function visibleHistory(s){if(s.kind==='compare')return s.history.filter(job=>job.kind==='compare');return s.embedded?s.history.filter(job=>s.sessionTab==='qa'?job.kind==='qa':job.kind!=='qa'):s.history;}
function render(){
  const s=current;if(!s||!s.container.isConnected)return;if(s.composing){s.pendingRender=true;return;}s.pendingRender=false;
  const restore=retainInteraction(s.container);
  const busy=!!(s.job&&['queued','running'].includes(s.job.status));
  s.container.hidden=false;
  s.container.innerHTML=`<section class="research-panel ${s.embedded?'research-dialogue':''}" aria-label="论文研究助手"><div class="research-panel-heading"><div><h2>${s.kind==='compare'?'论文对比':s.paper_id?names[s.kind]:s.kind==='knowledge'?'项目知识':'项目问答'}</h2></div>${s.embedded?'':'<button class="icon-button" data-research-close aria-label="收起研究面板">×</button>'}</div><p class="research-scope" id="research-scope">${s.kind==='compare'?`仅对比选中的 ${s.paper_ids?.length||0} 篇论文原文`:s.kind==='reference'?'按需读取所选前序工作的原文，逐项核对解释依据':s.paper_id?'参考当前论文与当前项目中可见的阅读材料':'参考此项目内全部可访问论文、笔记、解读和总结'}。原文证据可展开核对。</p>${s.kind==='compare'&&s.job?.result?`<p class="small muted">对比原文：${[...new Set((s.job.result.claims||[]).flatMap(c=>c.citations||[]).map(c=>c.paper_title).filter(Boolean))].map(esc).join(' · ')}</p>`:''}${s.selection?.quote?`<details class="research-selection"><summary>已选择的原文 · 第 ${esc(s.selection.page)} 页</summary><blockquote>${esc(s.selection.quote)}</blockquote></details>`:''}${s.kind==='reference'?`<p class="notice compact">前序论文按需读取，尚未自动加入项目。</p>${s.parentLineage?'<button class="text-button" data-research-parent>返回研究脉络</button>':''}`:['qa','compare'].includes(s.kind)?`<form id="research-question-form" aria-describedby="research-scope" aria-busy="${busy}"><label>研究问题<textarea name="question" rows="3" ${s.kind==='qa'?'required':''} maxlength="12000" placeholder="例如：这些方法的实验条件有哪些差异？">${esc(s.question||'')}</textarea></label><button class="button primary" type="submit" ${busy?'disabled':''}>${s.kind==='compare'?'开始论文对比':'结合原文回答'}</button></form>`:s.browseOnly?'':`<button class="button primary" data-research-generate ${busy?'disabled':''}>${s.job?'重新生成':s.kind==='knowledge'?'生成附原文引用的解读':names[s.kind]}</button>`}${s.error?`<p class="task-error" role="alert">${esc(s.error)}</p>`:''}${s.job?`<div class="research-job-state"><span role="status" aria-live="polite" aria-atomic="true"><span class="pill">${esc(states[s.job.status]||s.job.status)}</span><span>${esc(s.job.stage||'')}</span></span>${busy?'<button class="text-button danger" data-research-cancel>取消</button>':''}</div>${s.job.error?`<p class="task-error">${esc(s.job.error)}</p>`:''}${s.job.result?`${s.kind==='qa'&&s.job.question?`<blockquote class="research-prompt">${esc(s.job.question)}</blockquote>`:''}<div class="research-answer" role="region" aria-label="附原文证据的研究结果">${resultMarkup(s.job.result,s.job.id)}</div>${coverageMarkup(s.job.result.coverage||s.job.coverage)}${s.job.result.reference_material?.asset_id?`<div class="reference-material"><strong>${esc(s.job.result.reference_material.title||'前序论文')}</strong><p>${s.job.result.reference_material.added_to_project?'已加入当前项目':'原文已临时保存，可按需加入项目。'}</p>${!s.job.result.reference_material.added_to_project?'<button class="button small" data-import-reference>加入当前项目</button>':''}</div>`:''}${s.job.can_share||(s.job.can_edit&&s.job.visibility==='shared')?`<button class="text-button" data-research-share>${s.job.visibility==='shared'?'撤回共享，仅自己可见':'共享本次解读给项目成员'}</button>`:`<p class="small muted">${s.job.visibility==='shared'?'项目共享解读':'个人解读'}</p>`}`:''}`:''}${s.kind==='knowledge'&&!s.embedded?`<details class="knowledge-browser"><summary>筛选项目内已提取知识</summary><form id="knowledge-filter-form" aria-label="筛选项目知识" aria-controls="knowledge-filter-results" aria-busy="${!!s.knowledgeBusy}"><label>类别<select name="category"><option value="">全部类别</option>${Object.entries(categories).map(([key,name])=>`<option value="${key}" ${s.knowledgeFilter?.category===key?'selected':''}>${name}</option>`).join('')}</select></label><label>关键词<input name="q" maxlength="300" value="${esc(s.knowledgeFilter?.q||'')}" placeholder="数据集、方法或研究问题"></label><button class="button small" ${s.knowledgeBusy?'disabled':''}>筛选</button></form><p role="status" aria-live="polite" aria-atomic="true">${s.knowledgeBusy?'正在筛选项目知识…':s.knowledgeError?'筛选未完成，请重试。':s.knowledgeItems?`找到 ${s.knowledgeItems.length} 项知识`:''}</p><div id="knowledge-filter-results" role="region" aria-label="项目知识筛选结果" aria-busy="${!!s.knowledgeBusy}">${s.knowledgeItems?knowledgeMarkup(s.knowledgeItems):''}</div></details>`:''}<details class="research-history"><summary>历史问答与解读 (${visibleHistory(s).length})</summary>${visibleHistory(s).map(job=>`<button class="research-history-item" data-research-history="${esc(job.id)}"><span>${esc(job.question?.trim()?job.question.slice(0,70):names[job.kind]||job.kind)}</span><small>${esc(states[job.status]||job.status)} · ${job.visibility==='shared'?'共享':'个人'}</small></button>`).join('')||'<p class="small muted">还没有保存的问答。</p>'}</details></section>`;
  restore();
}
async function refreshHistory(){const s=current;if(!s)return;const query=new URLSearchParams({project_id:s.project_id});if(s.paper_id)query.set('paper_id',s.paper_id);try{const result=await api('/research/jobs?'+query);if(s===current){s.history=Array.isArray(result)?result:result.items||result.jobs||[];render();}}catch(error){if(s===current){s.error=error.message;render();}}}
async function poll(){
  const s=current,version=generation;if(!s?.job)return;if(!s.container.isConnected){disposeResearchPanel();return;}
  try{const job=await api('/research/jobs/'+encodeURIComponent(s.job.id));if(s!==current||version!==generation)return;s.job=job;render();if(['queued','running'].includes(job.status))timer=setTimeout(poll,1600);else await refreshHistory();}catch(error){if(s===current){s.error=error.message;render();}}
}
async function generate(){
  const s=current;if(!s||['queued','running'].includes(s.job?.status))return;s.error='';
  if(['figure','formula','explain','summary'].includes(s.kind)&&!s.selection){s.error='请先在论文中选择对应原文或框选图表、公式，再右键选择讲解。';render();return;}
  const body={project_id:s.project_id,kind:s.kind,question:s.question||''};if(s.paper_id)body.paper_id=s.paper_id;if(s.selection)body.selection=s.selection;if(s.kind==='compare')body.paper_ids=s.paper_ids;
  try{s.job={status:'queued',stage:'正在提交'};render();const job=await api('/research/jobs',{method:'POST',body});if(s!==current)return;s.job=job;render();clearTimeout(timer);await poll();}catch(error){if(s===current){s.job=null;s.error=error.message;render();}}
}
export async function jumpToCitation(citation){
  const href=internalHref(citation);if(!href)throw new Error('此证据没有可定位的论文。');
  const path=`/read/${encodeURIComponent(citation.paper_id)}`;
  if(['paper','figure'].includes(citation.type)&&location.pathname===path&&new URLSearchParams(location.search).get('project')===citation.project_id){window.dispatchEvent(new CustomEvent('paperstudio:evidence',{detail:citation}));return;}
  location.assign(href);
}
export async function openResearchPanel(detail={}){
  const container=qs(detail.target||'#reader-research-panel');if(!container)throw new Error('请先打开论文或选择研究项目。');
  clearTimeout(timer);generation++;
  if(current?.container&&current.container!==container)current.container.hidden=true;
  if(current?.sessionKey)readerSessions.set(current.sessionKey,current);
  const sessionKey=detail.embedded?JSON.stringify([detail.project_id,detail.paper_id,detail.sessionTab||'ai']):null;
  const cached=sessionKey&&readerSessions.get(sessionKey);
  if(detail.resume&&cached)detail={...detail,kind:cached.kind,selection:cached.selection,question:cached.question};
  const previous=detail.resume&&cached?cached:current;
  const same=previous?.project_id===detail.project_id&&previous?.paper_id===(detail.paper_id||null)&&previous?.kind===(detail.kind||'qa')&&JSON.stringify(previous?.selection||null)===JSON.stringify(detail.selection||null)&&JSON.stringify(previous?.paper_ids||[])===JSON.stringify(detail.paper_ids||[]);
  current={container,paper_ids:detail.paper_ids||[],project_id:detail.project_id,paper_id:detail.paper_id||null,kind:detail.kind||'qa',embedded:!!detail.embedded,sessionTab:detail.sessionTab,sessionKey,browseOnly:!!detail.browseOnly,selection:detail.selection||null,question:detail.question??(same?previous.question:''),job:same?previous.job:null,history:[],error:'',knowledgeFilter:same?previous.knowledgeFilter:{category:'',q:''},knowledgeItems:same?previous.knowledgeItems:null,parentLineage:same?previous.parentLineage:null};
  if(!current.project_id)throw new Error('请先选择一个研究项目。');
  container.oncompositionstart=()=>{if(current?.container===container)current.composing=true;};
  container.oncompositionend=event=>{if(current?.container!==container)return;current.composing=false;container.oninput(event);if(current.pendingRender)render();};
  container.oninput=container.onchange=event=>{if(!current||current.container!==container)return;const field=event.target;if(field.form?.id==='research-question-form'&&field.name==='question')current.question=field.value;if(field.form?.id==='knowledge-filter-form')current.knowledgeFilter={...current.knowledgeFilter,[field.name]:field.value};};
  container.onclick=async event=>{const button=event.target.closest('button');if(!button)return;event.stopPropagation();const active=current;try{
    if(button.hasAttribute('data-research-close')){container.hidden=true;disposeResearchPanel();return;}
    if(button.hasAttribute('data-research-generate')){await generate();return;}
    if(button.hasAttribute('data-research-cancel')&&current?.job?.id){await api('/research/jobs/'+encodeURIComponent(active.job.id)+'/cancel',{method:'POST'});if(active===current)await poll();return;}
    if(button.hasAttribute('data-research-share')){const job=await api('/research/jobs/'+encodeURIComponent(active.job.id)+'/visibility',{method:'PATCH',body:{visibility:active.job.visibility==='shared'?'private':'shared'}});if(active!==current)return;current.job=job;render();return;}
    if(button.dataset.researchHistory){const s=current,job=await api('/research/jobs/'+encodeURIComponent(button.dataset.researchHistory));if(s!==current)return;s.job=job;s.kind=job.kind;s.selection=job.selection||null;s.paper_ids=job.paper_ids||[];s.question=job.question||'';render();if(['queued','running'].includes(job.status))await poll();return;}
    if(button.hasAttribute('data-expand-reference')){const s=current,parent=s.job;button.disabled=true;const job=await api(`/research/jobs/${encodeURIComponent(parent.id)}/references/${Number(button.dataset.expandReference)}/expand`,{method:'POST',body:{}});if(s!==current)return;s.parentLineage=parent.id;s.job=job;s.kind='reference';s.error='';render();clearTimeout(timer);await poll();return;}
    if(button.hasAttribute('data-research-parent')&&active.parentLineage){const job=await api('/research/jobs/'+encodeURIComponent(active.parentLineage));if(active!==current)return;current.job=job;current.kind='lineage';render();return;}
    if(button.hasAttribute('data-import-reference')){const s=current,asset=s.job.result.reference_material;button.disabled=true;const paper=await api(`/research/reference-assets/${encodeURIComponent(asset.asset_id)}/import`,{method:'POST',body:{project_id:s.project_id}});if(s!==current)return;asset.added_to_project=true;asset.paper_id=paper.id;render();toast('前序论文已加入当前项目');return;}
    if(button.dataset.evidence){await jumpToCitation(JSON.parse(decodeURIComponent(button.dataset.evidence)));return;}
    if(button.dataset.relatedLabel)await filterKnowledge({label:button.dataset.relatedLabel});
  }catch(error){if(active===current){toast(error.message);current.error=error.message;render();}}};
  container.onsubmit=async event=>{event.preventDefault();event.stopPropagation();const fd=new FormData(event.target);try{if(event.target.id==='research-question-form'){current.question=String(fd.get('question')||'').trim();if(current.question||current.kind==='compare')await generate();}else if(event.target.id==='knowledge-filter-form'){await filterKnowledge({category:String(fd.get('category')||''),q:String(fd.get('q')||'')});}}catch(error){toast(error.message);}};
  const opened=current;render();await refreshHistory();if(opened!==current)return;
  if(detail.browseOnly){await filterKnowledge();return;}
  if(detail.autoGenerate===false&&!current.job&&!current.selection&&current.kind!=='qa'){
    current.job=current.history.find(job=>job.kind===current.kind&&['succeeded','queued','running'].includes(job.status))||null;
    render();
  }
  if(detail.autoGenerate!==false&&detail.kind&&detail.kind!=='qa')await generate();
  else if(current?.job&&['queued','running'].includes(current.job.status))await poll();
}
function knowledgeMarkup(items){
  return items.length?items.map(item=>`<article class="knowledge-match"><small>${esc(item.paper_title||'项目知识')} · ${esc(categories[item.category]||item.category)}</small><strong>${mathText(item.label)}</strong><p>${mathText(item.description)}</p>${evidence(item.citations)}</article>`).join(''):'<p class="muted">当前筛选下没有已提取知识。打开论文，点击“结构化知识”后即可积累。</p>';
}
async function filterKnowledge(filters={}){
  const s=current;if(!s||s.knowledgeBusy)return;s.knowledgeBusy=true;s.knowledgeError='';s.error='';render();
  try{const result=await api('/research/knowledge?'+new URLSearchParams({project_id:s.project_id,...filters}));if(s!==current)return;s.knowledgeItems=Array.isArray(result)?result:result.items||result.knowledge||[];}
  catch(error){if(s===current){s.knowledgeError=error.message;s.error=error.message;toast(error.message);}}
  finally{if(s===current){s.knowledgeBusy=false;render();s.container.querySelector('.knowledge-browser')?.setAttribute('open','');}}
}
