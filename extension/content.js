// 蝦皮頁面的橋接：收 inject.js 複製出來的 JSON → 解析 → 存進擴充功能的儲存空間；並提供「自動往下捲」。
(() => {
  const SC = self.SC;
  if (!SC || !SC.extractItems) return;
  const seen = new Set();   // 這個頁面這次看到的商品
  let lastNewAt = Date.now();
  let queue = Promise.resolve();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  window.addEventListener('message', (ev) => {
    const d = ev.data;
    if (ev.source !== window || !d || d.__sc !== 1) return;
    queue = queue.then(() => handle(d)).catch(() => {});
  });

  async function handle(d) {
    let json;
    try { json = JSON.parse(d.body); } catch (e) { return; }
    // 只收「限時特賣頁」「搜尋頁」，以及小幫手自己開來抓的「賣家商店頁」。蝦皮首頁等其他頁面的小區塊一律不收。
    const path = location.pathname;
    const onFlash = path.startsWith('/flash_sale');
    const onSearch = path.startsWith('/search');
    const arm = (await chrome.storage.local.get('arm')).arm;
    const onShop = !onFlash && !onSearch && !!arm && Date.now() < arm.until && path !== '/' &&
      (path.startsWith(arm.path) || path.startsWith('/shop/'));
    if (!onFlash && !onSearch && !onShop) return;
    if (onFlash && !/flash_sale/i.test(d.url)) return;
    if (onSearch && !/search/i.test(d.url)) return;
    const source = onFlash ? 'flash' : onSearch ? 'search' : 'shop';
    const promo = new URL(location.href).searchParams.get('promotionId') || '';
    const now = Date.now();
    const items = SC.extractItems(json, {
      source, now, promotionid: source === 'flash' ? promo : undefined,
      shopBase: (SC.config && SC.config.shopeeBase) || undefined,
    });
    const sessions = SC.extractSessions(json);
    if (source === 'shop') for (const it of items) it.shopRun = arm.runId;
    const g = await chrome.storage.local.get(['items', 'sessions', 'diag']);
    const all = g.items || {};
    const sess = g.sessions || {};
    for (const it of items) {
      all[it.key] = SC.mergeItem(all[it.key], it, Math.floor(Date.now() / 1000));
      if (!seen.has(it.key)) { seen.add(it.key); lastNewAt = Date.now(); }
    }
    for (const s of sessions) sess[s.promotionid] = { ...(sess[s.promotionid] || {}), ...s };
    // 診斷：留最近的回應樣本，之後蝦皮改版時用來修
    const diag = g.diag || [];
    diag.push({
      at: new Date().toISOString(), url: String(d.url).slice(0, 200), source,
      items: items.length, sessions: sessions.length,
      sample: items.length ? items[0] : String(d.body).slice(0, 1200),
    });
    await chrome.storage.local.set({ items: all, sessions: sess, diag: diag.slice(-40), lastCapture: Date.now() });
  }

  async function autoscroll(opt) {
    const maxMs = opt.maxMs || 120000;
    const quietMs = opt.quietMs || 7000;
    const expectPaths = [].concat(opt.expectPath || []);
    const t0 = Date.now();
    lastNewAt = Date.now();
    let stopped = false;
    while (Date.now() - t0 < maxMs) {
      if (expectPaths.length && (location.pathname === '/' || !expectPaths.some((p) => location.pathname.startsWith(p)))) { return { ok: true, seen: seen.size, redirected: location.href }; }
      if ((await chrome.storage.local.get('stop')).stop) { stopped = true; break; }
      window.scrollBy(0, Math.max(500, Math.floor(window.innerHeight * 0.85)));
      await sleep(650);
      await queue;
      chrome.storage.local.set({ progress: { text: `已讀到 ${seen.size} 個商品…`, at: Date.now() } });
      const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      if (atBottom && Date.now() - lastNewAt > quietMs) break;
    }
    await queue;
    window.scrollTo(0, 0);
    return { ok: true, seen: seen.size, stopped, seconds: Math.round((Date.now() - t0) / 1000) };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.cmd === 'ping') { sendResponse({ ok: true, url: location.href, seen: seen.size }); return; }
    if (msg && msg.cmd === 'autoscroll') { autoscroll(msg).then(sendResponse); return true; }
  });
})();
