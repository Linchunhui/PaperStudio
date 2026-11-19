import {feedbackMarkup} from './paper-feedback.js';
import {insightMarkup} from './paper-insights.js';
import {cardStructuredMarkup} from './card-structured.js';
import {mathText} from './math.js';
function conciseReasons(item) {
  const clean = value => String(typeof value === 'string' ? value : value?.reason || value?.label || '')
    .replace(/\s+/g, ' ').trim();
  const reading = (item.reading_card?.reasons || []).map(clean).filter(Boolean);
  const personal = (item.reasons || []).map(clean)
    .map(text => text.replace(/(?:\s*[·|｜;；,，]\s*)?(?:综合)?相似度\s*[:：]?\s*\d+(?:\.\d+)?\s*%?/g, '').trim())
    .filter(Boolean);
  const candidates = [reading[0], personal[0], ...reading.slice(1), ...personal.slice(1)];
  const seen = new Set();
  return candidates.filter(text => {
    if (!text) return false;
    const key = text.replace(/[\s，。；：、,.!:;！]/g, '').toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).slice(0, 2);
}

function availableFigure(item) {
  const figure = item?.paper_figure;
  const localImage = url => typeof url === 'string' && /^\/api\/catalog\/[a-zA-Z0-9_-]+\/figure\.png\?(?:[^#]*)$/.test(url);
  return figure && localImage(figure.url) && localImage(figure.full_url) ? figure : null;
}

export function openPaperFigure(root, item, button, esc) {
  const figure = availableFigure(item);
  if (!figure) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'paper-figure-dialog';
  dialog.setAttribute('aria-label', `${item.title} · 图 ${figure.number}`);
  const caption = String(figure.caption || '').replace(/\$\s+([^$]+?)\s+\$/g, (_, value) => '$' + value.trim() + '$');
  dialog.innerHTML = `<div class="paper-figure-heading"><h2>${esc(item.title)}</h2><button type="button" class="icon-button" aria-label="关闭论文配图">×</button></div><img src="${esc(figure.full_url)}" alt="${esc(figure.caption)}"><p class="paper-figure-caption">${mathText(caption)}</p><small>图 ${esc(figure.number)} · 原论文第 ${esc(figure.page)} 页</small>`;
  dialog.querySelector('img').addEventListener('error', () => {dialog.querySelector('img').replaceWith(document.createTextNode('配图暂时无法加载，请稍后重试。'));}, {once:true});
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('cancel', event => {event.preventDefault();dialog.close();});
  dialog.addEventListener('close', () => { dialog.remove(); if (button.isConnected) button.focus({preventScroll:true}); }, {once:true});
  root.append(dialog);
  dialog.showModal();
}

function citationMetric(item, esc) {
  const metric = item.openalex_citations;
  if (!Number.isSafeInteger(metric?.count) || metric.count < 0 || !/^W\d+$/.test(metric.work_id)) return '';
  const count = metric.count.toLocaleString('zh-CN');
  const date = metric.observed_at?.slice(0,10) || '';
  return `<a href="https://openalex.org/${esc(metric.work_id)}" title="OpenAlex${date?' · '+esc(date):''}" target="_blank" rel="noopener noreferrer">OpenAlex 被引 ${count} ↗</a>`;
}

function conciseRecommendation(item, index, {esc, safeURL, reasonLabel, dismiss, footer}) {
  const metric = value => value == null ? '—' : Number(value).toLocaleString('zh-CN');
  const institutions = (item.institutions || []).map(value=>typeof value==='string'?value:value?.name).filter(Boolean);
  const authors = (item.authors || []).map(a => typeof a === 'string' ? a : a.name).filter(Boolean);
  const team = institutions.length ? institutions.join(' · ') : authors.slice(0, 3).join(' · ');
  const abstract = item.reading_card?.abstract || item.abstract || item.abstract_preview || item.snippet || item.summary || '';
  const reasons = conciseReasons(item);
  const figure = availableFigure(item);
  const figureMarkup = figure ? `<button type="button" class="paper-brief-figure" data-paper-figure="${index}" aria-label="放大论文图 ${esc(figure.number)}"><img src="${esc(figure.url)}" alt="${esc(figure.caption)}" loading="lazy" decoding="async"><span>图 ${esc(figure.number)} · 点击放大</span></button>` : '';
  const github = safeURL(item.github_url);
  const published = String(item.published_at || item.published || item.year || '');
  const date = published.match(/^\d{4}(?:-\d{2}(?:-\d{2})?)?/)?.[0] || '';
  const titleURL = item.kind === 'article' && safeURL(item.url);
  const hf = [item.url, ...(item.provenance || []).map(p => p.source_url)].map(safeURL)
    .find(url => url && /^https:\/\/(?:huggingface\.co|hf-mirror\.com)\/papers\//.test(url));
  const metricMarkup = (label, value, url) => url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ${metric(value)} ↗</a>`
    : `<span>${label} ${metric(value)}</span>`;
  return `<article class="discovery-card unified-paper recommendation-compact">
    <h2>${titleURL ? `<a class="discovery-title" href="${esc(titleURL)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>` : `<button class="discovery-title title-button" data-paper-open="${index}">${esc(item.title)}</button>`}</h2>
    <div class="paper-brief-meta">${['withdrawn','withdrawn_duplicate'].includes(item.publication_status) ? '<span class="paper-brief-status">已撤稿</span>' : ''}${date ? `<time class="paper-brief-date" datetime="${esc(date)}" title="发表时间">${esc(date)}</time>` : '<span class="paper-brief-date">发表时间待补充</span>'}${team ? `<span class="paper-brief-team">${esc(team)}</span>` : ''}<span class="paper-brief-metrics" aria-label="论文公开热度">${metricMarkup('HF 点赞', item.hf_upvotes, hf)}${metricMarkup('GitHub Stars', item.github_stars, github)}${citationMetric(item, esc)}</span></div>
    <section class="paper-brief-abstract">${figureMarkup}<h3>${item.reading_card?.abstract?'生成摘要':'摘要'}</h3><p>${abstract ? mathText(abstract) : '摘要待补充'}</p></section>
    ${reasons.length ? `<section class="paper-brief-recommendation" aria-label="${esc(reasonLabel)}"><h3>${esc(reasonLabel)}</h3><div class="paper-brief-reasons"><p class="paper-reason-main">${mathText(reasons[0])}</p>${reasons[1] ? `<p class="paper-reason-context"><span>${mathText(reasons[1])}</span></p>` : ''}</div></section>` : ''}
    <details class="paper-metadata"><summary>论文信息与来源</summary>${authors.length?`<p>${esc(authors.join(" · "))}</p>`:""}${(item.conferences||[]).map(c=>`<p>${esc(c.conference)} ${esc(c.year)} · ${esc(c.presentation)} · ${c.verification_status==='pending'?'待核验':'来源标为确认'}</p>`).join('')}${insightMarkup({...item,structured:undefined},{compact:true})}${(item.provenance||[]).length?`<ul>${item.provenance.map(p=>`<li>${esc(p.source_id||"来源")} ${esc(p.ranking_week||p.week||'')} ${p.rank!=null?'第 '+esc(p.rank)+' 名':''}${safeURL(p.source_url)?` <a href="${esc(safeURL(p.source_url))}" target="_blank" rel="noopener noreferrer">来源 ↗</a>`:""}${p.notes?`<p>${esc(p.notes)}</p>`:""}</li>`).join("")}</ul>`:""}${!footer&&item.classification?.primary?`<button class="pill green" data-paper-topic="${index}">${esc(item.classification.primary.topic_name||item.classification.primary.category_name)}</button>`:''}${!footer&&item.kind!=='article'?`<button class="text-button" data-paper-graph="${index}">相关论文 ↗</button>`:''}</details>${cardStructuredMarkup(item,esc)}<div class="paper-social-row">${feedbackMarkup(item)}</div>${footer ? footer(item,index) : `<div class="discovery-card-actions">${item.kind==='article'&&!item.paper_urls?.length?'':`<button class="button primary small" data-paper-add="${index}">加入项目</button>`}${safeURL(item.url)?`<a class="text-link" href="${esc(safeURL(item.url))}" target="_blank" rel="noopener noreferrer">论文原页 ↗</a>`:''}${dismiss?`<button class="text-button" data-paper-dismiss="${index}">不感兴趣</button>`:''}</div>`}
  </article>`;
}

// All list surfaces share one renderer. Footer HTML comes from trusted UI callers only.
export function paperCards(items, {esc, safeURL, reasonLabel='推荐理由', dismiss=false, footer}={}) {
  return items.map((item,index)=>conciseRecommendation(item,index,{esc,safeURL,reasonLabel,dismiss,footer})).join('');
}
export function bindPaperAction(button,items,{onOpen,onAdd,onTopic}) {
  for(const [key,fn] of [['paperOpen',onOpen],['paperAdd',onAdd],['paperTopic',onTopic]])if(key in button.dataset){const item=items[Number(button.dataset[key])];if(item)return Promise.resolve(fn(item));}
  return null;
}
