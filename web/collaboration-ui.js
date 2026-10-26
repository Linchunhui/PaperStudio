import {api, esc, qs, list, toast, authSession} from './app.js';

// Replacing a panel must not discard keyboard position or an open disclosure.
function retainInteraction(root, preserveFields=false){
  const controls=()=>Array.from(root.querySelectorAll('input, textarea, select, button, summary, a'));
  const key=el=>el.id||[el.tagName,el.getAttribute('name')||'',el.form?.id||'',JSON.stringify(el.dataset),el.getAttribute('href')||'',el.tagName==='SUMMARY'?el.parentElement.className:el.tagName==='BUTTON'&&!Object.keys(el.dataset).length?el.textContent:''].join('|');
  const before=controls(),active=root.contains(document.activeElement)?document.activeElement:null;
  const activeKey=active?key(active):null,activeIndex=active?before.filter(el=>key(el)===activeKey).indexOf(active):0;
  const selection=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
  const fields=preserveFields?before.filter(el=>el.name&&!el.readOnly).map(el=>[key(el),el.value]):[];
  const disclosures=Array.from(root.querySelectorAll('details')).map(el=>[el.className,el.querySelector('summary')?.textContent.replace(/\(\d+\)/g,'')||'',el.open]);
  const scrollTop=root.scrollTop;
  return ()=>{
    const after=controls();
    for(const [fieldKey,value] of fields){const field=after.find(el=>key(el)===fieldKey);if(field&&(field.tagName!=='SELECT'||Array.from(field.options).some(option=>option.value===value)))field.value=value;}
    for(const el of root.querySelectorAll('details')){const index=disclosures.findIndex(([name,label])=>name===el.className&&label===(el.querySelector('summary')?.textContent.replace(/\(\d+\)/g,'')||''));if(index>=0){el.open=disclosures[index][2];disclosures.splice(index,1);}}
    const replacement=activeKey===null?null:after.filter(el=>key(el)===activeKey)[activeIndex];
    const focusTarget=replacement&&!replacement.disabled?replacement:activeKey===null?null:after.find(el=>!el.disabled&&el.form?.id===active.form?.id&&el.tagName!=='BUTTON')||after.find(el=>!el.disabled);
    if(focusTarget){focusTarget.focus({preventScroll:true});if(focusTarget===replacement&&selection&&replacement.setSelectionRange)replacement.setSelectionRange(...selection);}
    root.scrollTop=scrollTop;
  };
}

const roleNames={owner:'组长',admin:'管理员',editor:'可编辑成员',viewer:'只读成员'};
const roleOptions=selected=>['admin','editor','viewer'].map(role=>`<option value="${role}" ${role===selected?'selected':''}>${roleNames[role]}</option>`).join('');

export async function mountGroups(container){
  let groups=[],selected=null,error='',invitation=null,composing=false,pendingRender=false,submitting=false;
  const inviteLink=()=>invitation?.invite_url?new URL(invitation.invite_url,location.origin).href:invitation?.token||'';
  const manageable=()=>selected&&(selected.owner_id===authSession.user.id||selected.role==='admin'||selected.role==='owner');
  function render(){
    if(!container.isConnected)return;if(composing){pendingRender=true;return;}pendingRender=false;
    const restore=retainInteraction(container,true);
    container.innerHTML=`<div class="groups-layout"><section class="groups-list"><form id="create-group-form"><label>新建研究小组<input name="name" required maxlength="100" placeholder="例如：文档理解研究组"></label><button class="button primary">创建小组</button></form><form id="join-group-form"><label>加入已有小组<input name="token" value="${esc(new URLSearchParams(location.search).get('invite')||'')}" required autocomplete="off" placeholder="粘贴邀请令牌或邀请链接"></label><button class="button">接受邀请</button></form><div class="group-cards">${groups.map(group=>`<button class="group-card ${selected?.id===group.id?'active':''}" aria-pressed="${selected?.id===group.id}" data-group="${esc(group.id)}"><strong>${esc(group.name)}</strong><small>${esc(roleNames[group.role]||group.role)} · ${esc(group.member_count||1)} 位成员</small></button>`).join('')||'<p class="muted">创建小组或接受邀请，开始共享阅读材料。</p>'}</div></section><section class="group-detail">${error?`<div class="error-panel" role="alert">${esc(error)}</div>`:''}${selected?`<div class="section-heading"><div><h2>${esc(selected.name)}</h2><p>共享项目中的论文清单与已主动共享的解读、笔记、总结。</p></div><span class="pill">${esc(roleNames[selected.role]||selected.role||'成员')}</span></div>${manageable()?`<details class="group-invite"><summary>邀请新成员</summary><form id="invite-group-form" aria-label="生成小组邀请"><label>加入后的角色<select name="role">${roleOptions('editor')}</select></label><label>有效天数<input name="expires_days" type="number" min="1" max="30" value="7" required></label><button class="button">生成邀请链接</button></form>${invitation?`<label>邀请链接<input readonly value="${esc(inviteLink())}" id="group-invite-link"></label><button class="text-button" data-copy-invite>复制邀请链接</button><p class="small muted">将链接发给想邀请的成员。链接到期后需要重新生成。</p>`:''}</details>`:''}<h3>成员</h3><div class="group-members">${(selected.members||[]).map(member=>{const owner=member.user_id===selected.owner_id;return `<div class="group-member"><div><strong>${esc(member.display_name||member.username)}</strong><small>${esc(member.username)}${member.user_id===authSession.user.id?' · 你':''}</small></div>${manageable()&&!owner?`<select data-member-role="${esc(member.user_id)}" aria-label="${esc(member.display_name||member.username)} 的角色">${roleOptions(member.role)}</select><button class="text-button danger" aria-label="将 ${esc(member.display_name||member.username)} 移出小组" data-remove-member="${esc(member.user_id)}">移出</button>`:`<span class="pill">${owner?'组长':esc(roleNames[member.role]||member.role)}</span>`}</div>`;}).join('')}</div><h3>共享项目</h3><div class="group-projects">${(selected.projects||[]).map(project=>`<a href="/?view=library&project=${encodeURIComponent(project.id||project.project_id)}" class="shared-project-card"><strong>${esc(project.name||project.project_name)}</strong><span>查看论文与共享成果 ↗</span></a>`).join('')||'<p class="muted">暂无共享项目。项目所有者可以在项目页面中选择“共享项目”。</p>'}</div><p class="group-privacy">个人笔记默认私有。只有主动共享的内容才会进入小组的共同阅读材料。</p>`:'<div class="empty-state"><h2>让研究有共同的上下文。</h2><p>选择一个小组，查看成员和共享项目。</p></div>'}</section></div>`;
    restore();
  }
  async function reload(id=selected?.id){groups=list(await api('/groups'));if(id){selected=await api('/groups/'+encodeURIComponent(id));const summary=groups.find(group=>group.id===id);if(summary)selected={...summary,...selected};}render();}
  container.oncompositionstart=()=>{composing=true;};
  container.oncompositionend=()=>{composing=false;if(pendingRender)render();};
  container.onclick=async event=>{const button=event.target.closest('button');if(!button)return;event.stopPropagation();try{if(button.dataset.group){invitation=null;await reload(button.dataset.group);}else if(button.dataset.removeMember){if(!confirm('将该成员移出小组？其个人笔记会保留。'))return;await api(`/groups/${encodeURIComponent(selected.id)}/members/${encodeURIComponent(button.dataset.removeMember)}`,{method:'DELETE'});await reload();}else if(button.hasAttribute('data-copy-invite')){await navigator.clipboard.writeText(inviteLink());toast('邀请链接已复制');}}catch(e){error=e.message;render();}};
  container.onchange=async event=>{const field=event.target;if(!field.dataset.memberRole)return;event.stopPropagation();try{await api(`/groups/${encodeURIComponent(selected.id)}/members/${encodeURIComponent(field.dataset.memberRole)}`,{method:'PATCH',body:{role:field.value}});await reload();toast('成员角色已更新');}catch(e){error=e.message;await reload();}};
  container.onsubmit=async event=>{event.preventDefault();event.stopPropagation();if(submitting)return;submitting=true;const form=event.target,fd=new FormData(form),button=form.querySelector('button');if(button)button.disabled=true;form.setAttribute('aria-busy','true');error='';try{if(form.id==='create-group-form'){const group=await api('/groups',{method:'POST',body:{name:fd.get('name')}});await reload(group.id);}else if(form.id==='join-group-form'){let token=String(fd.get('token')||'').trim();try{const url=new URL(token);token=url.searchParams.get('invite')||token;}catch{}const group=await api('/groups/join',{method:'POST',body:{token}});await reload(group.id||group.group_id);}else if(form.id==='invite-group-form'){invitation=await api(`/groups/${encodeURIComponent(selected.id)}/invitations`,{method:'POST',body:{role:fd.get('role'),expires_days:Number(fd.get('expires_days'))}});render();}}catch(e){error=e.message;render();}finally{submitting=false;if(button?.isConnected)button.disabled=false;if(form.isConnected)form.setAttribute('aria-busy','false');}};
  try{await reload();}catch(e){error=e.message;render();}
}

let sharingDialogId=0;
export async function openProjectSharing(project){
  const opener=document.activeElement,dialog=document.createElement('dialog'),headingId=`sharing-title-${++sharingDialogId}`,descriptionId=`sharing-description-${sharingDialogId}`;
  dialog.className='sharing-dialog';dialog.setAttribute('aria-labelledby',headingId);dialog.setAttribute('aria-describedby',descriptionId);document.body.append(dialog);
  let groups=[],shares=[],error='',busy=true;
  async function load(){const result=await Promise.all([api('/groups'),api(`/projects/${encodeURIComponent(project.id)}/shares`)]);if(!dialog.isConnected)return;groups=list(result[0]);shares=Array.isArray(result[1])?result[1]:result[1].shares||result[1].items||[];}
  function render(){
    if(!dialog.isConnected)return;
    const restore=retainInteraction(dialog,true),available=groups.filter(group=>group.role!=='viewer'&&!shares.some(share=>(share.group_id||share.id)===group.id));
    dialog.innerHTML=`<div class="sharing-heading"><h2 id="${headingId}">共享项目 · ${esc(project.name)}</h2><button class="icon-button" data-close-sharing aria-label="关闭共享设置" autofocus>×</button></div><p id="${descriptionId}">小组成员可以阅读项目论文和已共享成果。成员的个人笔记保持私有。</p><p role="status" aria-live="polite" aria-atomic="true">${busy?'正在更新共享设置…':''}</p>${error?`<p class="task-error" role="alert">${esc(error)}</p>`:''}<form id="share-project-form" aria-busy="${busy}"><label>共享给小组<select name="group_id" required ${busy||!available.length?'disabled':''}><option value="">选择小组</option>${available.map(group=>`<option value="${esc(group.id)}">${esc(group.name)}</option>`).join('')}</select></label><button class="button primary" ${busy||!available.length?'disabled':''}>共享项目</button></form>${!busy&&!available.length?'<p class="small muted">暂无可共享的小组。请先创建小组或加入具有编辑权限的小组。</p>':''}<h3>已共享给</h3>${shares.map(share=>`<div class="project-share-row"><strong>${esc(share.group_name||share.name||groups.find(group=>group.id===share.group_id)?.name||'研究小组')}</strong><button class="text-button danger" data-unshare="${esc(share.group_id||share.id)}" ${busy?'disabled':''}>取消共享</button></div>`).join('')||(!busy?'<p class="muted">此项目目前未共享。</p>':'')}`;
    restore();
    if(dialog.open&&!dialog.contains(document.activeElement))dialog.querySelector('[data-close-sharing]').focus({preventScroll:true});
  }
  async function update(action){if(busy)return;busy=true;error='';render();try{await action();await load();}catch(e){error=e.message;}finally{busy=false;render();}}
  dialog.onclick=async event=>{const button=event.target.closest('button');if(!button)return;if(button.hasAttribute('data-close-sharing')){dialog.close();return;}if(button.dataset.unshare)await update(()=>api(`/projects/${encodeURIComponent(project.id)}/shares/${encodeURIComponent(button.dataset.unshare)}`,{method:'DELETE'}));};
  dialog.onsubmit=async event=>{event.preventDefault();const fd=new FormData(event.target);await update(()=>api(`/projects/${encodeURIComponent(project.id)}/shares`,{method:'POST',body:{group_id:fd.get('group_id')}}));};
  dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close();});
  dialog.addEventListener('close',()=>{dialog.remove();if(opener?.isConnected)opener.focus({preventScroll:true});},{once:true});
  render();dialog.showModal();try{await load();}catch(e){error=e.message;}finally{busy=false;render();}
}
