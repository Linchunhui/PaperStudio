// A recommendation stays in place while its PDF is added in the background.
export function openProjectPicker({item, api, esc, safeURL, toast, projectId, document: doc = document}) {
  doc.querySelector('#paper-project-picker')?.close();
  const opener = doc.activeElement, dialog = doc.createElement('dialog');
  dialog.id = 'paper-project-picker'; dialog.className = 'paper-project-picker';
  dialog.setAttribute('aria-labelledby', 'project-picker-title');
  let closed = false, submitting = false, timer, task = null;
  const url = item.resolve?.action === 'search' ? '' : safeURL(item.resolve?.url) || safeURL(item.pdf_url) || safeURL(item.url);
  dialog.innerHTML = `<div class="project-picker-heading"><h2 id="project-picker-title">加入项目</h2><button class="icon-button" type="button" data-picker-close aria-label="关闭加入项目弹窗">×</button></div><p class="project-picker-paper">${esc(item.title)}</p>
    <form id="project-picker-form"><label>目标项目<select name="project_id" required disabled><option value="">正在加载项目…</option></select></label>${!url ? '<label>论文链接<input name="url" type="url" required maxlength="4096" placeholder="粘贴这篇论文的 arXiv 或公开 PDF 链接"><small>该条目尚缺独立论文链接，补充后即可加入。</small></label>' : ''}
    <p id="project-picker-status" role="status" aria-live="polite"></p><div class="actions"><button class="button primary" type="submit" disabled>加入项目</button><button class="button" type="button" data-picker-close>取消</button></div></form><div id="project-picker-result"></div>`;
  doc.body.append(dialog); dialog.showModal();
  const form = dialog.querySelector('form'), select = form.elements.project_id;
  const status = dialog.querySelector('#project-picker-status'), submit = form.querySelector('[type=submit]');
  const result = dialog.querySelector('#project-picker-result');
  dialog.addEventListener('close', () => {closed = true; clearTimeout(timer); dialog.remove(); if (opener?.isConnected) opener.focus({preventScroll:true});});
  dialog.addEventListener('click', event => {if (event.target.closest('[data-picker-close]')) dialog.close();});
  const stages = {queued:'已提交，正在等待保存原文…', running:'正在保存原文并解析…', succeeded:'已加入项目', partial:'论文已保存，部分解析未完成', blocked:'加入任务需要处理', failed:'加入未完成', interrupted:'加入任务已中断', cancelled:'加入任务已取消'};
  function paintTask() {
    if (closed || !task) return;
    const active = ['queued','running'].includes(task.status);
    status.textContent = stages[task.status] || task.status;
    const closeButton=dialog.querySelector('form [data-picker-close]');if(closeButton)closeButton.textContent='继续浏览';
    const errors = [task.error, ...(task.steps || []).map(step => step.error)].filter(Boolean);
    result.innerHTML = `${errors.length ? `<p class="task-error" role="alert">${esc([...new Set(errors)].join('；'))}</p>` : ''}${task.paper_id ? `<a class="button primary" href="/read/${encodeURIComponent(task.paper_id)}?project=${encodeURIComponent(task.project_id)}">开始阅读</a>` : ''}${active ? '<p class="small muted">可以关闭弹窗继续浏览，任务会在后台完成。</p>' : ''}${['failed','partial','blocked','interrupted','cancelled'].includes(task.status) ? '<button class="button" type="button" data-picker-retry>重试任务</button>' : ''}`;
    dialog.querySelector('[data-picker-retry]')?.addEventListener('click', async event => {
      if(submitting)return;submitting=true;event.target.disabled = true;
      try {task = await api(`/imports/${encodeURIComponent(task.id)}/retry`, {method:'POST'}); paintTask(); schedule();}
      catch(error) {if (!closed) {status.textContent = error.message; event.target.disabled = false;}}
      finally{submitting=false;}
    });
  }
  function schedule() {clearTimeout(timer); if (!closed && ['queued','running'].includes(task?.status)) timer = setTimeout(poll, 1500);}
  async function poll() {
    try {task = await api('/imports/' + encodeURIComponent(task.id)); paintTask(); schedule();}
    catch(error) {if (!closed) {status.textContent = `状态暂时无法更新：${error.message}`; timer = setTimeout(poll, 5000);}}
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); event.stopPropagation(); if (submitting || task) return;
    submitting = true; submit.disabled = true; select.disabled = true;
    status.textContent = '正在加入项目…';
    try {
      const chosenURL = url || safeURL(form.elements.url?.value);
      if (!chosenURL) throw new Error('请填写可访问的论文链接。');
      // Title-only catalog entries cannot be source-verified by an arbitrary URL.
      const body = {project_id:select.value, url:chosenURL, title:item.title,
        processing:{parse:true,translate:false,guide:false,brief_modes:[]}};
      if (url && item.catalog_paper_id) body.catalog_paper_id = item.catalog_paper_id;
      task = await api('/imports', {method:'POST',body});
      if (closed) {toast(task.paper_id ? '论文已加入项目' : '已提交加入项目任务，可在任务页查看进度'); return;}
      submit.textContent = '已提交'; paintTask(); schedule();
    } catch(error) {if (!closed) {status.textContent = error.message; submit.disabled = false; select.disabled = false;}}
    finally {submitting = false;}
  });
  void (async () => {
    try {
      const response = await api('/projects'); if (closed) return;
      const projects = (Array.isArray(response) ? response : response.items || response.projects || []).filter(p => p.can_edit !== false);
      const byId = new Map(projects.map(p => [p.id,p]));
      const name = p => {const names = [], seen = new Set(); while(p && !seen.has(p.id)){seen.add(p.id);names.unshift(p.name);p=byId.get(p.parent_id);} return names.join(' / ');};
      select.innerHTML = projects.map(p => `<option value="${esc(p.id)}">${esc(name(p))}</option>`).join('');
      if (projects.some(p => p.id === projectId)) select.value = projectId;
      select.disabled = submit.disabled = !projects.length;
      status.textContent = projects.length ? '保存原文并解析，解读可在阅读时按需查看。' : '暂无可编辑项目，请先创建项目或请项目管理员授予编辑权限。';
      if (!doc.activeElement || doc.activeElement === dialog || doc.activeElement === opener || doc.activeElement?.hasAttribute?.('data-picker-close')) select.focus();
    } catch(error) {if (!closed) status.textContent = '无法加载项目：' + error.message;}
  })();
  return {close:() => dialog.close()};
}
