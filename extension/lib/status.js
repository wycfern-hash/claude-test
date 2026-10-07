// 日期時間與「開始特價了嗎／還要多久」。一律用台北時間顯示。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const TZ = 'Asia/Taipei';
  const fmt = new Intl.DateTimeFormat('zh-TW', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short',
  });
  function parts(tsSec) {
    const o = {};
    for (const p of fmt.formatToParts(new Date(tsSec * 1000))) o[p.type] = p.value;
    return o;
  }
  const dayKey = (p) => `${p.year}/${p.month}/${p.day}`;

  function fmtDateTime(tsSec) {
    if (!tsSec) return '—';
    const p = parts(tsSec);
    return `${dayKey(p)}（${p.weekday}） ${p.hour}:${p.minute}`;
  }
  function fmtRange(start, end) {
    if (!start) return '—';
    const a = parts(start);
    if (!end) return fmtDateTime(start);
    const b = parts(end);
    const tail = dayKey(a) === dayKey(b) ? `${b.hour}:${b.minute}` : `${b.month}/${b.day} ${b.hour}:${b.minute}`;
    return `${dayKey(a)}（${a.weekday}） ${a.hour}:${a.minute} – ${tail}`;
  }
  function fmtCountdown(sec) {
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return (d ? d + '天 ' : '') + `${pad(h)}:${pad(m)}:${pad(s)}`;
  }
  // 回傳 { state: none|upcoming|live|ended, label, left }
  function sessionStatus(start, end, nowMs) {
    const now = nowMs / 1000;
    if (!start) return { state: 'none', label: '—', left: null };
    if (now < start) return { state: 'upcoming', label: '⏳ 距離開始 ' + fmtCountdown(start - now), left: start - now };
    if (!end || now < end) {
      return { state: 'live', label: '🔥 已開始' + (end ? '・剩 ' + fmtCountdown(end - now) : ''), left: end ? end - now : null };
    }
    return { state: 'ended', label: '已結束', left: 0 };
  }
  const STATE_ZH = { upcoming: '尚未開始', live: '已開始', ended: '已結束', none: '' };

  return { fmtDateTime, fmtRange, fmtCountdown, sessionStatus, STATE_ZH, TZ };
});
