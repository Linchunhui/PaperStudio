import {paperCards,bindPaperAction,openPaperFigure} from './paper-cards.js';

export function mountStarred(root,{api,esc,safeURL,toast,onOpen,onAdd,onGraph,state={}}){
  let disposed=false,sequence=0,page=1,items=[],pages=1,total=0;
  state.columns=state.columns===1?1:2;
  function paint(){
    root.innerHTML=`<div class="section-heading"><h2>我的收藏 · ${total}</h2><button class="button" data-starred-refresh>刷新</button></div><div class="explore-results-toolbar"><span class="small muted">按收藏时间排列</span><span class="grow"></span><div class="result-layout" role="group" aria-label="结果列数"><button data-columns="1" aria-pressed="${state.columns===1}">单列</button><button data-columns="2" aria-pressed="${state.columns===2}">双列</button></div></div><div class="unified-paper-grid" data-columns="${state.columns}">${paperCards(items,{esc,safeURL})}</div>${!items.length?'<p class="muted">点击论文旁的星标，即可在这里继续阅读。</p>':''}<div class="search-pagination"><button class="button" data-starred-page="${page-1}" ${page===1?'disabled':''}>上一页</button><span>第 ${page} 页</span><button class="button" data-starred-page="${page+1}" ${page>=pages?'disabled':''}>下一页</button></div>`;
  }
  async function load(){
    const seq=++sequence;root.innerHTML='<p role="status">正在读取收藏…</p>';
    try{const r=await api('/paper-feedback/starred?'+new URLSearchParams({page}));if(disposed||seq!==sequence)return;items=r.items;pages=r.pages;total=r.total;paint();}
    catch(e){if(!disposed&&seq===sequence)root.innerHTML=`<p role="alert">${esc(e.message)}</p><button class="button" data-starred-refresh>重试</button>`;}
  }
  async function click(e){
    const b=e.target.closest('button');if(!b||!root.contains(b))return;e.stopPropagation();
    try{
      if(b.dataset.paperFigure!==undefined){openPaperFigure(root,items[Number(b.dataset.paperFigure)],b,esc);return;}
      if(b.dataset.paperGraph!==undefined){await onGraph(items[Number(b.dataset.paperGraph)]);return;}
      if(b.dataset.columns){state.columns=Number(b.dataset.columns)===1?1:2;paint();return;}
      if(b.dataset.starredPage){page=Number(b.dataset.starredPage);await load();return;}
      if(b.hasAttribute('data-starred-refresh')){await load();return;}
      const task=bindPaperAction(b,items,{onOpen,onAdd,onTopic:()=>{}});if(task)await task;
    }catch(e){toast(e.message);}
  }
  root.addEventListener('click',click);void load();
  return {dispose(){disposed=true;sequence++;root.querySelector('.paper-figure-dialog')?.close?.();root.removeEventListener('click',click);}};
}
