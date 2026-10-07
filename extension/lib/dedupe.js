// 清除重複：同一個商品被多個賣家重複上架、名稱幾乎一樣時，只留最好的那一個。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  // 名稱正規化：去掉【限時特價】[熱銷] 這類標籤、空白與標點，轉小寫
  function normName(n) {
    return String(n || '').toLowerCase()
      .replace(/[【\[（(][^】\]）)]*[】\]）)]/g, '')
      .replace(/[\s　·・,，.。!！?？~～\-_/\\|:：;；"'“”‘’+＋*＊#]+/g, '');
  }
  // 誰留下來：有分潤連結 > 折扣大 > 價格低 > 場次早
  function score(r) {
    return [r.aff && r.aff.url ? 1 : 0, r.discountPct || 0, -(r.price == null ? 1e12 : r.price), -(r.start || 9e12)];
  }
  function better(a, b) {
    const sa = score(a), sb = score(b);
    for (let i = 0; i < sa.length; i++) if (sa[i] !== sb[i]) return sa[i] > sb[i];
    return String(a.key) < String(b.key);
  }
  // mode: 'name'（名稱幾乎一樣，不分賣家）或 'nameShop'（名稱一樣且同一個賣家）
  function findDuplicates(rows, mode) {
    const groups = new Map();
    for (const r of rows) {
      const n = normName(r.name);
      if (!n) continue;
      const k = mode === 'nameShop' ? n + '|' + r.shopid : n;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(r);
    }
    const remove = [];
    let dupGroups = 0;
    for (const g of groups.values()) {
      if (g.length < 2) continue;
      dupGroups++;
      let best = g[0];
      for (const r of g) if (better(r, best)) best = r;
      for (const r of g) if (r !== best) remove.push(r);
    }
    return { groups: dupGroups, remove };
  }
  return { normName, findDuplicates };
});
