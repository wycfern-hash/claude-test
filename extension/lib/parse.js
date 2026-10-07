// 從蝦皮網頁自己載入的 JSON 裡找出商品與場次。不依賴固定欄位位置：
// 遞迴找「有 itemid + shopid + 名稱」的物件，所以限時特賣、搜尋結果、新舊格式都能讀。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const IMG_BASE = 'https://down-tw.img.susercontent.com/file/';
  const SHOP_BASE = 'https://shopee.tw';

  const num = (v) => {
    if (typeof v === 'number' && isFinite(v)) return v;
    if (typeof v === 'string' && v.trim() !== '' && isFinite(+v)) return +v;
    return null;
  };
  const pick = (...vals) => vals.find((v) => v !== undefined && v !== null && v !== '');

  // 蝦皮 API 的金額都乘了 100000（399 元 = 39900000）
  const money = (v) => {
    const n = num(v);
    if (n == null) return null;
    return n >= 100000 ? Math.round(n / 1000) / 100 : n;
  };
  const pctOf = (v) => {
    if (v == null) return null;
    if (typeof v === 'string') {
      const m = /(-?\d+(?:\.\d+)?)\s*%/.exec(v);
      if (m) return Math.abs(Math.round(+m[1]));
    }
    const n = num(v);
    return n == null ? null : Math.abs(Math.round(n));
  };
  // 時間戳：秒或毫秒都收，統一成秒；不像時間戳的數字回傳 null
  const tsSec = (v) => {
    const n = num(v);
    if (n == null) return null;
    if (n > 1e12) return Math.round(n / 1000);
    return n >= 1e9 ? n : null;
  };

  function walk(node, fn, depth = 0) {
    if (!node || typeof node !== 'object' || depth > 14) return;
    if (Array.isArray(node)) { for (const x of node) walk(x, fn, depth + 1); return; }
    if (fn(node) === false) return;
    for (const k of Object.keys(node)) walk(node[k], fn, depth + 1);
  }

  function normalizeItem(o, ctx = {}) {
    const asset = o.item_card_displayed_asset || {};
    const disp = o.item_card_display_price || {};
    const fs = o.flash_sale && typeof o.flash_sale === 'object' ? o.flash_sale : {};
    const name = pick(o.name, asset.name, o.title);
    if (!name || typeof name !== 'string') return null;
    const itemid = String(o.itemid);
    const shopid = String(o.shopid);
    const price = money(pick(o.price, disp.price, o.price_min, o.min_price));
    let original = money(pick(o.price_before_discount, disp.strikethrough_price, o.original_price));
    if (!original || (price != null && original <= price)) original = null;
    let pct = pctOf(pick(o.raw_discount, o.discount, disp.discount));
    if (price != null && original) pct = Math.round((1 - price / original) * 100);
    const img = pick(o.image, asset.image, Array.isArray(o.images) ? o.images[0] : undefined);
    const promo = pick(o.promotionid, fs.promotionid, ctx.promotionid);
    return {
      key: shopid + '.' + itemid,
      itemid, shopid, name,
      image: img ? (/^https?:/.test(img) ? img : IMG_BASE + img) : '',
      price, original,
      discountPct: pct == null ? 0 : pct,
      stock: num(pick(o.flash_sale_stock, o.stock)),
      sold: num(pick(o.sold, o.historical_sold, o.flash_sale_sold)),
      promotionid: promo != null ? String(promo) : '',
      start: tsSec(pick(o.start_time, fs.start_time, ctx.start)),
      end: tsSec(pick(o.end_time, fs.end_time, ctx.end)),
      source: ctx.source || 'other',
      url: (ctx.shopBase || SHOP_BASE) + '/product/' + shopid + '/' + itemid,
      capturedAt: ctx.now || Date.now(),
    };
  }

  function extractItems(json, ctx = {}) {
    const out = new Map();
    walk(json, (o) => {
      if (num(o.itemid) != null && num(o.shopid) != null) {
        const it = normalizeItem(o, ctx);
        if (it) { if (!out.has(it.key)) out.set(it.key, it); return false; }
      }
      return true;
    });
    return [...out.values()];
  }

  function extractSessions(json) {
    const out = new Map();
    walk(json, (o) => {
      if (o.promotionid != null && o.itemid == null) {
        const start = tsSec(pick(o.start_time, o.startTime));
        const end = tsSec(pick(o.end_time, o.endTime));
        if (start && end) {
          out.set(String(o.promotionid), {
            promotionid: String(o.promotionid), start, end,
            name: String(pick(o.name, o.session_name, o.description, '') || ''),
          });
        }
      }
      return true;
    });
    return [...out.values()];
  }

  function classify(url) {
    if (/flash_sale/i.test(url)) return 'flash';
    if (/search/i.test(url)) return 'search';
    return 'other';
  }

  // 合併同一個商品的新舊資料。同一個商品出現在多個場次時，留「還沒結束、最早開始」的那個場次（都結束了就留最晚的）。
  function mergeItem(old, neu, nowSec) {
    if (!old) return neu;
    const m = { ...old };
    for (const [k, v] of Object.entries(neu)) if (v !== null && v !== '' && v !== undefined) m[k] = v;
    if (old.source === 'flash' || neu.source === 'flash') m.source = 'flash';
    if (old.shopRun && !neu.shopRun) m.shopRun = old.shopRun;
    const cand = [old, neu].filter((x) => x.start && x.end);
    if (cand.length === 2 && old.promotionid !== neu.promotionid) {
      const live = cand.filter((x) => x.end > nowSec).sort((a, b) => a.start - b.start);
      const pick = live[0] || cand.sort((a, b) => b.end - a.end)[0];
      Object.assign(m, { promotionid: pick.promotionid, start: pick.start, end: pick.end });
    }
    return m;
  }

  return { num, money, pctOf, tsSec, walk, normalizeItem, extractItems, extractSessions, classify, mergeItem, IMG_BASE };
});
