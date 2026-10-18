#!/usr/bin/env node
'use strict';
// 无网络、无依赖的 DOM 契约测试。测试数据仅在此进程内使用，不写入社区存储。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const htmlPath = path.join(__dirname, '../assets/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
assert.equal(scripts.length, 1, '页面只含一个内联脚本');
const source = scripts[0][1];
new vm.Script(source, { filename: htmlPath });
assert(!/\b(?:innerHTML|outerHTML|insertAdjacentHTML|eval)\b|new\s+Function\s*\(/.test(source), '不得使用 HTML 注入或动态执行');
assert(!/<(?:script|link|iframe|img)\b[^>]*(?:src|href)=['"]https?:/i.test(html), '不加载外部资源');
assert(!/gradient\(|box-shadow\s*:/i.test(html), '不使用渐变或阴影');
for (const radius of html.matchAll(/border-radius\s*:\s*(\d+)px/g)) assert(Number(radius[1]) <= 4);
for (const token of ['#0F0E0E', '#F2F2F2', '#A8A8A8', '#E8C268', '#7DD9A6', '#E07866']) assert(html.includes(token));
assert(html.includes('grid-template-columns:210px minmax(0,680px) 260px'));
assert(html.includes('@media(max-width:700px)'));
assert(html.includes('max-width:calc(100vw - 16px)'));
assert.equal((source.match(/localStorage\.setItem\(/g) || []).length, 1, '只持久化当前身份');

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
  id: '11111111-1111-4111-8111-111111111111', title: '测试专用记录，不写入本地库', summary: '测试用摘要', category: 'coding',
  tags: ['测试'], author: {kind:'human',label:'本地用户',model:'unknown',framework:'unknown'},
  scope:{project:'测试项目',applicable:'测试条件',avoid:'测试边界'}, status:'observed', evidenceType:'hypothesis', source:'测试进程内的来源',
  body:'# 安全标题\n\n<img src=x onerror=alert(1)>\n\n```js\nalert("仅文本")\n```\n\n- 条目',
  createdAt:'2026-10-18T10:00:00Z', updatedAt:'2026-10-18T10:00:00Z', revision:1,
  likeCount:0,adoptionCount:0,helpedCount:0,failedCount:0,likes:[],comments:[],adoptions:[],
  revisionHistory:[{revision:1,origin:'created',createdAt:'2026-10-18T10:00:00Z',bodyHash:'test-hash'}]
});
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
function environment({protocol='http:', posts=[], fail=false, storageValue, rejectPatch=false} = {}) {
  const document = new MiniDocument(html); const timers = new Map(); let nextTimer = 0;
  const requests = []; const savedIdentity = []; const store = {posts:structuredClone(posts), fail, rejectPatch, gate:null};
  const context = {
    document, location:{protocol}, URLSearchParams, AbortController, Blob, console,
    URL:{createObjectURL: () => 'blob:test', revokeObjectURL() {}}, FormData:MiniFormData,
    localStorage:{getItem: () => storageValue || null, setItem: (key,value) => savedIdentity.push({key,value})},
    setTimeout: (fn, delay) => {const id = ++nextTimer; timers.set(id,{fn,delay});return id;}, clearTimeout: id => timers.delete(id),
    confirm: () => false,
    fetch: async (url, options = {}) => {
      requests.push({url, ...options, data:options.body ? JSON.parse(options.body) : null});
      if (store.gate) await store.gate;
      if (store.fail) throw new Error('测试网络断开');
      const method=options.method || 'GET'; let result; let status=200;
      if (url==='/api/config') result={csrfToken:'test-only-token',root:'/测试磁盘',categories:['scope','communication','tools','verification','coding','data','design','research','workflow']};
      else if (url.startsWith('/api/posts?') || url==='/api/posts') {
        if (method==='GET') {
          const params=new URLSearchParams(url.split('?')[1] || '');
          result={posts:store.posts.filter(p=>(!params.get('q') || (p.title+p.summary+p.body).includes(params.get('q'))) && (!params.get('category') || p.category===params.get('category')) && (!params.get('status') || p.status===params.get('status'))).map(p=>({...structuredClone(p),body:''}))};
        } else { const p={...fixture(),...JSON.parse(options.body),id:'22222222-2222-4222-8222-222222222222'}; store.posts.push(p);result={post:p};status=201; }
      } else if (url==='/api/export') result={schemaVersion:1,posts:structuredClone(store.posts)};
      else {
        const parts=url.split('/'); const p=store.posts.find(p=>p.id===parts[3]); const data=options.body ? JSON.parse(options.body) : {};
        if (!p) {status=404;result={error:{message:'测试记录不存在'}};}
        else if (method==='GET') result={post:structuredClone(p)};
        else if (method==='PATCH' && store.rejectPatch) {status=409;result={error:{message:'版本已变化'}};}
        else if (method==='PATCH') {Object.assign(p,data);delete p.expectedRevision;p.revision++;result={post:structuredClone(p)};}
        else if (parts[4]==='like') {
          const key=a=>JSON.stringify([a.model,a.framework,a.label]); p.likes=p.likes.filter(l=>key(l.actor)!==key(data.actor));
          if (data.liked) p.likes.push({actor:data.actor});p.likeCount=p.likes.length;result={post:structuredClone(p)};
        } else if (parts[4]==='comments') {p.comments.push({...data,id:'test-comment',createdAt:p.updatedAt});result={post:structuredClone(p)};}
        else if (parts[4]==='adoptions') {
          const key=a=>JSON.stringify([a.model,a.framework,a.label]);
          p.adoptions=p.adoptions.filter(a=>!(a.taskId===data.taskId && key(a.actor)===key(data.actor)));
          p.adoptions.push({...data,updatedAt:p.updatedAt});p.adoptionCount=p.adoptions.filter(a=>a.status!=='planned').length;
          p.helpedCount=p.adoptions.filter(a=>a.status==='helped').length;p.failedCount=p.adoptions.filter(a=>a.status==='failed').length;
          result={post:structuredClone(p)};
        }
      }
      return {ok:status>=200&&status<300,status,json:async()=>result};
    }
  };
  context.window = {addEventListener() {}};
  vm.createContext(context);
  // 只在测试进程内开放函数，不向交付页面加入调试接口。
  const instrumented=source.replace(/\}\)\(\);\s*$/, 'globalThis.ui={state,renderMarkdown,actorKey,normalizeIdentity,likedBy,logo,loadPosts,connect,offline,openEditor,openDetail,openIdentity,openComment,openAdoption,toggleLike,closeDialog,sortedPosts};})();');
  vm.runInContext(instrumented, context, {filename:htmlPath});
  return {context,document,ui:context.ui,requests,timers,savedIdentity,store,$:id=>document.getElementById(id)};
}
async function input(form, name, value) { const n=form.elements.namedItem(name);assert(n, name+' 字段存在');n.value=value;await n.emit('input');await n.emit('change');return n; }
function dataRequests(env, method) {return env.requests.filter(r => ['POST','PUT','PATCH'].includes(r.method) && (!method || r.method===method));}
async function run() {
  let checks=0;
  const ok=message=>{checks++;console.log('通过 · '+message);};
  const file=environment({protocol:'file:'});await flush();
  for(const dialog of file.document.querySelectorAll('dialog')) assert(file.$(dialog.getAttribute('aria-labelledby')), 'dialog 有可访问标题');
  assert(file.$('toast').getAttribute('aria-live'));
  assert.equal(file.requests.length,0);assert(!file.ui.state.connected);assert(file.$('newPostBtn').disabled);assert(file.$('connectionMessage').textContent.includes('先启动'));assert(![...file.timers.values()].some(t=>t.delay===15000));ok('file:// 不请求 API、不伪存帖子、不轮询失败');
  const e=environment();await flush();assert(e.ui.state.connected);assert(e.$('feed').textContent.includes('第一条经验'));assert.equal(e.$('stats').querySelector('dd').textContent,'0');ok('空库真实零统计与新建入口');
  e.ui.openIdentity();let f=e.$('identityBody').querySelector('form');await input(f,'kind','ai');await input(f,'label','实际执行者');await input(f,'model','');await input(f,'framework','');await f.emit('submit');assert.equal(e.ui.state.identity.model,'unknown');assert.equal(e.ui.state.identity.framework,'unknown');assert.equal(e.savedIdentity.length,1);assert.equal(e.savedIdentity[0].key,'agent-commons.current-identity');ok('真实自报身份；AI 空模型与框架规范为 unknown，仅身份进入 localStorage');
  const malicious=e.ui.renderMarkdown('# 安全\n\n<script>alert(1)</script>\n<img onerror=alert(1)>\n\n```\nconst x = 1;\n```\n\n- 条目\n\n1. 次序');
  assert.equal(malicious.querySelectorAll('script,img').length,0);assert.equal(malicious.querySelectorAll('h2').length,1);assert.equal(malicious.querySelectorAll('pre').length,1);assert.equal(malicious.querySelectorAll('ul').length,1);assert.equal(malicious.querySelectorAll('ol').length,1);assert(malicious.textContent.includes('<script>'));assert.equal(malicious.querySelector('code').textContent,'const x = 1;');
  for(const s of ['','```','- a\n1. b','\n## h\nparagraph','<svg onload=alert(1)>','https://example.invalid'])e.ui.renderMarkdown(s);ok('安全 Markdown 只创建允许的 DOM，HTML 与代码不执行');
  assert.equal(e.ui.actorKey({kind:'ai',model:'m',framework:'f',label:'l'}),e.ui.actorKey({kind:'human',model:'m',framework:'f',label:'l'}));
  assert.equal(e.ui.logo({model:'m',framework:'f',label:'l'}).querySelector('path').getAttribute('stroke'),e.ui.logo({model:'m',framework:'f',label:'l'}).querySelector('path').getAttribute('stroke'));ok('身份键与后端一致，作者几何标记稳定');
  await e.ui.openEditor();f=e.$('editorBody').querySelector('form');assert.equal(f.elements.namedItem('status').value,'observed');assert.equal(f.elements.namedItem('evidenceType').value,'hypothesis');
  await input(f,'title','新增测试');await input(f,'body','# 经验\n正文');await input(f,'summary','完整摘要');await input(f,'category','tools');await input(f,'tags','边界, 证据，边界');await input(f,'project','项目');await input(f,'applicable','适用');await input(f,'avoid','避免');await input(f,'source','真实来源');await f.emit('submit');await flush();
  const created=dataRequests(e,'POST')[0];assert.equal(created.url,'/api/posts');assert.deepEqual(created.data.tags,['边界','证据']);assert.deepEqual(created.data.scope,{project:'项目',applicable:'适用',avoid:'避免'});assert.equal(created.data.author.label,'实际执行者');assert.equal(created.headers['X-Commons-Token'],'test-only-token');assert(!e.$('editorDialog').open);ok('完整新增表单写入 API，默认案例 / 假设，携带当前身份及 CSRF');
  const p=fixture();const active=environment({posts:[p],rejectPatch:true});await flush();assert.equal(active.$('stats').querySelector('dd').textContent,'1');assert(active.$('feed').textContent.includes(p.title));
  await active.ui.openDetail(p.id);assert(active.$('detailBody').textContent.includes('<img src=x'));assert.equal(active.$('detailBody').querySelectorAll('img').length,0);assert(active.$('detailBody').textContent.includes('test-hash'));assert(active.$('detailBody').textContent.includes('测试边界'));assert(active.requests.some(r=>r.url==='/api/posts/'+p.id));ok('摘要列表之外始终 GET 完整详情，展示范围、来源与真实修订索引');
  await active.ui.openEditor(p.id);f=active.$('editorBody').querySelector('form');const bodyText='我的未保存版本\n<script>不执行</script>';await input(f,'body',bodyText);await input(f,'title','我的编辑标题');await f.emit('submit');
  assert.equal(dataRequests(active,'PATCH')[0].data.expectedRevision,1);assert.equal(f.elements.namedItem('body').value,bodyText);assert(active.$('editorBody').textContent.includes('版本冲突'));assert(!active.$('editorDialog').querySelector('.conflict').hidden);assert(!active.ui.closeDialog(active.$('editorDialog')));assert(active.$('editorDialog').open);
  const comparison=active.$('editorDialog').querySelector('.conflict').querySelectorAll('button')[0];await comparison.click();assert.equal(f.elements.namedItem('body').value,bodyText);const reload=active.$('editorDialog').querySelector('.conflict').querySelectorAll('button')[1];await reload.click();assert.equal(f.elements.namedItem('body').value,bodyText);ok('PATCH expectedRevision 与 409 保留全部输入，查看不覆盖、重载与关闭需确认');
  active.context.confirm=()=>true;active.store.posts[0].revision=2;active.store.posts[0].body='最新磁盘正文';await reload.click();f=active.$('editorBody').querySelector('form');assert.equal(f.elements.namedItem('body').value,'最新磁盘正文');assert.equal(active.$('editorDialog')._dirty,false);active.store.rejectPatch=false;await input(f,'title','确认重新读取后的修订');await f.emit('submit');await flush();assert.equal(dataRequests(active,'PATCH').at(-1).data.expectedRevision,2);assert.equal(active.store.posts[0].revision,3);active.context.confirm=()=>false;ok('确认后才重载最新正文，再次编辑使用新的 expectedRevision');
  const detailBefore=active.$('detailBody').textContent;const d=active.$('detailDialog');d.scrollTop=77;active.$('searchInput').value='未提交搜索';await active.ui.loadPosts(true);assert.equal(active.$('detailBody').textContent,detailBefore);assert.equal(d.scrollTop,77);assert.equal(active.$('searchInput').value,'未提交搜索');ok('定时列表读取不重置 dialog、滚动位置或搜索输入');
  active.ui.closeDialog(d,true);const card=active.$('feed').querySelector('article');const like=card.querySelector('[data-action="like"]');await active.ui.toggleLike(p.id,like);await flush();assert.equal(active.store.posts[0].likeCount,1);assert.equal(active.store.posts[0].status,'observed');let current=active.$('feed').querySelector('[data-action="like"]');assert.equal(current.getAttribute('aria-pressed'),'true');await active.ui.toggleLike(p.id,current);await flush();assert.equal(active.store.posts[0].likeCount,0);ok('喜欢 PUT true / false，读取 actor 键确认当前喜欢，不升级可信度');
  active.ui.openAdoption(p.id);f=active.$('adoptionBody').querySelector('form');await input(f,'taskId','task-real');await input(f,'reason','当前条件相符');await f.emit('submit');await flush();assert.equal(active.store.posts[0].adoptionCount,0);assert.equal(active.store.posts[0].adoptions[0].status,'planned');
  active.ui.openAdoption(p.id);f=active.$('adoptionBody').querySelector('form');await input(f,'taskId','task-real');await input(f,'reason','实测');await input(f,'status','helped');assert(f.elements.namedItem('outcome').required);let before=dataRequests(active,'PUT').length;await f.emit('submit');assert.equal(dataRequests(active,'PUT').length,before);await input(f,'outcome','实际测试符合预期');await f.emit('submit');await flush();assert.equal(active.store.posts[0].adoptionCount,1);assert.equal(active.store.posts[0].helpedCount,1);assert.equal(active.store.posts[0].status,'observed');
  active.ui.openAdoption(p.id);f=active.$('adoptionBody').querySelector('form');await input(f,'status','failed');assert(f.elements.namedItem('outcome').required);active.ui.closeDialog(active.$('adoptionDialog'),true);ok('采纳默认 planned 不计数，有效 / 失败要求 outcome，真实任务与原因必填');
  active.ui.openComment(p.id);f=active.$('commentBody').querySelector('form');before=dataRequests(active,'POST').length;await input(f,'text','   ');await f.emit('submit');assert.equal(dataRequests(active,'POST').length,before);await input(f,'text','<img onerror=alert(1)> 补充证据');await input(f,'section','自由章节');await f.emit('submit');await flush();assert.equal(active.store.posts[0].comments[0].section,'自由章节');await active.ui.openDetail(p.id);assert(active.$('detailBody').textContent.includes('自由章节'));assert.equal(active.$('detailBody').querySelectorAll('img').length,0);active.ui.closeDialog(active.$('detailDialog'),true);ok('评论非空验证、自由章节及不可信内容安全展示');
  let focusTarget=active.$('feed').querySelector('[data-action="detail"]');focusTarget.focus();await focusTarget.click();await active.ui.loadPosts(true);assert(!focusTarget.isConnected);const cancel=await active.$('detailDialog').emit('cancel');assert(cancel.defaultPrevented);assert(!active.$('detailDialog').open);assert.equal(active.document.activeElement.dataset.action,'detail');ok('ESC 关闭详情并在列表刷新后恢复到对应卡片焦点');
  let expand=active.$('feed').querySelector('[data-action="expand"]');await expand.click();assert(active.ui.state.expanded.has(p.id));assert(active.$('feed').querySelector('.expanded').textContent.includes('最新磁盘正文'));active.store.posts[0].revision++;active.store.posts[0].body='磁盘另一个修订';const reads=active.requests.filter(r=>r.url==='/api/posts/'+p.id).length;await active.ui.loadPosts(true);assert(active.$('feed').querySelector('.expanded').textContent.includes('修订已变化'));assert.equal(active.requests.filter(r=>r.url==='/api/posts/'+p.id).length,reads);expand=active.$('feed').querySelector('[data-action="expand"]');await expand.click();assert(active.$('feed').querySelector('.expanded').textContent.includes('磁盘另一个修订'));ok('展开始终读取完整正文；列表刷新发现修订变化时不显示旧缓存、不额外读取详情');
  active.ui.state.q='不存在的词';await active.ui.loadPosts();assert(active.$('feed').textContent.includes('没有匹配'));assert.equal(active.$('stats').querySelector('dd').textContent,'1');ok('搜索无结果与全库真实统计分离');
  active.ui.state.q='';await active.ui.loadPosts();active.store.fail=true;await active.ui.loadPosts();await flush();assert(!active.ui.state.connected);assert(active.$('newPostBtn').disabled);assert(active.$('feed').textContent.includes(active.store.posts[0].title));assert(![...active.timers.values()].some(t=>t.delay===15000));assert.equal(active.$('feed').getAttribute('aria-busy'),'false');active.store.fail=false;await active.ui.connect();assert(active.ui.state.connected);assert(!active.$('newPostBtn').disabled);ok('网络断开保留布局 / 记录，禁用写入且停止重试，手动重连恢复');
  const sort=environment({posts:[fixture(),{...fixture(),id:'33333333-3333-4333-8333-333333333333',status:'validated',title:'验证案例',adoptionCount:2,updatedAt:'2026-10-17T10:00:00Z'}]});await flush();sort.ui.state.sort='verified';assert.equal(sort.ui.sortedPosts()[0].status,'validated');sort.ui.state.sort='adopted';assert.equal(sort.ui.sortedPosts()[0].adoptionCount,2);sort.ui.state.sort='latest';assert.equal(sort.ui.sortedPosts()[0].status,'observed');ok('本地最新、验证优先、采纳最多排序');
  await sort.$('exportBtn').click();assert(sort.requests.some(r=>r.url==='/api/export'));assert.equal(sort.savedIdentity.length,0);ok('全量 JSON 导出从服务读取，用 Blob 下载，不缓存帖子');
  const raced=environment({posts:[fixture()]});await flush();raced.ui.openComment(p.id);f=raced.$('commentBody').querySelector('form');await input(f,'text','防重复测试');let release;raced.store.gate=new Promise(r=>{release=r;});const pending=f.emit('submit');await flush();assert(raced.$('commentDialog')._busy);assert(f.querySelector('[data-write]').disabled);assert(!raced.ui.closeDialog(raced.$('commentDialog')));const double=f.emit('submit');await flush();assert.equal(dataRequests(raced,'POST').length,1);release();await pending;await double;await flush();ok('提交 loading、按钮禁用、防重复与提交期间禁止关闭');
  console.log('\n'+checks+' 项界面契约测试通过。未启动 HTTP 服务、未写入社区数据。');
  console.log('范围说明：使用无依赖的轻量 DOM 检查逻辑与字段契约；375px 实际浏览器布局和服务端联调由交付检查执行。');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
