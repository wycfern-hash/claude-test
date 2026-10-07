// 分潤後台頁面：把商品連結轉成分潤短連結。優先用後台自己的批次轉換；失敗就改成模擬在「自訂連結」頁貼連結、按按鈕。
(() => {
  const SC = self.SC;
  if (!SC || !SC.buildBatchBody) return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);

  async function setAff(key, val) {
    const g = await chrome.storage.local.get('aff');
    const aff = g.aff || {};
    aff[key] = val;
    await chrome.storage.local.set({ aff });
  }
  const stopped = async () => !!((await chrome.storage.local.get('stop')).stop);

  async function viaGql(urls) {
    const csrf = (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1];
    const headers = { 'content-type': 'application/json; charset=UTF-8', 'affiliate-program-type': '1' };
    if (csrf) headers['csrf-token'] = csrf;
    const r = await fetch('/api/v3/gql?q=batchCustomLink', {
      method: 'POST', credentials: 'include', headers, body: JSON.stringify(SC.buildBatchBody(urls)),
    });
    if (!r.ok) throw new Error('後台回應 ' + r.status);
    const out = SC.parseBatchResponse(await r.json(), urls);
    if (!out) throw new Error('後台回應格式不符');
    return out;
  }

  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  function setValue(el, v) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function pageLinks() {
    const texts = [document.body.innerText || ''];
    for (const el of document.querySelectorAll('input,textarea')) texts.push(el.value || '');
    return new Set(texts.map((t) => SC.findShortLink(t)).filter(Boolean));
  }
  const seenShort = new Set(pageLinks());

  async function viaDom(url) {
    const fields = [...document.querySelectorAll('textarea, input[type="text"], input:not([type])')].filter(visible);
    const field = fields.find((f) => /連結|網址|link|url/i.test((f.placeholder || '') + (f.getAttribute('aria-label') || ''))) || fields[0];
    if (!field) throw new Error('找不到貼連結的欄位（請確認停在「自訂連結」頁）');
    setValue(field, '');
    setValue(field, url);
    await sleep(400);
    const btn = [...document.querySelectorAll('button,[role="button"]')].filter(visible)
      .find((b) => /取得連結|獲取連結|產生連結|生成連結|取得推廣連結|Get link|Generate/i.test(b.innerText || ''));
    if (!btn) throw new Error('找不到「取得連結」按鈕');
    btn.click();
    for (let i = 0; i < 24; i++) {
      await sleep(500);
      for (const s of pageLinks()) if (!seenShort.has(s)) { seenShort.add(s); return s; }
    }
    return null;
  }

  async function convert(items) {
    let via = 'gql', gqlErr = '', done = 0, failed = 0;
    const CHUNK = 5;
    for (let i = 0; i < items.length; i += CHUNK) {
      if (await stopped()) break;
      const part = items.slice(i, i + CHUNK);
      let results = null;
      if (via === 'gql') {
        try { results = await viaGql(part.map((x) => x.url)); } catch (e) { via = 'dom'; gqlErr = e.message; }
      }
      if (!results) {
        results = [];
        for (const it of part) {
          if (await stopped()) break;
          try { const s = await viaDom(it.url); results.push({ url: it.url, short: s, err: s ? '' : '頁面上沒有出現短連結' }); }
          catch (e) { results.push({ url: it.url, short: null, err: e.message }); }
          await sleep(rand(1200, 2600));
        }
      }
      for (let j = 0; j < results.length; j++) {
        const r = results[j];
        if (r.short) { done++; await setAff(part[j].key, { url: r.short, state: 'ok', at: Date.now() }); }
        else { failed++; await setAff(part[j].key, { url: '', state: 'fail', err: r.err, at: Date.now() }); }
      }
      chrome.storage.local.set({ progress: { text: `轉換分潤連結 ${done + failed}/${items.length}（成功 ${done}、失敗 ${failed}）`, at: Date.now() } });
      await sleep(rand(700, 1500));
    }
    return { ok: true, done, failed, via, gqlErr };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg) return;
    if (msg.cmd === 'ping') {
      sendResponse({ ok: true, url: location.href, loggedOut: /login/i.test(location.href) });
      return;
    }
    if (msg.cmd === 'convert') { convert(msg.items || []).then(sendResponse).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
    if (msg.cmd === 'probe') {
      sendResponse({
        url: location.href,
        inputs: [...document.querySelectorAll('input,textarea')].filter(visible).map((e) => ({ tag: e.tagName, type: e.type, placeholder: e.placeholder })),
        buttons: [...document.querySelectorAll('button,[role="button"]')].filter(visible).map((b) => (b.innerText || '').trim().slice(0, 30)),
      });
    }
  });
})();
