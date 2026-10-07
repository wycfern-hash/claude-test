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

  // 你手動轉 1 個連結時，記下後台真正的請求格式（見 inject_affiliate.js）
  window.addEventListener('message', async (ev) => {
    const d = ev.data;
    if (ev.source !== window || !d || d.__scAff !== 1) return;
    const tpl = SC.learnTemplate(d.req, d.res);
    if (tpl) await chrome.storage.local.set({ affTemplate: tpl });
  });

  // 用學到的格式送出（一次放很多個）。回傳 { results, size }；整批被拒就丟錯，呼叫端會減量重試。
  async function viaTemplate(tpl, urls) {
    const { body, count } = SC.buildFromTemplate(tpl, urls);
    const headers = { ...(tpl.headers || {}) };
    const csrf = (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1];
    if (csrf && !headers['csrf-token'] && 'csrf-token' in (tpl.headers || {})) headers['csrf-token'] = csrf;
    const r = await fetch(tpl.url, { method: tpl.method || 'POST', credentials: 'include', headers, body });
    const text = await r.text();
    if (!r.ok) throw new Error('後台回應 ' + r.status);
    if (count < urls.length) throw new Error('這個後台一次只能轉 1 個');
    if (urls.length === 1) {                       // 單筆：沒回短連結只算這一筆失敗，不要讓整批降級
      const sh = SC.extractShortLinks(text)[0];
      return [{ url: urls[0], short: sh || null, err: sh ? '' : '後台沒有回傳短連結（這個商品可能不能轉）' }];
    }
    const results = SC.mapResponseToUrls(text, urls);
    if (!results) throw new Error('後台回傳的連結數量對不上');
    return results;
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

  async function saveResults(part, results) {
    let ok = 0, bad = 0;
    for (let j = 0; j < results.length; j++) {
      const r = results[j];
      if (r.short) { ok++; await setAff(part[j].key, { url: r.short, state: 'ok', at: Date.now() }); }
      else { bad++; await setAff(part[j].key, { url: '', state: 'fail', err: r.err, at: Date.now() }); }
    }
    return { ok, bad };
  }

  async function convert(items) {
    const tpl = (await chrome.storage.local.get('affTemplate')).affTemplate || null;
    let method = tpl ? 'learned' : 'gql';          // learned：照你手動轉時學到的格式；gql：我猜的格式；dom：模擬手動操作
    let steps = tpl && tpl.batch ? SC.CHUNK_STEPS.slice() : [1];
    let size = tpl ? steps[0] : 5, gqlErr = '', done = 0, failed = 0, biggest = 0, i = 0;
    while (i < items.length) {
      if (await stopped()) break;
      const part = items.slice(i, i + (method === 'dom' ? 1 : size));
      const urls = part.map((x) => x.url);
      let results = null;
      if (method === 'dom') {
        const it = part[0];
        let r;
        try { const sh = await viaDom(it.url); r = { url: it.url, short: sh, err: sh ? '' : '頁面上沒有出現短連結' }; }
        catch (e) { r = { url: it.url, short: null, err: e.message }; }
        results = [r];
        await sleep(rand(1200, 2600));
      } else {
        try {
          results = method === 'learned' ? await viaTemplate(tpl, urls) : await viaGql(urls);
        } catch (e) {
          gqlErr = e.message;
          if (method === 'learned' && size > 1) {                           // 整批被拒：減量再試（20→10→5→1）
            steps = steps.filter((n) => n < size); size = steps[0] || 1;
            continue;
          }
          method = 'dom';                                                   // 都不行：最後才一個一個模擬操作
          continue;
        }
      }
      const { ok, bad } = await saveResults(part, results);
      done += ok; failed += bad; i += part.length; biggest = Math.max(biggest, part.length);
      chrome.storage.local.set({ progress: { text: `轉換分潤連結 ${done + failed}/${items.length}（成功 ${done}、失敗 ${failed}）`, at: Date.now() } });
      await sleep(rand(600, 1400));
    }
    return { ok: true, done, failed, via: method, gqlErr, batch: biggest };
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
