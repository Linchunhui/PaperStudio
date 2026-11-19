import {mathText} from './math.js';

const fields = [['research_problem','研究问题'],['tasks','研究任务'],['methods','方法'],['contributions','主要贡献'],['experiments','实验与结果'],['datasets','数据集'],['limitations','边界与局限']];
// Display only the supplied public interpretation. Opening a card never calls a model.
export function cardStructuredMarkup(item, esc) {
  if (item.kind === 'article') return '';
  const data=item.structured;
  const sections=fields.map(([key,label])=>{
    const values=(Array.isArray(data?.[key])?data[key]:[data?.[key]]).filter(v=>typeof v==='string'&&v.trim());
    return values.length?`<section class="guide-section"><header><h3>${label}</h3></header>${values.map(v=>`<div class="guide-claim guide-prose"><p>${mathText(v)}</p></div>`).join('')}</section>`:'';
  }).join('');
  const evidence=Array.isArray(data?.evidence)?data.evidence.filter(v=>typeof v==='string'&&v.trim()):[];
  const source=sections?`<details class="guide-source"><summary>解读来源与依据</summary><p>${esc(data.source_version||'版本未标注')}</p><p>已收录的结构化材料；${esc(data.evidence_scope||'原文覆盖范围未标注')}。带页码的原文引用请在阅读器中核对。</p>${evidence.map(v=>`<p>${mathText(v)}</p>`).join('')}</details>`:'';
  const state=sections?(data.status==='partial'||item.structured_status==='partial'?'部分内容':'已有'):'暂无';
  return `<details class="paper-card-structured"><summary><strong>结构化解读</strong><span class="card-structured-state">${state}</span><span class="card-structured-toggle" aria-hidden="true"></span></summary><div class="card-structured-content"><div class="card-structured-body structured-result-body">${sections?`<div class="structured-guide">${sections}${source}</div>`:'<p class="small muted">暂无已有结构化解读，可在阅读页查看处理进度或生成解读。</p>'}</div></div></details>`;
}
// Compatibility for callers mounting the old lazy reader. Native details need no requests.
export function mountCardStructured() { return {dispose(){}}; }
