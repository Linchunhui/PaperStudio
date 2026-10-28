// Production controller contract tests; DOM doubles are not visual browser QA.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../web/recommendations-ui.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace(/^export /gm, '');
class Fields {
  constructor(form) { this.data = form?.data || {}; }
  get(key) { return this.data[key] ?? ''; }
  getAll(key) { return Array.isArray(this.data[key]) ? this.data[key] : []; }
}
const context = vm.createContext({console, URLSearchParams, AbortController, FormData: Fields});
vm.runInContext('(function(){'+fs.readFileSync(new URL('../web/math.js',import.meta.url),'utf8').replace(/^export /gm,'')+';Object.assign(globalThis,{mathText,mathMarkdown,sourceSelection});})()',context);
vm.runInContext(fs.readFileSync(new URL('../web/paper-insights.js',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace(/^export /gm,''),context);
vm.runInContext(source, context);
const {mountRecommendations, loadProfile} = vm.runInContext('({mountRecommendations,loadProfile})', context);
const empty = {version: 0, configured: false, category_ids: [], topic_ids: [], institutions: [], conferences: [], keywords: [], freshness_days: 30};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeURL = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
const settle = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };

// A first visit migrates only this account's legacy preferences, once.
let server = {...empty}, writes = [];
const migrateAPI = async (path, request = {}) => {
  if (request.method === 'PUT') { writes.push(request.body); server = {...request.body, version: 1, configured: true}; }
  return server;
};
await loadProfile(migrateAPI, {interests: 'OCR, 对比学习', follows: ['institution:Example Lab', 'conference:ICLR', 'org001']});
assert.deepEqual(Array.from(server.institutions), ['Example Lab']);
assert.deepEqual(Array.from(server.conferences), ['ICLR']);
assert.deepEqual(Array.from(server.keywords), ['OCR', '对比学习']);
await loadProfile(migrateAPI, {interests: 'stale browser preference'});
assert.equal(writes.length, 1, 'Server preferences must win after migration');
let reads = 0;
const raced = await loadProfile(async (_path, request = {}) => {
  if (request.method === 'PUT') throw Object.assign(new Error('concurrent update'), {status: 409});
  return reads++ ? {...empty, version: 2, keywords: ['new preference']} : empty;
}, {interests: 'legacy'});
assert.equal(raced.version, 2);
await assert.rejects(loadProfile(async (_p, request = {}) => {
  if (request.method) throw new Error('offline'); return empty;
}, {interests: 'legacy'}), /offline/);

const handlers = new Map(), nodes = new Map(), requests = [], opened = [], notifications = [];
const button = () => ({isConnected: true, disabled: false});
const form = {id: 'recommendation-profile-form', data: {...empty, topic_ids: ['document.ocr'], keywords: 'Draft idea'}, querySelector: button};
const el = key => {
  if (!nodes.has(key)) nodes.set(key, {innerHTML: '', textContent: '', attributes: {}, open: true, focus() {}, setAttribute(k, v) { this.attributes[k] = v; }});
  return nodes.get(key);
};
const root = {innerHTML: '', querySelector(key) { return key === '#recommendation-profile-form' ? form : el(key); },
  contains() { return true; }, addEventListener(k, fn) { handlers.set(k, fn); }, removeEventListener(k) { handlers.delete(k); }};
const malicious = {id: 'one', title: '<img src=x onerror=1>', url: 'javascript:alert(1)', abstract_preview: '<script>bad()</script>', structured:{research_problem:'<script>Interpretation</script>',methods:['Safe method'],experiments:['Small benchmark'],source_version:'curated-v1',evidence:['Source excerpt']}, reasons: ['<b>untrusted</b>'], position: 1, score_signals: {age_days: null}};
const second = {id: 'two', title: 'Another paper', url: 'https://example.org/paper', reasons: [], position: 2};
const result = (items, next_cursor = null) => ({items, request_id: 'request-1', next_cursor, fallback: null});
let profile = {...empty, version: 1, configured: true}, behavior, saveFailure = false, delaySave = false, resolveSave;
const api = async (path, request = {}) => {
  requests.push({path, request});
  if (path === '/me/recommendation-profile') {
    if (request.method === 'PUT') {
      if (saveFailure) throw Object.assign(new Error('偏好已在其他页面更新'), {status: 409});
      if (delaySave) await new Promise(resolve => { resolveSave = resolve; });
      profile = {...request.body, version: profile.version + 1, configured: true};
    }
    return profile;
  }
  if (path === '/recommendations/options') return {taxonomy: {categories: [{id: 'document', name: '文档', topics: [{id: 'document.ocr', name: 'OCR', description: '文字识别'}]}]}};
  if (path.startsWith('/recommendations?')) return behavior ? behavior(path) : result([malicious], 'cursor-one');
  if (path.endsWith('/dismiss') || path === '/me/recommendation-dismissals') return {};
  throw new Error('Unexpected endpoint ' + path);
};
const state = {};
const controller = mountRecommendations(root, {api, esc, safeURL, toast: m => notifications.push(m),
  onOpen: (item, options) => opened.push({item, options}), onAdd: item => opened.push({item,options:{read:false}}), onProfile() {}, legacy: {}, state});
await settle();
assert.match(root.innerHTML, /我的推荐偏好/);
assert.match(root.innerHTML, /整个文档领域/);
assert.match(el('#recommendation-results').innerHTML, /&lt;img/);
assert.match(el('#recommendation-results').innerHTML, /&lt;script&gt;/);
assert.doesNotMatch(el('#recommendation-results').innerHTML, /href="javascript:/);
assert.equal(requests.filter(r => r.request.method).length, 0, 'No model call, PDF import or write on a configured visit');
const click = async dataset => {
  const target = {...button(), dataset};
  await handlers.get('click')({target: {closest: () => target}}); await settle();
};
await click({recOpen: 'one'});
assert.equal(opened[0].item.id, 'one'); assert.equal(opened[0].options.read, true);

await click({recImport:'one'});
assert.equal(opened.at(-1).item.id,'one');assert.equal(opened.at(-1).options.read,false);
assert.match(el('#recommendation-results').innerHTML,/结构化解读|推荐理由/);
assert.match(el('#recommendation-results').innerHTML,/Safe method/);
assert.match(el('#recommendation-results').innerHTML,/&lt;script&gt;Interpretation&lt;\/script&gt;/);
assert.doesNotMatch(el('#recommendation-results').innerHTML,/加入项目并处理/);
const completeAbstract='A long original abstract. '.repeat(40);
const expanded=context.insightMarkup({abstract:completeAbstract},{compact:true});
assert.ok(expanded.includes(completeAbstract),'The collapsed preview retains the complete abstract');
assert.match(expanded,/<details class="reading-abstract">[\s\S]*展开完整摘要/);
assert.doesNotMatch(context.insightMarkup({}),/结构化解读/,'Missing interpretation is not fabricated');

behavior = () => result([malicious, second]);
await click({recAction: 'more'});
assert.equal(state.items.length, 2, 'Appending a page cannot duplicate a paper');
assert.match(requests.filter(r => r.path.startsWith('/recommendations?')).at(-1).path, /cursor=cursor-one/);
await click({recDismiss: 'one'});
assert.deepEqual(Array.from(state.items, p => p.id), ['two']);
assert.ok(requests.some(r => r.path === '/recommendations/one/dismiss' && r.request.method === 'POST'));

let resolveSlow;
behavior = () => new Promise(resolve => { resolveSlow = resolve; });
const slow = click({recAction: 'refresh'}); await settle();
assert.match(el('#recommendation-results').innerHTML, /Another paper/);
assert.equal(el('#recommendation-results').attributes['aria-busy'], 'true');
const shell = root.innerHTML;
handlers.get('input')({target: {form}});
assert.deepEqual(Array.from(state.draft.keywords), ['Draft idea']);
behavior = () => result([{...second, title: 'Newest result'}], 'expired-cursor');
await click({recAction: 'refresh'});
resolveSlow(result([{...second, title: 'Stale result'}])); await slow;
assert.match(el('#recommendation-results').innerHTML, /Newest result/);
assert.doesNotMatch(el('#recommendation-results').innerHTML, /Stale result/);
assert.equal(root.innerHTML, shell, 'Refreshing results must not rebuild the preference form');
assert.deepEqual(Array.from(state.draft.keywords), ['Draft idea']);

behavior = () => { throw new Error('Network failed'); };
await click({recAction: 'refresh'});
assert.match(el('#recommendation-results').innerHTML, /Newest result/);
assert.match(el('#recommendation-results').innerHTML, /Network failed/);
behavior = path => {
  if (path.includes('cursor=')) throw Object.assign(new Error('snapshot expired'), {status: 409});
  return result([second]);
};
await click({recAction: 'more'});
assert.ok(notifications.some(s => s.includes('列表已更新')));
assert.equal(state.result.next_cursor, null);

saveFailure = true;
await handlers.get('submit')({target: form, preventDefault() {}, stopPropagation() {}});
assert.match(el('#recommendation-profile-feedback').textContent, /其他页面更新/);
assert.equal(state.draft.version, 1, 'Conflict must keep the original expected version and the unsaved draft');
saveFailure = false; behavior = () => result([second]);
await handlers.get('submit')({target: form, preventDefault() {}, stopPropagation() {}});
assert.equal(state.profile.version, 2);
assert.equal(state.draft, null);
const saved = requests.filter(r => r.request.method === 'PUT').at(-1).request.body;
assert.deepEqual(Array.from(saved.topic_ids), ['document.ocr']);
assert.equal(saved.expected_version, 1);

delaySave = true;
const saving = handlers.get('submit')({target: form, preventDefault() {}, stopPropagation() {}});
await settle();
form.data.keywords = 'Edited while saving';
handlers.get('input')({target: {form}});
resolveSave(); await saving;
assert.equal(state.profile.version, 3);
assert.equal(state.draft.version, 3);
assert.deepEqual(Array.from(state.draft.keywords), ['Edited while saving']);
assert.match(el('#recommendation-profile-feedback').textContent, /还有未保存/);

behavior = () => new Promise(resolve => { resolveSlow = resolve; });
const abandoned = click({recAction: 'refresh'}); await settle();
controller.dispose();
const finalMarkup = el('#recommendation-results').innerHTML;
resolveSlow(result([malicious])); await abandoned;
assert.equal(el('#recommendation-results').innerHTML, finalMarkup, 'Unmounted requests cannot repaint another workspace view');
assert.equal(handlers.size, 0);
console.log('PASS: recommendation preference migration, conflicts, safe rendering, paging, dismissals, retry, stale requests and draft preservation');
