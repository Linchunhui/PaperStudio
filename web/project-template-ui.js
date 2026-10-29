export function mountProjectTemplates(root,{api,esc,onCreated}){
  let disposed=false,busy=false;
  root.innerHTML='<p class="muted">正在载入研究项目模板…</p>';
  api('/onboarding/templates').then(data=>{
    if(disposed)return;
    root.innerHTML=`<section class="portability-panel"><h2>从一个研究问题开始</h2><p class="muted">${esc(data.notice)}</p><div class="project-template-grid">${data.templates.map(t=>`<article><h3>${esc(t.name)}</h3><p>${esc(t.description)}</p><details><summary>查看阅读问题与目录</summary><ul>${t.questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ul><p>${t.folders.map(esc).join(' / ')}</p></details><button class="button" data-project-template="${esc(t.id)}">创建项目目录</button></article>`).join('')}</div><p role="status">创建目录不会下载论文或调用模型。</p></section>`;
  }).catch(error=>{if(!disposed)root.innerHTML=`<p role="alert">${esc(error.message)}</p>`;});
  async function click(event){
    const button=event.target.closest?.('[data-project-template]');if(!button||busy)return;
    busy=true;button.disabled=true;
    try{const value=await api('/onboarding/templates/'+encodeURIComponent(button.dataset.projectTemplate),{method:'POST'});if(!disposed)await onCreated(value);}
    catch(error){if(!disposed)root.querySelector('[role="status"]').textContent=error.message;}
    finally{busy=false;if(button.isConnected)button.disabled=false;}
  }
  root.addEventListener('click',click);
  return {dispose(){disposed=true;root.removeEventListener('click',click);}};
}
