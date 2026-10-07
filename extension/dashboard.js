(() => {
  const SC = self.SC;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => (v == null ? '—' : '$' + Number(v).toLocaleString('zh-TW'));
  const S = { items: {}, sessions: {}, aff: {}, diag: [], progress: null, tab: 'flash', busy: false, stopCapture: false };

  function setMsg(text, kind = 'info') { const m = $('msg'); m.textContent = text; m.className = text ? kind : ''; }
  function setBusy(b) {
    S.busy = b;
    for (const id of ['btnFlash', 'btnSearch', 'btnShop', 'btnAff', 'btnClear', 'btnDedupe', 'btnDedupe2']) $(id).disabled = b;
  }

  // ------------------------------------------------------------ 資料
  async function load() {
    const g = await chrome.storage.local.get(['items', 'sessions', 'aff', 'diag', 'progress', 'affTemplate', 'lastRemoved']);
    S.tpl = g.affTemplate || null;
    S.removed = g.lastRemoved || null;
    S.items = g.items || {}; S.sessions = g.sessions || {}; S.aff = g.aff || {}; S.diag = g.diag || []; S.progress = g.progress || null;
    render();
  }
  let renderTimer = null;
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area !== 'local') return;
    if (ch.affTemplate) { S.tpl = ch.affTemplate.newValue || null; renderLearn(); }
    if (ch.lastRemoved) { S.removed = ch.lastRemoved.newValue || null; renderDup(); }
    for (const k of ['items', 'sessions', 'aff', 'diag', 'progress']) if (ch[k]) S[k] = ch[k].newValue || (k === 'diag' ? [] : k === 'progress' ? null : {});
    clearTimeout(renderTimer);
    renderTimer = setTimeout(render, 250);
  });

  function viewRows() {
    return Object.values(S.items).map((it) => {
      const ses = it.promotionid && S.sessions[it.promotionid];
      return { ...it, start: it.start || (ses && ses.start) || null, end: it.end || (ses && ses.end) || null, aff: S.aff[it.key] || null };
    });
  }
  function tabRows() {                      // 目前這個分頁的商品（還沒套用上面的篩選）
    const rows = viewRows();
    return S.tab === 'flash' ? rows.filter((r) => r.source === 'flash') : rows.filter((r) => r.source !== 'flash' && r.discountPct > 0);
  }
  function currentRows() {
    const now = Date.now();
    const minPct = +$('fPct').value || 0;
    const text = $('fText').value.trim().toLowerCase();
    const st = $('fState').value;
    const onlyAff = $('fAff').checked;
    let rows = tabRows();
    rows = rows.filter((r) => r.discountPct >= minPct && (!text || r.name.toLowerCase().includes(text)) && (!onlyAff || (r.aff && r.aff.url)));
    if (S.tab === 'flash' && st) rows = rows.filter((r) => SC.sessionStatus(r.start, r.end, now).state === st);
    rows.sort((a, b) => (S.tab === 'flash'
      ? ((a.start || 9e12) - (b.start || 9e12)) || (b.discountPct - a.discountPct)
      : b.discountPct - a.discountPct));
    return rows;
  }

  function affCell(r) {
    if (r.aff && r.aff.url) {
      return `<a href="${esc(r.aff.url)}" target="_blank">${esc(r.aff.url)}</a> <button class="g" data-copy="${esc(r.aff.url)}" style="padding:2px 8px;font-size:12px">複製</button>`;
    }
    if (r.aff && r.aff.state === 'fail') return `<span class="bad">轉換失敗：${esc(r.aff.err || '')}</span>`;
    return '<span class="warnx">尚未轉換（還不是分潤連結）</span>';
  }

  function renderLearn() {
    $('affLearn').innerHTML = S.tpl
      ? `分潤後台做法：<b class="good">已學會 ✓</b>（${S.tpl.batch ? '可以一次轉很多個' : '這個後台一次只能轉 1 個'}）`
      : '分潤後台做法：<b class="warnx">還沒學會</b>（會先用猜的；猜不中就一個一個轉，比較慢）';
  }
  function renderDup() {
    const n = S.removed ? Object.keys(S.removed.items || {}).length : 0;
    $('dupInfo').textContent = n ? `可以復原上次清除的 ${n} 個商品` : '';
    $('btnUndo').disabled = !n;
  }
  const SRC_ZH = { flash: '限時特賣', search: '搜尋', shop: '賣場', other: '其他' };
  function render() {
    renderLearn();
    renderDup();
    const all = viewRows();
    const rows = currentRows();
    const withAff = all.filter((r) => r.aff && r.aff.url).length;
    $('statAll').textContent = all.length; $('statShown').textContent = rows.length;
    $('statAff').textContent = withAff; $('statNoAff').textContent = all.length - withAff;
    $('progress').textContent = S.progress && Date.now() - S.progress.at < 20000 ? S.progress.text : '';
    const flash = S.tab === 'flash';
    $('stateWrap').style.display = flash ? '' : 'none';
    $('thWhen').textContent = flash ? '場次時間／狀態' : '場次';
    $('empty').style.display = rows.length ? 'none' : '';
    $('empty').textContent = all.length ? '這個條件下沒有商品，調整上面的篩選看看。'
      : (flash ? '還沒有商品。先按上面的「開始抓取限時特賣」。' : '還沒有商品。先在上面輸入關鍵字搜尋特價商品。');
    $('rows').innerHTML = rows.map((r) => `<tr>
      <td>${r.image ? `<img loading="lazy" src="${esc(r.image)}" alt="">` : ''}</td>
      <td><span class="srcTag">${SRC_ZH[r.source] || ''}</span><a href="${esc(r.url)}" target="_blank">${esc(r.name)}</a><div class="small">${esc(r.key)}</div></td>
      <td><b>${money(r.price)}</b> ${r.original ? `<s>${money(r.original)}</s>` : ''} ${r.discountPct ? `<span class="badge">-${r.discountPct}%</span>` : ''}</td>
      <td>${flash ? `${esc(SC.fmtRange(r.start, r.end))}<div class="status" data-start="${r.start || ''}" data-end="${r.end || ''}"></div>` : '—'}</td>
      <td>${r.stock == null ? '—' : r.stock} / ${r.sold == null ? '—' : r.sold}</td>
      <td>${affCell(r)}</td></tr>`).join('');
    tick();
  }
  function tick() {
    const now = Date.now();
    for (const el of document.querySelectorAll('.status')) {
      const st = SC.sessionStatus(+el.dataset.start || null, +el.dataset.end || null, now);
      el.textContent = st.label; el.className = 'status ' + st.state;
    }
  }
  setInterval(tick, 1000);

  // ------------------------------------------------------------ 開視窗 / 等頁面
  function waitComplete(tabId, timeout = 45000) {
    return new Promise((resolve, reject) => {
      const done = (fn, v) => { clearTimeout(t); chrome.tabs.onUpdated.removeListener(l); fn(v); };
      const t = setTimeout(() => done(reject, new Error('頁面載入逾時')), timeout);
      const l = (id, info) => { if (id === tabId && info.status === 'complete') done(resolve); };
      chrome.tabs.onUpdated.addListener(l);
      chrome.tabs.get(tabId).then((tab) => { if (tab.status === 'complete') done(resolve); }).catch(() => {});
    });
  }
  async function ping(tabId, tries = 24) {
    for (let i = 0; i < tries; i++) {
      try { const r = await chrome.tabs.sendMessage(tabId, { cmd: 'ping' }); if (r && r.ok) return r; } catch (e) { /* 還沒好 */ }
      await sleep(500);
    }
    throw new Error('擴充功能沒有在那個頁面啟動。請把那個視窗重新整理一次；如果剛安裝/更新擴充功能，也要重新整理蝦皮頁面。');
  }
  async function openWindow(url) {
    const w = await chrome.windows.create({ url, type: 'normal', width: 1100, height: 850, focused: true });
    const tabId = w.tabs[0].id;
    await waitComplete(tabId);
    return { winId: w.id, tabId };
  }
  // 開網址並確認真的停在預期的頁面（蝦皮可能把網址轉到首頁），是的話才往下捲。回傳 { skipped, url } 或捲動結果。
  async function onExpectedPage(tabId, expectPath) {
    await sleep(1200);                       // 給蝦皮一點時間做轉址
    const r = await ping(tabId);
    const p = new URL(r.url).pathname;
    const list = [].concat(expectPath);
    return { ok: list.some((e) => p.startsWith(e)) && !(p === '/' && !list.includes('/')), url: r.url };
  }
  async function gotoAndScroll(tabId, url, maxMs, quietMs, expectPath) {
    await chrome.tabs.update(tabId, { url });
    await waitComplete(tabId);
    await ping(tabId);
    const where = await onExpectedPage(tabId, expectPath);
    if (!where.ok) return { skipped: true, url: where.url, seen: 0 };
    return chrome.tabs.sendMessage(tabId, { cmd: 'autoscroll', maxMs, quietMs, expectPath });
  }
  const closeWin = (id) => chrome.windows.remove(id).catch(() => {});
  const base = () => SC.config.shopeeBase;

  // ------------------------------------------------------------ ① 抓限時特賣
  function flashUrl(text) {
    const t = text.trim();
    if (/^\d{6,}$/.test(t)) return `${base()}/flash_sale?promotionId=${t}`;
    if (/^https?:\/\//.test(t)) return t;
    throw new Error('請貼限時特賣的網址（例如 https://shopee.tw/flash_sale?promotionId=...）或場次 ID，一行一個');
  }
  const flashCount = async () => Object.values((await chrome.storage.local.get('items')).items || {}).filter((x) => x.source === 'flash').length;
  const promoOfUrl = (u) => { try { return new URL(u).searchParams.get('promotionId') || ''; } catch (e) { return ''; } };
  let capLines = [];
  function capLog(line) { if (line === null) capLines = []; else capLines.push(line); $('capLog').textContent = capLines.join('\n'); }
  const MAX_TOTAL_MS = 10 * 60 * 1000;
  const SKIP_HINT = '蝦皮把這個場次網址轉到別的頁面（通常是首頁）了，已跳過，沒有收首頁的資料。';

  async function captureFlash() {
    const lines = $('flashUrls').value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    if (!lines.length) throw new Error('請先貼限時特賣的網址。');
    const urls = [];
    const seenPromo = new Set();
    for (const l of lines) {                     // 同一個場次貼了兩次也只抓一次
      const u = flashUrl(l);
      const pid = promoOfUrl(u) || u;
      if (!seenPromo.has(pid)) { seenPromo.add(pid); urls.push(u); }
    }
    const extra = Math.max(0, Math.min(10, parseInt($('moreSessions').value, 10) || 0));
    S.stopCapture = false;
    await chrome.storage.local.set({ stop: false });
    setBusy(true);
    capLog(null);
    const t0 = Date.now();
    const tried = new Set(urls.map((u) => promoOfUrl(u)).filter(Boolean));   // 已「要求過」的場次：不管有沒有被轉址，絕不抓第二次
    const log = [];
    let win = null, sessionsDone = 0, skipped = 0, stopped = false;
    const record = (label, promo, r, added) => {
      log.push({ promotionid: promo, seen: r.seen || 0, added, skipped: !!r.skipped, redirectedTo: r.url || r.redirected || '', stopped: !!r.stopped });
      if (r.skipped || r.redirected) { skipped++; capLog(`⚠ ${label}：${SKIP_HINT}`); }
      else { sessionsDone++; capLog(`✓ ${label}：讀到 ${r.seen} 個商品，新增 ${added} 個`); }
      if (r.stopped) stopped = true;
    };
    try {
      setMsg('開啟限時特賣頁…會開新視窗並自動往下捲動，請不要關掉。', 'info');
      for (let i = 0; i < urls.length && !stopped && !S.stopCapture; i++) {
        const before = await flashCount();
        let r;
        if (!win) {
          win = await openWindow(urls[i]);
          await ping(win.tabId);
          const where = await onExpectedPage(win.tabId, '/flash_sale');
          r = where.ok ? await chrome.tabs.sendMessage(win.tabId, { cmd: 'autoscroll', maxMs: 100000, quietMs: SC.config.quietMs, expectPath: '/flash_sale' })
            : { skipped: true, url: where.url, seen: 0 };
        } else {
          setMsg(`讀取第 ${i + 1}/${urls.length} 個網址…（可以按「停止」）`, 'info');
          r = await gotoAndScroll(win.tabId, urls[i], 100000, SC.config.quietMs, '/flash_sale');
        }
        record(`網址 ${i + 1}（場次 ${promoOfUrl(urls[i]) || '未指定'}）`, promoOfUrl(urls[i]), r, (await flashCount()) - before);
      }
      for (let k = 0; k < extra && !stopped && !S.stopCapture; k++) {   // 實驗性：自動找後面的場次
        if (Date.now() - t0 > MAX_TOTAL_MS) { capLog('⏱ 已超過 10 分鐘，自動停止。'); break; }
        const sess = (await chrome.storage.local.get('sessions')).sessions || {};
        const next = Object.values(sess).filter((s) => !tried.has(s.promotionid) && s.end * 1000 > Date.now()).sort((a, b) => a.start - b.start)[0];
        if (!next) { capLog('（沒有更多還沒結束的場次了）'); break; }
        tried.add(next.promotionid);
        setMsg(`試著讀取後面的場次：${SC.fmtRange(next.start, next.end)}…（可以按「停止」）`, 'info');
        const before = await flashCount();
        const r = await gotoAndScroll(win.tabId, `${base()}/flash_sale?promotionId=${next.promotionid}`, 90000, SC.config.quietMs, '/flash_sale');
        record(`場次 ${SC.fmtRange(next.start, next.end)}`, next.promotionid, r, (await flashCount()) - before);
      }
      await chrome.storage.local.set({ captureLog: { at: new Date().toISOString(), sessions: log } });
      const n = await flashCount();
      const stoppedByUser = S.stopCapture || stopped;
      if (!n) {
        setMsg('沒有讀到任何限時特賣商品。可能是：頁面要求登入／驗證、網址被蝦皮轉走（場次已結束或網址不對）、或蝦皮改版。請先在 Chrome 正常打開那個網址確認看得到商品，再重試；仍不行請按「下載診斷檔」傳給我。', 'err');
      } else {
        setMsg(`${stoppedByUser ? '⏹ 已停止。' : '✅ 完成：'}讀了 ${sessionsDone} 個場次${skipped ? `（另有 ${skipped} 個被蝦皮轉走、已跳過）` : ''}，限時特賣共 ${n} 個商品。下一步按「② 轉成分潤連結」。`,
          stoppedByUser || skipped ? 'warn' : 'ok');
      }
    } catch (e) {
      setMsg('❌ ' + e.message, 'err');
    } finally {
      if (win) closeWin(win.winId);
      setBusy(false);
      load();
    }
  }

  // ------------------------------------------------------------ ① 搜尋其他特價商品
  async function captureSearch() {
    const kw = $('kw').value.trim();
    if (!kw) { setMsg('請先輸入關鍵字。', 'warn'); return; }
    const pages = +$('pages').value || 3;
    const sort = $('sort').value;
    setBusy(true);
    S.stopCapture = false;
    let win = null;
    try {
      let tabId;
      for (let p = 0; p < pages && !S.stopCapture; p++) {
        const url = `${base()}/search?keyword=${encodeURIComponent(kw)}&page=${p}&sortBy=${sort}`;
        setMsg(`搜尋「${kw}」第 ${p + 1}/${pages} 頁…（新視窗自動捲動，請不要關掉）`, 'info');
        if (!win) { win = await openWindow(url); tabId = win.tabId; await ping(tabId); await chrome.tabs.sendMessage(tabId, { cmd: 'autoscroll', maxMs: 60000, quietMs: SC.config.searchQuietMs, expectPath: '/search' }); }
        else await gotoAndScroll(tabId, url, 60000, SC.config.searchQuietMs, '/search');
      }
      const minPct = +$('minPctSearch').value || 0;
      $('fPct').value = minPct;
      S.tab = 'other'; syncTabs();
      const n = viewRows().filter((r) => r.source !== 'flash' && r.discountPct >= Math.max(minPct, 1)).length;
      setMsg(n ? `✅ 完成：找到 ${n} 個折扣 ≥ ${minPct}% 的特價商品（已排除限時特賣裡的）。` :
        `搜尋完成，但沒有折扣 ≥ ${minPct}% 的商品。可以調低折扣、換關鍵字，或多抓幾頁。`, n ? 'ok' : 'warn');
    } catch (e) {
      setMsg('❌ ' + e.message, 'err');
    } finally {
      if (win) closeWin(win.winId);
      setBusy(false);
      load();
    }
  }

  // ------------------------------------------------------------ ① 抓「賣場特價品」（賣家商店頁）
  async function pruneShopRun(runId, minPct, pathShopId) {
    const items = (await chrome.storage.local.get('items')).items || {};
    const mine = Object.values(items).filter((x) => x.shopRun === runId && x.source === 'shop');
    const cnt = {};
    for (const x of mine) cnt[x.shopid] = (cnt[x.shopid] || 0) + 1;
    const dominant = pathShopId || Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
    let kept = 0, removed = 0;
    for (const x of mine) {                  // 商店頁底下的「推薦其他賣家」、沒打折的都不要
      if (String(x.shopid) !== String(dominant) || (x.discountPct || 0) < minPct) { delete items[x.key]; removed++; } else kept++;
    }
    await chrome.storage.local.set({ items });
    return { kept, removed, shopid: dominant };
  }
  const shopRunCount = async (runId) => Object.values((await chrome.storage.local.get('items')).items || {}).filter((x) => x.shopRun === runId).length;

  async function captureShops() {
    const lines = $('shopUrls').value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    if (!lines.length) { setMsg('請先貼賣家的商店頁網址（例如 https://shopee.tw/shop/12345678）。', 'warn'); return; }
    const pages = +$('shopPages').value || 3;
    const minPct = +$('shopMinPct').value || 0;
    const targets = [];
    for (const l of lines) {
      let u;
      try { u = new URL(l); } catch (e) { setMsg('這個不是網址：' + l, 'err'); return; }
      const path = u.pathname.replace(/\/$/, '') || '/';
      if (!/shopee\.tw$/.test(u.hostname) && u.origin !== base()) { setMsg('只能貼蝦皮的網址：' + l, 'err'); return; }
      if (path === '/') { setMsg('這是蝦皮首頁，請貼某個賣家的商店頁網址（例如 https://shopee.tw/shop/12345678）。', 'warn'); return; }
      if (/^\/(product|flash_sale|search)/.test(path) || /-i\.\d+\.\d+/.test(path)) { setMsg('這看起來是商品頁／限時特賣／搜尋頁，不是賣家商店頁：' + l, 'warn'); return; }
      targets.push({ u, path, line: l, shopid: (/^\/shop\/(\d+)/.exec(path) || [])[1] || '' });
    }
    S.stopCapture = false;
    await chrome.storage.local.set({ stop: false });
    setBusy(true);
    capLog(null);
    let win = null, total = 0, removedTotal = 0, stopped = false;
    try {
      for (let t = 0; t < targets.length && !S.stopCapture; t++) {
        const { u, path, shopid } = targets[t];
        const runId = `shop${Date.now()}-${t}`;
        await chrome.storage.local.set({ arm: { path, runId, until: Date.now() + pages * 150000 + 60000 } });   // 讓內容腳本知道：這個賣家頁可以收
        const label = `賣家 ${t + 1}（${path}）`;
        for (let p = 0; p < pages && !S.stopCapture; p++) {
          u.searchParams.set('page', p);
          setMsg(`讀取${label}第 ${p + 1}/${pages} 頁…（可以按「停止」）`, 'info');
          const before = await shopRunCount(runId);
          let r;
          if (!win) {
            win = await openWindow(u.toString());
            await ping(win.tabId);
            const where = await onExpectedPage(win.tabId, [path, '/shop/']);
            r = where.ok ? await chrome.tabs.sendMessage(win.tabId, { cmd: 'autoscroll', maxMs: 60000, quietMs: SC.config.searchQuietMs, expectPath: [path, '/shop/'] })
              : { skipped: true, url: where.url, seen: 0 };
          } else {
            r = await gotoAndScroll(win.tabId, u.toString(), 60000, SC.config.searchQuietMs, [path, '/shop/']);
          }
          if (r.skipped || r.redirected) { capLog(`⚠ ${label}：蝦皮把網址轉到別的頁面（通常是首頁）了，已跳過。請確認網址是賣家商店頁。`); break; }
          if (r.stopped) { stopped = true; break; }
          const added = (await shopRunCount(runId)) - before;
          capLog(`✓ ${label} 第 ${p + 1} 頁：讀到 ${r.seen} 個商品，新增 ${added} 個`);
          if (added === 0) break;                 // 沒有新商品了，不用再往後翻頁
        }
        const res = await pruneShopRun(runId, minPct, shopid);
        total += res.kept; removedTotal += res.removed;
        capLog(`→ ${label}：留下 ${res.kept} 個折扣 ≥ ${minPct}% 的商品（排除 ${res.removed} 個沒打折或不是這個賣家的）`);
      }
      S.tab = 'other'; $('fPct').value = minPct; syncTabs();
      setMsg(total ? `${S.stopCapture || stopped ? '⏹ 已停止。' : '✅ 完成：'}共抓到 ${total} 個賣場特價品（折扣 ≥ ${minPct}%）。下一步按「② 轉成分潤連結」。`
        : '沒有抓到折扣商品。可能是：這個賣家目前沒有特價、折扣門檻太高、網址不是賣家商店頁，或需要登入／驗證。可以調低「折扣至少 %」再試，或按「下載診斷檔」傳給我。', total ? 'ok' : 'warn');
    } catch (e) {
      setMsg('❌ ' + e.message, 'err');
    } finally {
      await chrome.storage.local.remove('arm');
      if (win) closeWin(win.winId);
      setBusy(false);
      load();
    }
  }

  // ------------------------------------------------------------ 清除重複
  async function dedupe() {
    const { groups, remove } = SC.findDuplicates(tabRows(), $('dupMode').value);
    if (!remove.length) { setMsg('沒有找到重複的商品。', 'ok'); return; }
    if (!confirm(`找到 ${groups} 組重複，共 ${remove.length} 個多餘的商品。\n每一組只留 1 個（優先留已有分潤連結的、折扣最大的、價格最低的）。\n要清除嗎？（之後可以按「復原上次清除」）`)) return;
    const g = await chrome.storage.local.get(['items', 'aff']);
    const items = g.items || {}, aff = g.aff || {};
    const removed = { items: {}, aff: {} };
    for (const r of remove) {
      if (items[r.key]) { removed.items[r.key] = items[r.key]; delete items[r.key]; }
      if (aff[r.key]) { removed.aff[r.key] = aff[r.key]; delete aff[r.key]; }
    }
    await chrome.storage.local.set({ items, aff, lastRemoved: removed });
    await load();
    setMsg(`已清除 ${remove.length} 個重複的商品（${groups} 組，每組留 1 個）。`, 'ok');
  }
  async function undoDedupe() {
    const g = await chrome.storage.local.get(['items', 'aff', 'lastRemoved']);
    if (!g.lastRemoved) { setMsg('沒有可以復原的清除。', 'warn'); return; }
    await chrome.storage.local.set({ items: { ...(g.items || {}), ...g.lastRemoved.items }, aff: { ...(g.aff || {}), ...g.lastRemoved.aff } });
    await chrome.storage.local.remove('lastRemoved');
    await load();
    setMsg(`已復原 ${Object.keys(g.lastRemoved.items).length} 個商品。`, 'ok');
  }

  // ------------------------------------------------------------ ② 轉分潤連結
  async function convertAffiliate() {
    const todo = currentRows().filter((r) => !(r.aff && r.aff.url)).map((r) => ({ key: r.key, url: r.url }));
    if (!todo.length) { setMsg('目前列表的商品都已經有分潤連結了（或列表是空的）。', 'warn'); return; }
    if (!confirm(`要把目前列表中 ${todo.length} 個商品轉成分潤連結嗎？\n會開一個分潤後台視窗自動操作（每個約 1~3 秒）。`)) return;
    setBusy(true);
    await chrome.storage.local.set({ stop: false });
    let win = null;
    try {
      win = await openWindow(SC.config.affiliateBase + SC.config.customLinkPath);
      const p = await ping(win.tabId);
      if (p.loggedOut) {
        setMsg('分潤後台還沒登入。請在剛開的視窗登入蝦皮分潤，登入完成後回到這裡再按一次「轉成分潤連結」。（視窗不會自動關閉）', 'warn');
        win = null;
        return;
      }
      setMsg(`轉換中…共 ${todo.length} 個，請不要關掉那個視窗。`, 'info');
      const r = await chrome.tabs.sendMessage(win.tabId, { cmd: 'convert', items: todo });
      if (r && r.failed) {
        const probe = await chrome.tabs.sendMessage(win.tabId, { cmd: 'probe' }).catch(() => null);
        await chrome.storage.local.set({ diagAffiliate: { at: new Date().toISOString(), via: r.via, gqlErr: r.gqlErr, probe } });
      }
      const how = !r ? '' : r.via === 'learned' && r.conc > 1 ? `（這個後台一次只能轉 1 個，所以同時送出 ${r.conc} 個請求，比一個等一個快）`
        : r.via === 'learned' ? `（照你後台的做法，一次最多轉 ${r.batch} 個）`
        : r.via === 'gql' ? `（一次轉 ${r.batch} 個）`
          : '（用模擬操作一個一個轉，比較慢。建議先按「學習後台做法」手動轉 1 個，之後就能一次轉很多個）';
      setMsg(r && r.ok ? `完成${how}：成功 ${r.done} 個、失敗 ${r.failed} 個。` + (r.failed ? '失敗的會標示原因；可以再按一次重試，或下載診斷檔給我。' : '')
        : '轉換中斷：' + ((r && r.error) || '未知原因'), r && r.failed === 0 ? 'ok' : 'warn');
    } catch (e) {
      setMsg('❌ ' + e.message, 'err');
    } finally {
      if (win) closeWin(win.winId);
      setBusy(false);
      load();
    }
  }

  // ------------------------------------------------------------ ③ 匯出
  function download(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
  function exportCsv() {
    const rows = currentRows();
    if (!rows.length) { setMsg('目前列表是空的，沒有可匯出的商品。', 'warn'); return; }
    const noAff = rows.filter((r) => !(r.aff && r.aff.url)).length;
    download(`shopee_deals_${stamp()}.csv`, SC.toCsv(rows, Date.now(), SC), 'text/csv;charset=utf-8');
    setMsg(`已匯出 ${rows.length} 筆。` + (noAff ? `其中 ${noAff} 筆「尚未轉換」，CSV 的分潤連結欄是空的（不是分潤連結）。` : '全部都有分潤連結。'), noAff ? 'warn' : 'ok');
  }
  async function copyText() {
    const rows = currentRows().filter((r) => r.aff && r.aff.url);
    if (!rows.length) { setMsg('目前列表沒有已轉換的分潤連結可以複製。', 'warn'); return; }
    const now = Date.now();
    const text = rows.map((r) => {
      const st = SC.sessionStatus(r.start, r.end, now);
      return [`【${r.source === 'flash' ? '限時特賣' : '特價'}${r.discountPct ? '・-' + r.discountPct + '%' : ''}】${r.name}`,
        `特價 ${money(r.price)}${r.original ? `（原價 ${money(r.original)}）` : ''}`,
        r.start ? `場次：${SC.fmtRange(r.start, r.end)}（${SC.STATE_ZH[st.state]}）` : '', r.aff.url].filter(Boolean).join('\n');
    }).join('\n\n');
    await navigator.clipboard.writeText(text);
    setMsg(`已複製 ${rows.length} 個商品的文案（含分潤連結）。`, 'ok');
  }
  async function downloadDiag() {
    const g = await chrome.storage.local.get(['diag', 'diagAffiliate', 'sessions', 'items', 'captureLog']);
    const items = Object.values(g.items || {});
    download(`shopee_helper_diag_${stamp()}.json`, JSON.stringify({
      version: chrome.runtime.getManifest().version, userAgent: navigator.userAgent, now: new Date().toISOString(),
      itemCount: items.length, sampleItems: items.slice(0, 3), sessions: g.sessions || {},
      captured: g.diag || [], captureLog: g.captureLog || null, affiliate: g.diagAffiliate || null,
    }, null, 1), 'application/json');
    setMsg('已下載診斷檔，把它傳給我。', 'ok');
  }

  function syncTabs() { $('tabFlash').className = S.tab === 'flash' ? 'on' : ''; $('tabOther').className = S.tab === 'other' ? 'on' : ''; render(); }

  // ------------------------------------------------------------ 綁定
  $('btnFlash').addEventListener('click', () => captureFlash().catch((e) => { setMsg('❌ ' + e.message, 'err'); setBusy(false); }));
  $('btnSearch').addEventListener('click', captureSearch);
  $('btnShop').addEventListener('click', captureShops);
  $('btnDedupe').addEventListener('click', dedupe);
  $('btnDedupe2').addEventListener('click', dedupe);
  try { $('ver').textContent = '版本 ' + chrome.runtime.getManifest().version; } catch (e) {}
  $('btnUndo').addEventListener('click', undoDedupe);
  $('btnAff').addEventListener('click', convertAffiliate);
  const requestStop = async () => { S.stopCapture = true; await chrome.storage.local.set({ stop: true }); setMsg('已要求停止（幾秒內會停）。', 'warn'); };
  $('btnStop').addEventListener('click', requestStop);
  $('btnStopFlash').addEventListener('click', requestStop);
  $('btnStopShop').addEventListener('click', requestStop);
  $('btnLearn').addEventListener('click', async () => {
    await chrome.windows.create({ url: SC.config.affiliateBase + SC.config.customLinkPath, type: 'normal', width: 1100, height: 850, focused: true });
    setMsg('請在剛開的分潤後台視窗：貼一個蝦皮商品連結，按「取得連結」，看到短連結就好（不用關視窗）。回到這裡，「分潤後台做法」會變成「已學會 ✓」。', 'info');
  });
  $('btnCsv').addEventListener('click', exportCsv);
  $('btnCopy').addEventListener('click', copyText);
  $('btnDiag').addEventListener('click', downloadDiag);
  $('btnClear').addEventListener('click', async () => {
    if (!confirm('確定清空所有已抓的商品與分潤連結嗎？')) return;
    const keep = await chrome.storage.local.get('affTemplate');        // 學會的後台做法保留，不用重學
    await chrome.storage.local.clear(); if (keep.affTemplate) await chrome.storage.local.set(keep);
    await load(); setMsg('已清空（已學會的分潤後台做法會保留）。', 'ok');
  });
  $('tabFlash').addEventListener('click', () => { S.tab = 'flash'; syncTabs(); });
  $('tabOther').addEventListener('click', () => { S.tab = 'other'; syncTabs(); });
  for (const id of ['fState', 'fPct', 'fText', 'fAff']) { $(id).addEventListener('input', render); $(id).addEventListener('change', render); }
  $('rows').addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-copy]');
    if (b) { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = '已複製'; setTimeout(() => (b.textContent = '複製'), 1200); }
  });
  for (const id of ['shopUrls', 'shopPages', 'shopMinPct', 'flashUrls', 'moreSessions', 'kw', 'pages', 'sort', 'minPctSearch']) {
    try { const v = localStorage.getItem('sc_' + id); if (v != null) $(id).value = v; } catch (e) { /* ignore */ }
    $(id).addEventListener('change', () => { try { localStorage.setItem('sc_' + id, $(id).value); } catch (e) { /* ignore */ } });
  }
  load();
})();
