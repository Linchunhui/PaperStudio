// Local KaTeX -> native MathML. No CDN, injected HTML, links or shared macros.
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function mathText(value) {
  const text = String(value ?? '');
  const pattern = /(```[\s\S]*?```|`[^`\n]*`)|(\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\\d])\$(?!\s)([^$\n]+?)\$(?!\d))/g;
  let result = '', end = 0;
  for (const match of text.matchAll(pattern)) {
    result += escape(text.slice(end, match.index)); end = match.index + match[0].length;
    if (match[1]) { result += escape(match[0]); continue; }
    const tex = match[3] ?? match[4] ?? match[5] ?? match[6], displayMode = match[3] !== undefined || match[4] !== undefined;
    // Currency is ordinary text. Invalid/unavailable math remains readable source.
    if (/^\d[\d.,\s]*$/.test(tex) || tex.length > 12000 || !globalThis.katex) { result += escape(match[0]); continue; }
    try {
      const rendered = globalThis.katex.renderToString(tex, {output:'mathml', displayMode, throwOnError:true, trust:false, strict:'ignore', maxSize:20, maxExpand:200, macros:{}});
      result += `<span class="paper-math ${displayMode?'math-display':'math-inline'}" data-math-source="${escape(match[0])}">${rendered}</span>`;
    } catch { result += `<code class="math-source" title="公式保留 LaTeX 原文">${escape(match[0])}</code>`; }
  }
  return result + escape(text.slice(end));
}
export function mathMarkdown(text) {
  // Protect multi-line display equations before splitting prose into paragraphs.
  const math = [];
  const protectedText = String(text ?? '').replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]/g, value => `\u0000${math.push(value)-1}\u0000`);
  return protectedText.split('\n').map(line => {
    let safe = mathText(line).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    safe = safe.replace(/\u0000(\d+)\u0000/g, (_m, i) => mathText(math[Number(i)] ?? ''));
    if (/^#{1,3}\s/.test(line)) return `<h3>${safe.replace(/^#{1,3}\s/, '')}</h3>`;
    if (/^[-*]\s/.test(line)) return `<p class="markdown-bullet">${safe.replace(/^[-*]\s/, '')}</p>`;
    return line.trim() ? `<p>${safe}</p>` : '<div class="markdown-space"></div>';
  }).join('');
}
export function sourceSelection(range) {
  const expanded = range.cloneRange();
  const start = (range.startContainer.nodeType===1?range.startContainer:range.startContainer.parentElement)?.closest?.('[data-math-source]');
  const end = (range.endContainer.nodeType===1?range.endContainer:range.endContainer.parentElement)?.closest?.('[data-math-source]');
  if(start)expanded.setStartBefore(start);if(end)expanded.setEndAfter(end);
  const fragment = expanded.cloneContents();
  // Select formulas as complete source tokens so evidence still matches the PDF text.
  fragment.querySelectorAll('[data-math-source]').forEach(node => node.replaceWith(document.createTextNode(node.dataset.mathSource)));
  return fragment.textContent.trim();
}
