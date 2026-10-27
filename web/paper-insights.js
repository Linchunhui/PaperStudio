import {mathText} from './math.js';
// Catalog material is displayed separately from verified, page-bound quotations.
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function insightMarkup(item = {}, {compact = false} = {}) {
  const abstract = item.abstract || item.abstract_preview || '';
  const data = item.structured;
  const fields = [['tasks','研究任务'], ['methods','方法组件'], ['contributions','主要贡献'], ['experiments','实验'], ['datasets','数据集'], ['limitations','局限']];
  const sections = fields.filter(([key]) => Array.isArray(data?.[key]) && data[key].length);
  const hasInterpretation = !!data?.research_problem || sections.length > 0;
  return `<div class="paper-insights">${abstract ? `<section class="paper-abstract"><h3>摘要</h3>${compact && abstract.length > 260 ? `<p>${mathText(abstract.slice(0,260))}…</p><details><summary>展开完整摘要</summary><p>${mathText(abstract)}</p></details>` : `<p>${mathText(abstract)}</p>`}</section>` : '<p class="small muted">摘要待补充</p>'}
    ${hasInterpretation ? `<section class="paper-interpretation"><h3>结构化解读</h3>${data.research_problem ? `<div class="insight-problem"><span>研究问题</span><p>${mathText(data.research_problem)}</p></div>` : ''}${sections.map(([key, label]) => `<details class="insight-field"><summary>${label}<span>${data[key].length}</span></summary><ul>${data[key].map(text => `<li>${mathText(text)}</li>`).join('')}</ul></details>`).join('')}
      <details class="insight-source"><summary>解读来源与依据</summary><p>已收录的结构化材料 · ${escape(data.source_version || '版本未标注')}</p><p>以下为收录材料提供的依据；带页码的原文引用请在阅读器中展开核验。</p>${Array.isArray(data.evidence) && data.evidence.length ? `<ul>${data.evidence.map(text => `<li>${mathText(text)}</li>`).join('')}</ul>` : ''}</details></section>` : ''}</div>`;
}
