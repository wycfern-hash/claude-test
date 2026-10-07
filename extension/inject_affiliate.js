// 在分潤後台頁面本身（MAIN world）執行：當你「手動」轉 1 個連結時，記下後台真正送出的請求，
// 之後小幫手就能照同樣的格式一次轉很多個。只記有回傳短連結的 POST 請求，不記帳號密碼之類的東西（標頭只留 3 個）。
(function () {
  if (window.__scAffHooked) return;
  window.__scAffHooked = true;
  const KEEP = ['content-type', 'affiliate-program-type', 'csrf-token'];
  const SHORT = /https?:\/\/(?:s\.shopee\.tw|shp\.ee)\/[A-Za-z0-9_-]+/;

  function emit(req, text) {
    try {
      if (!text || !SHORT.test(text) || typeof req.body !== 'string') return;
      window.postMessage({ __scAff: 1, req, res: text.slice(0, 20000) }, '*');
    } catch (e) { /* ignore */ }
  }
  const pickHeaders = (h) => {
    const out = {};
    try {
      if (h && typeof h.forEach === 'function') h.forEach((v, k) => { if (KEEP.includes(String(k).toLowerCase())) out[String(k).toLowerCase()] = v; });
      else if (h) for (const k of Object.keys(h)) if (KEEP.includes(k.toLowerCase())) out[k.toLowerCase()] = h[k];
    } catch (e) { /* ignore */ }
    return out;
  };

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const p = origFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      const body = init && init.body;
      if (method === 'POST' && typeof body === 'string' && /\/api\//.test(url)) {
        const headers = pickHeaders((init && init.headers) || (input && input.headers));
        p.then((r) => r.clone().text().then((t) => emit({ url: new URL(url, location.href).pathname + new URL(url, location.href).search, method, headers, body }, t))).catch(() => {});
      }
    } catch (e) { /* ignore */ }
    return p;
  };

  const oOpen = XMLHttpRequest.prototype.open, oSet = XMLHttpRequest.prototype.setRequestHeader, oSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__sc = { method: String(m).toUpperCase(), url: u, headers: {} }; return oOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { try { if (this.__sc && KEEP.includes(String(k).toLowerCase())) this.__sc.headers[String(k).toLowerCase()] = v; } catch (e) { /* ignore */ } return oSet.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__sc;
    if (meta && meta.method === 'POST' && typeof body === 'string' && /\/api\//.test(String(meta.url))) {
      this.addEventListener('load', function () {
        try {
          const u = new URL(meta.url, location.href);
          emit({ url: u.pathname + u.search, method: meta.method, headers: meta.headers, body }, this.responseType === '' || this.responseType === 'text' ? this.responseText : '');
        } catch (e) { /* ignore */ }
      });
    }
    return oSend.apply(this, arguments);
  };
})();
