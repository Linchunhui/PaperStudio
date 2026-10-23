import { mountReader } from './reader.js';

export const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
export const qs = (selector, parent = document) => parent.querySelector(selector);
export const list = payload => Array.isArray(payload) ? payload : payload?.items || payload?.papers || payload?.sources || [];
export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const request = { ...options, headers };
  if (request.body && !(request.body instanceof FormData) && typeof request.body !== 'string') {
    headers.set('Content-Type', 'application/json');
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(`/api${path}`, request);
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.error ?? payload;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}
let toastTimer;
export function toast(message) {
  const box = qs('#toast'); box.textContent = message; box.classList.add('visible');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => box.classList.remove('visible'), 4800);
}
export function memberships(paper) {
  const value = paper.memberships || paper.projects || [];
  if (Array.isArray(value)) return value.map(x => typeof x === 'string' ? {project_id:x} : x);
  return Object.entries(value).map(([project_id, data]) => ({project_id, ...data}));
}
export const statuses = {unread:'待读',reading:'在读',read:'已读'};
export const jobLabels = {guide:'论文导读',brief:'论文摘要 / 粗读','selection-summary':'选区摘要','notes-summary':'读后笔记总结',translate:'全文翻译'};
export const jobStatuses = {queued:'等待执行',running:'执行中',succeeded:'已完成',failed:'失败',cancelled:'已取消',interrupted:'执行中断'};
export const icons = {
  book:'<svg viewBox="0 0 24 24"><path d="M3 4h7a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H3zM13 7a3 3 0 0 1 3-3h5v15h-4a4 4 0 0 0-4 2"/></svg>',
  search:'<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>',
  folder:'<svg viewBox="0 0 24 24"><path d="M3 7V5h6l2 2h10v12H3z"/></svg>',
  plus:'<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  arrow:'<svg viewBox="0 0 24 24"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>',
  spark:'<svg viewBox="0 0 24 24"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/></svg>',
  note:'<svg viewBox="0 0 24 24"><path d="M5 3h14v18H5zM8 8h8M8 12h8M8 16h5"/></svg>',
  source:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c-5 5-5 11 0 16 5-5 5-11 0-16"/></svg>'
};

// External content is evidence, never executable markup or a navigation scheme.
export function safeURL(value) {
  try { const u = new URL(String(value || '')); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; }
}
export function processingProgress(step) {
  const progress=step?.progress;
  if(!progress)return '';
  if(typeof progress==='string')return progress;
  const coverage=progress.coverage||progress;
  const count=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))&&Number(value)>=0?Number(value):null;
  const parts=[];
  if(progress.stage)parts.push(String(progress.stage));
  if(step.kind==='download'&&count(progress.downloaded_bytes)!==null){
    const downloaded=(Number(progress.downloaded_bytes)/1048576).toFixed(1),total=count(progress.total_bytes);
    parts.push(`已下载 ${downloaded}${total?' / '+(total/1048576).toFixed(1):''} MB`);
  }else if(step.kind==='translate'&&count(coverage.ready_pages)!==null){
    parts.push(`译文已就绪 ${coverage.ready_pages} / ${count(coverage.total_pages)??'?'} 页`);
  }else if(count(coverage.text_pages)!==null&&count(coverage.total_pages)!==null){
    parts.push(`已提取 ${coverage.text_pages} / ${coverage.total_pages} 页文字`);
    if(Array.isArray(coverage.empty_text_pages)&&coverage.empty_text_pages.length)parts.push(`${coverage.empty_text_pages.length} 页缺少文字层`);
  }
  if(progress.reused)parts.push('已复用解析结果');
  return parts.join(' · ');
}
export function paperMembership(paper, projectId, scope, status='all') {
  const candidates=memberships(paper).filter(m=>(!scope||scope.has(m.project_id))&&(status==='all'||m.status===status));
  return candidates.find(m=>m.project_id===projectId)||candidates[0]||{};
}
const route = location.pathname.match(/^\/read\/([^/]+)\/?$/);
if (route) {
  mountReader({paperId:decodeURIComponent(route[1]), projectId:new URLSearchParams(location.search).get('project')});
} else {
  mountWorkspace();
}

async function mountWorkspace() {
  const params = new URLSearchParams(location.search);
  const views = new Set(['discover','library','imports','sources','skills']);
  const stored = (key,fallback) => {try {const value=JSON.parse(localStorage.getItem(key));return value??fallback;} catch {return fallback;}};
  const remember = (key,value) => {try {localStorage.setItem(key,JSON.stringify(value));} catch {toast('浏览器无法保存偏好；论文和笔记仍保存在后端。');}};
  const selectedSources=stored('paperstudio-feed-sources',['hf','qbitai','jiqizhixin']);
  const state = {
    projects:[], papers:[], projectId:params.get('project')||'', view:views.has(params.get('view'))?params.get('view'):(params.get('project')?'library':'discover'),
    query:'', status:'all', includeChildren:true, sources:[], sourceGroups:{}, sourceQuery:'', priority:'all', skills:[], capabilities:{}, discoveryConfig:{}, loading:true, editProject:false,
    searchQuery:'', searchScope:'web', searchPage:1, discoveryMode:'feed', discoveryItems:[], discoveryStatuses:[], discoveryLoading:false, discoveryError:'', fetchedAt:null,
    interests:String(stored('paperstudio-interests','')), feedSources:new Set(Array.isArray(selectedSources)?selectedSources.filter(s=>['hf','qbitai','jiqizhixin'].includes(s)):['hf','qbitai','jiqizhixin']),
    imports:[], importError:'', importLoading:false,
    importer:{kind:'url',url:'',title:'',paperId:null,projectId:params.get('project')||'inbox',file:null,processing:{parse:true,translate:false,guide:false,brief_modes:[]}},
    importSubmitting:false, importMessage:'', importFailure:''
  };
  const follows=stored('paperstudio-source-follows',[]),hidden=stored('paperstudio-hidden-discovery',[]);
  const followed=new Set(Array.isArray(follows)?follows.map(String):[]),hiddenItems=new Set(Array.isArray(hidden)?hidden.map(String):[]);
  const app=qs('#app');
  const sourceNames={hf:'Hugging Face',qbitai:'量子位',jiqizhixin:'机器之心',arxiv:'arXiv',web:'全网搜索',brave:'Brave',searxng:'SearXNG'};
  const modeNames={abstract:'精简摘要',cards:'卡片粗读',summary:'全文总结',all:'摘要与完整粗读'};
  const importStatuses={queued:'排队中',running:'处理中',succeeded:'已完成',partial:'部分完成',blocked:'等待配置',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const stepNames={download:'下载 PDF',parse:'解析全文',translate:'逐页翻译',guide:'论文导读',brief:'论文粗读'};
  let querySequence=0,discoverySequence=0,importsSequence=0,debounce,pollTimer,pageActive=true;
  const dateText=value=>{if(!value)return '日期未提供';const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});};
  function projectPath(id) {
    const result=[],seen=new Set();let p=state.projects.find(x=>x.id===id);
    while(p&&!seen.has(p.id)){seen.add(p.id);result.unshift(p.name);p=state.projects.find(x=>x.id===p.parent_id);}
    return result.join(' / ');
  }
  function projectScope() {
    if(!state.projectId)return new Set(state.projects.map(p=>p.id));
    const ids=new Set([state.projectId]);let changed=state.includeChildren;
    while(changed){changed=false;for(const p of state.projects){if(ids.has(p.parent_id)&&!ids.has(p.id)){ids.add(p.id);changed=true;}}}
    return ids;
  }
  function updateURL(){const next=new URLSearchParams();if(state.projectId)next.set('project',state.projectId);if(state.view!=='discover')next.set('view',state.view);history.replaceState(null,'',`/${next.size?'?'+next:''}`);}
  function tree(parent=null,depth=0,seen=new Set()) {
    return state.projects.filter(p=>(p.parent_id||null)===parent&&!seen.has(p.id)).map(p=>{const next=new Set(seen);next.add(p.id);return `<button class="project-item ${state.view==='library'&&state.projectId===p.id?'active':''}" style="--depth:${depth}" data-project="${esc(p.id)}">${icons.folder}<span>${esc(p.name)}</span></button>${tree(p.id,depth+1,next)}`;}).join('');
  }
  function projectOptions(selected='',includeRoot=false,excludeId='') {
    const blocked=new Set([excludeId]);let changed=true;
    while(changed){changed=false;for(const p of state.projects){if(blocked.has(p.parent_id)&&!blocked.has(p.id)){blocked.add(p.id);changed=true;}}}
    return (includeRoot?'<option value="">根目录</option>':'')+state.projects.filter(p=>!excludeId||!blocked.has(p.id)).map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(projectPath(p.id))}</option>`).join('');
  }
  function sidebar(){
    const nav=[['discover','搜索与推荐','search'],['library','我的论文库','book'],['imports','导入与任务','plus'],['sources','来源与关注','source'],['skills','Skills 与服务','spark']];
    return `<aside class="workspace-sidebar"><a href="/" class="brand"><span class="brand-symbol">${icons.book}</span><span><strong>PaperStudio</strong><small>让阅读形成自己的脉络</small></span></a><div class="workspace-identity"><span class="avatar">私</span><div><strong>我的研究空间</strong><small>个人版 · 本机保存</small></div></div><nav aria-label="工作台导航">${nav.map(([key,name,icon])=>`<button class="nav-item ${state.view===key?'active':''}" data-view="${key}">${icons[icon]}${name}${key==='imports'?'<span id="active-import-count" class="nav-count"></span>':''}</button>`).join('')}</nav><div class="project-label">研究项目</div><button class="project-item ${state.view==='library'&&!state.projectId?'active':''}" data-project="">${icons.folder}<span>全部论文</span></button><div class="project-tree">${tree()}</div><details class="new-project"><summary>${icons.plus}新建项目</summary><form id="new-project-form"><label>项目名称<input name="name" required maxlength="100" placeholder="例如：文档理解" autocomplete="off"></label><label>所属目录<select name="parent_id">${projectOptions(state.projectId,true)}</select></label><button class="button primary" type="submit">创建项目</button></form></details><div class="sidebar-foot"><span class="connection-dot"></span>PDF 与笔记连接后端<small>AI ${state.capabilities.ai_configured?'已配置':'待配置'} · 来源独立更新</small></div></aside>`;
  }
  function publicLink(url,label,classes='text-link') {const safe=safeURL(url);return safe?`<a class="${classes}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>`:'';}
  function normalStatuses(input){return Array.isArray(input)?input:Object.entries(input||{}).map(([source_id,value])=>typeof value==='object'?{source_id,...value}:{source_id,status:value});}
  function sourceStatusCards(){
    return normalStatuses(state.discoveryStatuses).map(s=>{
      const name=sourceNames[s.source_id||s.source||s.id]||s.name||s.source_id||s.source||'来源';
      const status=s.status||'unknown';const stale=s.stale||status==='stale';const failed=!!s.error||['failed','error','unavailable','blocked','not_configured','unconfigured'].includes(status);
      const label=stale?'使用缓存':['unconfigured','not_configured'].includes(status)?'等待配置':failed?'更新失败':status==='cached'?'已缓存':status==='ok'||status==='success'||status==='fresh'?'已更新':status;
      const updated=s.last_success_at||s.fetched_at||s.updated_at;
      return `<div class="source-health ${failed?'has-error':''}"><div><strong>${esc(name)}</strong><span>${esc(label)}</span></div><small>${updated?'最近成功更新：'+esc(dateText(updated)):'尚无成功更新'}</small>${s.error||s.message?`<p>${esc(s.error||s.message)}</p>`:''}</div>`;
    }).join('');
  }
  function paperURLs(item){return [...new Set((item.paper_urls||[]).map(x=>safeURL(typeof x==='string'?x:x?.url)).filter(Boolean))];}
  function discoveryResults(){
    if(state.discoveryLoading)return '<div class="empty-state"><div class="loading-ring"></div><p>正在向来源获取内容…</p></div>';
    const health=sourceStatusCards();
    if(state.discoveryError)return `${health?`<div class="source-health-grid">${health}</div>`:''}<div class="error-panel"><strong>这次获取未完成</strong><p>${esc(state.discoveryError)}</p><button class="button" data-action="retry-discovery">重试</button><button class="text-button" data-view="skills">查看服务配置</button></div>`;
    const rows=state.discoveryItems.map((item,index)=>({item,index})).filter(({item})=>state.discoveryMode==='search'||!hiddenItems.has(String(item.id||item.url)));
    return `${health?`<div class="source-health-grid">${health}</div>`:''}<div class="result-caption"><span>${rows.length} 条${state.discoveryMode==='search'?'检索结果':'推荐内容'}</span><span>${state.discoveryMode==='search'?`第 ${state.searchPage} 页 · ${esc({web:'全网',arxiv:'arXiv',hf:'Hugging Face'}[state.searchScope])}`:'按兴趣、新鲜度与来源热度排序'}${state.fetchedAt?' · '+esc(dateText(state.fetchedAt)):''}</span></div><div class="discovery-grid">${rows.map(({item,index})=>{
      const papers=paperURLs(item),isPaper=item.kind==='paper',url=safeURL(item.url),importURL=isPaper?(papers[0]||url):papers[0];
      const reasons=Array.isArray(item.reasons)?item.reasons:[];
      const authors=Array.isArray(item.authors)?item.authors.map(a=>typeof a==='string'?a:a?.name||'').filter(Boolean).join(', '):String(item.authors||'');
      const sources=item.source_ids||item.sources;
      const sourceList=Array.isArray(sources)?sources.map(s=>sourceNames[typeof s==='string'?s:s?.source_id]||String(s?.name||s)).join(' · '):sourceNames[item.source_id]||item.source_id||item.provider||'外部来源';
      return `<article class="discovery-card"><div class="discovery-card-meta"><span class="pill ${isPaper?'green':''}">${isPaper?'论文':'媒体文章'}</span><span>${esc(sourceList)}</span><span>${esc(dateText(item.published_at))}</span></div><h2>${url?publicLink(url,esc(item.title),'discovery-title'):esc(item.title)}</h2>${authors?`<p class="discovery-authors">${esc(authors)}</p>`:''}<p class="discovery-snippet">${esc(String(item.snippet||item.summary||'来源暂未提供摘要。').slice(0,900))}</p>${reasons.length?`<div class="recommend-reasons">${reasons.slice(0,4).map(r=>`<span>${esc(typeof r==='string'?r:r.label||r.reason||'')}</span>`).join('')}</div>`:''}<div class="discovery-card-actions">${importURL?`<button class="button primary small" data-import-result="${index}">${icons.plus}加入项目并处理</button>`:`<span class="muted">未识别到可导入论文链接</span>`}${publicLink(url,isPaper?'论文原页 ↗':'阅读媒体原文 ↗')} ${state.discoveryMode==='feed'?`<button class="text-button" data-hide-result="${index}">不感兴趣</button>`:''}</div>${!isPaper&&papers.length?`<div class="article-paper-links"><span>关联论文：</span>${papers.slice(0,5).map((link,n)=>`${publicLink(link,`论文 ${n+1} ↗`)}<button class="text-button" data-import-article="${index}" data-link-index="${n}">导入</button>`).join('')}</div>`:''}</article>`;
    }).join('')||'<div class="empty-state"><h2>暂无可展示内容</h2><p>可以调整查询、来源或关注方向。来源更新失败时会在上方显示原因。</p></div>'}</div>${state.discoveryMode==='search'?`<div class="search-pagination"><button class="button" data-search-page="${state.searchPage-1}" ${state.searchPage<=1?'disabled':''}>上一页</button><span>第 ${state.searchPage} 页${state.searchScope==='hf'&&state.searchPage>=5?' · 已到 HF 检索上限':''}</span><button class="button" data-search-page="${state.searchPage+1}" ${!state.discoveryItems.length||state.searchPage>=(state.searchScope==='hf'?5:10)?'disabled':''}>下一页</button></div>`:''}`;
  }
  function discover(){
    return `<div class="eyebrow">DISCOVER & READ</div><div class="page-heading"><div><h1>从一个问题，找到下一篇值得读的论文。</h1><p>检索全网，追踪公开发布，把有价值的材料带进你的研究项目。</p></div><button class="button" data-action="open-import">${icons.plus}链接 / PDF 导入</button></div><form id="discovery-search-form" class="discovery-search"><label class="search-box">${icons.search}<input name="q" aria-label="全网或学术论文搜索" type="search" maxlength="500" value="${esc(state.searchQuery)}" placeholder="搜索论文、研究问题、机构文章或项目…" required></label><select name="scope" aria-label="搜索范围">${[['web','全网搜索'],['arxiv','arXiv 论文'],['hf','Hugging Face 论文']].map(([key,name])=>`<option value="${key}" ${state.searchScope===key?'selected':''}>${name}</option>`).join('')}</select><button class="button primary" type="submit">搜索</button></form><div class="discovery-feed-bar"><button class="feed-tab ${state.discoveryMode==='feed'?'active':''}" data-action="show-feed">为你发现</button>${state.discoveryMode==='search'?'<span class="feed-tab active">搜索结果</span>':''}<span class="grow"></span><button class="text-button" data-action="refresh-feed">刷新来源</button></div><form id="interests-form" class="interests-form"><label>关注方向<input name="interests" maxlength="300" value="${esc(state.interests)}" placeholder="例如：OCR, 多模态理解, Agent"></label><button class="button small" type="submit">更新推荐</button><div class="feed-source-choices" role="group" aria-label="推荐内容来源">${['hf','qbitai','jiqizhixin'].map(key=>`<label><input type="checkbox" data-feed-source="${key}" ${state.feedSources.has(key)?'checked':''}>${sourceNames[key]}</label>`).join('')}</div></form><div id="discovery-results" aria-live="polite">${discoveryResults()}</div><p class="discovery-footnote">推荐来自实际来源数据与透明排序规则。量子位使用公开 RSS，机器之心通过已配置搜索服务获取公开网站文章，并非完整的微信公众号消息流。媒体文章与论文分开标记；未连接的机构目录不会被计为已采集来源。${'<button class="text-button" id="restore-hidden-button" data-action="restore-hidden" '+(hiddenItems.size?'':'hidden')+'>恢复已隐藏内容</button>'}</p>`;
  }
  function library(){
    const project=state.projects.find(p=>p.id===state.projectId);
    return `<div class="eyebrow">YOUR RESEARCH LIBRARY</div><div class="page-heading"><div><h1>${esc(project?.name||'我的论文库')}</h1><p>${project?esc(projectPath(project.id)):'同一篇论文可关联多个项目，笔记与阅读进度分别保存。'}</p></div><div class="actions">${project?'<button class="button subtle" data-action="edit-project">编辑项目</button>':''}<button class="button primary" data-action="open-import">${icons.plus}导入论文</button></div></div>${state.editProject&&project?`<form id="edit-project-form" class="inline-panel"><label>项目名称<input name="name" value="${esc(project.name)}" required maxlength="100"></label><label>所属目录<select name="parent_id">${projectOptions(project.parent_id,true,project.id)}</select></label><button type="submit" class="button primary">保存</button><button class="button" type="button" data-action="edit-project">收起</button></form>`:''}<div class="library-toolbar"><label class="search-box">${icons.search}<input id="library-query" type="search" placeholder="搜索已入库的论文…" aria-label="搜索已入库论文" value="${esc(state.query)}"></label>${state.projectId?`<label class="check-label library-children"><input id="library-children" type="checkbox" ${state.includeChildren?'checked':''}>包含子项目</label>`:''}<select id="library-status" aria-label="筛选阅读状态"><option value="all">全部状态</option>${Object.entries(statuses).map(([key,name])=>`<option value="${key}" ${state.status===key?'selected':''}>${name}</option>`).join('')}</select></div><div id="library-results" class="library-results">${paperList()}</div>`;
  }
  function paperList(){
    if(state.loading)return '<div class="empty-state"><div class="loading-ring"></div><p>正在读取论文库…</p></div>';
    const scope=projectScope(),rows=state.papers.filter(p=>state.status==='all'||memberships(p).some(m=>scope.has(m.project_id)&&m.status===state.status));
    if(!rows.length)return `<div class="empty-state">${icons.book}<h2>${state.query?'没有找到匹配论文':'还没有入库论文'}</h2><p>粘贴 arXiv 链接或上传 PDF，即可开始阅读。</p><button class="button primary" data-action="open-import">导入论文</button></div>`;
    return `<div class="result-caption"><span>${rows.length} 篇论文</span><span>按加入时间排列</span></div><div class="paper-grid">${rows.map(p=>{const ms=memberships(p),m=paperMembership(p,state.projectId,scope,state.status),selected=m.project_id||'inbox';return `<article class="paper-card"><div class="paper-card-top"><span class="pdf-label">PDF</span><span class="pill ${m.status==='read'?'green':''}">${statuses[m.status]||'待读'}</span><span class="muted">${esc(p.page_count||0)} 页</span></div><h2><a href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected)}">${esc(p.title)}</a></h2><div class="paper-projects">${ms.map(x=>`<span>${esc(projectPath(x.project_id)||'项目')}</span>`).join('')}</div><div class="paper-card-foot"><span>${p.translated_filename?'译文 PDF 已就绪':p.active_translation_run_id?'已启动逐页翻译，可查看已就绪页面':p.has_translation?'译文可阅读':'可按需逐页翻译'}</span><a class="text-link" href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected)}">连续阅读 ${icons.arrow}</a></div><div class="paper-process"><button class="button small" data-process-paper="${esc(p.id)}" data-process-project="${esc(selected)}">安排解析 / AI 处理</button></div><details class="associate-paper"><summary>关联到其他项目</summary><form data-associate="${esc(p.id)}"><select name="project_id" aria-label="关联目标项目">${projectOptions(selected)}</select><button class="button small" type="submit">关联</button></form></details></article>`;}).join('')}</div>`;
  }
  function processingOptions(){
    const p=state.importer.processing;
    const option=(name,label,description,checked)=>`<label class="processing-option"><input type="checkbox" name="${name}" ${checked?'checked':''}><span><strong>${label}</strong><small>${description}</small></span></label>`;
    return `<fieldset class="processing-options"><legend>入库后按需处理</legend><div class="processing-grid">${option('parse','全文解析','提取带页码的全文，供导读和摘要使用。',p.parse)}${option('translate','逐页对照翻译','每页完成即可阅读；未完成的页面继续后台处理。',p.translate)}${option('guide','论文导读','说明问题、机制和推荐阅读路线。',p.guide)}${option('abstract','精简摘要','基于全文提炼背景、方法与主要结果。',p.brief_modes.includes('abstract'))}${option('cards','卡片粗读','按信息单元整理机制、实验和边界。',p.brief_modes.includes('cards'))}${option('summary','全文总结','连贯讲清论文；与读后的个人笔记总结分开。',p.brief_modes.includes('summary'))}</div></fieldset><p class="import-parse-hint" id="import-parse-hint">${p.guide||p.brief_modes.length?'已选择全文 AI 任务，会先完成全文解析。':'下载与保存 PDF 不依赖模型。'}${!state.capabilities.ai_configured?' 当前模型未配置，AI 步骤会等待配置，原文仍可入库。':''}</p>`;
  }
  function importer(){
    const s=state.importer;
    return `<section class="import-panel"><div class="import-panel-heading"><h2>${s.kind==='existing'?'为已入库论文安排处理':'把论文带进研究项目'}</h2><p>先保存原文，再按选择运行后台任务。无需等待翻译和摘要完成。</p></div>${s.kind!=='existing'?`<div class="import-kind-tabs" role="group" aria-label="导入方式"><button class="${s.kind==='url'?'active':''}" data-import-kind="url">论文链接</button><button class="${s.kind==='file'?'active':''}" data-import-kind="file">本地 PDF</button></div>`:`<div class="notice compact">${esc(s.title)}<button class="text-button" data-action="open-import">改为导入新论文</button></div>`}<form id="import-form"><div class="import-main-fields">${s.kind==='url'?`<label class="import-url-field">论文链接<input name="url" type="url" required value="${esc(s.url)}" placeholder="https://arxiv.org/abs/2501.12948v1" maxlength="2048"><small>支持 arXiv 摘要页 / PDF 链接与公开 PDF 直链。媒体文章请先选择其关联论文链接。</small></label>`:s.kind==='file'?`<label class="import-url-field">PDF 文件<input name="file" type="file" accept="application/pdf,.pdf" ${s.file?'':'required'}><small>${s.file?esc(s.file.name)+' · '+(s.file.size/1048576).toFixed(1)+' MB':'支持最大 50 MB 的未加密 PDF。'}</small></label>`:''}<label>加入项目<select name="project_id" required>${projectOptions(s.projectId)}</select></label>${s.kind!=='existing'?`<label class="import-title-field">标题（可选）<input name="title" maxlength="300" value="${esc(s.title)}" placeholder="留空使用论文或 PDF 标题"></label>`:''}</div>${processingOptions()}<div class="import-submit-row"><button class="button primary" type="submit" ${state.importSubmitting?'disabled':''}>${state.importSubmitting?'正在提交…':s.kind==='existing'?'创建处理计划':'导入并后台处理'}</button><span>阅读器中的“读完”会另行总结当前项目笔记。</span></div><div id="import-feedback" class="form-feedback ${state.importFailure?'has-error':''}" role="status">${esc(state.importFailure||state.importMessage)}</div></form></section>`;
  }
  function importList(){
    if(state.importLoading&&!state.imports.length)return '<div class="empty-state"><div class="loading-ring"></div><p>正在获取处理任务…</p></div>';
    if(state.importError)return `<div class="error-panel">${esc(state.importError)}<button class="button small" data-action="refresh-imports">重试</button></div>`;
    if(!state.imports.length)return '<div class="empty-state"><h2>暂时没有导入任务</h2><p>上方提交链接或 PDF 后，下载、解析与 AI 步骤会在这里持续更新。</p></div>';
    return `<div class="import-list">${state.imports.map(task=>{
      const original=safeURL(task.source?.canonical_url||task.url||task.source_url),steps=Array.isArray(task.steps)?task.steps:[],active=['queued','running'].includes(task.status),retry=['failed','blocked','partial','interrupted','cancelled'].includes(task.status);
      const project=task.project_id||state.importer.projectId;
      return `<article class="import-task"><div class="import-task-heading"><div><h3>${esc(task.title||task.paper_title||task.source?.arxiv_id||task.source?.canonical_url||task.url||'论文处理任务')}</h3><p>${esc(projectPath(project)||project)} · ${esc(dateText(task.created_at))}</p></div><span class="pill ${task.status==='succeeded'?'green':''}">${esc(importStatuses[task.status]||task.status)}</span></div>${original?publicLink(original,'查看导入来源 ↗'):''}${task.error?`<p class="task-error">${esc(task.error)}</p>`:''}<ol class="import-steps">${steps.map(step=>`<li class="step-${esc(step.status)}"><div><strong>${esc(step.kind==='brief'?(modeNames[step.mode]||'论文粗读'):(stepNames[step.kind]||step.kind))}</strong><span>${esc(importStatuses[step.status]||step.status)}</span></div>${step.error?`<p>${esc(step.error)}</p>`:''}${processingProgress(step)?`<small>${esc(processingProgress(step))}</small>`:''}</li>`).join('')}</ol><div class="import-task-actions">${task.paper_id?`<a class="button primary small" href="/read/${encodeURIComponent(task.paper_id)}?project=${encodeURIComponent(project)}">打开原文与处理成果 ${icons.arrow}</a>`:'<span class="muted">原文下载完成后即可阅读</span>'}<span class="grow"></span>${retry?`<button class="button small" data-import-retry="${esc(task.id)}">继续 / 重试未完成步骤</button>`:''}${active?`<button class="text-button danger" data-import-cancel="${esc(task.id)}">取消后续处理</button>`:''}</div></article>`;
    }).join('')}</div>`;
  }
  function importsArea(){return `<div class="eyebrow">IMPORT & PROCESS</div><div class="page-heading"><div><h1>导入一次，按需读懂。</h1><p>下载、解析、翻译与粗读分别记录进度；失败的步骤可以单独重试。</p></div><button class="button" data-action="refresh-imports">刷新任务</button></div>${importer()}<div class="section-heading"><h2>导入与后台任务</h2><span>自动更新 · 已入库原文始终保留</span></div><div id="import-results" aria-live="polite">${importList()}</div>`;}
  function sourcesArea(){
    const filtered=state.sources.filter(s=>(state.priority==='all'||s.priority===state.priority)&&[s.name,...(s.topics||[])].join(' ').toLowerCase().includes(state.sourceQuery.toLowerCase()));
    return `<div class="eyebrow">RESEARCH SOURCES</div><div class="page-heading"><div><h1>关注长期值得追踪的来源。</h1><p>保留 144 条机构与团队登记，以及你原来的分组与优先级。</p></div><span class="pill green">已关注 ${followed.size}</span></div><div class="notice">机构目录中的关注是个人偏好，尚未逐个接入采集器。首页已经接入的 HF 与媒体渠道会独立显示实时更新状态。</div><div class="library-toolbar"><label class="search-box">${icons.search}<input id="source-query" type="search" aria-label="搜索来源" placeholder="搜索机构、团队或研究方向" value="${esc(state.sourceQuery)}"></label><select id="source-priority" aria-label="筛选来源优先级">${['all','P0','P1','P2'].map(p=>`<option value="${p}" ${state.priority===p?'selected':''}>${p==='all'?'全部优先级':p}</option>`).join('')}</select></div><div class="result-caption">${filtered.length} 条匹配来源</div><div class="source-grid">${filtered.map(s=>`<article class="source-card"><div class="actions"><span class="pill ${s.priority==='P0'?'green':''}">${esc(s.priority)}</span><span class="grow"></span><button class="button small ${followed.has(String(s.id))?'':'subtle'}" data-follow="${esc(s.id)}">${followed.has(String(s.id))?'已关注':'＋ 关注'}</button></div><h2>${esc(s.name)}</h2><p>${esc((s.topics||[]).join(' · '))}</p><small>${esc(s.group_name||state.sourceGroups[s.group]||s.group||'研究来源')} · 来源登记</small></article>`).join('')||'<div class="empty-state">没有匹配来源。</div>'}</div>`;
  }
  function skillsArea(){
    const descriptions={'paper-guide':'先建立研究问题和阅读路线，再进入方法与实验。','paper-brief':'按全文证据生成摘要、卡片粗读或连贯总结。','paper-translate':'保留公式、图表和术语，逐页发布对照译文。','reading-notes-summary':'整理当前项目中的划线和个人笔记，区分论文结论与自己的判断。'};
    const search=state.discoveryConfig.web_search?.configured??false;
    return `<div class="eyebrow">SKILLS & SERVICES</div><div class="page-heading"><div><h1>每一步，都有明确的职责。</h1><p>模型与搜索在服务端配置；新任务使用当前 Skill，旧任务保留输入快照。</p></div><button class="button" data-action="refresh-capabilities">重新检查配置</button></div><div class="capability-grid"><div class="capability-box"><span class="status-dot ${search?'on':''}"></span><strong>全网搜索</strong><p>${search?esc(state.discoveryConfig.web_search?.provider||'搜索服务已配置'):'尚未配置。HF 与 arXiv 的独立学术检索不依赖全网搜索配置。'}</p></div><div class="capability-box"><span class="status-dot ${state.capabilities.ai_configured?'on':''}"></span><strong>阅读与摘要模型</strong><p>${state.capabilities.ai_configured?esc(state.capabilities.ai_model||'已配置'):'尚未配置；下载、PDF 阅读与笔记仍可使用。'}</p></div><div class="capability-box"><span class="status-dot ${state.capabilities.translation_configured?'on':''}"></span><strong>逐页翻译</strong><p>${state.capabilities.translation_configured?'翻译服务已配置，可在导入或阅读时按需启动。':'BabelDOC 待配置，可先导入已有的单语译文 PDF。'}</p></div></div><details class="service-config"><summary>部署配置说明</summary><p>全网搜索可使用自建 SearXNG：设置 SEARCH_PROVIDER=searxng、SEARXNG_BASE_URL，且实例需要启用 JSON 格式；也可设置 SEARCH_PROVIDER=brave、BRAVE_API_KEY。</p><p>阅读模型使用 AI_BASE_URL、AI_MODEL、AI_API_KEY；逐页翻译另需 BABELDOC_BIN。修改环境后重启服务，再重试等待配置的任务。密钥只在服务端保存，不填入本页面。</p></details><div class="skill-grid">${state.skills.map(s=>`<article class="skill-card"><span class="skill-icon">${icons.spark}</span><div class="eyebrow">${esc(s.id)}</div><h2>${esc(s.name||s.id)}</h2><p>${esc(descriptions[s.id]||s.description)}</p>${s.modes?.length?`<div class="paper-projects">${s.modes.map(m=>`<span>${esc(m)}</span>`).join('')}</div>`:''}<small>skills/${esc(s.id)}/SKILL.md</small></article>`).join('')||'<div class="notice">Skill 清单暂时不可用。</div>'}</div>`;
  }
  function render(){
    const titles={discover:'搜索与推荐',library:'我的论文库',imports:'导入与任务',sources:'来源与关注',skills:'Skills 与服务'};
    const contents={discover,library,imports:importsArea,sources:sourcesArea,skills:skillsArea};
    app.innerHTML=`<div class="workspace-layout workspace-v2">${sidebar()}<main class="workspace-main"><header class="workspace-topbar"><span>研究空间 <span class="crumb-separator">/</span> ${titles[state.view]}</span><span class="pill">PaperStudio 0.2</span></header><div class="workspace-content">${contents[state.view]()}</div></main></div>`;
    updateURL();updateImportCount();
  }
  function updateImportCount(){const count=state.imports.filter(t=>['queued','running'].includes(t.status)).length;const el=qs('#active-import-count');if(el){el.textContent=count?String(count):'';el.hidden=!count;}}
  function paintDiscovery(){const slot=qs('#discovery-results');if(slot)slot.innerHTML=discoveryResults();const restore=qs('#restore-hidden-button');if(restore)restore.hidden=!hiddenItems.size;}
  async function loadDiscovery({refresh=false}={}){
    const seq=++discoverySequence;state.discoveryLoading=true;state.discoveryError='';paintDiscovery();
    try{
      let path;
      if(state.discoveryMode==='search'){
        const query=state.searchQuery.trim();if(!query)throw new Error('请先输入检索词。');
        path='/discovery/search?'+new URLSearchParams({q:query,scope:state.searchScope,page:String(state.searchPage)});
      }else{
        if(!state.feedSources.size){state.discoveryItems=[];state.discoveryStatuses=[];state.fetchedAt=null;return;}
        path='/discovery/feed?'+new URLSearchParams({sources:[...state.feedSources].join(','),interests:state.interests,limit:'30',refresh:String(refresh)});
      }
      const result=await api(path);if(seq!==discoverySequence)return;state.discoveryItems=list(result);state.discoveryStatuses=result.statuses||[];state.fetchedAt=result.fetched_at||null;
    }catch(e){if(seq!==discoverySequence)return;state.discoveryItems=[];state.discoveryStatuses=[];state.discoveryError=e.message;}
    finally{if(seq===discoverySequence){state.discoveryLoading=false;paintDiscovery();}}
  }
  async function loadPapers(){
    const seq=++querySequence;state.loading=true;if(qs('#library-results'))qs('#library-results').innerHTML=paperList();
    const query=new URLSearchParams();if(state.projectId)query.set('project_id',state.projectId);if(state.query)query.set('q',state.query);if(state.projectId&&state.includeChildren)query.set('include_children','true');
    try{const result=await api('/papers?'+query);if(seq!==querySequence)return;state.papers=list(result);state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=paperList();}
    catch(e){if(seq!==querySequence)return;state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=`<div class="error-panel">${esc(e.message)}</div>`;}
  }
  function schedulePoll(){clearTimeout(pollTimer);if(!pageActive)return;const active=state.imports.some(t=>['queued','running'].includes(t.status));if(active||state.view==='imports')pollTimer=setTimeout(()=>loadImports(),active?2200:12000);}
  async function loadImports(){
    const seq=++importsSequence;state.importLoading=true;
    try{const result=await api('/imports');if(seq!==importsSequence)return;const previousPapers=new Set(state.imports.map(task=>task.paper_id).filter(Boolean));state.imports=list(result);state.importError='';updateImportCount();if(state.view==='library'&&state.imports.some(task=>task.paper_id&&!previousPapers.has(task.paper_id)))await loadPapers();}
    catch(e){if(seq!==importsSequence)return;state.importError=e.message;}
    finally{if(seq===importsSequence){state.importLoading=false;if(qs('#import-results'))qs('#import-results').innerHTML=importList();schedulePoll();}}
  }
  async function refreshCapabilities(){const [caps,config]=await Promise.allSettled([api('/capabilities'),api('/discovery/config')]);if(caps.status==='fulfilled')state.capabilities=caps.value;if(config.status==='fulfilled')state.discoveryConfig=config.value;}
  async function setView(view){if(!views.has(view))return;state.view=view;state.editProject=false;render();if(view==='library')await loadPapers();if(view==='discover'&&!state.discoveryItems.length&&!state.discoveryLoading)await loadDiscovery();if(view==='imports')await loadImports();schedulePoll();}
  function openImport({url='',title='',paperId=null,projectId}={}){
    state.importer={...state.importer,kind:paperId?'existing':'url',url,title,paperId,file:null,projectId:projectId||state.projectId||state.importer.projectId||'inbox'};
    if(!state.projects.some(p=>p.id===state.importer.projectId))state.importer.projectId=state.projects[0]?.id||'inbox';
    state.importFailure='';state.importMessage='';state.view='imports';render();loadImports();qs('#import-form input')?.focus();
  }
  function syncProcessing(form){const p=state.importer.processing;p.translate=form.elements.translate.checked;p.guide=form.elements.guide.checked;p.brief_modes=['abstract','cards','summary'].filter(key=>form.elements[key].checked);p.parse=form.elements.parse.checked||p.guide||p.brief_modes.length>0;form.elements.parse.checked=p.parse;const hint=qs('#import-parse-hint');if(hint)hint.textContent=(p.guide||p.brief_modes.length?'已选择全文 AI 任务，会先完成全文解析。':'下载与保存 PDF 不依赖模型。')+(!state.capabilities.ai_configured?' 当前模型未配置，AI 步骤会等待配置，原文仍可入库。':'');}
  app.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.view){await setView(button.dataset.view);return;}
      if('project' in button.dataset){state.projectId=button.dataset.project;await setView('library');return;}
      if(button.dataset.importKind){state.importer.kind=button.dataset.importKind;state.importer.paperId=null;state.importFailure='';render();return;}
      if(button.dataset.follow){const id=button.dataset.follow;followed.has(id)?followed.delete(id):followed.add(id);remember('paperstudio-source-follows',[...followed]);render();toast('关注偏好已保存');return;}
      if(button.dataset.processPaper){const p=state.papers.find(x=>x.id===button.dataset.processPaper);if(p)openImport({paperId:p.id,title:p.title,projectId:button.dataset.processProject});return;}
      if('importResult' in button.dataset||'importArticle' in button.dataset){const index=Number(button.dataset.importResult??button.dataset.importArticle),item=state.discoveryItems[index];if(!item)return;const urls=paperURLs(item);const url='importArticle' in button.dataset?urls[Number(button.dataset.linkIndex)]:urls[0]||(item.kind==='paper'?safeURL(item.url):'');if(url)openImport({url,title:item.kind==='paper'?item.title:''});return;}
      if('hideResult' in button.dataset){const item=state.discoveryItems[Number(button.dataset.hideResult)];if(item){hiddenItems.add(String(item.id||item.url));remember('paperstudio-hidden-discovery',[...hiddenItems]);paintDiscovery();}return;}
      if(button.dataset.searchPage){state.searchPage=Math.max(1,Math.min(state.searchScope==='hf'?5:10,Number(button.dataset.searchPage))); await loadDiscovery();return;}
      if(button.dataset.importCancel||button.dataset.importRetry){button.disabled=true;await api(`/imports/${encodeURIComponent(button.dataset.importCancel||button.dataset.importRetry)}/${button.dataset.importCancel?'cancel':'retry'}`,{method:'POST'});await loadImports();return;}
      switch(button.dataset.action){
        case'open-import':openImport();break;
        case'edit-project':state.editProject=!state.editProject;render();break;
        case'show-feed':state.discoveryMode='feed';state.discoveryError='';render();await loadDiscovery();break;
        case'refresh-feed':state.discoveryMode='feed';render();await loadDiscovery({refresh:true});break;
        case'retry-discovery':await loadDiscovery({refresh:true});break;
        case'restore-hidden':hiddenItems.clear();remember('paperstudio-hidden-discovery',[]);render();break;
        case'refresh-imports':await loadImports();break;
        case'refresh-capabilities':button.disabled=true;await refreshCapabilities();render();break;
      }
    }catch(e){toast(e.message);}finally{if(button.isConnected)button.disabled=false;}
  });
  app.addEventListener('input',event=>{
    const field=event.target;
    if(field.id==='library-query'){state.query=field.value;clearTimeout(debounce);debounce=setTimeout(loadPapers,220);}
    if(field.id==='source-query'){state.sourceQuery=field.value;const pos=field.selectionStart;render();const input=qs('#source-query');input.focus();input.setSelectionRange(pos,pos);}
    if(field.form?.id==='import-form'&&['url','title'].includes(field.name))state.importer[field.name]=field.value;
    if(field.form?.id==='discovery-search-form'&&field.name==='q')state.searchQuery=field.value;
    if(field.form?.id==='interests-form'&&field.name==='interests')state.interests=field.value;
  });
  app.addEventListener('change',async event=>{
    const field=event.target;
    if(field.id==='library-children'){state.includeChildren=field.checked;await loadPapers();}
    if(field.id==='library-status'){state.status=field.value;qs('#library-results').innerHTML=paperList();}
    if(field.id==='source-priority'){state.priority=field.value;render();}
    if(field.form?.id==='discovery-search-form'&&field.name==='scope')state.searchScope=field.value;
    if(field.dataset.feedSource){field.checked?state.feedSources.add(field.dataset.feedSource):state.feedSources.delete(field.dataset.feedSource);remember('paperstudio-feed-sources',[...state.feedSources]);state.discoveryMode='feed';render();await loadDiscovery();}
    if(field.form?.id==='import-form'){
      if(field.name==='project_id')state.importer.projectId=field.value;
      if(field.name==='file'){state.importer.file=field.files[0]||null;const label=field.parentElement.querySelector('small');if(label)label.textContent=state.importer.file?state.importer.file.name+' · '+(state.importer.file.size/1048576).toFixed(1)+' MB':'支持最大 50 MB 的未加密 PDF。';}
      if(field.type==='checkbox')syncProcessing(field.form);
    }
  });
  app.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;if(form.id==='import-form'&&state.importSubmitting)return;const fd=new FormData(form),submit=form.querySelector('[type=submit]')||form.querySelector('button');if(submit)submit.disabled=true;
    try{
      if(form.id==='new-project-form'){
        const created=await api('/projects',{method:'POST',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.projectId=created.id;await setView('library');toast('项目已创建');
      }else if(form.id==='edit-project-form'){
        await api('/projects/'+encodeURIComponent(state.projectId),{method:'PATCH',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.editProject=false;render();toast('项目已保存');
      }else if(form.id==='discovery-search-form'){
        state.searchQuery=String(fd.get('q')||'').trim();state.searchScope=String(fd.get('scope'));state.searchPage=1;state.discoveryMode='search';render();await loadDiscovery();
      }else if(form.id==='interests-form'){
        state.interests=String(fd.get('interests')||'');remember('paperstudio-interests',state.interests);state.discoveryMode='feed';render();await loadDiscovery();
      }else if(form.id==='import-form'){
        syncProcessing(form);const s=state.importer;s.projectId=String(fd.get('project_id')||s.projectId);s.title=String(fd.get('title')??s.title);state.importSubmitting=true;state.importFailure='';state.importMessage='正在提交导入和处理计划…';const feedback=qs('#import-feedback');feedback.textContent=state.importMessage;feedback.classList.remove('has-error');if(submit)submit.textContent='正在提交…';
        let task;
        if(s.kind==='file'){
          const file=s.file||fd.get('file');if(!file?.size)throw new Error('请选择 PDF 文件。');if(file.size>50*1048576)throw new Error('PDF 超过 50 MB 限制。');const body=new FormData();body.set('file',file);body.set('project_id',s.projectId);body.set('title',s.title);body.set('processing',JSON.stringify(s.processing));task=await api('/imports/file',{method:'POST',body});
        }else{
          const body={project_id:s.projectId,title:s.title,processing:{...s.processing}};
          if(s.kind==='existing')body.paper_id=s.paperId;else{const url=safeURL(fd.get('url'));if(!url)throw new Error('请填写有效的 HTTP(S) 论文链接。');s.url=url;body.url=url;}
          task=await api('/imports',{method:'POST',body});
        }
        state.importMessage=task.paper_id?'原文已入库，可从下方任务打开阅读；后台继续处理所选步骤。':'导入已排队。下载完成后即可打开原文，其余步骤继续在后台执行。';await loadImports();const updatedFeedback=qs('#import-feedback');if(updatedFeedback){updatedFeedback.textContent=state.importMessage;updatedFeedback.classList.remove('has-error');}toast('处理计划已保存');
      }else if(form.dataset.associate){await api(`/papers/${encodeURIComponent(form.dataset.associate)}/projects`,{method:'POST',body:{project_id:fd.get('project_id')}});toast('已关联项目');await loadPapers();}
    }catch(e){toast(e.message);if(form.id==='import-form'){state.importFailure=e.message;state.importMessage='';const feedback=qs('#import-feedback');if(feedback){feedback.textContent=e.message;feedback.classList.add('has-error');}}}
    finally{if(form.id==='import-form'){state.importSubmitting=false;if(submit?.isConnected)submit.textContent=state.importer.kind==='existing'?'创建处理计划':'导入并后台处理';}if(submit?.isConnected)submit.disabled=false;}
  });
  window.addEventListener('pagehide',()=>{pageActive=false;clearTimeout(pollTimer);});
  window.addEventListener('pageshow',event=>{pageActive=true;if(event.persisted)loadImports();});
  try{
    const [projects,caps,skills,sources,config]=await Promise.allSettled([api('/projects'),api('/capabilities'),api('/skills'),api('/sources'),api('/discovery/config')]);
    if(projects.status==='rejected')throw projects.reason;state.projects=list(projects.value);if(state.projectId&&!state.projects.some(p=>p.id===state.projectId))state.projectId='';
    if(!state.projects.some(p=>p.id===state.importer.projectId))state.importer.projectId=state.projects[0]?.id||'inbox';
    if(caps.status==='fulfilled')state.capabilities=caps.value;if(skills.status==='fulfilled')state.skills=list(skills.value);if(sources.status==='fulfilled'){state.sources=list(sources.value);state.sourceGroups=sources.value?.groups||{};}if(config.status==='fulfilled')state.discoveryConfig=config.value;
    render();loadImports();if(state.view==='discover')await loadDiscovery();else if(state.view==='library')await loadPapers();
  }catch(e){app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1>工作台暂时无法连接</h1><p>${esc(e.message)}</p><a class="button primary" href="/">重新连接</a></main>`;}
}
