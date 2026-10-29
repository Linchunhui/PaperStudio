export function mountDataBundle(root,{api,esc}){
  let disposed=false,timer=null;
  function render(state){if(disposed)return;
    const percent=state.total_bytes?Math.min(100,Math.round((state.downloaded_bytes||0)*100/state.total_bytes)):null;
    root.innerHTML=`<h2>论文数据包</h2><p>当前目录 <strong>${Number(state.catalog_records).toLocaleString()}</strong> 条记录。完整目录、阅读卡片和关系网络可以随时加载。</p><p class="small muted">${state.bundled?'安装包已包含完整数据，将直接校验并载入。':'将从本项目 GitHub Release 下载完整数据包并校验 SHA256。'}不会批量下载论文 PDF，不调用模型，也不会删除个人项目和笔记。</p>${percent===null?'':`<progress value="${percent}" max="100" aria-label="数据包下载进度">${percent}%</progress><span> ${percent}%</span>`}<p role="status">${esc(state.stage||'')}</p><button class="button" data-load-bundle ${state.state==='running'?'disabled':''}>${state.state==='running'?'正在加载…':state.state==='failed'||state.state==='interrupted'?'重试加载完整数据':'加载完整数据'}</button>`;
    if(state.state==='running')timer=setTimeout(refresh,1600);
  }
  async function refresh(){try{render(await api('/admin/data-bundle'));}catch(error){if(!disposed)root.innerHTML=`<p role="alert">${esc(error.message)}</p>`;}}
  async function click(event){const button=event.target.closest?.('[data-load-bundle]');if(!button)return;button.disabled=true;clearTimeout(timer);try{render(await api('/admin/data-bundle',{method:'POST'}));}catch(error){if(!disposed){root.querySelector('[role="status"]').textContent=error.message;button.disabled=false;}}}
  root.addEventListener('click',click);refresh();
  return {dispose(){disposed=true;clearTimeout(timer);root.removeEventListener('click',click);}};
}
