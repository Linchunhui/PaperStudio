// Dependency-free tests of the actual frontend helper source.
// This validates anchor identity and page bounds, not browser gestures or layout.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const file = fs.readFileSync(new URL('../web/reader.js', import.meta.url), 'utf8');
// app.js mounts the app at import time; isolate the pure helpers without a DOM.
const start = file.indexOf('// A page');
const end = file.indexOf('export async function mountReader');
assert.ok(start >= 0 && end > start, 'reader helper boundary exists');
const code = file.slice(start, end).replaceAll('export ', '');
const context = vm.createContext({});
vm.runInContext(code, context);
const current = context.translationAnchorCurrent;

const original = {side:'original',page:4,source_sha:'original'};
assert.equal(current(original, 'original', {}), true);
assert.equal(current(original, 'replaced', {}), false);

const note = {side:'translation',page:1,source_sha:'page-1-immutable',translation_run_id:'run-A'};
const manifest = {
  mode:'progressive',active_run_id:'run-A',
  pages:[{page:1,status:'ready',source_sha:'page-1-immutable'},{page:2,status:'running'}]
};
assert.equal(current(note, 'original', manifest), true);
manifest.pages[1] = {page:2,status:'ready',source_sha:'page-2-different'};
assert.equal(current(note, 'original', manifest), true, 'later pages cannot invalidate page 1');
assert.equal(current({...note,translation_run_id:'run-old'}, 'original', manifest), false);
assert.equal(current({...note,is_current:false}, 'original', manifest), false);
assert.equal(current(note, 'original', {...manifest,active_run_id:'run-B'}), false, 'same bytes from another run remain a different anchor');
assert.equal(current(note, 'original', {...manifest,pages:[{page:1,status:'failed',source_sha:note.source_sha}]}), false);

const manual = {mode:'manual',active_run_id:null,pages:[{page:1,status:'ready',source_sha:'whole-pdf',translation_run_id:null}]};
assert.equal(current({side:'translation',page:1,source_sha:'whole-pdf'}, 'original', manual), true);
assert.equal(current({...note,source_sha:'whole-pdf'}, 'original', manual), false, 'manual replacement cannot accept progressive run anchors');

assert.equal(context.clampPage(-1, 100), 1);
assert.equal(context.clampPage(500, 100), 100);
assert.equal(context.clampPage('4.8', 100), 4);
assert.equal(context.clampPage('bad', 100), 1);
// BFCache pagehide/pageshow must resume the same reader, never reinitialize it.
const lifecycleEvents = [];
const lifecycle = context.createReaderLifecycle(
  () => lifecycleEvents.push('pause'),
  () => lifecycleEvents.push('resume')
);
lifecycle.show({persisted:false});
lifecycle.show({persisted:true});
assert.deepEqual(lifecycleEvents, [], 'initial pageshow cannot start duplicate observers');
lifecycle.hide({persisted:true});
assert.deepEqual(lifecycleEvents, ['pause']);
lifecycle.show({persisted:false});
assert.deepEqual(lifecycleEvents, ['pause']);
lifecycle.show({persisted:true});
assert.deepEqual(lifecycleEvents, ['pause','resume'], 'back navigation resumes a cached reader');
lifecycle.show({persisted:true});
assert.deepEqual(lifecycleEvents, ['pause','resume'], 'resume only once per cache cycle');
lifecycle.hide({persisted:true});
lifecycle.show({persisted:true});
assert.deepEqual(lifecycleEvents, ['pause','resume','pause','resume'], 'a second back navigation also resumes');
lifecycle.hide({persisted:false});
assert.deepEqual(lifecycleEvents, ['pause','resume','pause','resume','pause']);
lifecycle.show({persisted:true});
assert.deepEqual(lifecycleEvents, ['pause','resume','pause','resume','pause'], 'a destroyed document cannot resume');
console.log('22 reader anchor/position/lifecycle assertions passed');
