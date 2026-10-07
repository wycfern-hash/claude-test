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
    for (const id of ['btnFlash', 'btnSearch', 'btnAff', 'btnClear']) $(id).disabled = b;
  }

  // ------------------------------------------------------------ 資料
  async function load() {
    const g = await chrome.storage.local.get(['items', 'sessions', 'aff', 'diag', 'progress']);
    S.items = g.items || {}; S.sessions = g.sessions || {}; S.aff = g.aff || {}; S.diag = g.diag || []; S.progress = g.progress || null;
    render();
  }
  let renderTimer = null;
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area !== 'local') return;
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
  function currentRows() {
    const now = Date.now();
    const minPct = +$('fPct').value || 0;
    const text = $('fText').value.trim().toLowerCase();
    const st = $('fState').value;
    const onlyAff = $('fAff').checked;
    let rows = viewRows();
    if (S.tab === 'flash') rows = rows.filter((r) => r.source === 'flash');
    else rows = rows.filter((r) => r.source !== 'flash' && r.discountPct > 0);
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

  function render() {
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
      <td><a href="${esc(r.url)}" target="_blank">${esc(r.name)}</a><div class="small">${esc(r.key)}</div></td>
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
  async function gotoAndScroll(tabId, url, maxMs, quietMs) {
    await chrome.tabs.update(tabId, { url });
    await waitComplete(tabId);
    await ping(tabId);
    return chrome.tabs.sendMessage(tabId, { cmd: 'autoscroll', maxMs, quietMs });
  }
  const closeWin = (id) => chrome.windows.remove(id).catch(() => {});
  const base = () => SC.config.shopeeBase;

  // ------------------------------------------------------------ ① 抓限時特賣
  function flashUrl(text) {
    const t = text.trim();
    if (/^\d{6,}$/.test(t)) return `${base()}/flash_sale?promotionId=${t}`;
    if (/^https?:\/\//.test(t)) return t;
    throw new Error('請貼限時特賣的網址（例如 https://shopee.tw/flash_sale?promotionId=...）或場次 ID');
  }
  async function captureFlash() {
    const first = flashUrl($('flashUrl').value);
    const startPromo = new URL(first).searchParams.get('promotionId') || '';
    S.stopCapture = false;
    setBusy(true);
    setMsg('開啟限時特賣頁…會開新視窗並自動往下捲動，請不要關掉。', 'info');
    let win = null;
    try {
      win = await openWindow(first);
      await ping(win.tabId);
      setMsg('讀取商品中…（自動往下捲動）', 'info');
      let r = await chrome.tabs.sendMessage(win.tabId, { cmd: 'autoscroll', maxMs: 150000, quietMs: SC.config.quietMs });
      let total = r.seen, sessionsDone = 1;
      if ($('allSessions').checked) {
        const done = new Set([startPromo]);
        for (let guard = 0; guard < 12 && !S.stopCapture; guard++) {
          const sess = (await chrome.storage.local.get('sessions')).sessions || {};
          const next = Object.values(sess).filter((s) => !done.has(s.promotionid) && s.end * 1000 > Date.now()).sort((a, b) => a.start - b.start)[0];
          if (!next) break;
          done.add(next.promotionid);
          setMsg(`讀取其他場次：${SC.fmtRange(next.start, next.end)}…`, 'info');
          r = await gotoAndScroll(win.tabId, `${base()}/flash_sale?promotionId=${next.promotionid}`, 120000, SC.config.quietMs);
          total += r.seen; sessionsDone++;
        }
      }
      const n = Object.values((await chrome.storage.local.get('items')).items || {}).filter((x) => x.source === 'flash').length;
      if (!n) {
        setMsg('沒有讀到任何限時特賣商品。可能是：頁面要求登入／驗證、蝦皮改版、或網址錯誤。請先在 Chrome 正常打開那個網址確認看得到商品，再重試；仍不行請按「下載診斷檔」傳給我。', 'err');
      } else {
        setMsg(`✅ 完成：讀了 ${sessionsDone} 個場次，限時特賣共 ${n} 個商品。下一步按「② 轉成分潤連結」。`, 'ok');
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
        if (!win) { win = await openWindow(url); tabId = win.tabId; await ping(tabId); await chrome.tabs.sendMessage(tabId, { cmd: 'autoscroll', maxMs: 60000, quietMs: SC.config.searchQuietMs }); }
        else await gotoAndScroll(tabId, url, 60000, SC.config.searchQuietMs);
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
      const how = r && r.via === 'gql' ? '' : '（用模擬操作的方式，速度較慢）';
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
    const g = await chrome.storage.local.get(['diag', 'diagAffiliate', 'sessions', 'items']);
    const items = Object.values(g.items || {});
    download(`shopee_helper_diag_${stamp()}.json`, JSON.stringify({
      version: chrome.runtime.getManifest().version, userAgent: navigator.userAgent, now: new Date().toISOString(),
      itemCount: items.length, sampleItems: items.slice(0, 3), sessions: g.sessions || {},
      captured: g.diag || [], affiliate: g.diagAffiliate || null,
    }, null, 1), 'application/json');
    setMsg('已下載診斷檔，把它傳給我。', 'ok');
  }

  function syncTabs() { $('tabFlash').className = S.tab === 'flash' ? 'on' : ''; $('tabOther').className = S.tab === 'other' ? 'on' : ''; render(); }

  // ------------------------------------------------------------ 綁定
  $('btnFlash').addEventListener('click', () => captureFlash().catch((e) => { setMsg('❌ ' + e.message, 'err'); setBusy(false); }));
  $('btnSearch').addEventListener('click', captureSearch);
  $('btnAff').addEventListener('click', convertAffiliate);
  $('btnStop').addEventListener('click', async () => { S.stopCapture = true; await chrome.storage.local.set({ stop: true }); setMsg('已要求停止（做完手上這個就會停）。', 'warn'); });
  $('btnCsv').addEventListener('click', exportCsv);
  $('btnCopy').addEventListener('click', copyText);
  $('btnDiag').addEventListener('click', downloadDiag);
  $('btnClear').addEventListener('click', async () => {
    if (!confirm('確定清空所有已抓的商品與分潤連結嗎？')) return;
    await chrome.storage.local.clear(); await load(); setMsg('已清空。', 'ok');
  });
  $('tabFlash').addEventListener('click', () => { S.tab = 'flash'; syncTabs(); });
  $('tabOther').addEventListener('click', () => { S.tab = 'other'; syncTabs(); });
  for (const id of ['fState', 'fPct', 'fText', 'fAff']) { $(id).addEventListener('input', render); $(id).addEventListener('change', render); }
  $('rows').addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-copy]');
    if (b) { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = '已複製'; setTimeout(() => (b.textContent = '複製'), 1200); }
  });
  for (const id of ['flashUrl', 'kw', 'pages', 'sort', 'minPctSearch']) {
    try { const v = localStorage.getItem('sc_' + id); if (v != null) $(id).value = v; } catch (e) { /* ignore */ }
    $(id).addEventListener('change', () => { try { localStorage.setItem('sc_' + id, $(id).value); } catch (e) { /* ignore */ } });
  }
  load();
})();
