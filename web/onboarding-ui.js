// First-use actions are explicit; viewing this panel never invokes a model.
export const checkedLabel = value => value === true ? '已通过' : value === false ? '未通过' : '未测试';

export function mountWelcome(root, {api, esc, isAdmin, onConfigure, onTemplates, onSample}) {
  let disposed = false;
  const click = async event => {
    const button = event.target.closest?.('[data-welcome]');
    if (!button) return;
    if (button.dataset.welcome === 'configure') { onConfigure(); return; }
    if (button.dataset.welcome === 'templates') { onTemplates(); return; }
    if (button.dataset.welcome === 'dismiss') { root.hidden = true; return; }
    button.disabled = true;
    const message = root.querySelector('[role="status"]');
    try {
      const sample = await api('/onboarding/sample', {method:'POST'});
      if (!disposed) onSample(sample);
    } catch (error) { if (!disposed) message.textContent = error.message; }
    finally { if (!disposed) button.disabled = false; }
  };
  root.addEventListener('click', click);
  api('/onboarding').then(state => {
    if (disposed || state.has_papers) return;
    root.innerHTML = `<section class="welcome-card" aria-labelledby="welcome-title"><div><span class="eyebrow">从第一篇开始</span><h2 id="welcome-title">先读起来，模型可以稍后配置。</h2><p>用自编示例体验原文、预制译文和笔记；也可以直接导入自己的 PDF。</p><ol><li>打开示例或导入论文</li><li>划选一句原文，右键保存笔记</li><li>将阅读成果导出到项目</li></ol><div class="actions"><button class="button primary" data-welcome="sample">打开阅读示例</button>${isAdmin?'<button class="button" data-welcome="configure">连接自己的模型</button>':''}<button class="button" data-welcome="templates">选择研究模板</button><a class="text-link" href="/demo/" target="_blank" rel="noopener">浏览公开演示 ↗</a><button class="text-button" data-welcome="dismiss">收起</button></div><p class="small muted">${esc(state.sample_label)}</p><p role="status"></p></div></section>`;
  }).catch(() => { /* A failed optional guide never blocks the real workspace. */ });
  return {dispose(){disposed=true;root.removeEventListener('click',click);}};
}

export function modelForm(state, esc) {
  const options = [['none','先不配置模型'],['openai','OpenAI 兼容 API'],['local','本地模型（Ollama / LM Studio 等）'],['codex','Codex CLI']];
  const status = (label, value) => `<div><small>${label}</small><strong class="${value===true?'check-pass':value===false?'check-failed':''}">${checkedLabel(value)}</strong></div>`;
  return `<div class="section-heading"><div><h2>连接阅读模型</h2><p class="muted">原文阅读、目录和笔记无需模型。翻译、摘要和问答由你选择的服务处理。</p></div></div>
    <form id="model-setup-form" class="model-setup-form"><fieldset ${state.locked?'disabled':''}><label>使用方式<select name="provider">${options.map(([key,label])=>`<option value="${key}" ${key===state.provider?'selected':''}>${label}</option>`).join('')}</select></label>
    <div data-api-fields><label>API 基地址<input name="base_url" type="url" value="${esc(state.base_url)}" placeholder="https://your-provider.example/v1" autocomplete="off"></label><label>API Key<input name="api_key" type="password" placeholder="${state.has_api_key?'已保存；留空保留，同一地址有效':'本地模型可留空'}" autocomplete="new-password"></label><label class="check-label"><input name="clear_key" type="checkbox">清除已保存密钥</label></div>
    <div data-model-fields><label>模型名称<input name="model" value="${esc(state.model)}" placeholder="填写服务实际支持的模型 ID" autocomplete="off"></label></div>
    <p data-provider-hint class="notice"></p><button class="button primary" type="submit">保存配置</button> <button class="button" type="button" data-model-action="reset">使用部署环境配置</button></fieldset></form>
    <p class="small muted">${state.locked?'部署环境已锁定配置，只能进行连通性测试。':`当前来源：${state.source==='workspace'?'工作台保存的配置':'部署环境'}。保存后对新任务生效，无需重启；密钥仅保存在服务端，不会返回浏览器。`}</p>
    <div class="setup-checks">${status('配置已填写',state.configured)}${status('运行组件可用',state.runtime_available)}${status('连接 / 登录',state.connection)}${status('实际模型调用',state.completion)}</div>
    <div class="actions"><button class="button" data-model-action="connection" ${state.configured?'':'disabled'}>测试连接</button><button class="button" data-model-action="completion" ${state.configured?'':'disabled'}>测试实际调用</button></div><p class="small muted">连接测试不生成内容；实际调用会发送一条简短测试消息，可能产生少量费用。测试仅针对已保存的配置。</p><p class="setup-result" role="status">${esc(state.message||'尚未调用模型。')}${state.tested_at?` · ${esc(new Date(state.tested_at).toLocaleString())}`:''}</p><p class="small muted">PDF 对照翻译还需要 BabelDOC：${state.babeldoc_installed?'已安装，翻译质量需实际验收':'未安装，段落翻译仍可在模型接通后使用'}。外部模型会收到你主动提交的论文内容；公开演示不使用任何密钥。</p>`;
}

export function mountModelSettings(root, {api, esc, onSaved=()=>{}}) {
  let disposed=false, busy=false, saved=null;
  const feedback=message=>{const box=root.querySelector('[role="status"]');if(box)box.textContent=message;};
  function toggle(){
    const form=root.querySelector('form'); if(!form)return;
    const provider=form.elements.provider.value;
    root.querySelector('[data-api-fields]').hidden=!['openai','local'].includes(provider);
    root.querySelector('[data-model-fields]').hidden=provider==='none';
    root.querySelector('[data-provider-hint]').textContent={none:'可以直接阅读、做笔记，之后随时连接模型。',openai:'填写兼容 /chat/completions 的 API 基地址和模型 ID。',local:'仅连接你信任的本地或局域网模型。在 Docker 中，宿主机通常为 http://host.docker.internal:11434/v1；容器中的 localhost 指容器自身。',codex:saved?.codex_installed?'已检测到 Codex CLI；先测试登录，再测试实际调用。':'当前环境未安装 Codex CLI。请使用 Codex 镜像或由部署管理员安装并登录。'}[provider];
  }
  function render(state){if(disposed)return;saved=state;root.innerHTML=modelForm(state,esc);toggle();}
  async function submit(event){
    if(event.target.id!=='model-setup-form')return;
    event.preventDefault();event.stopPropagation();if(busy)return;
    busy=true;const form=event.target,fd=new FormData(form);form.querySelector('button[type="submit"]').disabled=true;
    try{const state=await api('/admin/model-settings',{method:'PUT',body:{provider:fd.get('provider'),base_url:fd.get('base_url'),model:fd.get('model'),api_key:fd.get('api_key')||null,clear_key:fd.get('clear_key')==='on'}});render(state);if(!disposed)onSaved(state);}
    catch(error){feedback(error.message);}finally{busy=false;const b=root.querySelector('button[type="submit"]');if(b)b.disabled=false;}
  }
  async function click(event){
    const button=event.target.closest?.('[data-model-action]');if(!button||busy)return;
    busy=true;for(const b of root.querySelectorAll('button'))b.disabled=true;
    feedback(button.dataset.modelAction==='reset'?'正在切换配置…':'正在测试已保存的配置，请稍候…');
    try{const state=button.dataset.modelAction==='reset'?await api('/admin/model-settings',{method:'DELETE'}):await api('/admin/model-settings/test',{method:'POST',body:{kind:button.dataset.modelAction}});render(state);if(!disposed)onSaved(state);}
    catch(error){render(saved);feedback(error.message);}finally{busy=false;}
  }
  root.addEventListener('submit',submit);root.addEventListener('click',click);root.addEventListener('change',toggle);
  api('/admin/model-settings').then(render).catch(error=>{if(!disposed)root.innerHTML=`<p role="alert">${esc(error.message)}</p>`;});
  return {dispose(){disposed=true;root.removeEventListener('submit',submit);root.removeEventListener('click',click);root.removeEventListener('change',toggle);}};
}
