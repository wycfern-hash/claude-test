// 匯出 CSV。欄位名稱刻意對齊「蝦皮短影音自動化」程式的匯入（商品名稱／商品連結／分潤連結／價格／圖片連結），可直接匯入。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const HEAD = ['商品名稱', '商品連結', '分潤連結', '分潤狀態', '價格', '原價', '折扣', '場次開始', '場次結束', '狀態',
    '庫存', '已售', '來源', '圖片連結', '抓取時間'];
  const esc = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  // rows：dashboard 準備好的列（已合併場次與分潤連結）；S：status.js
  function toCsv(rows, nowMs, S) {
    const SRC = { flash: '限時特賣', search: '搜尋特價', shop: '賣場特價', other: '其他' };
    const lines = [HEAD.map(esc).join(',')];
    for (const r of rows) {
      const st = S.sessionStatus(r.start, r.end, nowMs);
      const hasAff = r.aff && r.aff.url;
      const dt = (t) => (t ? S.fmtDateTime(t).replace('（', ' ').replace('）', '') : '');
      lines.push([
        r.name, r.url, hasAff ? r.aff.url : '', hasAff ? '已轉換' : '尚未轉換（不是分潤連結）',
        r.price == null ? '' : r.price, r.original == null ? '' : r.original,
        r.discountPct ? r.discountPct + '%' : '', dt(r.start), dt(r.end), S.STATE_ZH[st.state],
        r.stock == null ? '' : r.stock, r.sold == null ? '' : r.sold, SRC[r.source] || '', r.image,
        new Date(r.capturedAt || nowMs).toISOString(),
      ].map(esc).join(','));
    }
    return '﻿' + lines.join('\r\n') + '\r\n';
  }
  return { toCsv, HEAD };
});
