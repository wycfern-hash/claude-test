// 分潤後台「自訂連結」的輔助：取短連結、組 GraphQL 請求、解析回應。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const SHORT_RE = /https?:\/\/(?:s\.shopee\.tw|shp\.ee)\/[A-Za-z0-9_-]+/;
  const QUERY = 'query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){ ' +
    'batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){ shortLink longLink failCode } }';

  const findShortLink = (text) => { const m = SHORT_RE.exec(text || ''); return m ? m[0] : null; };

  function buildBatchBody(urls, subId) {
    return {
      operationName: 'batchGetCustomLink', query: QUERY,
      variables: {
        linkParams: urls.map((u) => ({ originalLink: u, advancedLinkParams: subId ? { subId1: subId } : {} })),
        sourceCaller: 'CUSTOM_LINK_CALLER',
      },
    };
  }
  // 回傳 [{url, short, err}]；格式不符回 null（呼叫端改走網頁操作的備援）
  function parseBatchResponse(json, urls) {
    const arr = json && json.data && json.data.batchCustomLink;
    if (!Array.isArray(arr)) return null;
    return urls.map((u, i) => {
      const r = arr[i] || {};
      const short = findShortLink(r.shortLink || '');
      return { url: u, short, err: short ? '' : (r.failCode ? '失敗代碼 ' + r.failCode : '沒有回傳短連結') };
    });
  }

  // ---- 向你的後台學：把你手動轉 1 個連結時，後台真正送出的請求記下來，之後照樣子批次重送 ----
  const PRODUCT_RE = /^https?:\/\/(?:[\w-]+\.)*shopee\.tw\/\S+/i;
  const isProductUrl = (v) => typeof v === 'string' && PRODUCT_RE.test(v) && !SHORT_RE.test(v);

  function extractShortLinks(text) {
    const out = [];
    const re = new RegExp(SHORT_RE.source, 'g');
    let m;
    while ((m = re.exec(text || '')) !== null) if (!out.includes(m[0])) out.push(m[0]);
    return out;
  }
  // 在 JSON 裡找「商品網址」那個欄位的路徑，並判斷它是不是在陣列元素裡（能不能一次送很多個）
  function findLeaf(node, path) {
    if (typeof node === 'string') return isProductUrl(node) ? path : null;
    if (!node || typeof node !== 'object') return null;
    for (const k of Object.keys(node)) {
      const r = findLeaf(node[k], path.concat(Array.isArray(node) ? Number(k) : k));
      if (r) return r;
    }
    return null;
  }
  const getAt = (o, path) => path.reduce((a, k) => (a == null ? a : a[k]), o);
  const setAt = (o, path, v) => { const parent = getAt(o, path.slice(0, -1)); parent[path[path.length - 1]] = v; };
  const clone = (x) => JSON.parse(JSON.stringify(x));

  // req: { url, method, headers, body }（body 必須是 JSON 字串）；回傳範本或 null
  function learnTemplate(req, resText) {
    let body;
    try { body = JSON.parse(req.body); } catch (e) { return null; }
    if (!extractShortLinks(resText).length) return null;
    const leaf = findLeaf(body, []);
    if (!leaf) return null;
    let arrayPath = null, elemPath = null;
    for (let i = leaf.length - 1; i >= 0; i--) {          // 最近的「陣列元素」祖先
      if (typeof leaf[i] === 'number') { arrayPath = leaf.slice(0, i); elemPath = leaf.slice(i + 1); break; }
    }
    return { url: req.url, method: req.method || 'POST', headers: req.headers || {}, body, leaf, arrayPath, elemPath,
      batch: !!arrayPath, sampleUrl: getAt(body, leaf), learnedAt: Date.now() };
  }
  // 用範本組出送給後台的內容：陣列型就一次放進 urls 全部，否則只放第一個
  function buildFromTemplate(tpl, urls) {
    const body = clone(tpl.body);
    if (tpl.arrayPath) {
      const arr = getAt(body, tpl.arrayPath);
      const proto = clone(arr[0]);
      const items = urls.map((u) => { const e = clone(proto); setAt(e, tpl.elemPath, u); return e; });
      if (tpl.arrayPath.length) setAt(body, tpl.arrayPath, items);
      else return { body: JSON.stringify(items), count: urls.length };
      return { body: JSON.stringify(body), count: urls.length };
    }
    setAt(body, tpl.leaf, urls[0]);
    return { body: JSON.stringify(body), count: 1 };
  }
  // 回傳 [{url, short, err}]；送出幾個就要回幾個連結，對不起來就視為失敗（呼叫端會減量重試）
  function mapResponseToUrls(resText, urls) {
    const shorts = extractShortLinks(resText);
    if (shorts.length !== urls.length) return null;
    return urls.map((u, i) => ({ url: u, short: shorts[i], err: '' }));
  }
  const CHUNK_STEPS = [20, 10, 5, 1];

  return { SHORT_RE, findShortLink, buildBatchBody, parseBatchResponse, isProductUrl, extractShortLinks, learnTemplate,
    buildFromTemplate, mapResponseToUrls, CHUNK_STEPS };
});
