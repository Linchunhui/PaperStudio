export function mountPortability(root,{api,esc,safeURL,projects,projectId,onImport}){
  let disposed=false,preview=[],selected=projectId||projects.find(p=>p.can_edit!==false)?.id||'',busy=false;
  const options=projects.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)}${p.can_edit===false?'（只读）':''}</option>`).join('');
  root.innerHTML=`<section class="portability-panel"><h2>文献迁入与成果导出</h2><p>从 BibTeX、RIS 或 Zotero CSV 导入文献清单，再按需下载论文。导入清单不调用模型。</p><label>目标项目 <select id="portable-project">${options}</select></label><div class="export-options"><label><input type="checkbox" id="export-children">包含有权访问的子项目</label><label><input type="checkbox" id="export-generated">包含 AI 研究成果</label><label><input type="checkbox" id="export-shared">包含其他成员已共享的笔记与成果</label></div><p class="small muted">默认仅导出当前项目论文清单及自己的笔记，不包含 PDF 或密钥；分享前请检查内容。</p><div class="actions"><button class="button" data-portable-export="zip">下载成果包</button><button class="button" data-portable-export="html">导出阅读 HTML</button><button class="button" data-portable-export="bib">导出 BibTeX</button></div><hr><label>选择文献列表 <input id="bibliography-file" type="file" accept=".bib,.bibtex,.ris,.csv"></label><p class="small muted">UTF-8，最大 2 MiB / 500 条。Zotero 可选择“导出文献库 → BibTeX / RIS / CSV”。本地附件路径不会上传关联文件。</p><div id="bibliography-preview"></div><p role="status"></p><details open><summary>项目文献清单（不代表 PDF 已入库）</summary><div id="bibliography-saved"></div></details></section>`;
  const feedback=message=>{if(!disposed)root.querySelector('[role="status"]').textContent=message;};
  async function load(){
    const id=selected;
    try{const data=await api(`/projects/${encodeURIComponent(id)}/bibliography`);if(disposed||id!==selected)return;
      root.querySelector('#bibliography-saved').innerHTML=data.entries.map(row=>`<article class="bibliography-row"><h3>${esc(row.title)}</h3><p class="small">${esc(row.authors.join(', '))}${row.year?' · '+esc(row.year):''}</p>${safeURL(row.url)?`<a class="text-link" href="${esc(safeURL(row.url))}" target="_blank" rel="noopener noreferrer">论文来源 ↗</a> `:''}<button class="button small" data-bibliography-import="${esc(row.id)}">选择 PDF 链接并入库</button></article>`).join('')||'<p class="muted">还没有迁入文献，可先导入列表。</p>';
      saved=data.entries;
    }catch(error){feedback(error.message);}
  }
  let saved=[];
  async function change(event){
    if(event.target.id==='portable-project'){
      selected=event.target.value;preview=[];root.querySelector('#bibliography-preview').innerHTML='';load();return;
    }
    if(event.target.id!=='bibliography-file'||busy)return;
    const file=event.target.files?.[0];if(!file)return;
    if(file.size>2*1024*1024){feedback('列表超过 2 MiB，请拆分后导入。');return;}
    busy=true;feedback('正在解析文献列表…');const body=new FormData();body.set('file',file);
    try{const data=await api('/bibliography/preview',{method:'POST',body});if(disposed)return;preview=data.entries;
      root.querySelector('#bibliography-preview').innerHTML=`<h3>确认导入 ${preview.length} 条文献</h3><div class="portable-items">${preview.map((row,index)=>`<label><input type="checkbox" data-reference-index="${index}" checked><span>${esc(row.title)}<small> ${esc(row.year)}</small></span></label>`).join('')}</div><button class="button primary" data-bibliography-save>保存选中文献到项目</button>`;
      feedback([data.notice,...data.warnings].join(' '));
    }catch(error){feedback(error.message);}finally{busy=false;}
  }
  async function click(event){
    const exportButton=event.target.closest?.('[data-portable-export]');
    if(exportButton){const params=new URLSearchParams({format:exportButton.dataset.portableExport});for(const [name,id] of [['include_children','export-children'],['include_generated','export-generated'],['include_shared','export-shared']])params.set(name,String(root.querySelector('#'+id).checked));window.location.assign(`/api/projects/${encodeURIComponent(selected)}/export?${params}`);return;}
    const add=event.target.closest?.('[data-bibliography-import]');if(add){const row=saved.find(r=>r.id===add.dataset.bibliographyImport);if(row)onImport({...row,projectId:selected});return;}
    const save=event.target.closest?.('[data-bibliography-save]');if(!save||busy)return;
    const entries=[...root.querySelectorAll('[data-reference-index]:checked')].map(input=>preview[Number(input.dataset.referenceIndex)]);
    if(!entries.length){feedback('请至少选择一条文献。');return;}
    busy=true;save.disabled=true;
    try{const result=await api(`/projects/${encodeURIComponent(selected)}/bibliography`,{method:'POST',body:{entries}});feedback(`已保存 ${result.inserted} 条，跳过 ${result.duplicates} 条重复文献；尚未下载 PDF。`);await load();}
    catch(error){feedback(error.message);}finally{busy=false;if(save.isConnected)save.disabled=false;}
  }
  root.addEventListener('change',change);root.addEventListener('click',click);load();
  return {dispose(){disposed=true;root.removeEventListener('change',change);root.removeEventListener('click',click);}};
}
