/* category_bands.test.js — 大分类（顶级分类）分带渲染 / 筛选 / 扩展性
   跑法：NODE_PATH=.../node_modules node tests/category_bands.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8').replace(/<script src=[^>]+><\/script>/, '');
const code = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8').replace(/\nboot\(\);/g, '\n');

let fails = 0;
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, m) { if (!c) throw new Error(m); }

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/' });
const w = dom.window;
w.setInterval = () => 0;
w.confirm = () => true;
w.prompt = () => null;

const CATS = [
  { id: 'own', name: '自有账号', desc: '本公司自营账号', builtin: true, order: 10, group_count: 1, account_count: 1 },
  { id: 'bench', name: '对标账号', desc: '外部同行', builtin: true, order: 20, group_count: 1, account_count: 1 },
  { id: 'inbox', name: '未分类', desc: '暂存', builtin: true, order: 90, group_count: 1, account_count: 1 },
];
const GROUPS = [
  { id: 'own1', name: '黄慧账号', category_id: 'own', category_name: '自有账号', builtin: false, count: 1,
    members: [{ account: 'own_acc' }] },
  { id: 'bench1', name: '竞品观察', category_id: 'bench', category_name: '对标账号', builtin: false, count: 1,
    members: [{ account: 'rival_acc' }] },
  { id: 'default', name: '默认分组', category_id: 'inbox', category_name: '未分类', builtin: true, count: 1,
    members: [{ account: 'new_acc' }] },
];

w.eval(code + `
window.audit = {
  setup(cats, groups) {
    groupsData = { categories: cats, groups };
    lastOv = null; catFilter = ""; catNewFor = null; grpSelSet.clear(); grpFilter = "";
    renderGroupDetail();
  },
  legacy(groups) { groupsData = { groups }; grpSelSet.clear(); renderGroupDetail(); },
  setCat(v) { setCatFilter(v); },
  openCreate(cid) { toggleCatCreate(cid); },
  async submit(cid) { return submitCatCreate(cid); },
  editApi(fn) { api = fn; },
  laneMenu(gid) { toggleLaneMenu({ stopPropagation(){}, currentTarget: document.createElement('button') }, gid); },
  err() { return null; }
};`);

const d = w.document;
w.audit.setup(CATS.map((c) => ({ ...c })), GROUPS.map((g) => ({ ...g, members: g.members.slice() })));

check('分带：三个大类三条带，顺序按 order', () => {
  const bands = [...d.querySelectorAll('.cat-band')];
  assert(bands.length === 3, '带数 ' + bands.length);
  assert(bands.map((b) => b.dataset.cat).join(',') === 'own,bench,inbox',
    '顺序 ' + bands.map((b) => b.dataset.cat));
});

check('归属：分组落在所属大类的带内', () => {
  assert(d.querySelector('.cat-band[data-cat="own"] #lane-own1'), 'own 带缺 lane');
  assert(d.querySelector('.cat-band[data-cat="bench"] #lane-bench1'), 'bench 带缺 lane');
  assert(d.querySelector('.cat-band[data-cat="inbox"] #lane-default'), 'inbox 带缺 lane');
  assert(!d.querySelector('.cat-band[data-cat="own"] #lane-bench1'), '跨带串组');
});

check('带计数：N 组 · M 账号', () => {
  const cnt = d.querySelector('.cat-band[data-cat="own"] .cat-cnt');
  assert(/1 组 · 1 账号/.test(cnt.textContent), cnt.textContent);
});

check('带说明与内置标记', () => {
  const own = d.querySelector('.cat-band[data-cat="own"]');
  assert(/本公司自营账号/.test(own.querySelector('.cat-desc').textContent), '缺说明');
  assert(/内置/.test(own.querySelector('.cat-ops').textContent), '缺内置标记');
});

check('大类 chips：全部 + 每类 + ＋大类', () => {
  const chips = [...d.querySelectorAll('.lane-cats .chip-cat')];
  assert(chips.length === 5, 'chips ' + chips.length);
  assert(/全部/.test(chips[0].textContent), chips[0].textContent);
  assert(/自有账号/.test(chips[1].textContent), chips[1].textContent);
  assert(/＋ 大类/.test(chips[4].textContent), chips[4].textContent);
});

check('大类筛选：只保留目标带，其它整带隐藏', () => {
  w.audit.setCat('own');
  const vis = [...d.querySelectorAll('.cat-band:not(.cat-hide)')].map((b) => b.dataset.cat);
  assert(vis.length === 1 && vis[0] === 'own', '可见带 ' + vis.join(','));
  w.audit.setCat('');
  assert(d.querySelectorAll('.cat-band:not(.cat-hide)').length === 3, '清除筛选未还原');
});

check('空大类：仍有带 + 空态提示 + 可新建分组', () => {
  const cats = CATS.map((c) => ({ ...c }));
  w.audit.setup(cats, GROUPS.map((g) => ({ ...g, members: g.members.slice() })).filter((g) => g.id !== 'bench1'));
  const band = d.querySelector('.cat-band[data-cat="bench"]');
  assert(/还没有分组/.test(band.textContent), '缺空态提示');
  assert(/＋ 新建分组/.test(band.textContent), '缺新建入口');
  w.audit.setup(CATS.map((c) => ({ ...c })), GROUPS.map((g) => ({ ...g, members: g.members.slice() })));
});

check('内联新建分组：输入框出现在目标带，提交带 category_id', async () => {
  const sent = [];
  w.audit.editApi(async (url, opt) => {
    sent.push([url, opt && opt.body ? JSON.parse(opt.body) : null]);
    if (String(url).includes('/api/groups')) return { ok: true, json: async () => ({ groups: [] }) };
    return { ok: true, json: async () => ({}) };
  });
  w.audit.openCreate('bench');
  const inp = d.getElementById('cat-new-name-bench');
  assert(!!inp, '目标带未出现输入框');
  inp.value = '竞品二组';
  w.audit.submit('bench');
  const body = sent.find((s) => s[0] === '/api/groups');
  assert(body && body[1].category_id === 'bench', '未带 category_id：' + JSON.stringify(body));
  assert(body[1].name === '竞品二组', '名称错误');
});

check('组头 ⋯ 菜单含「移到大类」', () => {
  w.audit.laneMenu('own1');
  const menu = d.querySelector('.grp-ctx');
  assert(menu && /移到大类/.test(menu.textContent), '菜单缺移到大类');
  menu.remove();
});

check('旧数据兜底（无 categories）：单带、无大类 chips、泳道不变', () => {
  w.audit.legacy(GROUPS.map((g) => ({ ...g, members: g.members.slice() })));
  assert(d.querySelectorAll('.cat-band').length === 1, '兜底应单带');
  assert(d.querySelector('.lane-cats') === null, '兜底不应渲染大类 chips');
  assert(d.querySelectorAll('.lane').length === 3, '泳道数量异常');
});

console.log(fails ? `ALL PASS? no, ${fails} failed` : 'ALL PASS');
process.exit(fails ? 1 : 0);
