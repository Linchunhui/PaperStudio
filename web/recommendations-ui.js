// Account preferences and a catalog feed. Dependencies come from the workspace
// so this module can be mounted/disposed without a second global event owner.
export const preferenceFields = ['category_ids', 'topic_ids', 'institutions', 'conferences', 'keywords'];
export function profileBody(profile) {
  return Object.fromEntries([...preferenceFields.map(key => [key, profile[key] || []]),
    ['freshness_days', profile.freshness_days || 30], ['expected_version', profile.version]]);
}

export async function loadProfile(api, legacy = {}) {
  let profile = await api('/me/recommendation-profile');
  if (profile.version === 0) {
    const follows = Array.isArray(legacy.follows) ? legacy.follows : [];
    const draft = {...profile,
      keywords: String(legacy.interests || '').split(/[,，;；\n]+/).map(s => s.trim()).filter(Boolean).slice(0, 20).map(s => s.slice(0, 80)),
      institutions: follows.filter(s => s.startsWith('institution:')).map(s => s.slice(12)).slice(0, 100),
      conferences: follows.filter(s => s.startsWith('conference:')).map(s => s.slice(11)).slice(0, 100)};
    if (draft.keywords.length || draft.institutions.length || draft.conferences.length) {
      try { profile = await api('/me/recommendation-profile', {method: 'PUT', body: profileBody(draft)}); }
      catch (error) { if (error.status !== 409) throw error; profile = await api('/me/recommendation-profile'); }
    }
  }
  return profile;
}

export function mountRecommendations(root, {api, esc, safeURL, toast, onOpen, onProfile, legacy, state = {}}) {
  let disposed = false, sequence = 0, initialization = 0, controller, saving = false;
  state.items ??= [];
  state.category ??= '';
  state.topic ??= '';
  const find = selector => root.querySelector(selector);
  const categories = () => (state.options?.taxonomy?.categories || []).filter(c => c.id !== 'unclassified');
  const split = value => [...new Set(String(value || '').split(/[,，;；\n]+/).map(s => s.trim()).filter(Boolean))];
  const selected = (values, value) => (values || []).includes(value) ? 'checked' : '';

  function preferences() {
    const p = state.draft || state.profile;
    if (!p) return '';
    return `<details class="rec-preferences" ${state.preferencesOpen || !state.profile.configured ? 'open' : ''}><summary>我的推荐偏好</summary>
      <form id="recommendation-profile-form"><p class="muted">可以只关注子方向，也可以关注整个领域。偏好随账号保存。</p>
      <fieldset class="rec-directions"><legend>研究方向</legend>${categories().map(c => `<details ${c.topics.some(t => (p.topic_ids || []).includes(t.id)) ? 'open' : ''}>
        <summary>${esc(c.name)}</summary><label class="rec-check"><input type="checkbox" name="category_ids" value="${esc(c.id)}" ${selected(p.category_ids, c.id)}>整个${esc(c.name)}领域</label>
        <div class="rec-topic-list">${c.topics.map(t => `<label class="rec-check" title="${esc(t.description || '')}"><input type="checkbox" name="topic_ids" value="${esc(t.id)}" ${selected(p.topic_ids, t.id)}>${esc(t.name)}</label>`).join('')}</div></details>`).join('')}</fieldset>
      <div class="rec-profile-fields"><label>关注机构（每行一个）<textarea name="institutions" rows="3" maxlength="20000" placeholder="例如：清华大学">${esc((p.institutions || []).join('\n'))}</textarea></label>
      <label>关注会议（每行一个）<textarea name="conferences" rows="3" maxlength="20000" placeholder="例如：ICLR">${esc((p.conferences || []).join('\n'))}</textarea></label>
      <label>研究关键词<input name="keywords" maxlength="1600" value="${esc((p.keywords || []).join(', '))}" placeholder="多个关键词用逗号分隔"></label>
      <label>时效偏好<select name="freshness_days">${[7, 14, 30, 60, 90, 180, 365, p.freshness_days].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b).map(n => `<option value="${n}" ${n === p.freshness_days ? 'selected' : ''}>偏重近 ${n} 天</option>`).join('')}</select><small>越短越偏重新论文，仍保留相关的较早研究。</small></label></div>
      <p class="muted">机构和会议名称可从“论文索引与关注”中选择。</p>
      <div class="actions"><button class="button primary" type="submit">保存并更新推荐</button><button class="text-button" type="button" data-rec-action="reload-profile">重新载入已保存偏好</button></div>
      <p class="form-feedback" id="recommendation-profile-feedback" role="status"></p></form></details>`;
  }

  function paintShell() {
    if (disposed) return;
    root.innerHTML = `<div class="page-heading"><div><h1 tabindex="-1">为你推荐</h1><p>围绕关注方向与机构发现论文，同时关注研究的新进展。</p></div><button class="button" data-rec-action="refresh">刷新推荐</button></div>
      <div id="recommendation-preferences">${preferences()}</div>
      <form id="recommendation-filter-form" class="rec-feed-filters"><label>浏览领域<select name="category"><option value="">全部关注领域</option>${categories().map(c => `<option value="${esc(c.id)}" ${state.category === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label><label>子方向<select name="topic"><option value="">全部子方向</option>${categories().filter(c => !state.category || c.id === state.category).flatMap(c => c.topics).map(t => `<option value="${esc(t.id)}" ${state.topic === t.id ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label><button class="button" type="submit">筛选</button><button class="text-button" type="button" data-rec-action="restore">恢复不感兴趣的论文</button></form>
      <div id="recommendation-results" aria-live="polite"></div>`;
    paintResults();
  }

  function paintResults() {
    const target = find('#recommendation-results');
    if (disposed || !target) return;
    target.setAttribute('aria-busy', String(!!state.loading));
    const error = state.error ? `<div class="error-panel"><p>${esc(state.error)}</p>${state.items.length ? '<p>以下保留上次结果。</p>' : ''}<button class="button" data-rec-action="retry">重试</button></div>` : '';
    const fallback = state.result?.fallback;
    target.innerHTML = `${error}${state.loading ? '<p class="results-update" role="status">正在获取推荐…</p>' : ''}
      ${fallback === 'no_preferences' ? '<p class="notice compact">选择关注方向后可获得个性化推荐。当前展示近期与热门论文。</p>' : fallback === 'no_matches' ? '<p class="notice compact">暂未找到匹配偏好的论文，先展示目录中的近期与热门研究。</p>' : ''}
      <div class="result-caption"><span>${state.items.length} 篇论文</span><span>综合兴趣匹配、发表时间与热度</span></div>
      <div class="rec-paper-list">${state.items.map(item => {
        const url = safeURL(item.url), primary = item.classification?.primary;
        return `<article class="discovery-card" data-recommendation-id="${esc(item.id)}" data-request-id="${esc(state.result?.request_id || '')}" data-position="${Number(item.position)}">
          <div class="discovery-card-meta">${primary ? `<span class="pill green">${esc(primary.topic_name || primary.category_name)}</span>` : ''}<span>${esc(item.score_signals?.age_days != null ? String(item.published_at).slice(0, 10) : '发表日期待核验')}</span></div>
          <h2><button class="discovery-title title-button" data-rec-open="${esc(item.id)}">${esc(item.title)}</button></h2>
          <p class="discovery-snippet">${esc(item.abstract_preview || '摘要待补充，可打开论文查看原文。')}</p>
          <div class="recommend-reasons">${(item.reasons || []).map(reason => `<span>${esc(reason)}</span>`).join('')}</div>
          <div class="discovery-card-actions"><button class="button primary small" data-rec-import="${esc(item.id)}">加入项目并处理</button>${url ? `<a class="text-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">论文原页 ↗</a>` : ''}<button class="text-button" data-rec-dismiss="${esc(item.id)}">不感兴趣</button></div></article>`;
      }).join('') || (!state.loading && !state.error ? '<div class="empty-state"><h2>暂无匹配论文</h2><p>可放宽筛选，或稍后查看新收录的论文。</p></div>' : '')}</div>
      ${state.result?.next_cursor ? `<div class="search-pagination"><button class="button" data-rec-action="more" ${state.loading ? 'disabled' : ''}>继续浏览</button></div>` : ''}`;
  }

  async function feed(more = false) {
    if (disposed) return;
    const filterKey = JSON.stringify([state.category, state.topic]);
    if (more && state.resultFilters !== filterKey) more = false;
    const seq = ++sequence;
    controller?.abort(); controller = new AbortController();
    state.loading = true; state.error = ''; state.retryMore = more; paintResults();
    const params = new URLSearchParams({limit: '24', category_id: state.category, topic_id: state.topic});
    if (more && state.result?.next_cursor) params.set('cursor', state.result.next_cursor);
    try {
      const result = await api('/recommendations?' + params, {signal: controller.signal});
      if (disposed || seq !== sequence) return;
      state.items = more ? [...new Map([...state.items, ...result.items].map(item => [item.id, item])).values()] : result.items;
      state.result = result; state.resultFilters = filterKey;
    } catch (error) {
      if (disposed || seq !== sequence) return;
      if (more && error.status === 409) { toast('推荐列表已更新，已重新载入。'); return feed(); }
      if (error.name !== 'AbortError') state.error = error.message;
    } finally { if (!disposed && seq === sequence) { state.loading = false; paintResults(); } }
  }

  function captureDraft() {
    const form = find('#recommendation-profile-form');
    if (!form) return;
    const data = new FormData(form);
    state.draft = {...state.profile, version: state.draft?.version ?? state.profile.version, category_ids: data.getAll('category_ids'), topic_ids: data.getAll('topic_ids'),
      institutions: split(data.get('institutions')), conferences: split(data.get('conferences')),
      keywords: split(data.get('keywords')), freshness_days: Number(data.get('freshness_days'))};
    state.preferencesOpen = !!find('.rec-preferences')?.open;
  }

  async function initialize({reload = false} = {}) {
    const init = ++initialization;
    state.loading = true; state.error = ''; paintShell();
    try {
      const [profile, options] = await Promise.all([loadProfile(api, legacy), api('/recommendations/options')]);
      if (disposed || init !== initialization) return;
      state.profile = profile; state.options = options;
      if (reload) state.draft = null;
      onProfile(profile); paintShell(); await feed();
    } catch (error) { if (!disposed && init === initialization) { state.loading = false; state.error = error.message; paintResults(); } }
  }

  async function click(event) {
    const button = event.target.closest('button');
    if (!button || !root.contains(button)) return;
    try {
      const id = button.dataset.recOpen || button.dataset.recImport;
      if (id) { await onOpen(state.items.find(item => item.id === id), {read: !!button.dataset.recOpen}); return; }
      if (button.dataset.recDismiss) {
        button.disabled = true;
        await api(`/recommendations/${encodeURIComponent(button.dataset.recDismiss)}/dismiss`, {method: 'POST'});
        if (disposed) return;
        state.items = state.items.filter(item => item.id !== button.dataset.recDismiss); paintResults();
        find('[data-rec-open]')?.focus(); return;
      }
      switch (button.dataset.recAction) {
        case 'refresh': case 'retry':
          if (!state.profile || !state.options) await initialize(); else await feed(button.dataset.recAction === 'retry' && state.retryMore); break;
        case 'more': await feed(true); break;
        case 'restore': await api('/me/recommendation-dismissals', {method: 'DELETE'}); await feed(); break;
        case 'reload-profile': if (!saving) await initialize({reload: true}); break;
      }
    } catch (error) { if (!disposed) toast(error.message); }
    finally { if (button.isConnected) button.disabled = false; }
  }

  async function submit(event) {
    if (!['recommendation-profile-form', 'recommendation-filter-form'].includes(event.target.id)) return;
    event.preventDefault(); event.stopPropagation();
    if (event.target.id === 'recommendation-filter-form') {
      const data = new FormData(event.target);
      state.category = String(data.get('category') || ''); state.topic = String(data.get('topic') || '');
      await feed(); return;
    }
    if (saving) return;
    captureDraft(); saving = true;
    const submittedDraft = state.draft;
    const button = event.target.querySelector('[type=submit]'); button.disabled = true;
    const feedback = find('#recommendation-profile-feedback'); feedback.textContent = '正在保存…';
    try {
      const profile = await api('/me/recommendation-profile', {method: 'PUT', body: profileBody(state.draft)});
      if (disposed) return;
      state.profile = profile;
      state.draft = state.draft === submittedDraft ? null : {...state.draft, version: profile.version};
      onProfile(profile);
      feedback.textContent = state.draft ? '已保存提交的偏好；当前还有未保存的修改。' : '已保存'; toast('推荐偏好已保存'); await feed();
    } catch (error) { if (!disposed) feedback.textContent = error.message; }
    finally { saving = false; if (button.isConnected) button.disabled = false; }
  }

  function input(event) { if (event.target.form?.id === 'recommendation-profile-form') captureDraft(); }
  function change(event) {
    input(event);
    if (event.target.form?.id === 'recommendation-filter-form' && event.target.name === 'category') {
      const category = event.target.value, topic = event.target.form.elements.topic;
      topic.innerHTML = '<option value="">全部子方向</option>' + categories().filter(c => !category || c.id === category).flatMap(c => c.topics).map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
    }
  }
  root.addEventListener('click', click); root.addEventListener('submit', submit);
  root.addEventListener('input', input); root.addEventListener('change', change);
  void initialize();
  return {dispose() {
    state.preferencesOpen = !!find('.rec-preferences')?.open;
    disposed = true; sequence++; controller?.abort();
    root.removeEventListener('click', click); root.removeEventListener('submit', submit);
    root.removeEventListener('input', input); root.removeEventListener('change', change);
  }};
}
