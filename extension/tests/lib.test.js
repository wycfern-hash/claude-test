const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../lib/parse.js');
const S = require('../lib/status.js');
const C = require('../lib/csv.js');
const A = require('../lib/affiliate.js');
const D = require('../lib/dedupe.js');
const PO = require('../lib/posts.js');
const SO = require('../lib/social.js');

// ---------------------------------------------------------------- 解析
test('限時特賣商品（舊格式）：金額 /100000、折扣、場次欄位', () => {
  const json = { error: 0, data: { items: [{
    itemid: 111, shopid: 222, name: '滿漢大餐 蔥燒牛肉麵', image: 'abc123', price: 29900000, price_before_discount: 49900000,
    raw_discount: 40, flash_sale_stock: 50, stock: 120, sold: 300, promotionid: 497096348680566, start_time: 1790000000, end_time: 1790003600,
  }] } };
  const [it] = P.extractItems(json, { source: 'flash', now: 5 });
  assert.equal(it.key, '222.111');
  assert.equal(it.price, 299);
  assert.equal(it.original, 499);
  assert.equal(it.discountPct, 40);
  assert.equal(it.stock, 50);                       // 限時特賣用「特賣庫存」
  assert.equal(it.sold, 300);
  assert.equal(it.promotionid, '497096348680566');
  assert.equal(it.start, 1790000000);
  assert.equal(it.end, 1790003600);
  assert.equal(it.image, P.IMG_BASE + 'abc123');
  assert.equal(it.url, 'https://shopee.tw/product/222/111');
  assert.equal(it.source, 'flash');
});

test('搜尋結果（item_basic 包一層）與折扣字串', () => {
  const json = { items: [{ item_basic: { itemid: 3, shopid: 4, name: '保溫杯', image: 'x', price: 39900000, price_before_discount: 59900000, discount: '33%', historical_sold: 1200 } }] };
  const [it] = P.extractItems(json, { source: 'search' });
  assert.equal(it.price, 399);
  assert.equal(it.original, 599);
  assert.equal(it.discountPct, 33);
  assert.equal(it.sold, 1200);
  assert.equal(it.start, null);
});

test('搜尋結果（新格式 item_card_*）', () => {
  const json = { data: { items: [{ item_data: { itemid: 5, shopid: 6,
    item_card_displayed_asset: { name: '小風扇', image: 'imghash' },
    item_card_display_price: { price: 19900000, strikethrough_price: 29900000 } } }] } };
  const [it] = P.extractItems(json, { source: 'search' });
  assert.equal(it.name, '小風扇');
  assert.equal(it.price, 199);
  assert.equal(it.original, 299);
  assert.equal(it.discountPct, 33);
});

test('沒有名稱的 id 參照不算商品；同商品重複只留一個；沒折扣就是 0', () => {
  const json = { a: [{ itemid: 1, shopid: 2 }, { itemid: 7, shopid: 8, name: 'A', price: 1000000 }, { itemid: 7, shopid: 8, name: 'A', price: 1000000 }] };
  const items = P.extractItems(json);
  assert.equal(items.length, 1);
  assert.equal(items[0].discountPct, 0);
  assert.equal(items[0].original, null);
});

test('原價不高於特價就不算特價', () => {
  const [it] = P.extractItems({ x: { itemid: 1, shopid: 2, name: 'A', price: 5000000, price_before_discount: 5000000 } });
  assert.equal(it.original, null);
  assert.equal(it.discountPct, 0);
});

test('場次：秒或毫秒時間戳都收', () => {
  const json = { data: { sessions: [
    { promotionid: 497096348680566, start_time: 1790000000, end_time: 1790003600, name: '12:00' },
    { promotionid: 2, start_time: 1790003600000, end_time: 1790007200000 },
    { promotionid: 3, start_time: 5, end_time: 6 },
  ] } };
  const s = P.extractSessions(json);
  assert.equal(s.length, 2);
  assert.equal(s[1].start, 1790003600);
  assert.equal(s[1].end, 1790007200);
  assert.equal(s[0].promotionid, '497096348680566');
});

test('商品物件不會被當成場次', () => {
  assert.equal(P.extractSessions({ items: [{ itemid: 1, shopid: 2, name: 'A', promotionid: 9, start_time: 1790000000, end_time: 1790003600 }] }).length, 0);
});

test('classify', () => {
  assert.equal(P.classify('https://shopee.tw/api/v4/flash_sale/flash_sale_batch_get_items'), 'flash');
  assert.equal(P.classify('https://shopee.tw/api/v4/search/search_items?keyword=a'), 'search');
  assert.equal(P.classify('https://shopee.tw/api/v4/pdp/get'), 'other');
});

// ---------------------------------------------------------------- 時間與狀態（台北時間）
test('日期時間用台北時間，午夜是 00:00 不是 24:00', () => {
  assert.equal(S.fmtDateTime(1767225600), '2026/01/01（週四） 08:00');   // UTC 00:00 = 台北 08:00
  assert.equal(S.fmtDateTime(1767283200), '2026/01/02（週五） 00:00');   // UTC 16:00 = 台北隔天 00:00
  assert.equal(S.fmtDateTime(null), '—');
});

test('時間區間：同一天只寫一次日期；跨日顯示結束日期', () => {
  assert.equal(S.fmtRange(1767225600, 1767225600 + 7200), '2026/01/01（週四） 08:00 – 10:00');
  assert.equal(S.fmtRange(1767225600, 1767225600 + 86400 + 3600), '2026/01/01（週四） 08:00 – 01/02 09:00');
});

test('倒數格式', () => {
  assert.equal(S.fmtCountdown(3661), '01:01:01');
  assert.equal(S.fmtCountdown(90061), '1天 01:01:01');
  assert.equal(S.fmtCountdown(-5), '00:00:00');
});

test('開始了嗎／距離多久開始', () => {
  const start = 1767225600, end = start + 7200;
  const at = (t) => S.sessionStatus(start, end, t * 1000);
  const up = at(start - 3661);
  assert.equal(up.state, 'upcoming');
  assert.equal(up.label, '⏳ 距離開始 01:01:01');
  const live = at(start + 60);
  assert.equal(live.state, 'live');
  assert.equal(live.label, '🔥 已開始・剩 01:59:00');
  assert.equal(at(start).state, 'live');           // 剛好開始的那一秒算已開始
  assert.equal(at(end).state, 'ended');
  assert.equal(at(end + 1).label, '已結束');
  assert.equal(S.sessionStatus(null, null, Date.now()).state, 'none');
});

// ---------------------------------------------------------------- CSV
test('CSV：BOM、逗號/引號/換行跳脫、分潤狀態誠實標示', () => {
  const rows = [
    { name: '杯,"大"\n款', url: 'https://shopee.tw/product/1/2', price: 299, original: 499, discountPct: 40, stock: 5, sold: 9,
      source: 'flash', image: 'http://i/1.jpg', start: 1767225600, end: 1767232800, capturedAt: 1767225000000,
      aff: { url: 'https://s.shopee.tw/AbC123', state: 'ok' } },
    { name: '風扇', url: 'https://shopee.tw/product/3/4', price: 199, original: null, discountPct: 0, source: 'search', image: '', start: null, end: null, aff: null },
  ];
  const csv = C.toCsv(rows, 1767225600 * 1000 - 600000, S);
  assert.ok(csv.startsWith('﻿商品名稱,商品連結,分潤連結,分潤狀態,價格,原價,折扣,場次開始,場次結束,狀態'));
  const lines = csv.split('\r\n');
  assert.ok(csv.includes('"杯,""大""\n款"'));
  assert.ok(lines.some((l) => l.includes('https://s.shopee.tw/AbC123') && l.includes('已轉換') && l.includes('2026/01/01 週四 08:00') && l.includes('尚未開始') && l.includes('限時特賣')));
  const fan = lines.find((l) => l.startsWith('風扇'));
  assert.ok(fan.includes(',,尚未轉換（不是分潤連結）,'));        // 沒轉換：分潤連結欄是空的，狀態寫清楚
  assert.ok(fan.includes('搜尋特價'));
});

// ---------------------------------------------------------------- 分潤連結輔助
test('找短連結', () => {
  assert.equal(A.findShortLink('您的連結：https://s.shopee.tw/9Kz_AbC 請複製'), 'https://s.shopee.tw/9Kz_AbC');
  assert.equal(A.findShortLink('https://shp.ee/abc123'), 'https://shp.ee/abc123');
  assert.equal(A.findShortLink('https://shopee.tw/product/1/2'), null);
  assert.equal(A.findShortLink(''), null);
});

test('批次轉換請求與回應', () => {
  const urls = ['https://shopee.tw/product/1/2', 'https://shopee.tw/product/3/4', 'https://shopee.tw/product/5/6'];
  const body = A.buildBatchBody(urls);
  assert.equal(body.variables.linkParams.length, 3);
  assert.equal(body.variables.linkParams[1].originalLink, urls[1]);
  assert.match(body.query, /batchCustomLink/);
  const res = A.parseBatchResponse({ data: { batchCustomLink: [
    { shortLink: 'https://s.shopee.tw/AAA', failCode: 0 }, { shortLink: '', failCode: 1001 }, {} ] } }, urls);
  assert.equal(res[0].short, 'https://s.shopee.tw/AAA');
  assert.equal(res[1].short, null);
  assert.equal(res[1].err, '失敗代碼 1001');
  assert.equal(res[2].err, '沒有回傳短連結');
  assert.equal(A.parseBatchResponse({ errors: [{ message: 'x' }] }, urls), null);     // 格式不符 → 呼叫端改走備援
});

// ---------------------------------------------------------------- 向後台學：批次轉換
test('學習：陣列型請求 → 可批次，並照樣子組出多個', () => {
  const req = { url: '/api/v9/bulk_links?x=1', method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ op: 'make', opts: { sub: '' }, links: [{ raw: 'https://shopee.tw/product/1/2', tag: 'a' }] }) };
  const tpl = A.learnTemplate(req, '{"results":[{"short":"https://s.shopee.tw/AAA"}]}');
  assert.ok(tpl && tpl.batch);
  assert.deepEqual(tpl.arrayPath, ['links']);
  assert.deepEqual(tpl.elemPath, ['raw']);
  const urls = ['https://shopee.tw/product/3/4', 'https://shopee.tw/product/5/6', 'https://shopee.tw/product/7/8'];
  const b = JSON.parse(A.buildFromTemplate(tpl, urls).body);
  assert.equal(b.links.length, 3);
  assert.equal(b.links[2].raw, urls[2]);
  assert.equal(b.links[1].tag, 'a');                // 其他欄位照抄
  assert.equal(b.op, 'make');
});

test('學習：單筆型請求 → 不能批次，一次只放一個', () => {
  const req = { url: '/api/x', method: 'POST', headers: {}, body: JSON.stringify({ url: 'https://shopee.tw/product/1/2', subId: '' }) };
  const tpl = A.learnTemplate(req, 'ok https://s.shopee.tw/ZZZ');
  assert.equal(tpl.batch, false);
  const out = A.buildFromTemplate(tpl, ['https://shopee.tw/product/9/9', 'https://shopee.tw/product/8/8']);
  assert.equal(out.count, 1);
  assert.equal(JSON.parse(out.body).url, 'https://shopee.tw/product/9/9');
});

test('學習：最外層就是陣列、巢狀很深也找得到', () => {
  const top = A.learnTemplate({ url: '/a', body: JSON.stringify([{ link: 'https://shopee.tw/product/1/2' }]) }, 'https://s.shopee.tw/Q');
  assert.deepEqual(top.arrayPath, []);
  const b = JSON.parse(A.buildFromTemplate(top, ['https://shopee.tw/product/3/3', 'https://shopee.tw/product/4/4']).body);
  assert.equal(b.length, 2);
  const deep = A.learnTemplate({ url: '/a', body: JSON.stringify({ a: { b: [{ c: { d: 'https://shopee.tw/product/1/2' } }] } }) }, 'https://s.shopee.tw/Q');
  assert.deepEqual(deep.arrayPath, ['a', 'b']);
  assert.deepEqual(deep.elemPath, ['c', 'd']);
});

test('學習：回應沒有短連結、body 不是 JSON、body 裡沒有商品網址 → 學不到', () => {
  const ok = JSON.stringify({ url: 'https://shopee.tw/product/1/2' });
  assert.equal(A.learnTemplate({ url: '/a', body: ok }, '{"error":1}'), null);
  assert.equal(A.learnTemplate({ url: '/a', body: 'a=b&c=d' }, 'https://s.shopee.tw/Q'), null);
  assert.equal(A.learnTemplate({ url: '/a', body: JSON.stringify({ url: 'https://example.com/x' }) }, 'https://s.shopee.tw/Q'), null);
  assert.equal(A.learnTemplate({ url: '/a', body: JSON.stringify({ url: 'https://s.shopee.tw/Q' }) }, 'https://s.shopee.tw/R'), null);
});

test('回應對應：數量對得上才算，順序不變、去重', () => {
  const urls = ['u1', 'u2'];
  assert.deepEqual(A.mapResponseToUrls('{"a":"https://s.shopee.tw/A","b":"https://s.shopee.tw/B"}', urls).map((x) => x.short), ['https://s.shopee.tw/A', 'https://s.shopee.tw/B']);
  assert.equal(A.mapResponseToUrls('https://s.shopee.tw/A', urls), null);
  assert.deepEqual(A.extractShortLinks('https://s.shopee.tw/A https://s.shopee.tw/A https://shp.ee/B'), ['https://s.shopee.tw/A', 'https://shp.ee/B']);
  assert.deepEqual(A.CHUNK_STEPS, [20, 10, 5, 1]);
});

// ---------------------------------------------------------------- 清除重複
const R = (key, name, o = {}) => ({ key, shopid: key.split('.')[0], name, discountPct: 0, price: 100, start: null, aff: null, ...o });

test('名稱正規化：去標籤、空白、標點，轉小寫', () => {
  assert.equal(D.normName('【限時特價】Apple AirPods Pro 2 (台灣公司貨)'), 'appleairpodspro2');
  assert.equal(D.normName('保溫杯 500ML！'), D.normName('保溫杯500ml'));
  assert.equal(D.normName('[熱銷] 小風扇 - 靜音'), '小風扇靜音');
});

test('重複：每組只留一個；有分潤連結的優先，其次折扣大、價格低', () => {
  const rows = [
    R('7.1', '【特價】保溫杯 500ml', { discountPct: 30, price: 299 }),
    R('7.2', '保溫杯500ML', { discountPct: 40, price: 399 }),
    R('8.3', '保溫杯500ml(送杯套)', { discountPct: 20, price: 450, aff: { url: 'https://s.shopee.tw/A' } }),
    R('7.4', '小風扇', { discountPct: 30, price: 200 }),
    R('9.5', '小風扇', { discountPct: 30, price: 150 }),
    R('9.6', '完全不同的商品', { discountPct: 10 }),
  ];
  const { groups, remove } = D.findDuplicates(rows, 'name');
  assert.equal(groups, 2);
  assert.deepEqual(remove.map((r) => r.key).sort(), ['7.1', '7.2', '7.4']);   // 留 8.3（有分潤連結）與 9.5（折扣一樣但比較便宜）
});

test('重複：只比同一個賣家', () => {
  const rows = [R('7.1', '保溫杯', { discountPct: 10 }), R('7.2', '保溫杯', { discountPct: 20 }), R('9.3', '保溫杯', { discountPct: 5 })];
  const { remove } = D.findDuplicates(rows, 'nameShop');
  assert.deepEqual(remove.map((r) => r.key), ['7.1']);                        // 7.1 與 7.2 同賣家，留折扣大的；9.3 是別的賣家，不動
  assert.equal(D.findDuplicates(rows, 'name').remove.length, 2);
  assert.equal(D.findDuplicates([R('1.1', '【特價】')], 'name').remove.length, 0);   // 名稱清掉標籤後是空的，不拿來比
});

// ---------------------------------------------------------------- 同一商品多個場次
test('合併：同一個商品在多個場次，留還沒結束且最早開始的', () => {
  const now = 1000;
  const item = (promo, start, end, extra = {}) => ({ key: '7.1', name: 'A', promotionid: promo, start, end, source: 'flash', ...extra });
  assert.equal(P.mergeItem(item('a', 100, 500), item('b', 900, 1500), now).promotionid, 'b');      // a 已結束
  assert.equal(P.mergeItem(item('a', 2000, 3000), item('b', 1200, 1500), now).promotionid, 'b');   // 都還沒結束：留較早開始
  assert.equal(P.mergeItem(item('a', 100, 500), item('b', 200, 600), now).promotionid, 'b');       // 都結束了：留最晚的
  assert.equal(P.mergeItem(item('a', 900, 1500), item('a', 900, 1500, { price: 5 }), now).price, 5);
  assert.equal(P.mergeItem({ key: 'k', source: 'flash' }, { key: 'k', source: 'search' }, now).source, 'flash');
  assert.equal(P.mergeItem(undefined, { key: 'k' }, now).key, 'k');
});


// ---------------------------------------------------------------- 情境文案
const NOW = 1790000000 * 1000;
const ROW = { name: '【特價】保溫杯 500ml', price: 299, original: 427, discountPct: 30, start: 1790003600, end: 1790007200, image: 'x', aff: { url: 'https://s.shopee.tw/AbC' } };

test('範本：3 則情境貼文 + Threads；價格、折扣、場次都是抓到的事實，沒有編造', () => {
  const d = PO.templatePosts(ROW, S, NOW);
  assert.deepEqual(d.posts.map((p) => p.style), ['story', 'dialog', 'pain']);
  assert.ok(d.threads);
  const all = d.posts.map((p) => p.text).join('\n');
  assert.match(all, /\$299/); assert.match(all, /\$427/); assert.match(all, /30% off/);
  assert.match(all, /限時特賣/);
  assert.doesNotMatch(all, /最低價|回購|用了.*個月|保證/);
  assert.ok(d.posts.every((p) => /：/.test(p.text)));                  // 都是「我：／A：」這種小劇場
  assert.doesNotMatch(all, /你們有沒有|留言跟我說|週末整理/);        // 不要刻意的制式句
  assert.notEqual(PO.templatePosts({ ...ROW, name: '完全不同的商品名' }, S, NOW).posts[0].text, d.posts[0].text);
});
test('場次已結束就不提時間', () => {
  const d = PO.templatePosts({ ...ROW, start: 1, end: 2 }, S, NOW);
  assert.doesNotMatch(d.posts.map((p) => p.text).join(''), /限時特賣/);
});
test('連結：只用分潤連結；沒有就放提示，不會用一般網址', () => {
  const it = { text: '正文', comment: '看這' };
  assert.match(PO.compose(ROW, it, true, PO.DEFAULT_DISCLOSURE), /AbC[\s\S]*分潤連結，經由連結購買/);
  assert.doesNotMatch(PO.compose(ROW, it, false, PO.DEFAULT_DISCLOSURE), /AbC/);
  const noAff = { ...ROW, aff: null, url: 'https://shopee.tw/product/1/2' };
  const t = PO.compose(noAff, it, true, '');
  assert.ok(t.includes(PO.PLACEHOLDER) && !t.includes('shopee.tw/product'));
  assert.ok(PO.composeComment(noAff, it).includes(PO.PLACEHOLDER));
});
test('AI 提示詞：只給事實、禁止編造、不要網址', () => {
  const p = PO.buildPrompt(ROW, S, NOW);
  assert.match(p, /商品名稱：【特價】保溫杯 500ml/); assert.match(p, /特價：\$299/);
  assert.match(p, /不要編造/); assert.match(p, /幽默/); assert.match(p, /不要用「你們有沒有/); assert.match(p, /不要放任何網址/); assert.match(p, /功能、成分、規格/);
});
const AI = { posts: [{ style: 'story', text: 'S', comment: 'c' }, { style: 'dialog', text: 'D' }, { style: 'pain', text: 'P' }], threads: 'T' };
function mockFetch(check, body, ok = true) {
  return async (url, opt) => { check(url, opt); return { ok, status: ok ? 200 : 401, json: async () => body }; };
}
test('三家 AI 都能呼叫並解析（用假的 fetch）', async () => {
  const g = await PO.callAI({ provider: 'gemini', model: 'm', key: 'K' }, 'p', mockFetch((u, o) => {
    assert.match(u, /generativelanguage.*models\/m:generateContent/); assert.equal(o.headers['x-goog-api-key'], 'K');
  }, { candidates: [{ content: { parts: [{ text: JSON.stringify(AI) }] } }] }));
  assert.equal(g.threads, 'T');
  const o = await PO.callAI({ provider: 'openai', model: 'm', key: 'K' }, 'p', mockFetch((u, op) => {
    assert.match(u, /api.openai.com/); assert.equal(op.headers.authorization, 'Bearer K');
  }, { choices: [{ message: { content: '```json\n' + JSON.stringify(AI) + '\n```' } }] }));
  assert.equal(o.posts.length, 3);
  const c = await PO.callAI({ provider: 'claude', model: 'm', key: 'K' }, 'p', mockFetch((u, op) => {
    assert.match(u, /api.anthropic.com/); assert.equal(op.headers['x-api-key'], 'K');
  }, { content: [{ text: JSON.stringify(AI) }] }));
  assert.equal(c.posts[2].text, 'P');
});
test('沒填 key／模型會明確報錯；AI 失敗退回範本並標記原因', async () => {
  await assert.rejects(PO.callAI({ provider: 'gemini', model: '', key: 'K' }, 'p', async () => ({})), /模型/);
  const d = await PO.generate(ROW, { provider: 'openai', model: 'm', key: 'bad' }, S, NOW, mockFetch(() => {}, { error: { message: 'bad key' } }, false));
  assert.equal(d.by, 'template'); assert.match(d.aiError, /bad key/); assert.equal(d.posts.length, 3);
  const ok = await PO.generate(ROW, { provider: 'openai', model: 'm', key: 'k' }, S, NOW, mockFetch(() => {}, { choices: [{ message: { content: JSON.stringify(AI) } }] }));
  assert.equal(ok.by, 'openai'); assert.equal(ok.posts[0].text, 'S');
  assert.equal((await PO.generate(ROW, null, S, NOW)).by, 'template');
});
test('文案 CSV：每則一列、含 Threads、欄位正確跳脫', () => {
  const csv = PO.postsToCsv([{ row: ROW, data: { ...PO.templatePosts(ROW, S, NOW), by: 'template' } }], PO.DEFAULT_DISCLOSURE);
  assert.ok(csv.startsWith('\ufeff商品名稱,風格,'));
  assert.equal((csv.match(/Threads 短文/g) || []).length, 1);
  assert.ok(csv.includes('生活小故事') && csv.includes('對話情境劇') && csv.includes('痛點共鳴') && csv.includes('https://s.shopee.tw/AbC'));
});

test('查模型清單：Gemini 只留能 generateContent 的、去掉 models/ 前綴；錯誤訊息帶出來', async () => {
  const ms = await PO.listModels({ provider: 'gemini', key: 'K' }, mockFetch((u, o) => { assert.match(u, /v1beta\/models/); assert.equal(o.headers['x-goog-api-key'], 'K'); },
    { models: [{ name: 'models/gemini-3-flash-preview', supportedGenerationMethods: ['generateContent'] }, { name: 'models/embedding-001', supportedGenerationMethods: ['embedContent'] }] }));
  assert.deepEqual(ms, ['gemini-3-flash-preview']);
  assert.deepEqual(await PO.listModels({ provider: 'openai', key: 'K' }, mockFetch(() => {}, { data: [{ id: 'gpt-4.1-mini' }, { id: 'whisper-1' }] })), ['gpt-4.1-mini']);
  assert.deepEqual(await PO.listModels({ provider: 'claude', key: 'K' }, mockFetch(() => {}, { data: [{ id: 'claude-sonnet-5-5' }] })), ['claude-sonnet-5-5']);
  await assert.rejects(PO.listModels({ provider: 'gemini', key: 'bad' }, mockFetch(() => {}, { error: { message: 'API key not valid' } }, false)), /API key not valid/);
  await assert.rejects(PO.listModels({ provider: 'gemini', key: '' }), /API key/);
});
test('預設的 Gemini 模型建議不含已下架的 2.5-pro', () => {
  const g = PO.PROVIDERS.find((p) => p[0] === 'gemini')[2];
  assert.ok(g.includes('gemini-3.1-pro-preview') && !g.includes('gemini-2.5-pro'));
});

test('搜尋後回覆的規則：不能空白、不能放連結、每天上限', async () => {
  assert.match(SO.checkReply('  '), /空的/);
  for (const t of ['看 https://s.shopee.tw/AbC', '在這 shp.ee/xyz', 'http://a.b']) assert.match(SO.checkReply(t), /不能放連結/);
  assert.equal(SO.checkReply('我也在找，推薦先看容量'), '');
  const now = Date.parse('2026-10-07T10:00:00+08:00');
  assert.equal(SO.repliedToday([now - 1000, now - 2000, now - 86400e3 * 2], now), 2);
  assert.equal(SO.REPLY_DAILY_CAP, 20);
});
test('回覆草稿：沒選 AI 回傳空字串；有選就用 AI，且提示詞禁止連結與推銷', async () => {
  assert.equal(await PO.draftReply(null, '好難選', '保溫杯'), '');
  assert.match(PO.buildReplyPrompt('好難選', '保溫杯'), /不要放任何網址/);
  const r = await PO.draftReply({ provider: 'openai', model: 'm', key: 'k' }, '好難選', '保溫杯',
    async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '{"reply":"我也是選好久😂"}' } }] }) }));
  assert.equal(r, '我也是選好久😂');
  assert.equal(await PO.draftReply({ provider: 'openai', model: 'm', key: 'k' }, 'x', 'y', async () => ({ ok: false, status: 401, json: async () => ({ error: { message: 'bad' } }) })), '');
});
