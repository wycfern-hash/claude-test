// Threads／Facebook 的按鈕文字（正規表示式，| 代表「或」）。這些是我沒看過真實畫面時依一般介面猜的預設值，尚未在真站驗證；
// 猜錯時，小幫手會告訴你卡在哪一步，並把畫面上的按鈕清單放進「下載診斷檔」，據此改這裡的文字即可。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const LABELS = {
    threads: {
      loginMarkers: '登入|Log in|使用 Instagram 帳號繼續|Continue with Instagram',
      composerOpen: "有什麼新鮮事|What's new|開始發起串文|Start a thread",
      addToThread: '新增到串文|加入串文|Add to thread',
      post: '^發佈$|^Post$|^發布$',
      reply: '回覆|Reply',
      replySend: '^回覆$|^Reply$|^發佈$|^Post$',
      // 搜尋結果頁每篇貼文的 CSS
      item: "div[data-pressable-container='true']", link: "a[href*='/post/']", author: "a[href^='/@']", text: "span[dir='auto']", time: 'time[datetime]',
    },
    facebook: {
      loginMarkers: '登入|Log in|建立新帳號|Create new account',
      composerOpen: "建立貼文|Create post|在想些什麼|What's on your mind",
      next: '^下一步$|^Next$',
      post: '^發佈$|^Post$|^發布$',
      commentBox: '留言|Comment|寫下留言|Write a comment',
    },
  };
  const REPLY_DAILY_CAP = 20;   // 搜尋後回覆：每天最多幾篇（保護你的帳號）
  const URL_RE = /https?:\/\/|shp\.ee|s\.shopee|shope\.ee/i;
  // 對別人貼文的回覆：不能空白、不能放連結（在別人貼文貼商品連結會被當成垃圾訊息）
  function checkReply(text) {
    if (!String(text || '').trim()) return '回覆內容是空的，請先寫一則回覆';
    if (URL_RE.test(text)) return '回覆不能放連結（在別人的貼文貼商品連結會被當成垃圾訊息）';
    return '';
  }
  const dayKey = (ms) => new Date(ms + 8 * 3600e3).toISOString().slice(0, 10);   // 台北日期
  const repliedToday = (log, nowMs) => (log || []).filter((t) => dayKey(t) === dayKey(nowMs)).length;
  // 只留「幾天內」的貼文（at = 貼文時間 ms；讀不到時間的另外計數，不默默丟掉）
  function filterRecent(list, days, nowMs) {
    const cut = nowMs - days * 86400e3;
    const kept = [], old = [], unknown = [];
    for (const x of list) (x.at ? (x.at >= cut ? kept : old) : unknown).push(x);
    return { kept, old, unknown };
  }
  function ageText(at, nowMs) {
    if (!at) return '日期不明';
    const d = (nowMs - at) / 86400e3;
    if (d < 1 / 24) return '剛剛';
    if (d < 1) return Math.floor(d * 24) + ' 小時前';
    return Math.floor(d) + ' 天前';
  }
  return { LABELS, REPLY_DAILY_CAP, checkReply, repliedToday, filterRecent, ageText, URL_RE };
});
