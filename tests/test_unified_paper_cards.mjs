import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {esc,safeURL,paperRuntime} from './ui-harness.mjs';
const c=paperRuntime();vm.runInContext(fs.readFileSync(new URL('../web/vendor/katex.min.js',import.meta.url),'utf8'),c);
const item={id:'local1',paper_id:'local1',title:'Local custom title',abstract:'Author abstract',published_at:'2026-11-19',institutions:[{name:'Example Lab'}],hf_upvotes:0,github_stars:null,structured:{research_problem:'Problem <script>bad()</script>',methods:['A method $x^2$'],limitations:['Partial observation'],status:'partial',source_version:'v1',evidence_scope:'catalog-material',evidence:['Source <unsafe>']},local_artifacts:{id:'private',structured_status:'complete'}};
for(const options of [{},{concise:true},{concise:false}]){
 const html=c.paperCards([item],{esc,safeURL,...options});
 assert.match(html,/recommendation-compact/);assert.match(html,/Local custom title/);assert.match(html,/Example Lab/);assert.match(html,/HF 点赞 0/);assert.match(html,/GitHub Stars —/);assert.match(html,/<details class="paper-card-structured">/);assert.match(html,/部分内容/);assert.match(html,/catalog-material/);assert.match(html,/&lt;script&gt;/);assert.match(html,/<math/);assert.doesNotMatch(html,/\/corpus|data-card-structured|<script>/);
}
const html=c.paperCards([item],{esc,safeURL,footer:(p,i)=>`<footer data-context="${i}">${esc(p.id)} project controls</footer>`});
assert.match(html,/project controls/);assert.doesNotMatch(html,/data-paper-add|data-paper-graph|data-paper-dismiss/);
assert.match(c.cardStructuredMarkup({id:'a',local_artifacts:{structured_status:'complete'}},esc),/暂无已有/);
assert.equal(c.cardStructuredMarkup({kind:'article',structured:{methods:['A']}},esc),'');
assert.doesNotMatch(c.cardStructuredMarkup({structured:{methods:[{},null,3]}},esc),/已有<|\[object Object\]/);
let called=false;c.mountCardStructured({}, {api(){called=true;}}).dispose();assert.equal(called,false);
console.log('PASS: unified default/legacy cards, context footer, public inline structured content, partial provenance, math escaping and no private requests');
