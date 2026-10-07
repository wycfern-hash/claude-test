// 情境文案：挑好的特價商品 → 每個商品 3 則「情境劇」貼文（生活小故事／對話／痛點共鳴）＋ 1 則短文，附你的分潤連結。
// 只會用「抓到的事實」：商品名稱、特價、原價、折扣、場次時間。不編造功能、功效、評價、銷量。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const STYLES = [
    ['story', '生活小故事', '一個生活片段：有時間地點、一個小困擾，看到這個特價後的小心動，結尾自然帶出商品'],
    ['dialog', '對話情境劇', '兩個人的 LINE／當面對話（用「A：」「B：」），一個問、一個分享特價，像朋友聊天'],
    ['pain', '痛點共鳴', '第一句戳中一個很多人有的日常狀況（例如「又到了該補貨的時候」），再帶出這個特價'],
  ];
  const DEFAULT_DISCLOSURE = '（以上為情境示意）※ 內含蝦皮分潤連結，經由連結購買我可能獲得佣金，不影響你的售價。';
  const money = (v) => '$' + Number(v).toLocaleString('zh-TW');

  // 把一個商品的「事實」整理成文字（給範本與 AI 都用同一份）
  function facts(r, S, nowMs) {
    const f = [`商品名稱：${r.name}`];
    if (r.price != null) f.push(`特價：${money(r.price)}`);
    if (r.original) f.push(`原價：${money(r.original)}`);
    if (r.discountPct) f.push(`折扣：${r.discountPct}%`);
    if (r.start && S) {
      const st = S.sessionStatus(r.start, r.end, nowMs);
      f.push(`限時特賣場次：${S.fmtRange(r.start, r.end)}（${S.STATE_ZH[st.state] || ''}）`);
    }
    return f.join('\n');
  }
  function saleLine(r, S, nowMs) {
    if (!r.start || !S) return '';
    const st = S.sessionStatus(r.start, r.end, nowMs);
    if (st.state === 'ended') return '';
    return st.state === 'upcoming' ? `限時特賣 ${S.fmtDateTime(r.start)} 開跑` : `限時特賣進行中，到 ${S.fmtDateTime(r.end).replace(/^.*）\s*/, '')} 結束`;
  }
  function priceLine(r) {
    const a = [];
    if (r.price != null) a.push(`特價 ${money(r.price)}`);
    if (r.original) a.push(`原價 ${money(r.original)}`);
    if (r.discountPct) a.push(`${r.discountPct}% off`);
    return a.join('｜');
  }

  function templatePosts(r, S, nowMs) {
    const n = (r.name || '這個好物').replace(/[【\[].*?[】\]]/g, '').trim().slice(0, 22) || '這個好物';
    const sale = saleLine(r, S, nowMs), price = priceLine(r);
    const tail = [price, sale].filter(Boolean).join('\n');
    const tags = '#蝦皮特價 #好物推薦 #省錢';
    return {
      posts: [
        { style: 'story', text: `週末整理家裡的時候，又發現「${n}」快用完了……\n剛好滑到特價，直接心動。\n\n${tail}\n\n你們家有沒有也快見底的東西？留言跟我說 👇\n${tags}`, comment: '想看的在這 👇' },
        { style: 'dialog', text: `A：欸你最近有沒有在買「${n}」？\nB：有啊，剛好看到特價\nA：多少？\nB：${price || '現在有打折'}${sale ? '\n' + sale : ''}\n\n你身邊也有這種「先買先贏」的朋友嗎？😂\n${tags}`, comment: '朋友問的那款在這' },
        { style: 'pain', text: `每次想補貨都在等特價對不對？\n「${n}」現在就有：\n${tail}\n\n你還在等什麼價位才下手？留言聊聊 👇\n${tags}`, comment: '連結放這 👇' },
      ],
      threads: `每次都在等特價……「${n}」現在${price || '有折扣'}。你會入手嗎？`,
    };
  }

  const PROMPT = `你是台灣臉書／Threads 的生活風格小編，幫分潤特價商品寫「情境劇」貼文：用一個小場景或小對話帶出這個特價，而不是只念價格叫賣。
請依下列商品事實，寫 3 則風格不同的貼文＋1 則 Threads 短文，輸出 JSON。
風格定義：
{styles}
硬性規定：
- 繁體中文、台灣口語，像真人朋友在聊天；每則 100~200 字，第一行是一句會讓人想繼續看的開頭（不要寫「大家好」）。
- 情境與人物是虛構示意：不要假裝是真人的使用心得，不要編造「用了三個月」「已回購」「客人都說好」之類的經驗或評價。
- 你只知道下面這些事實（商品名稱、價格、折扣、場次時間）。商品的功能、成分、規格、功效、尺寸、產地一律不要寫，除非商品名稱裡本來就有；不可寫「最低價」「全網最便宜」「限量」「秒殺」等沒有依據的說法；不得有醫療／療效宣稱。
- 價格、折扣、場次時間要寫就必須和事實完全一致；場次已結束就不要提時間。
- 貼文裡不要放任何網址（我會另外加）。結尾用一句自然的提問引導留言互動。emoji 適量（每則 0~4 個）。hashtag 3~5 個放最後一行。
- comment：留言區的一句短文（20 字內），自然地請大家看商品連結。
- threads：Threads 短文，80 字內，一個小情境＋一句結尾，不含 hashtag。
商品事實：
{facts}
JSON 格式：{"posts":[{"style":"story","text":"...","comment":"..."},{"style":"dialog",...},{"style":"pain",...}],"threads":"..."}`;

  function buildPrompt(r, S, nowMs) {
    return PROMPT.replace('{styles}', STYLES.map(([k, zh, d]) => `- ${k}（${zh}）：${d}`).join('\n')).replace('{facts}', facts(r, S, nowMs));
  }

  function parseJson(text) {
    const a = String(text).indexOf('{'), b = String(text).lastIndexOf('}');
    if (a < 0 || b < 0) throw new Error('AI 沒有回傳 JSON：' + String(text).slice(0, 80));
    return JSON.parse(String(text).slice(a, b + 1));
  }
  function normalize(s) {
    const posts = (s.posts || []).filter((p) => p && String(p.text || '').trim()).slice(0, 3)
      .map((p) => ({ style: String(p.style || ''), text: String(p.text).trim(), comment: String(p.comment || '').trim() }));
    if (!posts.length) throw new Error('AI 沒有回傳貼文內容');
    return { posts, threads: String(s.threads || '').trim() };
  }

  // 供應商不預設：使用者自己選、自己填 key 與模型
  const PROVIDERS = [
    ['gemini', 'Google Gemini', ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro']],
    ['openai', 'OpenAI', ['gpt-4.1-mini', 'gpt-4.1', 'gpt-4o-mini', 'gpt-4o']],
    ['claude', 'Anthropic Claude', ['claude-haiku-4-5-20251001', 'claude-sonnet-5-5', 'claude-opus-5-5']],
  ];
  async function callAI(cfg, prompt, fetchImpl) {
    const f = fetchImpl || fetch;
    if (!cfg.key) throw new Error('還沒填 API key');
    if (!cfg.model) throw new Error('還沒填模型名稱');
    let res, data;
    if (cfg.provider === 'gemini') {
      res = await f(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(cfg.model)}:generateContent`, {
        method: 'POST', headers: { 'content-type': 'application/json', 'x-goog-api-key': cfg.key },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { responseMimeType: 'application/json' } }),
      });
      data = await res.json();
      if (!res.ok) throw new Error('Gemini：' + ((data.error && data.error.message) || res.status));
      return parseJson(data.candidates[0].content.parts.map((p) => p.text || '').join(''));
    }
    if (cfg.provider === 'openai') {
      res = await f('https://api.openai.com/v1/chat/completions', {
        method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + cfg.key },
        body: JSON.stringify({ model: cfg.model, messages: [{ role: 'user', content: prompt }], response_format: { type: 'json_object' } }),
      });
      data = await res.json();
      if (!res.ok) throw new Error('OpenAI：' + ((data.error && data.error.message) || res.status));
      return parseJson(data.choices[0].message.content);
    }
    if (cfg.provider === 'claude') {
      res = await f('https://api.anthropic.com/v1/messages', {
        method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': cfg.key, 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' },
        body: JSON.stringify({ model: cfg.model, max_tokens: 2000, messages: [{ role: 'user', content: prompt }] }),
      });
      data = await res.json();
      if (!res.ok) throw new Error('Claude：' + ((data.error && data.error.message) || res.status));
      return parseJson(data.content.map((c) => c.text || '').join(''));
    }
    throw new Error('沒有選 AI 服務');
  }

  // 產生一個商品的文案。有選 AI 就用 AI，失敗（額度用完、key 錯等）退回範本並標記。
  async function generate(r, cfg, S, nowMs, fetchImpl) {
    if (cfg && cfg.provider) {
      try { return { ...normalize(await callAI(cfg, buildPrompt(r, S, nowMs), fetchImpl)), by: cfg.provider }; }
      catch (e) { return { ...templatePosts(r, S, nowMs), by: 'template', aiError: e.message }; }
    }
    return { ...templatePosts(r, S, nowMs), by: 'template' };
  }

  const PLACEHOLDER = '【這個商品還沒有分潤連結，請先轉成分潤連結】';
  function linkOf(r) { return (r.aff && r.aff.url) || ''; }     // 只認分潤後台轉出來的連結，絕不用一般商品網址冒充
  function compose(r, item, withLink, disclosure) {
    const parts = [item.text.trim()];
    if (withLink) parts.push('👉 商品連結：' + (linkOf(r) || PLACEHOLDER));
    if (disclosure && disclosure.trim()) parts.push(disclosure.trim());
    return parts.join('\n\n');
  }
  function composeComment(r, item) { return `${item.comment || '商品連結在這 👇'}\n${linkOf(r) || PLACEHOLDER}`; }
  const styleZh = (k) => (STYLES.find((s) => s[0] === k) || [0, k || '貼文'])[1];

  const HEAD = ['商品名稱', '風格', '貼文（含分潤連結）', '貼文（不含連結）', '留言區文字', '分潤連結', '圖片連結'];
  const q = (v) => { const s = v == null ? '' : String(v); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  function postsToCsv(entries, disclosure) {            // entries: [{row, data}]
    const lines = [HEAD.map(q).join(',')];
    for (const { row, data } of entries) {
      for (const it of data.posts) {
        lines.push([row.name, styleZh(it.style), compose(row, it, true, disclosure), compose(row, it, false, disclosure),
          composeComment(row, it), linkOf(row), row.image || ''].map(q).join(','));
      }
      if (data.threads) {
        lines.push([row.name, 'Threads 短文', data.threads + (linkOf(row) ? '\n' + linkOf(row) : ''), data.threads, '', linkOf(row), row.image || ''].map(q).join(','));
      }
    }
    return '﻿' + lines.join('\r\n') + '\r\n';
  }

  return { STYLES, PROVIDERS, DEFAULT_DISCLOSURE, facts, buildPrompt, templatePosts, parseJson, normalize, callAI, generate, linkOf, compose, composeComment, styleZh, postsToCsv, PLACEHOLDER };
});
