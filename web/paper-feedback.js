const escape=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
export function feedbackReference(item){
  if(item.paper_id)return {paper_id:item.paper_id};
  if(item.catalog_paper_id)return {catalog_id:item.catalog_paper_id};
  if(item.graph_id)return {graph_id:item.graph_id};
  if(item.arxiv_id)return {arxiv_id:item.arxiv_id};
  if(item.id&&(item.source_ids||item.provenance))return {catalog_id:item.id};
  try{const url=new URL(item.url);if(url.hostname==='arxiv.org'){const id=url.pathname.match(/^\/(?:abs|pdf)\/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?$/)?.[1];if(id)return {arxiv_id:id};}}catch{}
  return null;
}
export function feedbackMarkup(item){
  if(item.kind==='article')return '';
  const ref=feedbackReference(item);if(!ref)return '';
  return `<div class="paper-feedback" data-feedback-ref="${escape(encodeURIComponent(JSON.stringify(ref)))}" role="group" aria-label="对这篇论文的反馈"><button type="button" disabled aria-label="点赞"><span aria-hidden="true">👍</span></button><button type="button" disabled aria-label="收藏"><span aria-hidden="true">☆</span></button><button type="button" disabled aria-label="踩"><span aria-hidden="true">👎</span></button></div>`;
}
export function mountFeedback(root,{api,toast=()=>{},onChange=()=>{}}){
  let disposed=false,pending=false;const states=new Map(),busy=new Set(),loading=new Set(),failed=new Set();
  const elements=()=>[...root.querySelectorAll('[data-feedback-ref]')];
  function paint(){for(const el of elements()){const key=el.dataset.feedbackRef,s=states.get(key);if(!s)continue;const version=JSON.stringify([s,busy.has(s.subject)]);if(el.dataset.feedbackVersion===version)continue;el.dataset.feedbackVersion=version;
    el.innerHTML=[['like','点赞','👍',s.vote===1,s.likes],['star','收藏',s.starred?'★':'☆',s.starred,s.stars],['dislike','踩','👎',s.vote===-1,s.dislikes]].map(([action,label,icon,active,count])=>`<button type="button" data-feedback-action="${action}" aria-label="${active?'取消':''}${label}" title="${active?'取消':''}${label}" aria-pressed="${active}" ${busy.has(s.subject)?'disabled':''}><span aria-hidden="true">${icon}</span>${count?`<small>${count}</small>`:''}</button>`).join('');}}
  async function refresh(){if(disposed)return;paint();const keys=[...new Set(elements().map(el=>el.dataset.feedbackRef))].filter(key=>!states.has(key)&&!loading.has(key)&&!failed.has(key)).slice(0,100);if(!keys.length)return;keys.forEach(key=>loading.add(key));
    try{const result=await api('/paper-feedback/batch',{method:'POST',body:{references:keys.map(k=>JSON.parse(decodeURIComponent(k)))}});if(disposed)return;result.items.forEach((s,i)=>states.set(keys[i],s));paint();}
    catch(error){if(!disposed){keys.forEach(key=>failed.add(key));for(const el of elements())if(keys.includes(el.dataset.feedbackRef)){el.innerHTML='<button type="button" data-feedback-retry>重试反馈</button>';el.title=error.message;}}}
    finally{keys.forEach(key=>loading.delete(key));}}
  async function click(event){const button=event.target.closest('button'),host=button?.closest('[data-feedback-ref]');if(!host||!root.contains(host))return;event.preventDefault();event.stopImmediatePropagation();if(button.hasAttribute('data-feedback-retry')){failed.delete(host.dataset.feedbackRef);await refresh();return;}
    const s=states.get(host.dataset.feedbackRef);if(!s||busy.has(s.subject))return;const action=button.dataset.feedbackAction,body={reference:JSON.parse(decodeURIComponent(host.dataset.feedbackRef))};
    if(action==='star')body.starred=!s.starred;else body.vote=s.vote===(action==='like'?1:-1)?0:action==='like'?1:-1;
    busy.add(s.subject);paint();try{const next=await api('/paper-feedback',{method:'PATCH',body});if(disposed)return;for(const [key,value] of states)if(value.subject===s.subject)states.set(key,next);onChange(next);}
    catch(error){if(!disposed)toast(error.message);}finally{busy.delete(s.subject);if(!disposed){paint();host.querySelector(`[data-feedback-action="${action}"]`)?.focus({preventScroll:true});}}}
  root.addEventListener('click',click,true);
  const observer=typeof MutationObserver==='undefined'?null:new MutationObserver(()=>{if(pending||disposed)return;pending=true;queueMicrotask(()=>{pending=false;void refresh();});});
  observer?.observe(root,{childList:true,subtree:true});void refresh();
  return {refresh,dispose(){disposed=true;observer?.disconnect();root.removeEventListener('click',click,true);}};
}
