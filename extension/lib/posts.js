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

  const hashOf = (str) => { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; };
  // 範本（不用 AI）：幽默吐槽風，每個商品依名稱挑不同的哏，只用抓到的價格／折扣／場次，不編造功能或心得
  function templatePosts(r, S, nowMs) {
    const n = (r.name || '這個好物').replace(/[【\[].*?[】\]]/g, '').replace(/[🔥⭐✨💥❗]/g, '').trim().slice(0, 18) || '這個好物';
    const sale = saleLine(r, S, nowMs);
    const priceTxt = r.price != null ? money(r.price) : '特價';
    const pctTxt = r.discountPct ? `${r.discountPct}% off` : '在特價';
    const facts = priceLine(r);
    const foot = [facts, sale].filter(Boolean).join('\n');
    const h = hashOf(r.name);
    const pick = (arr) => arr[h % arr.length];
    const tags = '#蝦皮特價 #手滑 #省錢';
    const story = pick([
      `我：這個月要存錢，不亂買。\n蝦皮：「${n}」${priceTxt}。\n我：……\n（手已經在結帳）\n\n${foot}`,
      `朋友問我最近有沒有存到錢。\n我看了一眼購物車裡的「${n}」，默默把手機蓋起來。\n\n${foot}`,
      `錢包：拜託不要。\n眼睛：看到「${n}」${pctTxt}。\n手指：我來處理。\n\n${foot}`,
    ]);
    const dialog = pick([
      `A：你又買東西了？\nB：沒有，是它自己跑進購物車的。\nA：什麼東西？\nB：「${n}」，${pctTxt}，我能怎麼辦。\n\n${foot}`,
      `A：這個真的需要嗎？\nB：現在 ${priceTxt}，需要。\nA：你上次也這樣說。\nB：這次是真的（大概）。\n\n「${n}」\n${foot}`,
      `客服：請問還有其他需要嗎？\n我：沒有了。\n（五分鐘後）\n我：「${n}」${priceTxt}，可以加購嗎。\n\n${foot}`,
    ]);
    const pain = pick([
      `${r.original ? '原價 ' + money(r.original) + '，' : ''}現在 ${priceTxt}。\n理智：「${n}」不是必需品。\n手指：已經按下去了。\n\n${sale || facts}`,
      `大人的三大謊言：\n1. 我馬上睡\n2. 我只是看看\n3. 這個特價我不買\n\n（「${n}」${priceTxt}，第三個先破功）\n${sale}`.trim(),
      `今日省錢小技巧：不要滑到「${n}」。\n我：已經滑到了。\n\n${foot}`,
    ]);
    const cm = pick(['想一起手滑的在這 👇', '連結放這，後果自負 😂', '要買的話在這 👇']);
    return {
      posts: [
        { style: 'story', text: `${story}\n\n${tags}`, comment: cm },
        { style: 'dialog', text: `${dialog}\n\n${tags}`, comment: cm },
        { style: 'pain', text: `${pain}\n\n${tags}`, comment: cm },
      ],
      threads: pick([`「${n}」${priceTxt}。我的理智已下線。`, `說好不買的，然後「${n}」${pctTxt}。`, `我的荷包：「${n}」${priceTxt}。我：好。`]),
    };
  }

  const PROMPT = `你是台灣 Threads／臉書上很會寫貼文的幽默小編。幫分潤特價商品寫「情境劇」貼文：用一個好笑、有點自嘲、讓人會心一笑的小場景或小對話，帶出這個特價，而不是廣告口吻。
目標：滑到的人會停下來看完、甚至想留言「我也是」。請輸出 JSON。
風格定義（三則的哏要完全不同）：
{styles}
寫法要求：
- 繁體中文、台灣口語、像朋友吐槽；短、有節奏、有反差或自嘲（例如「說好不買的」「錢包：拜託不要」「手已經在結帳」那種），結尾要有個小梗收尾（不要用「你們有沒有類似的經驗？留言告訴我」這種制式問句）。
- 每則 50~120 字；第一行就要有梗或有畫面，不要寫「大家好」「週末整理家裡」這類刻意的開場。不要像在念商品規格。
- 三則的開頭、結構、梗都不要一樣；不要三則都用同一個句型。emoji 最多 0~2 個，hashtag 2~3 個放最後一行。
硬性規定：
- 情境與人物是虛構示意：不要假裝是真人的使用心得，不要編造「用了三個月」「已回購」「客人都說好」之類的經驗或評價。
- 你只知道下面這些事實（商品名稱、價格、折扣、場次時間）。商品的功能、成分、規格、功效、尺寸、產地一律不要寫，除非商品名稱裡本來就有；不可寫「最低價」「全網最便宜」「限量」「秒殺」等沒有依據的說法；不得有醫療／療效宣稱（商品是口罩、保健品等也不能說有什麼效果）。
- 價格、折扣、場次時間要寫就必須和事實完全一致；場次已結束就不要提時間。
- 貼文裡不要放任何網址（我會另外加）。
- comment：留言區的一句短文（20 字內），輕鬆有趣地請大家看連結。
- threads：Threads 短文，60 字內，一個梗，不含 hashtag。
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
    ['gemini', 'Google Gemini', ['gemini-3-flash-preview', 'gemini-3.1-flash-lite', 'gemini-3.1-pro-preview']],
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

  // 向該家服務查「你的 key 現在能用哪些模型」（模型會改版/下架，清單比我寫死的準）
  async function listModels(cfg, fetchImpl) {
    const f = fetchImpl || fetch;
    if (!cfg.key) throw new Error('請先填 API key');
    if (cfg.provider === 'gemini') {
      const res = await f('https://generativelanguage.googleapis.com/v1beta/models?pageSize=200', { headers: { 'x-goog-api-key': cfg.key } });
      const d = await res.json();
      if (!res.ok) throw new Error('Gemini：' + ((d.error && d.error.message) || res.status));
      return (d.models || []).filter((m) => (m.supportedGenerationMethods || []).includes('generateContent')).map((m) => m.name.replace(/^models\//, ''));
    }
    if (cfg.provider === 'openai') {
      const res = await f('https://api.openai.com/v1/models', { headers: { authorization: 'Bearer ' + cfg.key } });
      const d = await res.json();
      if (!res.ok) throw new Error('OpenAI：' + ((d.error && d.error.message) || res.status));
      return (d.data || []).map((m) => m.id).filter((id) => /^(gpt|o\d|chatgpt)/.test(id));
    }
    if (cfg.provider === 'claude') {
      const res = await f('https://api.anthropic.com/v1/models?limit=100', { headers: { 'x-api-key': cfg.key, 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' } });
      const d = await res.json();
      if (!res.ok) throw new Error('Claude：' + ((d.error && d.error.message) || res.status));
      return (d.data || []).map((m) => m.id);
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

  // Threads「搜尋後回覆」的草稿：有回應到對方內容的短回覆，不放連結、不推銷。失敗或沒選 AI 回傳空字串（自己寫）
  function buildReplyPrompt(text, keyword, topic) {
    return '你是台灣 Threads 的一般使用者，要回覆下面這篇貼文。請寫一則自然、友善、有點幽默、有回應到貼文內容的繁體中文口語回覆（50 字內）。'
      + '規定：不要放任何網址或商品連結、不要推銷或提到購買、不要編造個人經驗細節；沒有話可說就回空字串。\n'
      + (topic ? `我平常會聊的話題（只是讓你抓方向，回覆不要提到購買、不要推銷）：${topic}\n` : '')
      + `搜尋關鍵字：${keyword}\n貼文：${String(text).slice(0, 300)}\n輸出 JSON：{"reply":"..."}`;
  }
  async function draftReply(cfg, text, keyword, fetchImpl, topic) {
    if (!cfg || !cfg.provider) return '';
    try { const s = await callAI(cfg, buildReplyPrompt(text, keyword, topic), fetchImpl); return String(s.reply || '').trim(); } catch (e) { return ''; }
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

  return { STYLES, PROVIDERS, DEFAULT_DISCLOSURE, facts, buildPrompt, templatePosts, parseJson, normalize, callAI, listModels, buildReplyPrompt, draftReply, generate, linkOf, compose, composeComment, styleZh, postsToCsv, PLACEHOLDER };
});
