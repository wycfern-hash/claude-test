// 在 Threads／Facebook 頁面裡，用「你自己登入的帳號」幫你填文章、貼連結留言、搜尋、回覆。
// 小幫手畫面（dashboard）傳訊息過來，這裡照做；任何一步找不到按鈕就停下，回報卡在哪一步＋畫面上的按鈕清單（診斷用）。
(() => {
  const SC = self.SC;
  if (!SC || !SC.LABELS) return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isVisible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const rx = (p) => new RegExp(p, 'i');
  const fail = (stage, msg) => { const e = new Error(msg || `找不到「${stage}」`); e.stage = stage; return e; };

  async function waitFor(fn, ms = 15000) {
    const t0 = Date.now();
    for (;;) {
      const v = fn();
      if (v && (!Array.isArray(v) || v.length)) return v;
      if (Date.now() - t0 > ms) return null;
      await sleep(250);
    }
  }
  // 找「文字（或 aria-label）符合」的可見元素，只取最內層的那個
  function byText(re, scope = document, sel = 'button,[role=button],[role=menuitem],a,div,span') {
    const hit = [...scope.querySelectorAll(sel)].filter(isVisible).filter((e) => {
      const t = (e.innerText || e.getAttribute('aria-label') || '').trim();
      return t && t.length < 80 && re.test(t);
    });
    return hit.filter((e) => !hit.some((o) => o !== e && e.contains(o)));
  }
  const textboxes = (scope = document) => [...scope.querySelectorAll('[role=textbox],[contenteditable=true],textarea')].filter(isVisible);

  async function clickText(stage, re, opt = {}) {
    const els = await waitFor(() => byText(re, opt.scope), opt.ms || 15000);
    if (!els) { if (opt.optional) return false; throw fail(stage); }
    (opt.last ? els[els.length - 1] : els[0]).click();
    await sleep(opt.after == null ? 700 : opt.after);
    return true;
  }
  async function typeInto(stage, el, text) {
    if (!el) throw fail(stage);
    el.focus();
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      el.value = text; el.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      const lines = String(text).split('\n');
      lines.forEach((ln, i) => {
        if (ln) document.execCommand('insertText', false, ln);
        if (i < lines.length - 1) document.execCommand('insertParagraph');
      });
    }
    await sleep(500);
  }
  const pressEnter = (el) => {
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }
  };

  // 畫面上的按鈕／輸入框清單：卡住時放進診斷檔，改 lib/social.js 的文字就靠這個
  function dump() {
    const els = [...document.querySelectorAll('button,[role=button],a[href],[role=textbox],[contenteditable=true],textarea,input')].filter(isVisible).slice(0, 120);
    return {
      url: location.href,
      elements: els.map((e) => ({ tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '', text: (e.innerText || e.value || '').trim().slice(0, 40), aria: e.getAttribute('aria-label') || '' })),
    };
  }
  function checkLogin(L, platform) {
    if (/\/login|\/accounts\/login/.test(location.pathname)) throw fail('登入', `尚未登入 ${platform}：請先在這個 Chrome 登入再試`);
  }

  async function threadsPost(m) {
    const L = SC.LABELS.threads;
    checkLogin(L, 'Threads');
    await clickText('開啟發文框', rx(L.composerOpen));
    let boxes = await waitFor(() => textboxes());
    if (!boxes) throw fail('填文字', '找不到輸入框（可能還沒登入）');
    await typeInto('填文字', boxes[boxes.length - 1], m.text);
    if (m.comment) {                                    // 連結放在串文的第二則（自己的貼文下面）
      const n = boxes.length;
      await clickText('新增串文', rx(L.addToThread));
      const b2 = await waitFor(() => { const t = textboxes(); return t.length > n ? t : null; });
      if (!b2) throw fail('新增串文', '點了「新增到串文」但沒有出現第二個輸入框');
      await typeInto('填連結留言', b2[b2.length - 1], m.comment);
    }
    if (m.preview) return { ok: true, published: false };
    await clickText('發佈', rx(L.post), { last: true, after: 2500 });
    return { ok: true, published: true };
  }

  async function facebookPost(m) {
    const L = SC.LABELS.facebook;
    checkLogin(L, 'Facebook');
    await clickText('開啟發文框', rx(L.composerOpen));
    const dlg = await waitFor(() => [...document.querySelectorAll('[role=dialog]')].filter(isVisible)[0] || null, 8000);
    const scope = dlg || document;
    const boxes = await waitFor(() => textboxes(scope));
    if (!boxes) throw fail('填文字', '找不到輸入框（可能還沒登入，或這個帳號沒有粉絲專頁的發文權限）');
    await typeInto('填文字', boxes[0], m.text);
    await clickText('下一步', rx(L.next), { scope, optional: true, ms: 2500 });
    if (m.preview) return { ok: true, published: false };
    await clickText('發佈', rx(L.post), { scope, last: true, after: 4000 });
    if (m.comment) {                                    // 發完後新貼文在動態最上面，在它下面留言放連結
      const art = await waitFor(() => [...document.querySelectorAll('[role=article]')].filter(isVisible)[0] || null, 10000);
      if (!art) throw fail('留言放連結', '找不到剛發出的貼文');
      const box = await waitFor(() => textboxes(art).find((b) => rx(L.commentBox).test(b.getAttribute('aria-label') || b.innerText || '')) || textboxes(art)[0]);
      if (!box) throw fail('留言放連結', '找不到留言輸入框');
      await typeInto('留言放連結', box, m.comment);
      pressEnter(box);
      await sleep(2000);
    }
    return { ok: true, published: true };
  }

  async function search() {
    const L = SC.LABELS.threads;
    checkLogin(L, 'Threads');
    await sleep(3000);
    for (let i = 0; i < 2; i++) { window.scrollBy(0, 2500); await sleep(900); }
    const seen = new Set(), out = [];
    for (const e of document.querySelectorAll(L.item)) {
      const a = e.querySelector(L.link);
      const text = [...e.querySelectorAll(L.text)].map((x) => x.innerText.trim()).filter(Boolean).join(' ');
      if (!a || !text || seen.has(a.href)) continue;
      seen.add(a.href);
      out.push({ url: a.href, author: ((e.querySelector(L.author) || {}).innerText || '').trim(), text });
    }
    if (!out.length) throw fail('搜尋', '沒有讀到任何貼文（可能沒登入，或畫面格式不同）');
    return { ok: true, results: out.slice(0, 20) };
  }

  async function reply(m) {
    const L = SC.LABELS.threads;
    checkLogin(L, 'Threads');
    await sleep(2500);
    await clickText('點回覆', rx(L.reply));
    const boxes = await waitFor(() => textboxes());
    if (!boxes) throw fail('填回覆', '找不到回覆輸入框');
    await typeInto('填回覆', boxes[boxes.length - 1], m.text);
    await clickText('送出回覆', rx(L.replySend), { last: true, after: 2000 });
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, send) => {
    if (!msg || typeof msg.cmd !== 'string' || !msg.cmd.startsWith('social:')) return;
    if (msg.cmd === 'social:ping') { send({ ok: true }); return; }
    const fn = { 'social:threads-post': threadsPost, 'social:facebook-post': facebookPost, 'social:search': search, 'social:reply': reply }[msg.cmd];
    if (!fn) return;
    fn(msg).then(send).catch((e) => send({ ok: false, stage: e.stage || '', error: String(e.message || e), dump: dump() }));
    return true;
  });
})();
