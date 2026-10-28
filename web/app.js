import {feedbackMarkup,mountFeedback} from './paper-feedback.js';
import { mountReader } from './reader.js';

export let authSession = {user:null, csrf_token:''};

export const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
export const qs = (selector, parent = document) => parent.querySelector(selector);
export const list = payload => Array.isArray(payload) ? payload : payload?.items || payload?.papers || payload?.sources || [];
export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const request = { credentials:'same-origin', ...options, headers };
  if (!['GET','HEAD','OPTIONS'].includes((request.method || 'GET').toUpperCase()) && authSession.csrf_token) headers.set('X-CSRF-Token', authSession.csrf_token);
  if (request.body && !(request.body instanceof FormData) && typeof request.body !== 'string') {
    headers.set('Content-Type', 'application/json');
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(`/api${path}`, request);
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.error ?? payload;
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
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
export const jobLabels = {guide:'论文导读',brief:'论文摘要 / 粗读','selection-summary':'选区摘要','notes-summary':'读后笔记总结',translate:'全文翻译','paragraph-translate':'段落翻译'};
export const jobStatuses = {queued:'等待执行',running:'执行中',succeeded:'已完成',failed:'失败',cancelled:'已取消',interrupted:'执行中断'};
export const icons = {
  menu:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
  close:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
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
startApp();

async function startApp() {
  try {
    authSession = await api('/auth/session');
    if (!authSession.user) { mountAuthentication(authSession.setup_required); return; }
    installResearchEvents();
    window.addEventListener('pagehide',()=>{const app=qs('#app');if(app?.style)app.style.visibility='hidden';});
    window.addEventListener('pageshow',async event=>{if(!event.persisted)return;try{const latest=await api('/auth/session');if(!latest.user||latest.user.id!==authSession.user?.id){qs('#app').innerHTML='';location.reload();return;}authSession=latest;const app=qs('#app');if(app?.style)app.style.visibility='';}catch{qs('#app').innerHTML='<main class="boot-state"><h1 tabindex="-1">请重新连接研究空间</h1><a class="button primary" href="/">重新连接</a></main>';const app=qs('#app');if(app?.style)app.style.visibility='';}});
    if (route) mountReader({paperId:decodeURIComponent(route[1]), projectId:new URLSearchParams(location.search).get('project')});
    else mountWorkspace();
  } catch (error) {
    qs('#app').innerHTML=`<main class="boot-state"><h1 tabindex="-1">暂时无法打开研究空间</h1><p>${esc(error.message)}</p><a class="button primary" href="${route?esc(location.pathname+location.search):'/'}">重新连接</a></main>`;
  }
}

function mountAuthentication(setupRequired=false) {
  const app=qs('#app'), invitation=new URLSearchParams(location.search).get('invite')||'';
  let mode=setupRequired?'setup':invitation?'register':'login';
  function renderAuth(){
    const setup=mode==='setup', register=mode==='register';
    app.innerHTML=`<main class="auth-layout"><section class="auth-intro"><span class="brand-symbol">${icons.book}</span><h1 tabindex="-1">让阅读形成<br>自己的脉络。</h1><p>读懂论文，记录想法，和研究小组一起积累。</p></section><section class="auth-card"><h2>${setup?'创建管理员账号':register?'接受邀请，加入研究小组':'登录研究空间'}</h2><form id="auth-form">${setup?'<label>初始化令牌<input name="setup_token" type="password" required autocomplete="off"></label><p class="small muted">由部署管理员从服务端 bootstrap-token 文件或 PAPERSTUDIO_SETUP_TOKEN 获取。</p>':''}${register?`<label>邀请令牌<input name="invite_token" value="${esc(invitation)}" required autocomplete="off"></label>`:''}<label>用户名<input name="username" required minlength="3" maxlength="64" autocomplete="username"></label>${setup||register?'<label>显示名称<input name="display_name" maxlength="100" autocomplete="name"></label>':''}<label>密码<input name="password" type="password" required ${setup||register?'minlength="12"':''} autocomplete="${setup||register?'new-password':'current-password'}"></label><button class="button primary" type="submit">${setup?'创建并进入':register?'创建账号并加入':invitation?'登录并接受邀请':'登录'}</button><p id="auth-error" role="alert"></p></form>${!setup?`<button class="text-button auth-switch" data-auth-mode="${register?'login':'register'}">${register?'已有账号？登录后加入':'使用小组邀请注册'}</button>`:''}</section></main>`;
    qs('#auth-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),fd=new FormData(form);button.disabled=true;qs('#auth-error').textContent='';try{const body=Object.fromEntries(fd.entries());authSession=await api('/auth/'+mode,{method:'POST',body});if(mode==='login'&&invitation)await api('/groups/join',{method:'POST',body:{token:invitation}});location.assign(route?location.pathname+location.search:invitation?'/?view=groups':'/');}catch(error){qs('#auth-error').textContent=error.message;button.disabled=false;}});
    qs('[data-auth-mode]')?.addEventListener('click',event=>{mode=event.currentTarget.dataset.authMode;renderAuth();});
  }
  renderAuth();
}

function installResearchEvents(){
  let panelModule, opening=0;
  window.addEventListener('paperstudio:research',async event=>{const version=++opening;try{panelModule??=await import('./research-ui.js');if(version===opening)await panelModule.openResearchPanel(event.detail);}catch(error){toast(error.message);}});
  for(const name of ['paperstudio:reader-unmount','paperstudio:reader-paused','paperstudio:reader-disposed'])window.addEventListener(name,()=>{opening++;panelModule?.disposeResearchPanel();});
  window.addEventListener('pagehide',()=>{opening++;panelModule?.disposeResearchPanel();});
}

async function mountWorkspace() {
  const params = new URLSearchParams(location.search);
  const isAdmin=authSession.user?.is_admin||authSession.user?.role==='admin';
  const views = new Set(['discover','recommendations','catalog','library','imports','groups',...(isAdmin?['skills']:[])]);
  const stored = (key,fallback) => {try {const value=JSON.parse(localStorage.getItem(key+':'+authSession.user.id));return value??fallback;} catch {return fallback;}};
  const remember = (key,value) => {try {localStorage.setItem(key+':'+authSession.user.id,JSON.stringify(value));} catch {toast('浏览器无法保存偏好；论文和笔记仍保存在后端。');}};
  const selectedSources=stored('paperstudio-feed-sources',['hf','qbitai','jiqizhixin']);
  const state = {
    navOpen:false,catalogAdvancedOpen:false,disclosures:{},projects:[], papers:[], projectId:params.get('project')||'', view:params.get('invite')?'groups':params.get('view')==='sources'?'catalog':views.has(params.get('view'))?params.get('view'):(params.get('project')?'library':'discover'),
    query:'', status:'all', includeChildren:true, sources:[], sourceGroups:{}, sourceQuery:'', priority:'all', skills:[], capabilities:{}, discoveryConfig:{}, loading:true, editProject:false,
    searchQuery:'', discoveryDirection:'', discoveryTopic:'', discoveryTaxonomy:null, discoveryHasMore:false, discoveryFilterScope:'', discoveryDirections:[], searchScope:'web', searchPage:1, discoveryMode:'feed', discoveryItems:[], discoveryStatuses:[], discoveryLoading:false, discoveryError:'', fetchedAt:null,
    interests:String(stored('paperstudio-interests','')), feedSources:new Set(Array.isArray(selectedSources)?selectedSources.filter(s=>['hf','qbitai','jiqizhixin'].includes(s)):['hf','qbitai','jiqizhixin']),
    imports:[], importError:'', importLoading:false,
    catalogQuery:params.get('q')||'',catalogYear:'',catalogSource:'all',catalogInstitution:'',catalogConference:'',catalogConferenceYear:'',catalogPresentation:'',catalogVerification:'all',catalogDirection:'',catalogTopic:'',catalogSort:'votes',catalogPage:1,catalogPageSize:24,
    catalogItems:[],catalogTotal:0,catalogPages:0,catalogStats:null,catalogLoading:false,catalogError:'',catalogStatsError:'',catalogImporting:false,catalogHfWeek:'',catalogFeedback:'',catalogImportError:'',
    importer:{kind:'url',url:'',title:'',paperId:null,projectId:params.get('project')||authSession.user.inbox_id||'inbox',file:null,processing:{parse:true,translate:false,guide:false,brief_modes:[]}},
    importSubmitting:false, importMessage:'', importFailure:''
  };
  const follows=stored('paperstudio-source-follows',[]),hidden=stored('paperstudio-hidden-discovery',[]);
  const followed=new Set(Array.isArray(follows)?follows.map(String):[]),hiddenItems=new Set(Array.isArray(hidden)?hidden.map(String):[]);
  const app=qs('#app');
  const recommendationState={};
  const exploreState={q:params.get('q')||''},indexState={q:params.get('q')||'',mode:params.get('graph')||params.get('graph_catalog')?'graph':params.get('q')?'papers':'entities',graph:{focus:params.get('graph_catalog')?{catalog_id:params.get('graph_catalog')}:params.get('graph')?{paper:params.get('graph')}:undefined}};
  state.librarySection=params.get('view')==='imports'?'imports':(['papers','starred','follows','imports','tasks'].includes(params.get('section'))?params.get('section'):'papers');
  if(state.view==='recommendations')state.view='discover';
  if(state.view==='imports')state.view='library';
  let recommendationPanel=null,recommendationMount=0,feedbackController=null;
  function syncRecommendationProfile(profile){
    for(const key of [...followed])if(/^(institution|conference|author|category|topic):/.test(key))followed.delete(key);
    for(const name of profile.institutions)followed.add('institution:'+name);
    for(const name of profile.conferences)followed.add('conference:'+name);
    for(const [key,field] of [['author','authors'],['category','category_ids'],['topic','topic_ids']])for(const value of profile[field]||[])followed.add(key+':'+value);
    remember('paperstudio-source-follows',[...followed]);
  }
  const legacyRecommendationProfile=()=>({interests:state.interests,follows:[...followed]});
  const mobileNavigation=window.matchMedia?.('(max-width: 900px)');
  const sourceNames={hf:'Hugging Face',qbitai:'量子位',jiqizhixin:'机器之心',arxiv:'arXiv',web:'全网搜索',brave:'Brave',searxng:'SearXNG'};
  const modeNames={abstract:'精简摘要',cards:'卡片粗读',summary:'全文总结',all:'摘要与完整粗读'};
  const importStatuses={queued:'排队中',running:'处理中',succeeded:'已完成',partial:'部分完成',blocked:'等待配置',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const stepNames={download:'下载 PDF',parse:'解析全文',translate:'逐页翻译',guide:'论文导读',brief:'论文粗读'};
  let querySequence=0,discoverySequence=0,importsSequence=0,catalogSequence=0,catalogController=null,debounce,pollTimer,pageActive=true;
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
  function updateURL(){const next=new URLSearchParams();if(state.projectId)next.set('project',state.projectId);if(state.view!=='discover')next.set('view',state.view);if(state.view==='library'&&state.librarySection!=='papers')next.set('section',state.librarySection);history.replaceState(null,'',`/${next.size?'?'+next:''}`);}
  function tree(parent=null,depth=0,seen=new Set()) {
    return state.projects.filter(p=>p.id!=='corpus'&&!/^批量成果/.test(p.name)&&(p.parent_id||null)===parent&&!seen.has(p.id)).map(p=>{const next=new Set(seen);next.add(p.id);return `<button class="project-item ${state.view==='library'&&state.projectId===p.id?'active':''}" style="--depth:${depth}" data-project="${esc(p.id)}" ${state.view==='library'&&state.projectId===p.id?'aria-current="page"':''}>${icons.folder}<span>${esc(p.name)}</span></button>${tree(p.id,depth+1,next)}`;}).join('');
  }
  function projectOptions(selected='',includeRoot=false,excludeId='') {
    const blocked=new Set([excludeId]);let changed=true;
    while(changed){changed=false;for(const p of state.projects){if(blocked.has(p.parent_id)&&!blocked.has(p.id)){blocked.add(p.id);changed=true;}}}
    return (includeRoot?'<option value="">根目录</option>':'')+state.projects.filter(p=>p.can_edit!==false&&p.id!=='corpus'&&!/^批量成果/.test(p.name)&&(!excludeId||!blocked.has(p.id))).map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(projectPath(p.id))}</option>`).join('');
  }
  function sidebar(){
    const nav=[['discover','搜索与推荐','search'],['catalog','论文索引','source'],['library','我的论文库','book'],['groups','研究小组','folder'],...(isAdmin?[['skills','管理员设置','spark']]:[])];
    return `<aside id="workspace-navigation" class="workspace-sidebar" aria-label="研究空间导航"><div class="workspace-nav-head"><a href="/" class="brand"><span class="brand-symbol">${icons.book}</span><span><strong>PaperStudio</strong><small>让阅读形成自己的脉络</small></span></a><button class="workspace-nav-close icon-button" data-action="close-navigation" aria-label="关闭导航">${icons.close}</button></div><div class="workspace-identity"><span class="avatar">${esc((authSession.user.display_name||authSession.user.username||'我').slice(0,1))}</span><div><strong>${esc(authSession.user.display_name||authSession.user.username)}</strong><small>${isAdmin?'管理员':'个人研究空间'}</small></div><button class="text-button" data-action="logout">退出</button></div><nav aria-label="工作台导航">${nav.map(([key,name,icon])=>`<button class="nav-item ${state.view===key?'active':''}" data-view="${key}" ${state.view===key?'aria-current="page"':''}>${icons[icon]}${name}${key==='imports'?'<span id="active-import-count" class="nav-count"></span>':''}</button>`).join('')}</nav><div class="project-label">研究项目</div><button class="project-item ${state.view==='library'&&!state.projectId?'active':''}" data-project="" ${state.view==='library'&&!state.projectId?'aria-current="page"':''}>${icons.folder}<span>全部论文</span></button><div class="project-tree">${tree()}</div><details class="new-project"><summary>${icons.plus}新建项目</summary><form id="new-project-form"><label>项目名称<input name="name" required maxlength="100" placeholder="例如：文档理解" autocomplete="off"></label><label>所属目录<select name="parent_id">${projectOptions(state.projectId,true)}</select></label><button class="button primary" type="submit">创建项目</button></form></details><div class="sidebar-foot"><span class="connection-dot"></span>论文、笔记与研究成果已连接<small>按项目积累 · 按需共享</small></div></aside>`;
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
  function resultSkeleton(label){return `<div class="results-loading" role="status"><p>${esc(label)}</p><div class="result-skeleton" aria-hidden="true">${Array.from({length:3},()=>'<div class="skeleton-result"><span class="skeleton-line short"></span><span class="skeleton-line"></span><span class="skeleton-line medium"></span></div>').join('')}</div></div>`;}
  function discoveryResults(){
    if(state.discoveryLoading&&!state.discoveryItems.length)return resultSkeleton('正在获取论文…');
    const health=sourceStatusCards();
    const updateNotice=state.discoveryLoading?'<p class="results-update" role="status">正在更新，以下保留上次结果。</p>':'';
    const errorNotice=state.discoveryError?`<div class="error-panel"><strong>这次获取未完成</strong><p>${esc(state.discoveryError)}</p><button class="button" data-action="retry-discovery">重试</button>${isAdmin?'<button class="text-button" data-view="skills">查看服务配置</button>':''}</div>`:'';
    if(state.discoveryError&&!state.discoveryItems.length)return errorNotice;
    const rows=state.discoveryItems.map((item,index)=>({item,index})).filter(({item})=>state.discoveryMode==='search'||!hiddenItems.has(String(item.id||item.url)));
    return `${updateNotice}${errorNotice}<div class="result-caption"><span>${rows.length} 条${state.discoveryMode==='search'?'检索结果':'推荐内容'}</span><span>${state.discoveryMode==='search'?`第 ${state.searchPage} 页 · ${esc({web:'全网',arxiv:'arXiv',hf:'Hugging Face'}[state.searchScope])}`:'按兴趣、新鲜度与来源热度排序'}${state.fetchedAt?' · '+esc(dateText(state.fetchedAt)):''}</span></div><div class="discovery-grid">${rows.map(({item,index})=>{
      const papers=paperURLs(item),isPaper=item.kind==='paper',url=safeURL(item.url),importURL=isPaper?(papers[0]||url):papers[0];
      const reasons=Array.isArray(item.reasons)?item.reasons:[];
      const authors=Array.isArray(item.authors)?item.authors.map(a=>typeof a==='string'?a:a?.name||'').filter(Boolean).join(', '):String(item.authors||'');
      const sources=item.source_ids||item.sources;
      const sourceList=Array.isArray(sources)?sources.map(s=>sourceNames[typeof s==='string'?s:s?.source_id]||String(s?.name||s)).join(' · '):sourceNames[item.source_id]||item.source_id||item.provider||'外部来源';
      return `<article class="discovery-card"><div class="discovery-card-meta"><span class="pill ${isPaper?'green':''}">${isPaper?'论文':'媒体文章'}</span><span>${esc(sourceList)}</span><span>${esc(dateText(item.published_at))}</span></div><h2><button class="discovery-title title-button" data-open-result="${index}">${esc(item.title)}</button></h2>${directionTags(item.directions,'discover',item.classification)}${authors?`<p class="discovery-authors">${esc(authors)}</p>`:''}<p class="discovery-snippet">${esc(String(item.snippet||item.summary||'来源暂未提供摘要。').slice(0,900))}</p>${reasons.length?`<div class="recommend-reasons">${reasons.slice(0,4).map(r=>`<span>${esc(typeof r==='string'?r:r.label||r.reason||'')}</span>`).join('')}</div>`:''}<div class="discovery-card-actions">${importURL?`<button class="button primary small" data-import-result="${index}">${icons.plus}加入项目并处理</button>`:`<span class="muted">未识别到可导入论文链接</span>`}${publicLink(url,isPaper?'论文原页 ↗':'阅读媒体原文 ↗')} ${state.discoveryMode==='feed'?`<button class="text-button" data-hide-result="${index}">不感兴趣</button>`:''}</div>${!isPaper&&papers.length?`<div class="article-paper-links"><span>关联论文：</span>${papers.slice(0,5).map((link,n)=>`${publicLink(link,`论文 ${n+1} ↗`)}<button class="text-button" data-import-article="${index}" data-link-index="${n}">导入</button>`).join('')}</div>`:''}</article>`;
    }).join('')||'<div class="empty-state"><h2>暂无可展示内容</h2><p>试试更宽的查询，或清除方向限制。</p><button class="button" data-action="reset-discovery">清除筛选并查看推荐</button></div>'}</div>${state.discoveryMode==='search'?`<div class="search-pagination"><button class="button" data-search-page="${state.searchPage-1}" ${state.searchPage<=1?'disabled':''}>上一页</button><span>第 ${state.searchPage} 页${state.searchScope==='hf'&&state.searchPage>=5?' · 已到 HF 检索上限':''}</span><button class="button" data-search-page="${state.searchPage+1}" ${!state.discoveryHasMore||state.searchPage>=(state.searchScope==='hf'?5:10)?'disabled':''}>下一页</button></div>`:''}${health?`<details class="source-status-disclosure" id="source-status-disclosure" ${state.disclosures['source-status-disclosure']?'open':''}><summary>来源更新状态</summary><div class="source-health-grid">${health}</div></details>`:''}`;
  }
  function discover(){return '<div id="explore-panel"><p role="status">正在载入论文…</p></div>';}
  function catalogMetric(value) {
    return value!==null && value!==undefined && Number.isFinite(Number(value)) && Number(value)>=0 ? Number(value).toLocaleString('zh-CN') : '未提供';
  }
  function catalogProvenance(entry) {
    const value=entry && typeof entry==='object'?entry:{};
    const parts=[value.name||value.dataset_name||value.dataset, (value.source_id||value.source)==='hf'?'HF 热榜':(value.source_id||value.source)==='institution'?'机构清单':value.source_id||value.source, value.week||value.ranking_week, value.rank!==null&&value.rank!==undefined?'第 '+String(value.rank)+' 名':'', value.observed_at||value.fetched_at||value.snapshot_at];
    return `<li>${parts.filter(v=>typeof v==='string'||typeof v==='number').map(v=>esc(v)).join(' · ')||'已登记来源'}${value.url||value.source_url?' '+publicLink(value.url||value.source_url,'来源 ↗'):''}${value.note||value.notes?'<p>'+esc(value.note||value.notes)+'</p>':''}${value.institution_evidence?'<small>机构记录依据：'+esc(value.institution_evidence)+'</small>':''}</li>`;
  }
  function catalogSummary(){
    if(!state.catalogStats)return `<div class="notice">${state.catalogStatsError?esc(state.catalogStatsError):'正在读取索引覆盖范围…'}</div>`;
    const stats=state.catalogStats,years=Array.isArray(stats.by_year)?stats.by_year:[],sources=Array.isArray(stats.sources)?stats.sources:[],coverage=Array.isArray(stats.coverage)?stats.coverage:[],last=stats.last_import;
    return `<p class="catalog-counts"><span><strong>${catalogMetric(stats.total)}</strong> 篇去重论文</span>${stats.source_records!==undefined?`<span>来自 <strong>${catalogMetric(stats.source_records)}</strong> 条来源记录</span>`:''}</p><details class="catalog-coverage" id="catalog-coverage-details" ${state.disclosures['catalog-coverage-details']?'open':''}><summary>数据覆盖与来源说明${last?' · 最近更新 '+esc(dateText(last.created_at)):''}</summary><dl class="coverage-breakdown">${years.map(y=>`<div><dt>${esc(y.year??'年份未知')} 年</dt><dd>${catalogMetric(y.count)} 篇</dd></div>`).join('')}${sources.map(source=>`<div><dt>${esc(source.id==='hf'?'HF 来源':source.id==='institution'?'机构来源':source.id==='conference'?'会议来源':source.id)}</dt><dd>${catalogMetric(source.count)} 篇 · 可交叉收录</dd></div>`).join('')}</dl><p>这里只统计实际收录的记录，不代表 2025–2026 年全部论文或所有周榜。点赞和 GitHub Stars 是采集时的快照；缺失指标显示“未提供”。机构信息沿用来源记录，待核机构不代表已核实作者隶属；论文可能属于多个机构。HF 上榜年份与论文发表年份可能不同。会议展示年份单独记录；常规口头报告与精选 Oral 保留区别。待核验候选保留并明确标注，不混算为已核实精选。</p>${coverage.length?`<ul>${coverage.map(d=>`<li><strong>${esc(d.name||'未命名数据集')}</strong>${d.source_id||d.source?' · '+esc(d.source_id||d.source):''}${d.ranking_week?' · '+esc(d.ranking_week):''}${d.coverage_start||d.coverage_end?' · '+esc(d.coverage_start||'未知')+' — '+esc(d.coverage_end||'未知'):''}${d.source_url||d.url?' · '+publicLink(d.source_url||d.url,'来源 ↗'):''}${d.note||d.description?'<p>'+esc(d.note||d.description)+'</p>':''}</li>`).join('')}</ul>`:'<p>尚无覆盖范围登记。</p>'}${(stats.import_errors||[]).length?`<div class="catalog-import-errors"><strong>最近未完成的更新</strong><ul>${stats.import_errors.slice(0,5).map(e=>`<li>${esc(e.name||'索引更新')} · ${esc(e.error||'更新失败')} · ${esc(dateText(e.created_at))}</li>`).join('')}</ul></div>`:''}${state.catalogStatsError?`<p class="task-error">${esc(state.catalogStatsError)}</p>`:''}</details>`;
  }
  function directionTags(values,scope='catalog',classification=null){
    if(classification?.primary){
      const labels=[classification.primary,...(classification.secondary||[])];
      return `<div class="direction-tags direction-hierarchy" aria-label="论文方向">${labels.map((item,index)=>`<span class="direction-path"><small>${index?'关联':'主方向'}</small><button data-direction="${esc(item.category_id)}" data-direction-scope="${scope}" title="筛选一级方向">${esc(item.category_name)}</button>${item.topic_id?`<span aria-hidden="true">›</span><button data-direction="${esc(item.category_id)}" data-topic="${esc(item.topic_id)}" data-direction-scope="${scope}" title="筛选二级方向">${esc(item.topic_name)}</button>`:'<span class="muted">二级待确认</span>'}</span>`).join('')}</div>`;
    }
    const directions=Array.isArray(values)?values:[];
    return `<div class="direction-tags">${(directions.length?directions:['未分类']).map(value=>{const label=typeof value==='string'?value:value.name||value.label;return `<button data-direction="${esc(label)}" data-direction-scope="${scope}" title="按此方向筛选">${esc(label)}</button>`;}).join('')}</div>`;
  }
  function directionCategories(scope){
    const tree=scope==='discover'?state.discoveryTaxonomy:state.catalogStats?.direction_taxonomy;
    if(tree?.categories)return tree.categories;
    const values=scope==='discover'?state.discoveryDirections:(state.catalogStats?.directions||[]);
    return values.map(value=>typeof value==='string'?{id:value,name:value,topics:[]}:{...value,id:value.name,topics:[]});
  }
  function directionOptions(scope,level){
    const discover=scope==='discover',direction=discover?state.discoveryDirection:state.catalogDirection,topic=discover?state.discoveryTopic:state.catalogTopic;
    const categories=directionCategories(scope),selected=level==='topic'?topic:direction;
    const option=item=>`<option value="${esc(item.id)}" ${selected===item.id?'selected':''}>${esc(item.name)}${item.match_count!==undefined?' · '+item.match_count:item.count!==undefined?' · '+item.count:''}</option>`;
    if(level==='topic')return '<option value="">全部二级方向</option>'+categories.filter(c=>!direction||c.id===direction).filter(c=>c.topics?.length).map(c=>`<optgroup label="${esc(c.name)}">${c.topics.map(option).join('')}</optgroup>`).join('');
    return '<option value="">全部一级方向</option>'+categories.map(option).join('')+(direction&&!categories.some(c=>c.id===direction)?option({id:direction,name:direction}):'');
  }
  function setDirection(scope,direction,topic=''){
    const parent=topic&&directionCategories(scope).find(c=>c.topics?.some(t=>t.id===topic));
    if(scope==='discover'){state.discoveryDirection=parent?.id||direction;state.discoveryTopic=topic;state.searchPage=1;}
    else{state.catalogDirection=parent?.id||direction;state.catalogTopic=topic;state.catalogPage=1;}
  }
  function discoveryDirectionFilter(){
    return `<div class="direction-filter"><label>一级方向<select id="discovery-direction">${directionOptions('discover','category')}</select></label><label>二级方向<select id="discovery-topic">${directionOptions('discover','topic')}</select></label>${state.discoveryDirection||state.discoveryTopic?'<button class="text-button" data-action="clear-discovery-direction">清除方向</button>':''}<span class="small muted">关键词初标，待确认；筛选包含主方向及关联方向${state.discoveryFilterScope?' · '+esc(state.discoveryFilterScope):''}</span></div>`;
  }
  function catalogResults(){
    if(state.catalogLoading&&!state.catalogItems.length)return resultSkeleton('正在检索论文索引…');
    const updateNotice=state.catalogLoading?'<p class="results-update" role="status">正在更新筛选结果，以下保留上次结果。</p>':'';
    const errorNotice=state.catalogError?`<div class="error-panel"><strong>索引读取未完成</strong><p>${esc(state.catalogError)}</p><button class="button" data-action="refresh-catalog">重试</button></div>`:'';
    if(state.catalogError&&!state.catalogItems.length)return errorNotice;
    return `${updateNotice}${errorNotice}<div class="result-caption"><span>${catalogMetric(state.catalogTotal)} 篇匹配论文</span><span>全量保留 · 按需加入项目</span></div><div class="catalog-grid">${state.catalogItems.map(item=>{
      const url=safeURL(item.url),authors=Array.isArray(item.authors)?item.authors.map(a=>typeof a==='string'?a:a?.name||'').filter(Boolean):[],institutions=Array.isArray(item.institutions)?item.institutions:[],sources=Array.isArray(item.source_ids)?item.source_ids:[],provenance=Array.isArray(item.provenance)?item.provenance:[],conferences=Array.isArray(item.conferences)?item.conferences:[];
      return `<article class="catalog-card"><div class="discovery-card-meta"><span class="pill">${esc(item.year??'发表年份未提供')}</span>${sources.map(source=>`<span>${esc(source==='hf'?'HF 热榜':source==='institution'?'机构清单':source==='conference'?'会议清单':source)}</span>`).join('')}</div><h2><button class="discovery-title title-button" data-open-catalog="${esc(item.id)}">${esc(item.title)}</button></h2>${directionTags(item.directions,'catalog',item.classification)}<div class="catalog-institutions">${institutions.map(value=>{const name=typeof value==='string'?value:value.name;return `<button class="text-button" data-catalog-institution="${esc(name)}">${esc(name)}</button>`;}).join(' · ')||'机构未提供'}</div>${conferences.length?`<div class="conference-tags">${conferences.map(conf=>`<button class="conference-tag ${conf.verification_status==='pending'?'pending':''}" data-catalog-conference="${esc(conf.conference)}"><strong>${esc(conf.conference)} ${esc(conf.year)}</strong><span>${esc(conf.presentation||'展示标签未提供')} · ${conf.verification_status==='pending'?'待核验':'来源标为确认'}${conf.selectivity?' · '+esc({'regular-presentation':'常规展示','not-established':'精选性质未核实','selective':'精选展示'}[conf.selectivity]||conf.selectivity):''}</span></button>`).join('')}</div>`:''}${authors.length?`<p class="discovery-authors">${esc(authors.join(', '))}</p>`:''}${item.abstract?`<p class="discovery-snippet">${esc(String(item.abstract).slice(0,650))}</p>`:''}<div class="catalog-metrics"><span>HF 点赞 <strong>${catalogMetric(item.hf_upvotes)}</strong></span><span>GitHub Stars <strong>${catalogMetric(item.github_stars)}</strong></span>${publicLink(item.github_url,'代码 ↗')}</div><div class="discovery-card-actions"><button class="button primary small" data-catalog-paper="${esc(item.id)}">${icons.plus}${item.resolve?.action==='search'?'查找论文入口':'选择并加入项目'}</button>${publicLink(url,'论文原页 ↗')}</div>${item.direction_evidence?.status==='heuristic-unverified'?'<small class="muted">方向标签由关键词辅助分类，可继续核对。</small>':''}${provenance.length?`<details class="catalog-provenance"><summary>${catalogMetric(item.source_record_count??provenance.length)} 条来源记录</summary><ul>${provenance.map(catalogProvenance).join('')}</ul></details>`:''}</article>`;
    }).join('')||'<div class="empty-state"><h2>暂无匹配论文</h2><p>可以移除部分条件，或回到完整论文索引。</p><button class="button" data-action="clear-catalog-filters">清除全部筛选</button></div>'}</div><div class="search-pagination"><button class="button" data-catalog-page="${state.catalogPage-1}" ${state.catalogPage<=1?'disabled':''}>上一页</button><span>第 ${state.catalogPage} / ${Math.max(1,state.catalogPages)} 页</span><button class="button" data-catalog-page="${state.catalogPage+1}" ${state.catalogPage>=state.catalogPages?'disabled':''}>下一页</button></div>`;
  }
  function catalogFollows(){
    const groups=[['institution','单位',state.catalogStats?.institutions||[]],['conference','会议',state.catalogStats?.conferences||[]]];
    const chosen=[...followed].filter(key=>/^(institution|conference):/.test(key));
    return `<details class="catalog-follows" id="catalog-follows-details" ${state.disclosures['catalog-follows-details']?'open':''}><summary>我的关注与分类目录 <span>${chosen.length} 个已关注</span></summary><div class="followed-chips">${chosen.map(key=>{const [type,...rest]=key.split(':');const name=rest.join(':');return `<button data-follow-filter="${esc(key)}">${esc(name)} ↗</button>`;}).join('')||'<span class="muted">关注单位或会议，之后可以直接查看该类目的全部论文。</span>'}</div>${groups.map(([type,label,values])=>`<h3>${label}</h3><div class="catalog-follow-grid">${values.map(value=>{const name=typeof value==='string'?value:value.name,key=type+':'+name;return `<div><button class="text-button" data-follow-filter="${esc(key)}">${esc(name)} <small>${catalogMetric(value.count)} 篇</small></button><button class="text-button" data-follow="${esc(key)}" aria-label="${followed.has(key)?'取消关注':'关注'} ${esc(name)}">${followed.has(key)?'已关注':'＋关注'}</button></div>`;}).join('')}</div>`).join('')}</details>`;
  }
  const catalogFilterFields={q:['catalogQuery','查询'],direction:['catalogDirection','一级方向'],topic:['catalogTopic','二级方向'],institution:['catalogInstitution','单位'],conference:['catalogConference','会议'],year:['catalogYear','发表年份'],source:['catalogSource','来源'],conference_year:['catalogConferenceYear','会议年份'],presentation:['catalogPresentation','展示标签'],verification_status:['catalogVerification','核验状态']};
  function catalogFilterChips(){
    const labels={hf:'HF 热榜',institution:'重点机构',conference:'会议清单',confirmed:'来源标为确认',pending:'待核验候选'};
    for(const category of directionCategories('catalog')){labels[category.id]=category.name;for(const topic of category.topics||[])labels[topic.id]=topic.name;}
    const active=Object.entries(catalogFilterFields).filter(([, [field]])=>state[field]&&state[field]!=='all');
    return active.length?`<span class="filter-caption">当前筛选</span>${active.map(([name,[field,label]])=>`<button type="button" class="filter-chip" data-clear-catalog="${name}" aria-label="移除${esc(label)}筛选：${esc(labels[state[field]]||state[field])}"><span>${esc(label)}：${esc(labels[state[field]]||state[field])}</span>${icons.close}</button>`).join('')}<button class="text-button" type="button" data-action="clear-catalog-filters">清除全部</button>`:'';
  }
  function catalogArea(){return '<div id="index-panel"><p role="status">正在载入索引…</p></div>';}
  function catalogInstitutionOptions(){return (state.catalogStats?.institutions||[]).map(i=>`<option value="${esc(i.name)}">${esc(i.name)} · ${catalogMetric(i.count)} 篇</option>`).join('');}
  function paintCatalog(){
    const results=qs('#catalog-results');if(results){results.innerHTML=catalogResults();results.setAttribute?.('aria-busy',String(state.catalogLoading));}
    const chips=qs('#catalog-active-filters');if(chips)chips.innerHTML=catalogFilterChips();
    const summary=qs('#catalog-summary');if(summary)summary.innerHTML=catalogSummary();
    const institutions=qs('#catalog-institutions');if(institutions)institutions.innerHTML=catalogInstitutionOptions();
    const follows=qs('#catalog-follows');if(follows)follows.innerHTML=catalogFollows();
    const form=qs('#catalog-search-form');if(form?.elements){if(form.elements.direction)form.elements.direction.innerHTML=directionOptions('catalog','category');if(form.elements.topic)form.elements.topic.innerHTML=directionOptions('catalog','topic');}if(form?.elements){for(const [name,key,field] of [['conference','conferences','catalogConference'],['presentation','presentations','catalogPresentation']]){const select=form.elements[name];if(select){const rows=state.catalogStats?.[key]||[];select.innerHTML='<option value="">全部</option>'+rows.map(row=>`<option value="${esc(row.name)}" ${state[field]===String(row.name)?'selected':''}>${esc(row.name)} · ${row.count}</option>`).join('');}}}
  }
  async function loadCatalog({stats=false}={}){
    const seq=++catalogSequence;catalogController?.abort();catalogController=new AbortController();
    const signal=catalogController.signal;state.catalogLoading=true;state.catalogError='';paintCatalog();
    const query=new URLSearchParams({source:state.catalogSource,sort:state.catalogSort,page:String(state.catalogPage),page_size:String(state.catalogPageSize)});
    for(const [name,field] of [['conference','catalogConference'],['conference_year','catalogConferenceYear'],['presentation','catalogPresentation'],['verification_status','catalogVerification'],['direction','catalogDirection'],['topic','catalogTopic']])if(state[field]&&state[field]!=='all')query.set(name,state[field]);
    if(state.catalogQuery.trim())query.set('q',state.catalogQuery.trim());if(state.catalogYear)query.set('year',state.catalogYear);if(state.catalogInstitution.trim())query.set('institution',state.catalogInstitution.trim());
    const results=await Promise.allSettled([api('/catalog?'+query,{signal}),stats||!state.catalogStats?api('/catalog/stats',{signal}):Promise.resolve(state.catalogStats),stats?api('/me/recommendation-profile',{signal}):Promise.resolve(null)]);
    if(seq!==catalogSequence||signal.aborted)return;
    const [papers,summary,profile]=results;
    if(profile.status==='fulfilled'&&profile.value?.configured)syncRecommendationProfile(profile.value);
    if(papers.status==='fulfilled'){state.catalogItems=list(papers.value);state.catalogTotal=Number(papers.value.total)||0;state.catalogPages=Number(papers.value.pages)||0;state.catalogPage=Number(papers.value.page)||1;}
    else{state.catalogError=papers.reason.message||'索引查询失败，请重试。';}
    if(summary.status==='fulfilled'){state.catalogStats=summary.value;state.catalogStatsError='';}else state.catalogStatsError=summary.reason.message||'覆盖信息读取失败。';
    state.catalogLoading=false;paintCatalog();
  }
  function libraryNavigation(){return `<nav class="library-sections" aria-label="论文库内容">${[['papers','论文与项目'],['starred','我的收藏'],['follows','我的关注'],['imports','导入论文'],['tasks','后台任务']].map(([id,name])=>`<button data-library-section="${id}" ${state.librarySection===id?'aria-current="page"':''}>${name}${id==='tasks'?'<span id="active-import-count" class="nav-count"></span>':''}</button>`).join('')}</nav>`;}
  function library(){
    if(state.librarySection==='papers')return libraryPapers();
    return `<div class="page-heading"><h1 tabindex="-1">我的论文库</h1></div>${libraryNavigation()}${state.librarySection==='starred'?'<div id="starred-panel"></div>':state.librarySection==='follows'?'<div id="follows-panel"></div>':state.librarySection==='imports'?importsArea():`<div class="section-heading"><h2>后台任务</h2><button class="button" data-action="refresh-imports">刷新</button></div><div id="import-results" aria-live="polite">${importList()}</div>`}`;
  }
  function libraryPapers(){
    const project=state.projects.find(p=>p.id===state.projectId);
    return `<div class="page-heading"><div><h1 tabindex="-1">${esc(project?.name||'我的论文库')}</h1><p>${project?esc(projectPath(project.id)):'同一篇论文可关联多个项目，笔记与阅读进度分别保存。'}</p></div><div class="actions">${project?'<button class="button" data-action="project-compare">论文对比</button><button class="button" data-action="project-qa">项目问答</button><details class="project-more-actions"><summary class="button">更多</summary><div><button class="button" data-action="project-knowledge">项目知识</button>':''}${project?.owner_id===authSession.user.id?'<button class="button subtle" data-action="share-project">共享项目</button>':''}${project&&project.can_edit!==false?'<button class="button subtle" data-action="edit-project">编辑项目</button>':''}${project?'</div></details>':''}<button class="button primary" data-action="open-import">${icons.plus}导入论文</button></div></div>${libraryNavigation()}<div id="project-research-panel" class="project-research-panel" hidden></div>${state.editProject&&project?`<form id="edit-project-form" class="inline-panel"><label>项目名称<input name="name" value="${esc(project.name)}" required maxlength="100"></label><label>所属目录<select name="parent_id">${projectOptions(project.parent_id,true,project.id)}</select></label><button type="submit" class="button primary">保存</button><button class="button" type="button" data-action="edit-project">收起</button></form>`:''}<div class="library-toolbar"><label class="search-box">${icons.search}<input id="library-query" type="search" placeholder="搜索已入库的论文…" aria-label="搜索已入库论文" value="${esc(state.query)}"></label>${state.projectId?`<label class="check-label library-children"><input id="library-children" type="checkbox" ${state.includeChildren?'checked':''}>包含子项目</label>`:''}<select id="library-status" aria-label="筛选阅读状态"><option value="all">全部状态</option>${Object.entries(statuses).map(([key,name])=>`<option value="${key}" ${state.status===key?'selected':''}>${name}</option>`).join('')}</select></div><div id="library-results" class="library-results">${paperList()}</div>`;
  }
  function paperList(){
    if(state.loading)return '<div class="empty-state"><div class="loading-ring"></div><p>正在读取论文库…</p></div>';
    const scope=projectScope(),rows=state.papers.filter(p=>state.status==='all'||memberships(p).some(m=>scope.has(m.project_id)&&m.status===state.status));
    if(!rows.length)return `<div class="empty-state">${icons.book}<h2>${state.query?'没有找到匹配论文':'还没有入库论文'}</h2><p>粘贴 arXiv 链接或上传 PDF，即可开始阅读。</p><button class="button primary" data-action="open-import">导入论文</button></div>`;
    return `<div class="result-caption"><span>${rows.length} 篇论文</span><span>按加入时间排列</span></div><div class="paper-grid">${rows.map(p=>{const ms=memberships(p),m=paperMembership(p,state.projectId,scope,state.status),selected=m.project_id||'inbox';return `<article class="paper-card"><div class="paper-card-top"><span class="pdf-label">PDF</span><span class="pill ${m.status==='read'?'green':''}">${statuses[m.status]||'待读'}</span><span class="muted">${esc(p.page_count||0)} 页</span></div><h2><a href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected)}">${esc(p.title)}</a></h2><div class="paper-projects">${ms.map(x=>`<span>${esc(projectPath(x.project_id)||'项目')}</span>`).join('')}</div><div class="paper-card-foot"><span>${p.translated_filename?'译文 PDF 已就绪':p.active_translation_run_id?'已启动逐页翻译，可查看已就绪页面':p.has_translation?'译文可阅读':'可按需逐页翻译'}</span><a class="text-link" href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected)}">连续阅读 ${icons.arrow}</a></div><div class="paper-social-row">${feedbackMarkup({paper_id:p.id})}</div><div class="paper-process"><button class="button small" data-process-paper="${esc(p.id)}" data-process-project="${esc(selected)}">安排解析 / AI 处理</button></div><details class="associate-paper"><summary>关联到其他项目</summary><form data-associate="${esc(p.id)}"><select name="project_id" aria-label="关联目标项目">${projectOptions(selected)}</select><button class="button small" type="submit">关联</button></form></details></article>`;}).join('')}</div>`;
  }
  function processingOptions(){
    const p=state.importer.processing;
    const option=(name,label,description,checked)=>`<label class="processing-option"><input type="checkbox" name="${name}" ${checked?'checked':''}><span><strong>${label}</strong><small>${description}</small></span></label>`;
    return `<fieldset class="processing-options"><legend>入库后按需处理</legend><div class="processing-grid">${option('parse','全文解析','提取带页码的全文，供导读和摘要使用。',p.parse)}${option('translate','逐页对照翻译','每页完成即可阅读；未完成的页面继续后台处理。',p.translate)}${option('guide','论文导读','说明问题、机制和推荐阅读路线。',p.guide)}${option('abstract','精简摘要','基于全文提炼背景、方法与主要结果。',p.brief_modes.includes('abstract'))}${option('cards','卡片粗读','按信息单元整理机制、实验和边界。',p.brief_modes.includes('cards'))}${option('summary','全文总结','连贯讲清论文；与读后的个人笔记总结分开。',p.brief_modes.includes('summary'))}</div></fieldset><p class="import-parse-hint" id="import-parse-hint">${p.guide||p.brief_modes.length?'已选择全文 AI 任务，会先完成全文解析。':'下载与保存 PDF 不依赖模型。'}${isAdmin&&!state.capabilities.ai_configured?' 当前模型未配置，AI 步骤会等待配置，原文仍可入库。':''}</p>`;
  }
  function importer(){
    const s=state.importer;
    return `<section class="import-panel"><div class="import-panel-heading"><h2>${s.kind==='existing'?'为已入库论文安排处理':'把论文带进研究项目'}</h2><p>先保存原文，再按选择运行后台任务。无需等待翻译和摘要完成。</p></div>${s.kind!=='existing'?`<div class="import-kind-tabs" role="group" aria-label="导入方式"><button class="${s.kind==='url'?'active':''}" data-import-kind="url">论文链接</button><button class="${s.kind==='file'?'active':''}" data-import-kind="file">本地 PDF</button></div>`:`<div class="notice compact">${esc(s.title)}<button class="text-button" data-action="open-import">改为导入新论文</button></div>`}<form id="import-form"><div class="import-main-fields">${s.kind==='url'?`<label class="import-url-field">论文链接<input name="url" type="url" required value="${esc(s.url)}" placeholder="https://arxiv.org/abs/2501.12948v1" maxlength="2048"><small>支持 arXiv 摘要页 / PDF 链接与公开 PDF 直链。媒体文章请先选择其关联论文链接。</small></label>`:s.kind==='file'?`<label class="import-url-field">PDF 文件<input name="file" type="file" accept="application/pdf,.pdf" ${s.file?'':'required'}><small>${s.file?esc(s.file.name)+' · '+(s.file.size/1048576).toFixed(1)+' MB':'支持最大 50 MB 的未加密 PDF。'}</small></label>`:''}<label>加入项目<select name="project_id" required>${projectOptions(s.projectId)}</select></label>${s.kind!=='existing'?`<label class="import-title-field">标题（可选）<input name="title" maxlength="300" value="${esc(s.title)}" placeholder="留空使用论文或 PDF 标题"></label>`:''}</div>${processingOptions()}<div class="import-submit-row"><button class="button primary" type="submit" ${state.importSubmitting?'disabled':''}>${state.importSubmitting?'正在提交…':s.kind==='existing'?'创建处理计划':'导入并后台处理'}</button><span>阅读器中的“读完”会另行总结当前项目笔记。</span></div><div id="import-feedback" class="form-feedback ${state.importFailure?'has-error':''}" role="status">${esc(state.importFailure||state.importMessage)}</div></form></section>`;
  }
  function importList(){
    if(state.importLoading&&!state.imports.length)return '<div class="empty-state"><div class="loading-ring"></div><p>正在获取处理任务…</p></div>';
    if(state.importError)return `<div class="error-panel">${esc(state.importError)}<button class="button small" data-action="refresh-imports">重试</button></div>`;
    if(!state.imports.length)return '<div class="empty-state"><h2>暂时没有导入任务</h2><p>在“导入论文”提交链接或 PDF 后，处理进度会在这里更新。</p></div>';
    return `<div class="import-list">${state.imports.map(task=>{
      const original=safeURL(task.source?.canonical_url||task.url||task.source_url),steps=Array.isArray(task.steps)?task.steps:[],active=['queued','running'].includes(task.status),retry=['failed','blocked','partial','interrupted','cancelled'].includes(task.status);
      const project=task.project_id||state.importer.projectId;
      return `<article class="import-task"><div class="import-task-heading"><div><h3>${esc(task.title||task.paper_title||task.source?.arxiv_id||task.source?.canonical_url||task.url||'论文处理任务')}</h3><p>${esc(projectPath(project)||project)} · ${esc(dateText(task.created_at))}</p></div><span class="pill ${task.status==='succeeded'?'green':''}">${esc(importStatuses[task.status]||task.status)}</span></div>${original?publicLink(original,'查看导入来源 ↗'):''}${task.error?`<p class="task-error">${esc(task.error)}</p>`:''}<ol class="import-steps">${steps.map(step=>`<li class="step-${esc(step.status)}"><div><strong>${esc(step.kind==='brief'?(modeNames[step.mode]||'论文粗读'):(stepNames[step.kind]||step.kind))}</strong><span>${esc(importStatuses[step.status]||step.status)}</span></div>${step.error?`<p>${esc(step.error)}</p>`:''}${processingProgress(step)?`<small>${esc(processingProgress(step))}</small>`:''}</li>`).join('')}</ol><div class="import-task-actions">${task.paper_id?`<a class="button primary small" href="/read/${encodeURIComponent(task.paper_id)}?project=${encodeURIComponent(project)}">打开原文与处理成果 ${icons.arrow}</a>`:'<span class="muted">原文下载完成后即可阅读</span>'}<span class="grow"></span>${retry?`<button class="button small" data-import-retry="${esc(task.id)}">继续 / 重试未完成步骤</button>`:''}${active?`<button class="text-button danger" data-import-cancel="${esc(task.id)}">取消后续处理</button>`:''}</div></article>`;
    }).join('')}</div>`;
  }
  function importsArea(){return `<div class="section-heading"><h2>导入论文</h2><button class="text-button" data-library-section="tasks">查看后台任务</button></div>${importer()}`;}
  function sourcesArea(){
    const filtered=state.sources.filter(s=>(state.priority==='all'||s.priority===state.priority)&&[s.name,...(s.topics||[])].join(' ').toLowerCase().includes(state.sourceQuery.toLowerCase()));
    return `<div class="page-heading"><div><h1 tabindex="-1">关注长期值得追踪的来源。</h1><p>保留 144 条机构与团队登记，以及你原来的分组与优先级。</p></div><span class="pill green">已关注 ${followed.size}</span></div><div class="notice">机构目录中的关注是个人偏好，尚未逐个接入采集器。首页已经接入的 HF 与媒体渠道会独立显示实时更新状态。</div><div class="library-toolbar"><label class="search-box">${icons.search}<input id="source-query" type="search" aria-label="搜索来源" placeholder="搜索机构、团队或研究方向" value="${esc(state.sourceQuery)}"></label><select id="source-priority" aria-label="筛选来源优先级">${['all','P0','P1','P2'].map(p=>`<option value="${p}" ${state.priority===p?'selected':''}>${p==='all'?'全部优先级':p}</option>`).join('')}</select></div><div class="result-caption">${filtered.length} 条匹配来源</div><div class="source-grid">${filtered.map(s=>`<article class="source-card"><div class="actions"><span class="pill ${s.priority==='P0'?'green':''}">${esc(s.priority)}</span><span class="grow"></span><button class="button small ${followed.has(String(s.id))?'':'subtle'}" data-follow="${esc(s.id)}">${followed.has(String(s.id))?'已关注':'＋ 关注'}</button></div><h2>${esc(s.name)}</h2><p>${esc((s.topics||[]).join(' · '))}</p><small>${esc(s.group_name||state.sourceGroups[s.group]||s.group||'研究来源')} · 来源登记</small></article>`).join('')||'<div class="empty-state">没有匹配来源。</div>'}</div>`;
  }
  function skillsArea(){
    const descriptions={'paper-guide':'先建立研究问题和阅读路线，再进入方法与实验。','paper-brief':'按全文证据生成摘要、卡片粗读或连贯总结。','paper-translate':'保留公式、图表和术语，逐页发布对照译文。','reading-notes-summary':'整理当前项目中的划线和个人笔记，区分论文结论与自己的判断。'};
    const search=state.discoveryConfig.web_search?.configured??false;
    return `<div class="page-heading"><div><h1 tabindex="-1">管理员设置</h1><p>模型与搜索在服务端配置；新任务使用当前 Skill，旧任务保留输入快照。</p></div><button class="button" data-action="refresh-capabilities">重新检查配置</button></div><div class="capability-grid"><div class="capability-box"><span class="status-dot ${search?'on':''}"></span><strong>全网搜索</strong><p>${search?esc(state.discoveryConfig.web_search?.provider||'搜索服务已配置'):'尚未配置。HF 与 arXiv 的独立学术检索不依赖全网搜索配置。'}</p></div><div class="capability-box"><span class="status-dot ${state.capabilities.ai_configured?'on':''}"></span><strong>阅读与摘要模型</strong><p>${state.capabilities.ai_configured?esc(state.capabilities.ai_model||'已配置'):'尚未配置；下载、PDF 阅读与笔记仍可使用。'}</p></div><div class="capability-box"><span class="status-dot ${state.capabilities.translation_configured?'on':''}"></span><strong>逐页翻译</strong><p>${state.capabilities.translation_configured?'翻译服务已配置，可在导入或阅读时按需启动。':'BabelDOC 待配置，可先导入已有的单语译文 PDF。'}</p></div></div><details class="service-config"><summary>部署配置说明</summary><p>全网搜索可使用自建 SearXNG：设置 SEARCH_PROVIDER=searxng、SEARXNG_BASE_URL，且实例需要启用 JSON 格式；也可设置 SEARCH_PROVIDER=brave、BRAVE_API_KEY。</p><p>阅读模型使用 AI_BASE_URL、AI_MODEL、AI_API_KEY；逐页翻译另需 BABELDOC_BIN。修改环境后重启服务，再重试等待配置的任务。密钥只在服务端保存，不填入本页面。</p></details><div class="skill-grid">${state.skills.map(s=>`<article class="skill-card"><span class="skill-icon">${icons.spark}</span><h2>${esc(s.name||s.id)}</h2><p>${esc(descriptions[s.id]||s.description)}</p>${s.modes?.length?`<div class="paper-projects">${s.modes.map(m=>`<span>${esc(m)}</span>`).join('')}</div>`:''}<small>skills/${esc(s.id)}/SKILL.md</small></article>`).join('')||'<div class="notice">Skill 清单暂时不可用。</div>'}</div>`;
  }
  function navigationState(open,{returnFocus=false}={}){
    state.navOpen=!!open;
    const mobile=!!mobileNavigation?.matches,layout=qs('.workspace-layout'),aside=qs('#workspace-navigation'),main=qs('#main-content'),toggle=qs('#workspace-nav-toggle'),backdrop=qs('.workspace-nav-backdrop');
    layout?.classList?.toggle?.('nav-open',state.navOpen&&mobile);
    toggle?.setAttribute?.('aria-expanded',String(state.navOpen&&mobile));
    if(aside){aside.inert=mobile&&!state.navOpen;if(mobile&&state.navOpen){aside.setAttribute?.('role','dialog');aside.setAttribute?.('aria-modal','true');}else{aside.removeAttribute?.('role');aside.removeAttribute?.('aria-modal');}}
    if(main)main.inert=mobile&&state.navOpen;
    if(backdrop)backdrop.hidden=!(mobile&&state.navOpen);
    if(open&&mobile)qs('#workspace-navigation [aria-current="page"]')?.focus?.();
    if(returnFocus)toggle?.focus?.();
  }
  function restoreFormFocus(previous){if(!previous)return;const field=qs(previous.selector);field?.focus?.({preventScroll:true});if(typeof previous.start==='number'&&field?.setSelectionRange)try{field.setSelectionRange(previous.start,previous.end);}catch{}}
  function render({focusHeading=false}={}){
    recommendationPanel?.dispose();recommendationPanel=null;feedbackController?.dispose();feedbackController=null;const recMount=++recommendationMount;
    const focused=document.activeElement;
    const previous=!focusHeading&&focused?.form?.id&&focused?.name?{selector:`#${focused.form.id} [name="${focused.name}"]`,start:focused.selectionStart,end:focused.selectionEnd}:null;
    const titles={recommendations:'为你推荐',discover:'搜索与推荐',catalog:'论文索引',library:'我的论文库',imports:'导入与任务',groups:'研究小组',skills:'管理员设置'};
    const contents={recommendations:()=>'<div id="recommendations-panel"><p role="status">正在载入推荐…</p></div>',discover,catalog:catalogArea,library,imports:importsArea,groups:()=>'<div class="page-heading"><div><h1 tabindex="-1">研究小组</h1><p>共享阅读清单与研究成果，保留每个人的独立思考。</p></div></div><div id="groups-panel"></div>',skills:skillsArea};
    app.innerHTML=`<a class="skip-link" href="#main-content">跳到主要内容</a><div class="workspace-layout workspace-v2 ${state.navOpen?'nav-open':''}">${sidebar()}<button class="workspace-nav-backdrop" data-action="close-navigation" aria-label="关闭导航" tabindex="-1" hidden></button><main id="main-content" class="workspace-main" tabindex="-1"><header class="workspace-topbar"><button id="workspace-nav-toggle" class="workspace-nav-toggle button" data-action="toggle-navigation" aria-controls="workspace-navigation" aria-expanded="false">${icons.menu}<span>导航</span></button><span>研究空间 <span class="crumb-separator">/</span> ${titles[state.view]}</span><span class="pill">PaperStudio</span></header><div class="workspace-content">${contents[state.view]()}</div></main></div>`;
    feedbackController=mountFeedback(app,{api,toast});
    const panelSpec=state.view==='discover'?['explore-panel','./explore-ui.js','mountExplore',exploreState]:state.view==='catalog'?['index-panel','./index-browser.js','mountIndex',indexState]:state.view==='library'&&state.librarySection==='follows'?['follows-panel','./follows-ui.js','mountFollows',{}]:state.view==='library'&&state.librarySection==='starred'?['starred-panel','./starred-ui.js','mountStarred',{}]:null;
    if(panelSpec)import(panelSpec[1]).then(module=>{
      const root=qs('#'+panelSpec[0]);if(recMount!==recommendationMount||!root)return;
      recommendationPanel=module[panelSpec[2]](root,{api,esc,safeURL,toast,isAdmin,state:panelSpec[3],legacy:legacyRecommendationProfile(),onProfile:syncRecommendationProfile,
        onGraph:async item=>{indexState.mode='graph';indexState.graph={focus:item.catalog_paper_id?{catalog_id:item.catalog_paper_id}:item.graph_id?{paper:item.graph_id}:item.arxiv_id?{paper:'arxiv_'+item.arxiv_id.replace('.','_')}:item.id?{catalog_id:item.id}:undefined};await setView('catalog');},
        onOpen:item=>item.kind==='article'?openIndexedPaper({...item,url:item.paper_urls?.[0]||item.url},{read:true,quickAdd:true}):openIndexedPaper(item,{read:true,quickAdd:true}),
        onAdd:async item=>{const {openProjectPicker}=await import('./project-picker.js');openProjectPicker({item:item.kind==='article'?{...item,url:item.paper_urls?.[0],title:''}:item,api,esc,safeURL,toast,projectId:state.projectId||authSession.user.inbox_id});},
        onBrowse:async(kind,value)=>{indexState.mode=kind?'papers':'entities';indexState.filters=kind?{[kind]:value}:{};indexState.q='';await setView('catalog');}});
    }).catch(error=>{const root=panelSpec&&qs('#'+panelSpec[0]);if(recMount===recommendationMount&&root)root.innerHTML='<p role="alert">'+esc(error.message)+'</p>';});
    updateURL();updateImportCount();navigationState(state.navOpen);if(focusHeading)qs('.workspace-content h1')?.focus?.();else restoreFormFocus(previous);if(state.view==='groups')import('./collaboration-ui.js').then(module=>module.mountGroups(qs('#groups-panel'))).catch(error=>toast(error.message));
  }
  function updateImportCount(){const count=state.imports.filter(t=>['queued','running'].includes(t.status)).length;const el=qs('#active-import-count');if(el){el.textContent=count?String(count):'';el.hidden=!count;}}
  function paintDiscovery(){const focusId=document.activeElement?.id;const restoreDirection=['discovery-direction','discovery-topic'].includes(focusId);const filter=qs('.direction-filter');if(filter)filter.outerHTML=discoveryDirectionFilter();if(restoreDirection)qs('#'+focusId)?.focus?.({preventScroll:true});const slot=qs('#discovery-results');if(slot){slot.innerHTML=discoveryResults();slot.setAttribute?.('aria-busy',String(state.discoveryLoading));}const restore=qs('#restore-hidden-button');if(restore)restore.hidden=!hiddenItems.size;}
  async function loadDiscovery(){exploreState.q=state.discoveryMode==='search'?state.searchQuery:'';exploreState.scope=state.searchScope;render();}
  async function loadPapers(){
    const seq=++querySequence;state.loading=true;if(qs('#library-results'))qs('#library-results').innerHTML=paperList();
    const query=new URLSearchParams();if(state.projectId)query.set('project_id',state.projectId);if(state.query)query.set('q',state.query);if(state.projectId&&state.includeChildren)query.set('include_children','true');
    try{const result=await api('/papers?'+query);if(seq!==querySequence)return;state.papers=list(result);state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=paperList();}
    catch(e){if(seq!==querySequence)return;state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=`<div class="error-panel">${esc(e.message)}</div>`;}
  }
  function schedulePoll(){clearTimeout(pollTimer);if(!pageActive)return;const active=state.imports.some(t=>['queued','running'].includes(t.status));if(active||(state.view==='library'&&state.librarySection==='tasks'))pollTimer=setTimeout(()=>loadImports(),active?2200:12000);}
  async function loadImports(){
    const seq=++importsSequence;state.importLoading=true;
    try{const result=await api('/imports');if(seq!==importsSequence)return;const previousPapers=new Set(state.imports.map(task=>task.paper_id).filter(Boolean));state.imports=list(result);state.importError='';updateImportCount();if(state.view==='library'&&state.imports.some(task=>task.paper_id&&!previousPapers.has(task.paper_id)))await loadPapers();}
    catch(e){if(seq!==importsSequence)return;state.importError=e.message;}
    finally{if(seq===importsSequence){state.importLoading=false;if(qs('#import-results'))qs('#import-results').innerHTML=importList();schedulePoll();}}
  }
  async function refreshCapabilities(){if(!isAdmin)return;const [caps,config]=await Promise.allSettled([api('/capabilities'),api('/discovery/config')]);if(caps.status==='fulfilled')state.capabilities=caps.value;if(config.status==='fulfilled')state.discoveryConfig=config.value;}
  async function setView(view){if(!views.has(view))return;if(view==='recommendations')view='discover';if(view==='imports'){view='library';state.librarySection='imports';}state.view=view;state.editProject=false;state.navOpen=false;render({focusHeading:true});if(view==='library'&&state.librarySection==='papers')await loadPapers();if(view==='library'&&['imports','tasks'].includes(state.librarySection))await loadImports();schedulePoll();}
  function openImport({url='',title='',paperId=null,projectId,catalogPaperId=null}={}){
    state.importer={...state.importer,kind:paperId?'existing':'url',url,title,paperId,catalogPaperId,file:null,projectId:projectId||state.projectId||state.importer.projectId||'inbox'};
    if(!state.projects.some(p=>p.id===state.importer.projectId&&p.can_edit!==false))state.importer.projectId=authSession.user.inbox_id||state.projects.find(p=>p.can_edit!==false)?.id||'inbox';
    state.importFailure='';state.importMessage='';state.view='library';state.librarySection='imports';render();loadImports();qs('#import-form input')?.focus();
  }
  function syncProcessing(form){const p=state.importer.processing;p.translate=form.elements.translate.checked;p.guide=form.elements.guide.checked;p.brief_modes=['abstract','cards','summary'].filter(key=>form.elements[key].checked);p.parse=form.elements.parse.checked||p.guide||p.brief_modes.length>0;form.elements.parse.checked=p.parse;const hint=qs('#import-parse-hint');if(hint)hint.textContent=(p.guide||p.brief_modes.length?'已选择全文 AI 任务，会先完成全文解析。':'下载与保存 PDF 不依赖模型。')+(isAdmin&&!state.capabilities.ai_configured?' 当前模型未配置，AI 步骤会等待配置，原文仍可入库。':'');}
  async function browseCatalogCategory(type,name){for(const field of ['catalogQuery','catalogYear','catalogInstitution','catalogConference','catalogConferenceYear','catalogPresentation','catalogDirection','catalogTopic'])state[field]='';state.catalogSource='all';state.catalogVerification='all';if(type==='institution')state.catalogInstitution=name;else if(type==='conference')state.catalogConference=name;state.catalogPage=1;await setView('catalog');}
  async function openIndexedPaper(item,{read=false,quickAdd=false}={}){
    if(!item)return;
    if(read&&item.reader_href&&/^\/read\/[^<>\s]+$/.test(item.reader_href)){location.assign(item.reader_href);return;}
    const url=safeURL(item.resolve?.url)||safeURL(item.pdf_url)||safeURL(item.url);
    const quickPick=async()=>{const {openProjectPicker}=await import('./project-picker.js');return openProjectPicker({item,api,esc,safeURL,toast,projectId:state.projectId||authSession.user.inbox_id});};
    if(quickAdd&&(!read||item.resolve?.action==='search'||!url))return quickPick();
    if(item.resolve?.action==='search'||!url){state.searchQuery=item.resolve?.query||item.title;state.searchScope='arxiv';state.searchPage=1;state.discoveryMode='search';state.view='discover';render();await loadDiscovery();toast('正在查找独立论文入口；不会下载会议日程。');return;}
    if(!read){openImport({url,title:item.title,catalogPaperId:item.catalog_paper_id});return;}
    const projectId=state.projectId||authSession.user.inbox_id||state.projects[0]?.id;
    const result=await api('/resolve',{method:'POST',body:{url,project_id:projectId,title:item.title,...(item.catalog_paper_id?{catalog_paper_id:item.catalog_paper_id}:{})}});
    const paperId=result.paper_id||result.paper?.id;
    if(paperId){location.assign(`/read/${encodeURIComponent(paperId)}?project=${encodeURIComponent(result.project_id||projectId)}`);return;}
    if(quickAdd)return quickPick();
    if(result.action==='search'){state.searchQuery=result.query||item.title;state.searchScope='arxiv';state.searchPage=1;state.discoveryMode='search';state.view='discover';render();await loadDiscovery();return;}
    openImport({url:safeURL(result.source?.download_url)||url,title:item.title,projectId,catalogPaperId:item.catalog_paper_id});
  }
  app.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.clearCatalog){const mapping=catalogFilterFields[button.dataset.clearCatalog];if(mapping){const [field]=mapping;state[field]=['source','verification_status'].includes(button.dataset.clearCatalog)?'all':'';if(button.dataset.clearCatalog==='direction')state.catalogTopic='';const control=qs('#catalog-search-form')?.elements?.[button.dataset.clearCatalog];if(control)control.value=state[field];state.catalogPage=1;await loadCatalog();(qs('#catalog-active-filters button')||qs('#catalog-search-form input'))?.focus?.();}return;}
      if(button.dataset.librarySection){state.librarySection=button.dataset.librarySection;await setView('library');return;}
      if(button.dataset.view){await setView(button.dataset.view);return;}
      if('project' in button.dataset){state.projectId=button.dataset.project;state.librarySection='papers';await setView('library');return;}
      if(button.dataset.catalogPaper){const item=state.catalogItems.find(p=>p.id===button.dataset.catalogPaper);if(item)await openIndexedPaper({...item,catalog_paper_id:item.id});return;}
      if(button.dataset.openCatalog){const item=state.catalogItems.find(p=>p.id===button.dataset.openCatalog);if(item)await openIndexedPaper({...item,catalog_paper_id:item.id},{read:true});return;}
      if('openResult' in button.dataset){const item=state.discoveryItems[Number(button.dataset.openResult)];if(item.kind==='paper'){await openIndexedPaper(item,{read:true});}else{state.searchQuery=item.title;state.searchScope='arxiv';state.discoveryMode='search';render();await loadDiscovery();}return;}
      if(button.dataset.catalogInstitution){await browseCatalogCategory('institution',button.dataset.catalogInstitution);return;}
      if(button.dataset.catalogConference){await browseCatalogCategory('conference',button.dataset.catalogConference);return;}
      if(button.dataset.followFilter){const [type,...rest]=button.dataset.followFilter.split(':');await browseCatalogCategory(type,rest.join(':'));return;}
      if(button.dataset.direction){if(button.dataset.directionScope==='discover'){setDirection('discover',button.dataset.direction,button.dataset.topic||'');render();await loadDiscovery();}else{setDirection('catalog',button.dataset.direction,button.dataset.topic||'');render();await loadCatalog();}return;}
      if(button.dataset.catalogPage){state.catalogPage=Math.max(1,Math.min(state.catalogPages||1,Number(button.dataset.catalogPage)));await loadCatalog();return;}
      if(button.dataset.importKind){state.importer.kind=button.dataset.importKind;state.importer.paperId=null;state.importFailure='';render();return;}
      if(button.dataset.follow){const id=button.dataset.follow,enabled=!followed.has(id);button.disabled=true;
        try{if(id.startsWith('institution:')||id.startsWith('conference:')){
          const module=await import('./recommendations-ui.js');await module.loadProfile(api,legacyRecommendationProfile());
          const [kind,...rest]=id.split(':');syncRecommendationProfile(await api('/me/recommendation-profile/follow',{method:'PATCH',body:{kind,value:rest.join(':'),enabled}}));
        }else{enabled?followed.add(id):followed.delete(id);remember('paperstudio-source-follows',[...followed]);}
        render();toast('关注偏好已保存');}finally{if(button.isConnected)button.disabled=false;}return;}
      if(button.dataset.processPaper){const p=state.papers.find(x=>x.id===button.dataset.processPaper);if(p)openImport({paperId:p.id,title:p.title,projectId:button.dataset.processProject});return;}
      if('importResult' in button.dataset||'importArticle' in button.dataset){const index=Number(button.dataset.importResult??button.dataset.importArticle),item=state.discoveryItems[index];if(!item)return;const urls=paperURLs(item);const url='importArticle' in button.dataset?urls[Number(button.dataset.linkIndex)]:urls[0]||(item.kind==='paper'?safeURL(item.url):'');if(url)openImport({url,title:item.kind==='paper'?item.title:''});return;}
      if('hideResult' in button.dataset){const item=state.discoveryItems[Number(button.dataset.hideResult)];if(item){hiddenItems.add(String(item.id||item.url));remember('paperstudio-hidden-discovery',[...hiddenItems]);paintDiscovery();}return;}
      if(button.dataset.searchPage){state.searchPage=Math.max(1,Math.min(state.searchScope==='hf'?5:10,Number(button.dataset.searchPage))); await loadDiscovery();return;}
      if(button.dataset.importCancel||button.dataset.importRetry){button.disabled=true;await api(`/imports/${encodeURIComponent(button.dataset.importCancel||button.dataset.importRetry)}/${button.dataset.importCancel?'cancel':'retry'}`,{method:'POST'});await loadImports();return;}
      switch(button.dataset.action){
        case'toggle-navigation':navigationState(!state.navOpen);break;
        case'close-navigation':navigationState(false,{returnFocus:true});break;
        case'reset-discovery':setDirection('discover','');state.searchQuery='';state.searchPage=1;state.discoveryMode='feed';render();await loadDiscovery();break;
        case'logout':await api('/auth/logout',{method:'POST'});location.assign('/');break;
        case'project-compare':{const module=await import('./compare-picker.js');await module.openComparePicker({projectId:state.projectId,api,esc,toast,onCompare:async detail=>{const research=await import('./research-ui.js');await research.openResearchPanel(detail);}});break;}
        case'project-qa':window.dispatchEvent(new CustomEvent('paperstudio:research',{detail:{kind:'qa',project_id:state.projectId,target:'#project-research-panel'}}));break;
        case'project-knowledge':{const module=await import('./research-ui.js');await module.openResearchPanel({kind:'knowledge',project_id:state.projectId,target:'#project-research-panel',browseOnly:true});break;}
        case'share-project':{const module=await import('./collaboration-ui.js');await module.openProjectSharing(state.projects.find(p=>p.id===state.projectId));break;}
        case'clear-discovery-direction':setDirection('discover','');render();await loadDiscovery();break;
        case'clear-catalog-filters':for(const key of ['catalogQuery','catalogYear','catalogInstitution','catalogConference','catalogConferenceYear','catalogPresentation','catalogDirection','catalogTopic'])state[key]='';state.catalogSource='all';state.catalogVerification='all';state.catalogPage=1;render();await loadCatalog();qs('#catalog-search-form input')?.focus?.();break;
        case'open-import':openImport();break;
        case'edit-project':state.editProject=!state.editProject;render();break;
        case'show-feed':state.discoveryMode='feed';state.discoveryError='';render();await loadDiscovery();break;
        case'refresh-feed':state.discoveryMode='feed';render();await loadDiscovery({refresh:true});break;
        case'retry-discovery':await loadDiscovery({refresh:true});break;
        case'restore-hidden':hiddenItems.clear();remember('paperstudio-hidden-discovery',[]);render();break;
        case'refresh-catalog':await loadCatalog({stats:true});break;
        case'refresh-imports':await loadImports();break;
        case'refresh-capabilities':button.disabled=true;await refreshCapabilities();render();break;
      }
    }catch(e){toast(e.message);}finally{if(button.isConnected)button.disabled=false;}
  });
  app.addEventListener('keydown',event=>{
    if(!state.navOpen||!mobileNavigation?.matches)return;
    if(event.key==='Escape'){event.preventDefault();navigationState(false,{returnFocus:true});return;}
    if(event.key==='Tab'){
      const links=Array.from(qs('#workspace-navigation')?.querySelectorAll?.('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),summary')||[]).filter(element=>element.getClientRects?.().length);
      if(!links.length)return;
      const first=links[0],last=links.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    }
  });
  app.addEventListener('toggle',event=>{if(event.target.id==='catalog-advanced-filters')state.catalogAdvancedOpen=event.target.open;else if(['catalog-coverage-details','catalog-follows-details','discovery-preferences','discovery-method','source-status-disclosure'].includes(event.target.id))state.disclosures[event.target.id]=event.target.open;},true);
  mobileNavigation?.addEventListener?.('change',()=>navigationState(false));
  app.addEventListener('input',event=>{
    const field=event.target;
    if(field.form?.id==='catalog-hf-form'&&field.name==='week')state.catalogHfWeek=field.value;
    if(field.form?.id==='catalog-search-form'){const key={q:'catalogQuery',institution:'catalogInstitution'}[field.name];if(key)state[key]=field.value;}
    if(field.id==='library-query'){state.query=field.value;clearTimeout(debounce);debounce=setTimeout(loadPapers,220);}
    if(field.id==='source-query'){state.sourceQuery=field.value;const pos=field.selectionStart;render();const input=qs('#source-query');input.focus();input.setSelectionRange(pos,pos);}
    if(field.form?.id==='import-form'&&['url','title'].includes(field.name)){if(field.name==='url'&&field.value!==state.importer.url)state.importer.catalogPaperId=null;state.importer[field.name]=field.value;}
    if(field.form?.id==='discovery-search-form'&&field.name==='q')state.searchQuery=field.value;
    if(field.form?.id==='interests-form'&&field.name==='interests')state.interests=field.value;
  });
  app.addEventListener('change',async event=>{
    const field=event.target;
    if(field.form?.id==='catalog-search-form'){const key={year:'catalogYear',source:'catalogSource',sort:'catalogSort',conference:'catalogConference',conference_year:'catalogConferenceYear',presentation:'catalogPresentation',verification_status:'catalogVerification',direction:'catalogDirection',topic:'catalogTopic'}[field.name];if(key){if(field.name==='direction')setDirection('catalog',field.value);else if(field.name==='topic')setDirection('catalog',state.catalogDirection,field.value);else state[key]=field.value;state.catalogPage=1;await loadCatalog();}}
    if(field.id==='discovery-direction'){setDirection('discover',field.value);await loadDiscovery();}
    if(field.id==='discovery-topic'){setDirection('discover',state.discoveryDirection,field.value);await loadDiscovery();}
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
    event.preventDefault();const form=event.target;if(form.id==='import-form'&&state.importSubmitting||['catalog-import-form','catalog-hf-form'].includes(form.id)&&state.catalogImporting)return;const fd=new FormData(form),submit=form.querySelector('[type=submit]')||form.querySelector('button');if(submit)submit.disabled=true;
    try{
      if(form.id==='new-project-form'){
        const created=await api('/projects',{method:'POST',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.projectId=created.id;await setView('library');toast('项目已创建');
      }else if(form.id==='edit-project-form'){
        await api('/projects/'+encodeURIComponent(state.projectId),{method:'PATCH',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.editProject=false;render();toast('项目已保存');
      }else if(form.id==='discovery-search-form'){
        state.searchQuery=String(fd.get('q')||'').trim();state.searchScope=String(fd.get('scope'));state.searchPage=1;state.discoveryMode='search';render();await loadDiscovery();
      }else if(form.id==='catalog-search-form'){
        state.catalogQuery=String(fd.get('q')||'').trim();state.catalogYear=String(fd.get('year')||'');state.catalogSource=String(fd.get('source')||'all');state.catalogInstitution=String(fd.get('institution')||'').trim();state.catalogSort=String(fd.get('sort')||'votes');for(const [name,key] of [['conference','catalogConference'],['conference_year','catalogConferenceYear'],['presentation','catalogPresentation'],['verification_status','catalogVerification'],['direction','catalogDirection'],['topic','catalogTopic']])state[key]=String(fd.get(name)|| (name==='verification_status'?'all':''));state.catalogPage=1;await loadCatalog();
      }else if(form.id==='catalog-hf-form'){
        const week=String(fd.get('week')||'').trim();if(!/^202[56]-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(week))throw new Error('请选择 2025 或 2026 年的 HF 周榜。');state.catalogHfWeek=week;state.catalogImporting=true;state.catalogImportError='';state.catalogFeedback='正在获取 HF '+week+' 热榜…';const feedback=qs('#catalog-import-feedback');if(feedback){feedback.textContent=state.catalogFeedback;feedback.classList.remove('has-error');}if(submit)submit.textContent='正在获取…';const result=await api('/catalog/refresh-hf',{method:'POST',body:{week}});state.catalogFeedback=`HF ${week}：已处理 ${catalogMetric(result.row_count)} 条，新增 ${catalogMetric(result.inserted)}，更新 ${catalogMetric(result.updated)}。`;state.catalogPage=1;await loadCatalog({stats:true});const updated=qs('#catalog-import-feedback');if(updated)updated.textContent=state.catalogFeedback;toast('HF 周榜索引已更新');
      }else if(form.id==='catalog-import-form'){
        const file=fd.get('file');if(!file?.size)throw new Error('请选择 JSON 或 CSV 索引文件。');if(file.size>20*1048576)throw new Error('索引文件超过 20 MB 限制。');state.catalogImporting=true;state.catalogImportError='';state.catalogFeedback='正在校验并更新索引…';const feedback=qs('#catalog-import-feedback');if(feedback){feedback.textContent=state.catalogFeedback;feedback.classList.remove('has-error');}if(submit)submit.textContent='正在更新…';const body=new FormData();body.set('file',file);const result=await api('/catalog/import',{method:'POST',body});state.catalogFeedback=`已处理 ${catalogMetric(result.row_count)} 条记录：新增 ${catalogMetric(result.inserted)}，更新 ${catalogMetric(result.updated)}，未变化 ${catalogMetric(result.unchanged)}。`;state.catalogPage=1;await loadCatalog({stats:true});const updated=qs('#catalog-import-feedback');if(updated)updated.textContent=state.catalogFeedback;toast('论文索引已更新');
      }else if(form.id==='interests-form'){
        state.interests=String(fd.get('interests')||'');remember('paperstudio-interests',state.interests);state.discoveryMode='feed';render();await loadDiscovery();
      }else if(form.id==='import-form'){
        syncProcessing(form);const s=state.importer;s.projectId=String(fd.get('project_id')||s.projectId);s.title=String(fd.get('title')??s.title);state.importSubmitting=true;state.importFailure='';state.importMessage='正在提交导入和处理计划…';const feedback=qs('#import-feedback');feedback.textContent=state.importMessage;feedback.classList.remove('has-error');if(submit)submit.textContent='正在提交…';
        let task;
        if(s.kind==='file'){
          const file=s.file||fd.get('file');if(!file?.size)throw new Error('请选择 PDF 文件。');if(file.size>50*1048576)throw new Error('PDF 超过 50 MB 限制。');const body=new FormData();body.set('file',file);body.set('project_id',s.projectId);body.set('title',s.title);body.set('processing',JSON.stringify(s.processing));task=await api('/imports/file',{method:'POST',body});
        }else{
          const body={project_id:s.projectId,title:s.title,processing:{...s.processing}};
          if(s.kind==='existing')body.paper_id=s.paperId;else{const url=safeURL(fd.get('url'));if(!url)throw new Error('请填写有效的 HTTP(S) 论文链接。');s.url=url;body.url=url;if(s.catalogPaperId)body.catalog_paper_id=s.catalogPaperId;}
          task=await api('/imports',{method:'POST',body});
        }
        state.importMessage=task.paper_id?'原文已入库，可在“后台任务”打开阅读；后台继续处理所选步骤。':'导入已排队。下载完成后即可打开原文，其余步骤继续在后台执行。';await loadImports();const updatedFeedback=qs('#import-feedback');if(updatedFeedback){updatedFeedback.textContent=state.importMessage;updatedFeedback.classList.remove('has-error');}toast('处理计划已保存');
      }else if(form.dataset.associate){await api(`/papers/${encodeURIComponent(form.dataset.associate)}/projects`,{method:'POST',body:{project_id:fd.get('project_id')}});toast('已关联项目');await loadPapers();}
    }catch(e){toast(e.message);if(['catalog-import-form','catalog-hf-form'].includes(form.id)){state.catalogImportError=e.message;state.catalogFeedback='';const feedback=qs('#catalog-import-feedback');if(feedback){feedback.textContent=e.message;feedback.classList.add('has-error');}}if(form.id==='import-form'){state.importFailure=e.message;state.importMessage='';const feedback=qs('#import-feedback');if(feedback){feedback.textContent=e.message;feedback.classList.add('has-error');}}}
    finally{if(['catalog-import-form','catalog-hf-form'].includes(form.id)){state.catalogImporting=false;if(submit?.isConnected)submit.textContent=form.id==='catalog-hf-form'?'获取这周 Top 50':'导入索引';}if(form.id==='import-form'){state.importSubmitting=false;if(submit?.isConnected)submit.textContent=state.importer.kind==='existing'?'创建处理计划':'导入并后台处理';}if(submit?.isConnected)submit.disabled=false;}
  });
  window.addEventListener('pagehide',()=>{pageActive=false;clearTimeout(pollTimer);catalogController?.abort();++catalogSequence;recommendationPanel?.dispose();recommendationPanel=null;feedbackController?.dispose();feedbackController=null;++recommendationMount;});
  window.addEventListener('pageshow',event=>{pageActive=true;if(event.persisted){loadImports();if(['catalog','discover'].includes(state.view)||(state.view==='library'&&['follows','starred'].includes(state.librarySection)))render();}});
  try{
    const [projects,caps,skills,sources,config]=await Promise.allSettled([api('/projects'),isAdmin?api('/capabilities'):Promise.resolve({}),isAdmin?api('/skills'):Promise.resolve([]),Promise.resolve({sources:[]}),isAdmin?api('/discovery/config'):Promise.resolve({})]);
    if(projects.status==='rejected')throw projects.reason;state.projects=list(projects.value);if(state.projectId&&!state.projects.some(p=>p.id===state.projectId))state.projectId='';
    if(!state.projects.some(p=>p.id===state.importer.projectId&&p.can_edit!==false))state.importer.projectId=authSession.user.inbox_id||state.projects.find(p=>p.can_edit!==false)?.id||'inbox';
    if(caps.status==='fulfilled')state.capabilities=caps.value;if(skills.status==='fulfilled')state.skills=list(skills.value);if(sources.status==='fulfilled'){state.sources=list(sources.value);state.sourceGroups=sources.value?.groups||{};}if(config.status==='fulfilled')state.discoveryConfig=config.value;
    render();loadImports();if(state.view==='library'&&state.librarySection==='papers')await loadPapers();
  }catch(e){app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1 tabindex="-1">工作台暂时无法连接</h1><p>${esc(e.message)}</p><a class="button primary" href="/">重新连接</a></main>`;}
}


