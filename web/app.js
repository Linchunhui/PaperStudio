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

const route = location.pathname.match(/^\/read\/([^/]+)\/?$/);
if (route) {
  mountReader({paperId:decodeURIComponent(route[1]), projectId:new URLSearchParams(location.search).get('project')});
} else {
  mountWorkspace();
}

async function mountWorkspace() {
  const params = new URLSearchParams(location.search);
  const state = {projects:[],papers:[],projectId:params.get('project') || '',view:params.get('view') || 'library',query:'',status:'all',includeChildren:true,sources:[],sourceGroups:{},sourceQuery:'',priority:'all',skills:[],capabilities:{},loading:true,editProject:false};
  let storedFollows=[];try{storedFollows=JSON.parse(localStorage.getItem('paperstudio-source-follows')||'[]');}catch{}
  const followed = new Set(Array.isArray(storedFollows)?storedFollows:[]);
  const app = qs('#app');
  function projectPath(id) {
    const result=[],seen=new Set();let p=state.projects.find(x=>x.id===id);
    while(p&&!seen.has(p.id)){seen.add(p.id);result.unshift(p.name);p=state.projects.find(x=>x.id===p.parent_id)}
    return result.join(' / ');
  }
  function updateURL() {
    const next = new URLSearchParams();if(state.projectId)next.set('project',state.projectId);if(state.view!=='library')next.set('view',state.view);
    history.replaceState(null,'',`/${next.size?'?'+next:''}`);
  }
  function tree(parent = null, depth = 0, seen = new Set()) {
    return state.projects.filter(p=>(p.parent_id||null)===parent&&!seen.has(p.id)).map(p=>{
      const next=new Set(seen);next.add(p.id);
      return `<button class="project-item ${state.view==='library'&&state.projectId===p.id?'active':''}" style="--depth:${depth}" data-project="${esc(p.id)}">${icons.folder}<span>${esc(p.name)}</span></button>${tree(p.id,depth+1,next)}`;
    }).join('');
  }
  function projectOptions(selected='', includeAll=false, excludeId='') {
    const blocked=new Set([excludeId]);let grew=true;while(grew){grew=false;state.projects.forEach(p=>{if(blocked.has(p.parent_id)&&!blocked.has(p.id)){blocked.add(p.id);grew=true}})}
    return (includeAll?'<option value="">根目录</option>':'')+state.projects.filter(p=>!excludeId||!blocked.has(p.id)).map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(projectPath(p.id))}</option>`).join('');
  }
  function sidebar() {
    return `<aside class="workspace-sidebar"><a href="/" class="brand"><span class="brand-symbol">${icons.book}</span><span><strong>PaperStudio</strong><small>让阅读形成自己的脉络</small></span></a><div class="workspace-identity"><span class="avatar">私</span><div><strong>我的研究空间</strong><small>本机个人版 · SQLite 保存</small></div></div><nav aria-label="工作台导航"><button class="nav-item ${state.view==='library'?'active':''}" data-view="library">${icons.book}论文工作台</button><button class="nav-item ${state.view==='sources'?'active':''}" data-view="sources">${icons.source}来源与关注<span class="nav-count">144</span></button><button class="nav-item ${state.view==='skills'?'active':''}" data-view="skills">${icons.spark}研究 Skills</button></nav><div class="project-label">研究项目</div><button class="project-item ${state.view==='library'&&!state.projectId?'active':''}" data-project="">${icons.folder}<span>全部论文</span></button><div class="project-tree">${tree()}</div><details class="new-project"><summary>${icons.plus}新建项目</summary><form id="new-project-form"><label>项目名称<input name="name" required maxlength="100" placeholder="例如：文档理解" autocomplete="off"></label><label>所属目录<select name="parent_id">${projectOptions(state.projectId,true)}</select></label><button class="button primary" type="submit">创建项目</button></form></details><div class="sidebar-foot"><span class="connection-dot"></span>阅读与笔记已连接本地后端<small>AI ${state.capabilities.ai_configured?'已配置':'待配置'} · PDF 保留原始文件</small></div></aside>`;
  }
  function library() {
    const project=state.projects.find(p=>p.id===state.projectId);
    return `<div class="eyebrow">YOUR RESEARCH LIBRARY</div><div class="page-heading"><div><h1>${esc(project?.name||'从一篇论文，开始积累。')}</h1><p>${project?esc(projectPath(project.id)):'原生 PDF、逐句笔记与研究总结，留在同一个工作台。'}</p></div><div class="actions">${project?'<button class="button subtle" data-action="edit-project">编辑项目</button>':''}<button class="button primary" data-action="show-upload">${icons.plus}导入 PDF</button></div></div>${state.editProject&&project?`<form id="edit-project-form" class="inline-panel"><label>项目名称<input name="name" value="${esc(project.name)}" required maxlength="100"></label><label>所属目录<select name="parent_id">${projectOptions(project.parent_id,true,project.id)}</select></label><button class="button primary">保存</button><button class="button" type="button" data-action="edit-project">收起</button></form>`:''}<section class="upload-panel" id="upload-panel" hidden><div><h2>把论文带进来</h2><p>保存原始 PDF 后即可阅读、划词和做笔记。下载或翻译不自动发生。</p></div><form id="upload-paper-form"><label>论文 PDF<input type="file" name="file" accept="application/pdf,.pdf" required></label><label>论文标题（可选）<input name="title" maxlength="300" placeholder="留空使用文件名"></label><label>加入项目<select name="project_id" required>${projectOptions(state.projectId||state.projects[0]?.id)}</select></label><div class="actions"><button class="button primary" type="submit">保存并打开阅读</button><button class="button" type="button" data-action="hide-upload">取消</button></div><p class="form-feedback" id="upload-feedback" role="status"></p></form></section><div class="library-toolbar"><label class="search-box">${icons.search}<input id="library-query" type="search" placeholder="搜索已入库的论文…" aria-label="搜索已入库论文" value="${esc(state.query)}"></label>${state.projectId?`<label class="check-label library-children"><input id="library-children" type="checkbox" ${state.includeChildren?'checked':''}>包含子项目</label>`:''}<select id="library-status" aria-label="筛选阅读状态"><option value="all">全部状态</option>${Object.entries(statuses).map(([k,v])=>`<option value="${k}" ${state.status===k?'selected':''}>${v}</option>`).join('')}</select></div><div class="library-results" id="library-results">${paperList()}</div><div class="library-boundary">当前搜索范围是已入库论文。外部搜索、每日雷达和推荐采集将在后续版本接入；来源目录可先整理关注。</div>`;
  }
  function paperList() {
    if(state.loading)return '<div class="empty-state"><div class="loading-ring"></div><p>正在读取论文库…</p></div>';
    const rows=state.papers.filter(p=>state.status==='all'||memberships(p).some(m=>(!state.projectId||state.includeChildren||m.project_id===state.projectId)&&m.status===state.status)||p.status===state.status);
    if(!rows.length)return `<div class="empty-state">${icons.book}<h2>${state.query?'没有找到匹配论文':'给这个项目放入第一篇论文'}</h2><p>导入一份 PDF，就可以打开三栏阅读页面。</p><button class="button primary" data-action="show-upload">导入 PDF</button></div>`;
    return `<div class="result-caption"><span>${rows.length} 篇论文</span><span>按加入时间排列 · 原始 PDF 文件</span></div><div class="paper-grid">${rows.map(p=>{
      const ms=memberships(p),m=ms.find(x=>x.project_id===state.projectId)||ms[0]||{project_id:state.projectId,status:p.status||'unread'};
      const selected=m.project_id||state.projectId||state.projects[0]?.id;
      return `<article class="paper-card"><div class="paper-card-top"><span class="pdf-label">PDF</span><span class="pill ${m.status==='read'?'green':''}">${statuses[m.status]||'待读'}</span><span class="muted">${p.page_count||0} 页</span></div><h2><a href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected||'')}">${esc(p.title)}</a></h2><p class="paper-file">${esc(p.filename?.split(/[\\/]/).pop()||'原始 PDF 已保存')}</p><div class="paper-projects">${ms.map(x=>`<span>${esc(projectPath(x.project_id)||x.project_name||'项目')}</span>`).join('')}</div><div class="paper-card-foot"><span>${p.translated_filename?'<span class="tiny-dot"></span>译文 PDF 已就绪':'译文按需生成或导入'}</span><a class="text-link" href="/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(selected||'')}">打开阅读 ${icons.arrow}</a></div><details class="associate-paper"><summary>关联到其他项目</summary><form data-associate="${esc(p.id)}"><select name="project_id" aria-label="关联目标项目">${projectOptions(selected)}</select><button class="button small" type="submit">关联</button></form></details></article>`;
    }).join('')}</div>`;
  }
  function sourcesArea() {
    const filtered=state.sources.filter(s=>(state.priority==='all'||s.priority===state.priority)&&[s.name,...(s.topics||[])].join(' ').toLowerCase().includes(state.sourceQuery.toLowerCase()));
    return `<div class="eyebrow">RESEARCH SOURCES</div><div class="page-heading"><div><h1>把值得关注的来源，放在一起。</h1><p>144 条机构与团队登记，保留你原来的分组和优先级。</p></div><span class="pill green">已关注 ${followed.size}</span></div><div class="notice">这是来源登记与关注偏好。官方入口和采集器需要逐条核验，关注不会自动启动采集；P0 / P1 / P2 不代表论文质量。</div><div class="library-toolbar"><label class="search-box">${icons.search}<input id="source-query" type="search" aria-label="搜索来源" placeholder="搜索机构、团队或研究方向" value="${esc(state.sourceQuery)}"></label><select id="source-priority" aria-label="筛选来源优先级">${['all','P0','P1','P2'].map(p=>`<option value="${p}" ${state.priority===p?'selected':''}>${p==='all'?'全部优先级':p}</option>`).join('')}</select></div><div class="result-caption">${filtered.length} 条匹配来源</div><div class="source-grid">${filtered.map(s=>`<article class="source-card"><div class="actions"><span class="pill ${s.priority==='P0'?'green':''}">${esc(s.priority)}</span><span class="grow"></span><button class="button small ${followed.has(s.id)?'':'subtle'}" data-follow="${esc(s.id)}">${followed.has(s.id)?'已关注':'＋ 关注'}</button></div><h2>${esc(s.name)}</h2><p>${esc((s.topics||[]).join(' · '))}</p><small>${esc(s.group_name||state.sourceGroups[s.group]||s.group||'研究来源')} · 待核验</small></article>`).join('')||'<div class="empty-state">没有匹配来源。</div>'}</div>`;
  }
  function skillsArea() {
    const descriptions={ 'paper-guide':'先搭起研究问题和阅读路线，再进入方法与实验。','paper-brief':'按全文证据生成摘要、卡片粗读或连贯总结。','paper-translate':'保留公式、图表和术语，生成可并排阅读的译文。','reading-notes-summary':'整理当前项目中的划线和个人笔记，区分论文结论与自己的判断。' };
    return `<div class="eyebrow">SKILL-DRIVEN READING</div><div class="page-heading"><div><h1>不同角色，做好各自的一步。</h1><p>每个角色由项目内的 SKILL.md 和引用规则定义。修改规则后，新任务读取新版本。</p></div></div><div class="capability-grid"><div class="capability-box"><span class="status-dot ${state.capabilities.ai_configured?'on':''}"></span><strong>阅读与摘要模型</strong><p>${state.capabilities.ai_configured?esc(state.capabilities.ai_model||'已配置'):'未配置模型；PDF 阅读、批注与笔记导出可正常使用。'}</p></div><div class="capability-box"><span class="status-dot ${state.capabilities.translation_configured?'on':''}"></span><strong>全文翻译</strong><p>${state.capabilities.translation_configured?'翻译服务已配置，可在阅读页按需运行。':'BabelDOC 待配置；可以先导入已有的单语译文 PDF。'}</p></div></div><div class="skill-grid">${state.skills.map(s=>`<article class="skill-card"><span class="skill-icon">${icons.spark}</span><div class="eyebrow">${esc(s.id)}</div><h2>${esc(s.name||s.id)}</h2><p>${esc(descriptions[s.id]||s.description)}</p>${s.modes?.length?`<div class="paper-projects">${s.modes.map(m=>`<span>${esc(m)}</span>`).join('')}</div>`:''}<small>skills/${esc(s.id)}/SKILL.md</small></article>`).join('')||'<div class="notice">Skill 清单暂时不可用，请检查后端服务。</div>'}</div><div class="notice">模型配置保存在服务端环境变量中。本页面不收集 API Key。当前版本面向本机个人使用，不包含多人账户系统。</div>`;
  }
  function render() {
    app.innerHTML=`<div class="workspace-layout">${sidebar()}<main class="workspace-main"><header class="workspace-topbar"><span>研究空间 <span class="crumb-separator">/</span> ${state.view==='sources'?'来源与关注':state.view==='skills'?'研究 Skills':'论文工作台'}</span><span class="pill">MVP 0.1</span></header><div class="workspace-content">${state.view==='sources'?sourcesArea():state.view==='skills'?skillsArea():library()}</div></main></div>`;
    updateURL();
  }
  let querySequence=0;
  async function loadPapers() {
    const seq=++querySequence;state.loading=true;
    const slot=qs('#library-results');if(slot)slot.innerHTML=paperList();
    const params=new URLSearchParams();if(state.projectId)params.set('project_id',state.projectId);if(state.query)params.set('q',state.query);if(state.projectId&&state.includeChildren)params.set('include_children','true');
    try{const result=await api('/papers?'+params);if(seq!==querySequence)return;state.papers=list(result);state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=paperList();}catch(e){if(seq!==querySequence)return;state.loading=false;if(qs('#library-results'))qs('#library-results').innerHTML=`<div class="error-panel">${esc(e.message)}</div>`;}
  }
  app.addEventListener('click', async event=>{
    const target=event.target.closest('button,[data-action]');if(!target)return;
    if(target.dataset.view){state.view=target.dataset.view;state.editProject=false;render();if(state.view==='library')loadPapers();}
    if('project' in target.dataset){state.projectId=target.dataset.project;state.view='library';state.editProject=false;render();loadPapers();}
    if(target.dataset.action==='show-upload'){state.view='library';if(!qs('#upload-panel'))render();qs('#upload-panel').hidden=false;qs('#upload-panel input[type=file]').focus();}
    if(target.dataset.action==='hide-upload')qs('#upload-panel').hidden=true;
    if(target.dataset.action==='edit-project'){state.editProject=!state.editProject;render();}
    if(target.dataset.follow){const id=target.dataset.follow;followed.has(id)?followed.delete(id):followed.add(id);localStorage.setItem('paperstudio-source-follows',JSON.stringify([...followed]));render();toast('已保存关注偏好，未启动自动采集');}
  });
  let debounce;
  app.addEventListener('input',event=>{
    if(event.target.id==='library-query'){state.query=event.target.value;clearTimeout(debounce);debounce=setTimeout(loadPapers,220);}
    if(event.target.id==='source-query'){state.sourceQuery=event.target.value;const start=event.target.selectionStart;render();const input=qs('#source-query');input.focus();input.setSelectionRange(start,start);}
  });
  app.addEventListener('change',event=>{
    if(event.target.id==='library-children'){state.includeChildren=event.target.checked;loadPapers();}
    if(event.target.id==='library-status'){state.status=event.target.value;qs('#library-results').innerHTML=paperList();}
    if(event.target.id==='source-priority'){state.priority=event.target.value;render();}
  });
  app.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target,fd=new FormData(form);const submit=form.querySelector('[type=submit]')||form.querySelector('button');if(submit)submit.disabled=true;
    try{
      if(form.id==='new-project-form'){
        const created=await api('/projects',{method:'POST',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.projectId=created.id;state.view='library';render();await loadPapers();toast('项目已创建');
      }else if(form.id==='edit-project-form'){
        await api('/projects/'+encodeURIComponent(state.projectId),{method:'PATCH',body:{name:fd.get('name'),parent_id:fd.get('parent_id')||null}});state.projects=list(await api('/projects'));state.editProject=false;render();toast('项目已保存');
      }else if(form.id==='upload-paper-form'){
        const file=fd.get('file');if(!file?.size)throw new Error('请选择一份 PDF。');if(file.size>40*1024*1024)throw new Error('第一版单文件上限为 40 MB。');qs('#upload-feedback').textContent='正在保存 PDF 并读取页面信息…';
        const p=await api('/papers',{method:'POST',body:fd});location.href=`/read/${encodeURIComponent(p.id)}?project=${encodeURIComponent(fd.get('project_id'))}`;
      }else if(form.dataset.associate){await api(`/papers/${encodeURIComponent(form.dataset.associate)}/projects`,{method:'POST',body:{project_id:fd.get('project_id')}});toast('已关联项目');await loadPapers();}
    }catch(e){toast(e.message);const feedback=qs('#upload-feedback');if(form.id==='upload-paper-form'&&feedback)feedback.textContent=e.message;}finally{if(submit?.isConnected)submit.disabled=false;}
  });
  try{
    const [projects,caps,skills,sources]=await Promise.allSettled([api('/projects'),api('/capabilities'),api('/skills'),api('/sources')]);
    if(projects.status==='rejected')throw projects.reason;state.projects=list(projects.value);if(state.projectId&&!state.projects.some(p=>p.id===state.projectId))state.projectId='';
    if(caps.status==='fulfilled')state.capabilities=caps.value;if(skills.status==='fulfilled')state.skills=list(skills.value);if(sources.status==='fulfilled'){state.sources=list(sources.value);state.sourceGroups=sources.value?.groups||{};}
    render();await loadPapers();
  }catch(e){app.innerHTML=`<main class="boot-state"><span class="brand-symbol">${icons.book}</span><h1>工作台暂时无法连接</h1><p>${esc(e.message)}</p><a class="button primary" href="/">重新连接</a></main>`;}
}
