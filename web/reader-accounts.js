// Account management is local to this workspace; no external service is called.
export function readerInput(form) {
  const fields = new FormData(form);
  const interests = fields.getAll('topic').map(topic_id => ({topic_id, level:String(fields.get('level')||'入门')}));
  for (const custom of String(fields.get('custom')||'').split(/[,，\n]/).map(s=>s.trim()).filter(Boolean))
    interests.push({custom, level:String(fields.get('level')||'入门')});
  if (!interests.length) throw new Error('请选择至少一个方向，或填写自定义方向。');
  if (interests.length>20) throw new Error('最多选择 20 个方向。');
  return {name:String(fields.get('name')||'').trim(), persona:String(fields.get('persona')||'').trim(),
    goals:String(fields.get('goals')||'').split('\n').map(s=>s.trim()).filter(Boolean), interests};
}

export function mountReaderAccounts(root,{api,esc,onSwitched}) {
  let disposed=false,busy=false;
  const status=message=>{const el=root.querySelector('[role="status"]');if(el)el.textContent=message;};
  const load=async()=>{
    const data=await api('/me/reader-accounts');
    if(disposed)return;
    const users=[data.owner,...data.items.map(item=>item.user)];
    root.innerHTML=`<section class="portability-panel"><h2>阅读用户</h2>
      <p class="muted">同一登录账号下的阅读空间。论文库、关注、笔记与行为分别保存；子用户不继承管理员权限。</p>
      <div class="actions">${users.map(user=>`<button class="button" data-reader-switch="${esc(user.id)}" ${user.id===data.active_user_id?'disabled aria-current="true"':''}>${esc(user.display_name)}${user.id===data.owner.id?' · 主账号':''}${user.id===data.active_user_id?' · 当前':''}</button>`).join('')}</div>
      <details><summary>新建阅读用户</summary><form class="model-setup-form" data-reader-create>
        <label>名称<input name="name" required maxlength="80"></label>
        <label>身份或研究阶段<input name="persona" required maxlength="200" placeholder="例如：博士生、算法工程师"></label>
        <label>阅读目标，每行一个<textarea name="goals" required maxlength="1600"></textarea></label>
        <label>研究方向，可多选<select name="topic" multiple size="8">${(data.taxonomy.categories||[]).filter(c=>c.topics.length).map(c=>`<optgroup label="${esc(c.name)}">${c.topics.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</optgroup>`).join('')}</select></label>
        <label>其他方向，以逗号分隔<input name="custom" maxlength="500"></label>
        <label>当前熟悉程度<input name="level" value="入门" required maxlength="200"></label>
        <button class="button primary" type="submit">创建阅读用户</button>
      </form></details><p role="status"></p></section>`;
  };
  const lock=value=>{busy=value;for(const el of root.querySelectorAll('button'))el.disabled=value||el.getAttribute('aria-current')==='true';};
  const click=async event=>{
    const button=event.target.closest?.('[data-reader-switch]');
    if(!button||busy)return;
    lock(true);
    try {
      const result=await api('/me/reader-accounts/switch',{method:'POST',body:{user_id:button.dataset.readerSwitch}});
      // Parent must apply the new CSRF token before its next write, and reload
      // the workspace to dispose all state and in-flight requests of the old user.
      // The server session has changed even if navigation disposed this panel.
      // Always reconcile global identity; disposal only suppresses local rendering.
      await onSwitched(result);
    } catch(error){if(!disposed)status(error.message);}
    finally{if(!disposed)lock(false);}
  };
  const submit=async event=>{
    if(!event.target.matches?.('[data-reader-create]'))return;
    event.preventDefault();if(busy)return;
    try {
      const body=readerInput(event.target);lock(true);
      await api('/me/reader-accounts',{method:'POST',body});
      if(!disposed){await load();status('已创建。点击名称切换阅读空间。');}
    }catch(error){if(!disposed)status(error.message);}
    finally{if(!disposed)lock(false);}
  };
  root.addEventListener('click',click);root.addEventListener('submit',submit);
  root.innerHTML='<p role="status">正在载入阅读用户…</p>';
  load().catch(error=>{if(!disposed)status(error.message);});
  return {dispose(){disposed=true;root.removeEventListener('click',click);root.removeEventListener('submit',submit);}};
}
