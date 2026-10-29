let page=1,translation=true,tourTimer=null,tourStep=0;
const status=document.querySelector('#tour-status');
function scene(name){
  for(const tab of document.querySelectorAll('[data-scene]')){tab.setAttribute('aria-selected',String(tab.dataset.scene===name));tab.tabIndex=tab.dataset.scene===name?0:-1;}
  for(const panel of document.querySelectorAll('[role=tabpanel]'))panel.hidden=panel.id!=='scene-'+name;
}
function notes(name){
  document.querySelector('#brief-content').hidden=name!=='brief';
  document.querySelector('#notes-content').hidden=name!=='notes';
  for(const tab of document.querySelectorAll('[data-note-tab]'))tab.setAttribute('aria-pressed',String(tab.dataset.noteTab===name));
}
for(const tab of document.querySelectorAll('[data-scene]'))tab.addEventListener('click',()=>scene(tab.dataset.scene));
for(const tab of document.querySelectorAll('[data-note-tab]'))tab.addEventListener('click',()=>notes(tab.dataset.noteTab));
document.querySelector('#translation-toggle').addEventListener('click',event=>{
  translation=!translation;document.querySelector('#translation-pane').hidden=!translation;
  document.querySelector('.reader-grid').classList.toggle('translation-hidden',!translation);
  event.currentTarget.setAttribute('aria-pressed',String(translation));event.currentTarget.textContent='对照译文：'+(translation?'开':'关');
});
document.querySelector('#page-toggle').addEventListener('click',event=>{
  page=page===1?2:1;document.querySelector('#original-page').src=`assets/original-${page}.svg`;
  document.querySelector('#translated-page').src=`assets/translation-${page}.svg`;
  document.querySelector('#original-page').alt=`自编阅读示例的英文原文，第 ${page} 页`;
  document.querySelector('#translated-page').alt=`预制的对应中文译文，第 ${page} 页`;
  event.currentTarget.textContent=`查看第 ${page===1?2:1} 页`;
});
const steps=[()=>{scene('reading');notes('brief');return '1 / 4 · 在同一个阅读区核对原文与对应译文。';},()=>{notes('notes');return '2 / 4 · 原文摘录与自己的理解分开保留。';},()=>{scene('projects');return '3 / 4 · 按研究问题整理项目，导出可复用的成果。';},()=>{scene('setup');return '4 / 4 · 自部署后连接模型，按需启动 AI 任务。';}];
function stop(){clearTimeout(tourTimer);tourTimer=null;document.querySelector('#tour').textContent='播放操作导览';}
function advance(){status.textContent=steps[tourStep++]();if(tourStep<steps.length)tourTimer=setTimeout(advance,15000);else tourTimer=setTimeout(stop,15000);}
document.querySelector('#tour').addEventListener('click',()=>{if(tourTimer){stop();return;}tourStep=0;document.querySelector('#tour').textContent='停止导览';document.querySelector('#workspace').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});advance();});
addEventListener('pagehide',stop);

document.querySelector('[role=tablist]').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const tabs=[...document.querySelectorAll('[data-scene]')];const index=tabs.indexOf(event.target);if(index<0)return;event.preventDefault();const target=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;scene(tabs[target].dataset.scene);tabs[target].focus();});
