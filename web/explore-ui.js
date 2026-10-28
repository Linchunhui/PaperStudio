import {loadProfile} from './recommendations-ui.js';
import {paperCards,bindPaperAction} from './paper-cards.js';
export function mountExplore(root,{api,esc,safeURL,toast,onOpen,onAdd,onGraph,onProfile,legacy,state={}}){
  let disposed=false,sequence=0,controller;
  Object.assign(state,{q:state.q||'',scope:state.scope||'catalog',sort:state.sort||'relevance',page:1,items:[],columns:state.columns||2,category:state.category||'',topic:state.topic||''});
  const find=q=>root.querySelector(q);
  function shell(){
    root.innerHTML=`<div class="page-heading"><div><h1 tabindex="-1">搜索与推荐</h1></div></div><form id="explore-search" class="explore-search"><label class="search-box"><input name="q" type="search" maxlength="500" aria-label="搜索论文" placeholder="搜索论文、作者或研究问题；留空查看推荐" value="${esc(state.q)}"></label><select name="scope" aria-label="搜索范围">${[['catalog','论文索引'],['arxiv','arXiv'],['hf','Hugging Face'],['web','全网']].map(([id,name])=>`<option value="${id}" ${state.scope===id?'selected':''}>${name}</option>`).join('')}</select><button class="button primary">搜索</button></form><div class="explore-results-toolbar"><strong>${state.q?'搜索结果':'为你推荐'}</strong>${state.q?`<button class="text-button" data-explore="recommend">返回推荐</button><select id="explore-sort" aria-label="结果排序" ${state.scope!=='catalog'?'hidden':''}>${[['relevance','相关度'],['published','最新发表'],['oldest','最早发表'],['votes','HF 点赞'],['stars','GitHub Stars']].map(([id,name])=>`<option value="${id}" ${state.sort===id?'selected':''}>${name}</option>`).join('')}</select>`:'<button class="text-button" data-explore="refresh">换一批 / 刷新</button>'}<span class="grow"></span><div class="result-layout" role="group" aria-label="结果列数"><button data-columns="1" aria-pressed="${state.columns===1}">单列</button><button data-columns="2" aria-pressed="${state.columns===2}">双列</button></div></div>${state.category||state.topic?`<button class="filter-chip" data-explore="clear-topic">${esc(state.topicName||state.topic||state.category)} ×</button>`:''}<div id="explore-results" aria-live="polite"></div>`;
    paint();
  }
  function paint(){
    if(disposed)return;const box=find('#explore-results');if(!box)return;
    box.setAttribute('aria-busy',String(!!state.loading));
    box.innerHTML=`${state.loading?'<p role="status">正在查找论文…</p>':''}${state.error?`<div class="error-panel">${esc(state.error)} <button class="button" data-explore="retry">重试</button></div>`:''}${(state.result?.statuses||[]).filter(s=>s.error||s.stale).map(s=>`<p class="notice compact">${s.stale?'当前展示缓存结果。 ':''}${esc(s.error||'来源暂未更新')}</p>`).join('')}${state.result?.fallback==='no_preferences'?'<p class="small muted">当前展示近期与热门论文；可在论文索引中关注方向、机构或作者。</p>':''}${state.result?.fallback==='no_matches'?'<p class="small muted">暂无匹配关注的论文，先展示近期与热门论文。</p>':''}<div class="unified-paper-grid" data-columns="${state.columns}">${paperCards(state.items,{esc,safeURL,reasonLabel:state.q?'排序理由':'推荐理由',dismiss:!state.q})}</div>${!state.loading&&!state.error&&!state.items.length?'<div class="empty-state"><h2>暂无结果</h2><p>试试更短的关键词，或更换搜索范围。</p></div>':''}<div class="search-pagination">${state.q?`<button class="button" data-explore="prev" ${state.page<=1||state.loading?'disabled':''}>上一页</button><span>第 ${state.page} 页${state.result?.total!=null?' · '+state.result.total+' 篇':''}</span><button class="button" data-explore="next" ${!state.hasMore||state.loading?'disabled':''}>下一页</button>`:state.result?.next_cursor?`<button class="button" data-explore="more" ${state.loading?'disabled':''}>继续浏览</button>`:''}</div>`;
  }
  async function load(more=false){
    const seq=++sequence;controller?.abort();controller=new AbortController();state.loading=true;state.error='';state.retryMore=more;paint();
    const query=new URLSearchParams();let path;
    if(!state.q){query.set('limit','24');query.set('category_id',state.category);query.set('topic_id',state.topic);if(more&&state.result?.next_cursor)query.set('cursor',state.result.next_cursor);path='/recommendations?'+query;}
    else if(state.scope==='catalog'){query.set('q',state.q);query.set('sort',state.sort);query.set('page',state.page);query.set('direction',state.category);query.set('topic',state.topic);path='/catalog?'+query;}
    else{query.set('q',state.q);query.set('scope',state.scope);query.set('page',state.page);query.set('direction',state.category);query.set('topic',state.topic);path='/discovery/search?'+query;}
    try{
      const result=await api(path,{signal:controller.signal});if(disposed||seq!==sequence)return;
      const items=(result.items||[]).map(item=>state.q&&state.scope!=='catalog'?{...item,abstract:item.abstract||item.snippet||item.summary||'',reasons:item.reasons?.length?item.reasons:[`沿用 ${state.scope==='web'?'搜索服务':state.scope} 返回的相关度顺序；本站未重新评分`]}:{...item,catalog_paper_id:item.id});
      state.items=more?[...new Map([...state.items,...items].map(i=>[i.id,i])).values()]:items;state.result=result;
      state.hasMore=state.scope==='catalog'?state.page<result.pages:!!result.has_more&&state.page<(state.scope==='hf'?5:10);
    }catch(error){if(disposed||seq!==sequence)return;if(more&&error.status===409){state.items=[];return load();}if(error.name!=='AbortError')state.error=error.message;}
    finally{if(!disposed&&seq===sequence){state.loading=false;paint();}}
  }
  async function click(event){
    const b=event.target.closest('button');if(!b||!root.contains(b))return;event.stopPropagation();
    try{
      if(b.dataset.paperGraph!==undefined){await onGraph(state.items[Number(b.dataset.paperGraph)]);return;}
      const action=bindPaperAction(b,state.items,{onOpen,onAdd,onTopic:async item=>{const c=item.classification?.primary;if(!c)return;state.category=c.category_id;state.topic=c.topic_id||'';state.topicName=c.topic_name||c.category_name;state.page=1;state.items=[];shell();await load();}});if(action){b.disabled=true;await action;return;}
      if('paperDismiss' in b.dataset){b.disabled=true;const item=state.items[Number(b.dataset.paperDismiss)];await api('/recommendations/'+encodeURIComponent(item.id)+'/dismiss',{method:'POST'});if(!disposed){state.items=state.items.filter(i=>i.id!==item.id);paint();}return;}
      if(b.dataset.columns){state.columns=Number(b.dataset.columns);for(const control of root.querySelectorAll('[data-columns]'))control.setAttribute('aria-pressed',String(Number(control.dataset.columns)===state.columns));paint();return;}
      const name=b.dataset.explore;if(!name)return;
      if(name==='recommend'){state.q='';state.category='';state.topic='';state.page=1;state.items=[];shell();}
      if(name==='clear-topic'){state.category='';state.topic='';state.page=1;state.items=[];shell();}
      if(name==='prev')state.page--;if(name==='next')state.page++;
      if(['prev','next','refresh'].includes(name))state.items=[];
      await load(name==='more'||name==='retry'&&state.retryMore);
    }catch(error){if(!disposed)toast(error.message);}finally{if(b.isConnected)b.disabled=false;}
  }
  async function submit(e){if(e.target.id!=='explore-search')return;e.preventDefault();e.stopPropagation();const data=new FormData(e.target);state.q=String(data.get('q')||'').trim();state.scope=String(data.get('scope'));state.page=1;state.items=[];state.result=null;shell();await load();}
  function change(e){if(e.target.id==='explore-sort'){state.sort=e.target.value;state.page=1;state.items=[];void load();}}
  root.addEventListener('click',click);root.addEventListener('submit',submit);root.addEventListener('change',change);shell();
  void (async()=>{try{const profile=await loadProfile(api,legacy);if(disposed)return;onProfile(profile);await load();}catch(error){if(!disposed){state.error=error.message;paint();}}})();
  return {dispose(){disposed=true;sequence++;controller?.abort();root.removeEventListener('click',click);root.removeEventListener('submit',submit);root.removeEventListener('change',change);}};
}
