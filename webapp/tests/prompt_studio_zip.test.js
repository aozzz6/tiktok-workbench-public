/* tests/prompt_studio_zip.test.js — 提示词优化：zip 批量导入（2026-10-26）
 * 覆盖：store zip 解包入档、子目录、非文本跳过、系统垃圾跳过、
 *       坏 zip 拒绝、包内无文本拒绝
 * 跑法：NODE_PATH=…/node_modules node tests/prompt_studio_zip.test.js
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const psJs = fs.readFileSync('static/prompt_studio.js', 'utf8');

let fails = 0;
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log('PASS', name))
    .catch((e) => { fails++; console.log('FAIL', name, '->', e.message); });
}
function assert(c, msg) { if (!c) throw new Error(msg); }

/* 最小 store(0) zip：不压缩、CRC 填 0（本模块解包不校验 CRC） */
function makeZip(entries) {
  const parts = [], central = [];
  let offset = 0;
  for (const e of entries) {
    const name = Buffer.from(e.name, 'utf8');
    const data = Buffer.isBuffer(e.data) ? e.data : Buffer.from(e.data, 'utf8');
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0);
    lh.writeUInt16LE(20, 4);
    lh.writeUInt16LE(0, 8);                 // method = store
    lh.writeUInt32LE(0, 14);                // crc
    lh.writeUInt32LE(data.length, 18);
    lh.writeUInt32LE(data.length, 22);
    lh.writeUInt16LE(name.length, 26);
    parts.push(lh, name, data);

    const cd = Buffer.alloc(46);
    cd.writeUInt32LE(0x02014b50, 0);
    cd.writeUInt16LE(20, 4);
    cd.writeUInt16LE(20, 6);
    cd.writeUInt16LE(0, 10);                // method = store
    cd.writeUInt32LE(0, 16);                // crc
    cd.writeUInt32LE(data.length, 20);
    cd.writeUInt32LE(data.length, 24);
    cd.writeUInt16LE(name.length, 28);
    cd.writeUInt32LE(offset, 42);
    central.push(cd, name);

    offset += 30 + name.length + data.length;
  }
  const cdBuf = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cdBuf.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, cdBuf, eocd]);
}

const SINGLE = [
  '# 原片还原提示词',
  '',
  '## S1 车尾取包后沿人行道展示高腰裤并走向12号店门（连续跟拍） · 0.00–15.04s',
  '',
  '### 中文单图',
  '沿用图片1的起始构图和现有姿势：女性背对镜头、上身前倾站在打开的后备箱前，双臂正探向车尾区域；从这一姿势直接继续。'
].join('\n');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8789/#overview' });
const w = dom.window;
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
w.eval(psJs);
const d = w.document;
w.PS_load();

function fileFrom(buf, name) {
  return new w.File([buf], name, { type: 'application/zip' });
}
function rows() {
  return [...d.querySelectorAll('#ps-files .ps-file')].map((x) => ({
    text: x.textContent.trim(),
    err: x.classList.contains('err'),
  }));
}

(async () => {
  await check('store zip（含子目录 + pdf + 系统垃圾）只入档 md 一份', async () => {
    const buf = makeZip([
      { name: 'pack/prompts.md', data: SINGLE },
      { name: 'pack/readme.pdf', data: 'PDF-PLACEHOLDER' },
      { name: '__MACOSX/._prompts.md', data: 'junk' },
    ]);
    await w.PS.importFiles([fileFrom(buf, 'bundle.zip')]);
    const list = w.PS.state.bench.list;
    assert(list.length === 1, '档案数=' + list.length);
    const r = rows();
    assert(r.length === 1, '结果行=' + JSON.stringify(r));
    assert(/bundle\.zip › pack\/prompts\.md/.test(r[0].text), '行文案=' + r[0].text);
    assert(!r[0].err, '被误标为失败');
    assert(/1 拍/.test(r[0].text), '拍数=' + r[0].text);
  });

  await check('zip 内文档命名取「文件名 · 首拍名」', () => {
    const rec = w.PS.state.bench.list[0];
    assert(/^prompts · S1 /.test(rec.name), '档案名=' + rec.name);
  });

  await check('压缩包内两份 md 都入档', async () => {
    const buf = makeZip([
      { name: 'a.md', data: SINGLE.replace('S1 车尾', 'S1 甲') },
      { name: 'b.md', data: SINGLE.replace('S1 车尾', 'S1 乙') },
    ]);
    await w.PS.importFiles([fileFrom(buf, 'two.zip')]);
    assert(w.PS.state.bench.list.length === 3, '档案数=' + w.PS.state.bench.list.length);
    assert(rows().filter((x) => !x.err).length === 2, '成功行=' + rows().length);
  });

  await check('坏 zip：明确报「不是有效的 zip 文件」', async () => {
    await w.PS.importFiles([fileFrom(Buffer.from('this is not a zip at all'), 'bad.zip')]);
    const r = rows();
    assert(r.length === 1 && r[0].err, '未标失败：' + JSON.stringify(r));
    assert(/不是有效的 zip/.test(r[0].text), '文案=' + r[0].text);
  });

  await check('包内只有非文本：报「包内没有 .md / .txt」', async () => {
    const buf = makeZip([{ name: 'only.pdf', data: 'x' }]);
    await w.PS.importFiles([fileFrom(buf, 'nopod.zip')]);
    const r = rows();
    assert(r.length === 1 && r[0].err, '未标失败：' + JSON.stringify(r));
    assert(/包内没有/.test(r[0].text), '文案=' + r[0].text);
    assert(w.PS.state.bench.list.length === 3, '档案被误加：' + w.PS.state.bench.list.length);
  });

  await check('zip 含图/视频/音频/报告：挂到档案上并标注素材', async () => {
    const jpg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46]);
    const mp4 = Buffer.from('fake-mp4-bytes-0123456789');
    const buf = makeZip([
      { name: 'pack/2.md', data: SINGLE.replace('S1 车尾', 'S1 戊') },
      { name: 'images/x_001.jpg', data: jpg },
      { name: 'images/f_001.jpg', data: jpg },
      { name: 's01_reference.mp4', data: mp4 },
      { name: 'original_audio.m4a', data: mp4 },
      { name: 'report.json', data: JSON.stringify({ product_summary: '测试产品', scene_summary: '测试场景' }) },
    ]);
    await w.PS.importFiles([fileFrom(buf, 'assets.zip')]);
    const rec = w.PS.state.bench.list[0];
    assert(rec.assets, '未挂素材');
    assert(rec.assets.images === 2 && rec.assets.videos === 1 && rec.assets.audios === 1, JSON.stringify(rec.assets));
    assert(rec.assets.refs === 1 && rec.assets.frames === 1, JSON.stringify(rec.assets));
    assert(rec.report && rec.report.product === '测试产品', JSON.stringify(rec.report));
    const r = rows()[0];
    assert(/素材 2 图 \/ 1 视频/.test(r.text), '行文案=' + r.text);
  });

  await check('同内容重复导入：沿用原 id，素材不被抹掉', async () => {
    const prev = w.PS.state.bench.list.filter((b) => /S1 戊/.test(b.raw))[0];
    assert(prev, '前置档案不存在');
    const again = makeZip([{ name: 'pack/2.md', data: SINGLE.replace('S1 车尾', 'S1 戊') }]);
    await w.PS.importFiles([fileFrom(again, 'again.zip')]);
    const after = w.PS.state.bench.list.filter((b) => b.raw === prev.raw)[0];
    assert(after, '重复导入后档案丢了');
    assert(after.id === prev.id, 'id 变了：' + prev.id + ' → ' + after.id);
    assert(after.assets && after.assets.images === 2, '素材被抹：' + JSON.stringify(after.assets));
  });

  await check('普通 .md 与 .zip 混选：两条路径都走通', async () => {
    const buf = makeZip([{ name: 'z.md', data: SINGLE.replace('S1 车尾', 'S1 丙') }]);
    await w.PS.importFiles([
      new w.File([Buffer.from(SINGLE.replace('S1 车尾', 'S1 丁'), 'utf8')], 'plain.md', { type: 'text/markdown' }),
      fileFrom(buf, 'mix.zip'),
    ]);
    assert(w.PS.state.bench.list.length === 6, '档案数=' + w.PS.state.bench.list.length);
    const ok = rows().filter((x) => !x.err);
    assert(ok.length === 2, '成功行=' + JSON.stringify(rows()));
  });

  w.close();
  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})();
