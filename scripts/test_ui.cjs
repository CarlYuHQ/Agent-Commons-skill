#!/usr/bin/env node
'use strict';
// Offline, dependency-free DOM contract tests for assets/index.html.
// Fixtures live only inside this process; nothing is written to a commons store.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const htmlPath = path.join(__dirname, '../assets/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
assert.equal(scripts.length, 1, 'page contains exactly one inline script');
const source = scripts[0][1];
new vm.Script(source, { filename: htmlPath });

// Static guarantees: no HTML injection, no external resources, restrained visual language.
assert(!/\b(?:innerHTML|outerHTML|insertAdjacentHTML|eval)\b|new\s+Function\s*\(/.test(source), 'no HTML injection or dynamic code execution');
assert(!/<(?:script|link|iframe|img)\b[^>]*(?:src|href)=['"]https?:/i.test(html), 'no external resources');
assert(!/gradient\(|box-shadow\s*:/i.test(html), 'no gradients or shadows');
for (const radius of html.matchAll(/border-radius\s*:\s*(\d+)px/g)) assert(Number(radius[1]) <= 4, 'border radius stays <= 4px');
for (const token of ['#0F0E0E', '#F2F2F2', '#A8A8A8', '#E8C268', '#7DD9A6', '#E07866']) assert(html.includes(token), 'palette token ' + token);
assert(/<html lang="en">/.test(html), 'document language is English');
assert(!/[\u3400-\u9fff]/.test(html), 'page contains no CJK text');
assert(html.includes('grid-template-columns:200px minmax(0,1fr) 280px'), 'three-column desktop layout');
for (const bp of ['@media(max-width:1100px)', '@media(max-width:760px)', '@media(max-width:700px)']) assert(html.includes(bp), 'breakpoint ' + bp);
assert(html.includes('max-width:calc(100vw - 16px)'), 'mobile dialogs fit the viewport');
assert.equal((source.match(/localStorage\.setItem\(/g) || []).length, 1, 'only the current identity is persisted');

class TextNode {
  constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
}
class MiniElement {
  constructor(tag, owner) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = owner; this.nodeType = 1;
    this.childNodes = []; this.parentNode = null; this.attrs = {}; this.dataset = {};
    this.listeners = {}; this.style = {}; this.disabled = false; this.required = false;
    this.hidden = false; this.open = false; this.scrollTop = 0; this.validityMessage = '';
    this.className = ''; this.name = ''; this.id = ''; this.type = ''; this._value = undefined;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(n => !names.includes(n)).join(' '); },
      toggle: (name, force) => {
        const add = force === undefined ? !this.classList.contains(name) : Boolean(force);
        if (add) this.classList.add(name); else this.classList.remove(name);
        return add;
      }
    };
  }
  get children() { return this.childNodes.filter(c => c.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get parentElement() { return this.parentNode?.nodeType === 1 ? this.parentNode : null; }
  get isConnected() { let p = this; while (p.parentNode) p = p.parentNode; return p === this.ownerDocument.root; }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
  set textContent(value) { this.replaceChildren(new TextNode(value)); }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === 'SELECT') return this.children[0]?.value || '';
    if (this.tagName === 'OPTION') return this.textContent;
    return '';
  }
  set value(value) { this._value = String(value); }
  get elements() { return { namedItem: name => this.querySelectorAll('input,select,textarea').find(n => n.name === name) || null }; }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'id') this.id = String(value);
    else if (name === 'class') this.className = String(value);
    else if (name === 'name') this.name = String(value);
    else if (name === 'value') this.value = value;
    else if (name === 'disabled') this.disabled = true;
    else if (name === 'required') this.required = true;
    else if (name === 'hidden') this.hidden = true;
    else if (name === 'type') this.type = String(value);
    else if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) {
    if (name === 'id') return this.id || null;
    if (name === 'class') return this.className || null;
    if (name.startsWith('data-')) return this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] ?? null;
    return this.attrs[name] ?? null;
  }
  removeAttribute(name) { delete this.attrs[name]; }
  append(...children) {
    for (let child of children) {
      if (typeof child !== 'object') child = new TextNode(child);
      if (child.parentNode) child.parentNode.childNodes = child.parentNode.childNodes.filter(c => c !== child);
      child.parentNode = this; this.childNodes.push(child);
    }
  }
  replaceChildren(...children) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = []; this.append(...children);
    if (this.tagName === 'SELECT') this._value = undefined;
  }
  remove() {
    if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(c => c !== this);
    this.parentNode = null;
  }
  matches(selector) {
    selector = selector.trim();
    const tag = selector.match(/^[a-z][\w-]*/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const id = selector.match(/#([\w-]+)/)?.[1];
    if (id && this.id !== id) return false;
    for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(name)) return false;
    for (const [, name, value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
      if (value === undefined ? this.getAttribute(name) === null : this.getAttribute(name) !== value) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const parts = selector.split(','); const result = [];
    const visit = node => { for (const c of node.children) { if (parts.some(s => c.matches(s))) result.push(c); visit(c); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let p = this; p; p = p.parentElement) if (p.matches(selector)) return p; return null; }
  addEventListener(name, fn, options = {}) { (this.listeners[name] ||= []).push({fn, once: options.once}); }
  async emit(name, props = {}) {
    const event = { target: this, preventDefault() { this.defaultPrevented = true; }, ...props };
    const listeners = [...(this.listeners[name] || [])];
    for (const entry of listeners) {
      if (entry.once) this.listeners[name] = this.listeners[name].filter(x => x !== entry);
      await entry.fn(event);
    }
    if (this.parentElement && ['input','change','click'].includes(name)) await this.parentElement.emit(name, event);
    return event;
  }
  click() { if (!this.disabled) return this.emit('click'); }
  focus() { if (!this.disabled) this.ownerDocument.activeElement = this; }
  showModal() { this.open = true; this.querySelector('button,input,select,textarea')?.focus(); }
  close() { this.open = false; this.emit('close'); }
  scrollIntoView() { this.scrolled = true; }
  getBoundingClientRect() { return {left: 0, top: 0, right: 760, bottom: 700}; }
  setCustomValidity(message) { this.validityMessage = message; }
  reportValidity() {
    if (this.tagName === 'FORM') return this.querySelectorAll('input,select,textarea').every(c => c.reportValidity());
    return this.disabled || (!this.validityMessage && (!this.required || Boolean(this.value)));
  }
}
class MiniDocument {
  constructor(markup) {
    this.root = new MiniElement('root', this); this.hidden = false; this.listeners = {};
    const content = markup.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '').replace(/<!--[\s\S]*?-->/g, '');
    const stack = [this.root];
    const voids = new Set(['META', 'INPUT', 'BR', 'LINK', 'HR']);
    for (const token of content.matchAll(/<[^>]+>|[^<]+/g)) {
      const text = token[0];
      if (text.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (text.startsWith('<!')) continue;
      if (text.startsWith('<')) {
        const name = text.match(/^<([\w-]+)/)?.[1]; if (!name) continue;
        const node = new MiniElement(name, this);
        const attributes = text.slice(name.length + 1, -1);
        for (const [, key, quoted, single, unquoted] of attributes.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) node.setAttribute(key, quoted ?? single ?? unquoted ?? '');
        stack[stack.length - 1].append(node);
        if (!voids.has(node.tagName) && !text.endsWith('/>')) stack.push(node);
      } else stack[stack.length - 1].append(new TextNode(text));
    }
    this.body = this.root.querySelector('body'); this.activeElement = this.body;
  }
  createElement(tag) { return new MiniElement(tag, this); }
  createElementNS(ns, tag) { return this.createElement(tag); }
  getElementById(id) { return this.root.querySelectorAll('[id]').find(n => n.id === id) || null; }
  querySelectorAll(selector) { return this.root.querySelectorAll(selector); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
}
class MiniFormData {
  constructor(form) { this.data = form.querySelectorAll('input,select,textarea').filter(c => c.name && !c.disabled).map(c => [c.name, c.value]); }
  entries() { return this.data.values(); }
}
const fixture = () => ({
  id: '11111111-1111-4111-8111-111111111111', title: 'Test-only record, never written to a store', summary: 'Test summary', category: 'coding',
  tags: ['test'], author: {kind:'human', label:'local-user', model:'unknown', framework:'unknown'},
  scope: {project:'test-project', applicable:'test condition', avoid:'test boundary'}, status:'observed', evidenceType:'hypothesis', source:'in-process test source',
  body: '# Safe heading\n\n<img src=x onerror=alert(1)>\n\n```js\nalert("text only")\n```\n\n- item',
  createdAt:'2026-10-18T10:00:00Z', updatedAt:'2026-10-18T10:00:00Z', revision:1,
  likeCount:0, adoptionCount:0, helpedCount:0, failedCount:0, likes:[], comments:[], adoptions:[],
  revisionHistory: [{revision:1, origin:'created', createdAt:'2026-10-18T10:00:00Z', bodyHash:'test-hash'}]
});
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
const actorKeyOf = a => JSON.stringify([a.model, a.framework, a.label]);

// Builds an isolated page instance with a fake fetch that mimics the local API contract.
function environment({protocol = 'http:', posts = [], fail = false, storageValue, rejectPatch = false} = {}) {
  const document = new MiniDocument(html); const timers = new Map(); let nextTimer = 0;
  const requests = []; const savedIdentity = []; const store = {posts: structuredClone(posts), fail, rejectPatch, gate: null};
  const context = {
    document, location: {protocol}, URLSearchParams, AbortController, Blob, console,
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}}, FormData: MiniFormData,
    localStorage: {getItem: () => storageValue || null, setItem: (key, value) => savedIdentity.push({key, value})},
    setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; }, clearTimeout: id => timers.delete(id),
    confirm: () => false,
    fetch: async (url, options = {}) => {
      requests.push({url, ...options, data: options.body ? JSON.parse(options.body) : null});
      if (store.gate) await store.gate;
      if (store.fail) throw new Error('test network down');
      const method = options.method || 'GET'; let result; let status = 200;
      if (url === '/api/config') result = {csrfToken: 'test-only-token', root: '/test-disk', categories: ['scope','communication','tools','verification','coding','data','design','research','workflow']};
      else if (url.startsWith('/api/posts?') || url === '/api/posts') {
        if (method === 'GET') {
          const params = new URLSearchParams(url.split('?')[1] || '');
          result = {posts: store.posts.filter(p => (!params.get('q') || (p.title + p.summary + p.body).includes(params.get('q')))
            && (!params.get('category') || p.category === params.get('category'))
            && (!params.get('status') || p.status === params.get('status'))).map(p => ({...structuredClone(p), body: ''}))};
        } else { const p = {...fixture(), ...JSON.parse(options.body), id: '22222222-2222-4222-8222-222222222222'}; store.posts.push(p); result = {post: p}; status = 201; }
      } else if (url === '/api/export') result = {schemaVersion: 1, posts: structuredClone(store.posts)};
      else {
        const parts = url.split('/'); const p = store.posts.find(p => p.id === parts[3]); const data = options.body ? JSON.parse(options.body) : {};
        if (!p) { status = 404; result = {error: {message: 'test record not found'}}; }
        else if (method === 'GET') result = {post: structuredClone(p)};
        else if (method === 'PATCH' && store.rejectPatch) { status = 409; result = {error: {message: 'revision changed'}}; }
        else if (method === 'PATCH') { Object.assign(p, data); delete p.expectedRevision; p.revision++; result = {post: structuredClone(p)}; }
        else if (parts[4] === 'like') {
          p.likes = p.likes.filter(l => actorKeyOf(l.actor) !== actorKeyOf(data.actor));
          if (data.liked) p.likes.push({actor: data.actor}); p.likeCount = p.likes.length; result = {post: structuredClone(p)};
        } else if (parts[4] === 'comments') { p.comments.push({...data, id: 'test-comment', createdAt: p.updatedAt}); result = {post: structuredClone(p)}; }
        else if (parts[4] === 'adoptions') {
          p.adoptions = p.adoptions.filter(a => !(a.taskId === data.taskId && actorKeyOf(a.actor) === actorKeyOf(data.actor)));
          p.adoptions.push({...data, updatedAt: p.updatedAt}); p.adoptionCount = p.adoptions.filter(a => a.status !== 'planned').length;
          p.helpedCount = p.adoptions.filter(a => a.status === 'helped').length; p.failedCount = p.adoptions.filter(a => a.status === 'failed').length;
          result = {post: structuredClone(p)};
        }
      }
      return {ok: status >= 200 && status < 300, status, json: async () => result};
    }
  };
  context.window = {addEventListener() {}};
  vm.createContext(context);
  // Expose internals only inside the test process; the shipped page has no debug hooks.
  const instrumented = source.replace(/\}\)\(\);\s*$/, 'globalThis.ui={state,renderMarkdown,actorKey,normalizeIdentity,likedBy,logo,loadPosts,connect,offline,openEditor,openDetail,openIdentity,openComment,openAdoption,toggleLike,closeDialog,sortedPosts,displayName};})();');
  assert.notEqual(instrumented, source, 'instrumentation hook applied');
  vm.runInContext(instrumented, context, {filename: htmlPath});
  return {context, document, ui: context.ui, requests, timers, savedIdentity, store, $: id => document.getElementById(id)};
}
async function input(form, name, value) { const n = form.elements.namedItem(name); assert(n, 'field ' + name + ' exists'); n.value = value; await n.emit('input'); await n.emit('change'); return n; }
function dataRequests(env, method) { return env.requests.filter(r => ['POST','PUT','PATCH'].includes(r.method) && (!method || r.method === method)); }
const firstStat = env => env.$('stats').querySelector('dd').textContent;
const polling = env => [...env.timers.values()].some(t => t.delay === 15000);

async function run() {
  let checks = 0;
  const ok = message => { checks++; console.log('ok · ' + message); };

  const file = environment({protocol: 'file:'}); await flush();
  for (const dialog of file.document.querySelectorAll('dialog')) assert(file.$(dialog.getAttribute('aria-labelledby')), 'every dialog has an accessible title');
  assert(file.$('toast').getAttribute('aria-live'));
  assert.equal(file.requests.length, 0); assert(!file.ui.state.connected); assert(file.$('newPostBtn').disabled);
  assert(file.$('connectionMessage').textContent.includes('Start scripts/commons.py serve')); assert(!polling(file));
  ok('file:// makes no API calls, stores nothing, and does not poll');

  const e = environment(); await flush();
  assert(e.ui.state.connected); assert(e.$('feed').textContent.includes('Record the first lesson')); assert.equal(firstStat(e), '0');
  ok('empty library shows real zero stats and a create entry point');

  e.ui.openIdentity(); let f = e.$('identityBody').querySelector('form');
  await input(f, 'kind', 'ai'); await input(f, 'label', 'actual-operator'); await input(f, 'model', ''); await input(f, 'framework', ''); await f.emit('submit');
  assert.equal(e.ui.state.identity.model, 'unknown'); assert.equal(e.ui.state.identity.framework, 'unknown');
  assert.equal(e.savedIdentity.length, 1); assert.equal(e.savedIdentity[0].key, 'agent-commons.current-identity');
  ok('self-reported identity; empty AI model/framework normalize to unknown; only identity hits localStorage');

  assert.equal(e.ui.displayName({kind: 'ai', label: '', model: 'model-x', framework: 'fw'}), 'model-x');
  assert.equal(e.ui.displayName({kind: 'ai', label: 'reviewer', model: 'model-x', framework: 'fw'}), 'reviewer');
  ok('AI authors without a label fall back to the model name');

  const malicious = e.ui.renderMarkdown('# Safe\n\n<script>alert(1)</script>\n<img onerror=alert(1)>\n\n```\nconst x = 1;\n```\n\n- item\n\n1. order\n\n> quoted\n\nUse `npm test` and **never** skip.');
  assert.equal(malicious.querySelectorAll('script,img').length, 0); assert.equal(malicious.querySelectorAll('h2').length, 1);
  assert.equal(malicious.querySelectorAll('pre').length, 1); assert.equal(malicious.querySelectorAll('ul').length, 1); assert.equal(malicious.querySelectorAll('ol').length, 1);
  assert.equal(malicious.querySelectorAll('blockquote').length, 1); assert.equal(malicious.querySelectorAll('strong').length, 1);
  assert(malicious.textContent.includes('<script>')); assert.equal(malicious.querySelector('pre').querySelector('code').textContent, 'const x = 1;');
  assert(malicious.querySelectorAll('code').some(c => c.textContent === 'npm test'));
  for (const s of ['', '```', '- a\n1. b', '\n## h\nparagraph', '<svg onload=alert(1)>', 'https://example.invalid', '`unclosed', '**unclosed', '>']) e.ui.renderMarkdown(s);
  ok('safe Markdown builds only allowed DOM (incl. inline code, bold, quotes); HTML and code never execute');

  assert.equal(e.ui.actorKey({kind: 'ai', model: 'm', framework: 'f', label: 'l'}), e.ui.actorKey({kind: 'human', model: 'm', framework: 'f', label: 'l'}));
  assert.equal(e.ui.logo({model: 'm', framework: 'f', label: 'l'}).querySelector('path').getAttribute('stroke'), e.ui.logo({model: 'm', framework: 'f', label: 'l'}).querySelector('path').getAttribute('stroke'));
  ok('actor key matches the backend; author glyph is stable');

  await e.ui.openEditor(); f = e.$('editorBody').querySelector('form');
  assert.equal(f.elements.namedItem('status').value, 'observed'); assert.equal(f.elements.namedItem('evidenceType').value, 'hypothesis');
  await input(f, 'title', 'New test lesson'); await input(f, 'body', '# Lesson\nBody'); await input(f, 'summary', 'Full summary'); await input(f, 'category', 'tools');
  // The full-width comma must split tags too.
  await input(f, 'tags', 'boundary, evidence\uFF0Cboundary'); await input(f, 'project', 'project'); await input(f, 'applicable', 'applies'); await input(f, 'avoid', 'avoid'); await input(f, 'source', 'real source');
  await f.emit('submit'); await flush();
  const created = dataRequests(e, 'POST')[0]; assert.equal(created.url, '/api/posts'); assert.deepEqual(created.data.tags, ['boundary', 'evidence']);
  assert.deepEqual(created.data.scope, {project: 'project', applicable: 'applies', avoid: 'avoid'}); assert.equal(created.data.author.label, 'actual-operator');
  assert.equal(created.headers['X-Commons-Token'], 'test-only-token'); assert(!e.$('editorDialog').open);
  ok('create form posts to the API with observed/hypothesis defaults, current identity, and CSRF token');

  const p = fixture(); const active = environment({posts: [p], rejectPatch: true}); await flush();
  assert.equal(firstStat(active), '1'); assert(active.$('feed').textContent.includes(p.title));
  await active.ui.openDetail(p.id);
  assert(active.$('detailBody').textContent.includes('<img src=x')); assert.equal(active.$('detailBody').querySelectorAll('img').length, 0);
  assert(active.$('detailBody').textContent.includes('test-hash')); assert(active.$('detailBody').textContent.includes('test boundary'));
  assert(active.requests.some(r => r.url === '/api/posts/' + p.id));
  ok('detail always GETs the full post and shows scope, source, and the real revision index');

  await active.ui.openEditor(p.id); f = active.$('editorBody').querySelector('form');
  const bodyText = 'My unsaved version\n<script>never runs</script>'; await input(f, 'body', bodyText); await input(f, 'title', 'My edited title'); await f.emit('submit');
  assert.equal(dataRequests(active, 'PATCH')[0].data.expectedRevision, 1); assert.equal(f.elements.namedItem('body').value, bodyText);
  assert(active.$('editorBody').textContent.includes('Revision conflict')); assert(!active.$('editorDialog').querySelector('.conflict').hidden);
  assert(!active.ui.closeDialog(active.$('editorDialog'))); assert(active.$('editorDialog').open);
  const comparison = active.$('editorDialog').querySelector('.conflict').querySelectorAll('button')[0]; await comparison.click(); assert.equal(f.elements.namedItem('body').value, bodyText);
  const reload = active.$('editorDialog').querySelector('.conflict').querySelectorAll('button')[1]; await reload.click(); assert.equal(f.elements.namedItem('body').value, bodyText);
  ok('PATCH sends expectedRevision; 409 keeps all input; viewing never overwrites; reload and close require confirmation');

  active.context.confirm = () => true; active.store.posts[0].revision = 2; active.store.posts[0].body = 'Latest body on disk'; await reload.click();
  f = active.$('editorBody').querySelector('form'); assert.equal(f.elements.namedItem('body').value, 'Latest body on disk'); assert.equal(active.$('editorDialog')._dirty, false);
  active.store.rejectPatch = false; await input(f, 'title', 'Revision after confirmed reload'); await f.emit('submit'); await flush();
  assert.equal(dataRequests(active, 'PATCH').at(-1).data.expectedRevision, 2); assert.equal(active.store.posts[0].revision, 3); active.context.confirm = () => false;
  ok('latest body loads only after confirmation; the next save uses the new expectedRevision');

  const detailBefore = active.$('detailBody').textContent; const d = active.$('detailDialog'); d.scrollTop = 77; active.$('searchInput').value = 'unsubmitted query';
  await active.ui.loadPosts(true);
  assert.equal(active.$('detailBody').textContent, detailBefore); assert.equal(d.scrollTop, 77); assert.equal(active.$('searchInput').value, 'unsubmitted query');
  ok('background refresh never resets dialogs, scroll position, or search input');

  active.ui.closeDialog(d, true); const card = active.$('feed').querySelector('article'); const like = card.querySelector('[data-action="like"]');
  await active.ui.toggleLike(p.id, like); await flush(); assert.equal(active.store.posts[0].likeCount, 1); assert.equal(active.store.posts[0].status, 'observed');
  let current = active.$('feed').querySelector('[data-action="like"]'); assert.equal(current.getAttribute('aria-pressed'), 'true');
  await active.ui.toggleLike(p.id, current); await flush(); assert.equal(active.store.posts[0].likeCount, 0);
  ok('like PUTs true/false keyed by actor and never upgrades trust');

  active.ui.openAdoption(p.id); f = active.$('adoptionBody').querySelector('form');
  await input(f, 'taskId', 'task-real'); await input(f, 'reason', 'conditions match'); await f.emit('submit'); await flush();
  assert.equal(active.store.posts[0].adoptionCount, 0); assert.equal(active.store.posts[0].adoptions[0].status, 'planned');
  active.ui.openAdoption(p.id); f = active.$('adoptionBody').querySelector('form');
  await input(f, 'taskId', 'task-real'); await input(f, 'reason', 'tested'); await input(f, 'status', 'helped'); assert(f.elements.namedItem('outcome').required);
  let before = dataRequests(active, 'PUT').length; await f.emit('submit'); assert.equal(dataRequests(active, 'PUT').length, before);
  await input(f, 'outcome', 'actual test matched expectations'); await f.emit('submit'); await flush();
  assert.equal(active.store.posts[0].adoptionCount, 1); assert.equal(active.store.posts[0].helpedCount, 1); assert.equal(active.store.posts[0].status, 'observed');
  active.ui.openAdoption(p.id); f = active.$('adoptionBody').querySelector('form'); await input(f, 'status', 'failed'); assert(f.elements.namedItem('outcome').required);
  active.ui.closeDialog(active.$('adoptionDialog'), true);
  ok('adoption defaults to planned (not counted); helped/failed require an outcome; task and reason are mandatory');

  active.ui.openComment(p.id); f = active.$('commentBody').querySelector('form'); before = dataRequests(active, 'POST').length;
  await input(f, 'text', '   '); await f.emit('submit'); assert.equal(dataRequests(active, 'POST').length, before);
  await input(f, 'text', '<img onerror=alert(1)> extra evidence'); await input(f, 'section', 'free-form section'); await f.emit('submit'); await flush();
  assert.equal(active.store.posts[0].comments[0].section, 'free-form section'); await active.ui.openDetail(p.id);
  assert(active.$('detailBody').textContent.includes('free-form section')); assert.equal(active.$('detailBody').querySelectorAll('img').length, 0);
  active.ui.closeDialog(active.$('detailDialog'), true);
  ok('comments reject blanks, accept free-form sections, and render untrusted text safely');

  const focusTarget = active.$('feed').querySelector('[data-action="detail"]'); focusTarget.focus(); await focusTarget.click(); await active.ui.loadPosts(true);
  assert(!focusTarget.isConnected); const cancel = await active.$('detailDialog').emit('cancel'); assert(cancel.defaultPrevented); assert(!active.$('detailDialog').open);
  assert.equal(active.document.activeElement.dataset.action, 'detail');
  ok('ESC closes the detail and restores focus to the matching card after a refresh');

  let expand = active.$('feed').querySelector('[data-action="expand"]'); await expand.click();
  assert(active.ui.state.expanded.has(p.id)); assert(active.$('feed').querySelector('.expanded').textContent.includes('Latest body on disk'));
  active.store.posts[0].revision++; active.store.posts[0].body = 'Another revision on disk';
  const reads = active.requests.filter(r => r.url === '/api/posts/' + p.id).length; await active.ui.loadPosts(true);
  assert(active.$('feed').querySelector('.expanded').textContent.includes('revision changed')); assert.equal(active.requests.filter(r => r.url === '/api/posts/' + p.id).length, reads);
  expand = active.$('feed').querySelector('[data-action="expand"]'); await expand.click(); assert(active.$('feed').querySelector('.expanded').textContent.includes('Another revision on disk'));
  ok('expand always reads the full body; a newer revision drops the stale cache without extra reads');

  active.ui.state.q = 'nonexistent-term'; await active.ui.loadPosts();
  assert(active.$('feed').textContent.includes('No matching lessons')); assert.equal(firstStat(active), '1');
  ok('empty search results stay separate from real library-wide stats');

  active.ui.state.q = ''; await active.ui.loadPosts(); active.store.fail = true; await active.ui.loadPosts(); await flush();
  assert(!active.ui.state.connected); assert(active.$('newPostBtn').disabled); assert(active.$('feed').textContent.includes(active.store.posts[0].title));
  assert(!polling(active)); assert.equal(active.$('feed').getAttribute('aria-busy'), 'false'); assert(active.$('connText').textContent.includes('Offline'));
  active.store.fail = false; await active.ui.connect(); assert(active.ui.state.connected); assert(!active.$('newPostBtn').disabled);
  ok('network loss keeps layout and records, disables writes, stops retrying; manual reconnect recovers');

  const sort = environment({posts: [fixture(), {...fixture(), id: '33333333-3333-4333-8333-333333333333', status: 'validated', title: 'Validated case', adoptionCount: 2, updatedAt: '2026-10-17T10:00:00Z'}]}); await flush();
  sort.ui.state.sort = 'verified'; assert.equal(sort.ui.sortedPosts()[0].status, 'validated');
  sort.ui.state.sort = 'adopted'; assert.equal(sort.ui.sortedPosts()[0].adoptionCount, 2);
  sort.ui.state.sort = 'latest'; assert.equal(sort.ui.sortedPosts()[0].status, 'observed');
  ok('latest, verified-first, and most-adopted sorting');

  const scoped = environment({posts: [
    fixture(),
    {...fixture(), id: '44444444-4444-4444-8444-444444444444', scope: {project: '', applicable: 'only for small edits', avoid: ''}},
    {...fixture(), id: '55555555-5555-4555-8555-555555555555', scope: {project: '', applicable: '', avoid: ''}}
  ]}); await flush();
  const taskRows = id => scoped.$('feed').querySelectorAll('article').find(a => a.dataset.postId === id).querySelectorAll('.post-task');
  assert.equal(taskRows(fixture().id)[0].querySelector('.task-label').textContent, 'TASK');
  assert.equal(taskRows('44444444-4444-4444-8444-444444444444')[0].querySelector('.task-label').textContent, 'APPLIES');
  assert.equal(taskRows('55555555-5555-4555-8555-555555555555').length, 0);
  assert(!scoped.$('feed').textContent.includes('null'));
  ok('card context row shows the project, falls back to the applicable condition, and is omitted when both are empty');

  await sort.$('exportBtn').click(); assert(sort.requests.some(r => r.url === '/api/export')); assert.equal(sort.savedIdentity.length, 0);
  ok('full JSON export reads from the server and downloads via Blob without caching posts');

  const raced = environment({posts: [fixture()]}); await flush(); raced.ui.openComment(p.id); f = raced.$('commentBody').querySelector('form');
  await input(f, 'text', 'double-submit test'); let release; raced.store.gate = new Promise(r => { release = r; });
  const pending = f.emit('submit'); await flush();
  assert(raced.$('commentDialog')._busy); assert(f.querySelector('[data-write]').disabled); assert(!raced.ui.closeDialog(raced.$('commentDialog')));
  const double = f.emit('submit'); await flush(); assert.equal(dataRequests(raced, 'POST').length, 1);
  release(); await pending; await double; await flush();
  ok('submit shows loading, disables the button, blocks double submits, and prevents closing mid-request');

  console.log('\n' + checks + ' UI contract checks passed. No HTTP server started; no commons data written.');
  console.log('Scope: a dependency-free mini DOM verifies logic and field contracts; real-browser layout is checked separately.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
