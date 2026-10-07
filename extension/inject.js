// 在蝦皮頁面本身（MAIN world）執行：複製頁面自己載入的商品 JSON，交給 content.js。
// 不自己呼叫蝦皮的 API，所以不會被擋；只是「看」頁面本來就在載入的資料。
(function () {
  if (window.__scHooked) return;
  window.__scHooked = true;

  function emit(url, text) {
    try {
      if (!text || text.length > 8e6) return;
      if (text.indexOf('"itemid"') < 0 && text.indexOf('"promotionid"') < 0) return;
      window.postMessage({ __sc: 1, url: String(url), body: text }, '*');
    } catch (e) { /* ignore */ }
  }
  const isApi = (u) => /\/api\//.test(String(u || ''));

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args);
    try {
      const req = args[0];
      const url = typeof req === 'string' ? req : (req && req.url) || '';
      p.then((r) => {
        try {
          const u = r.url || url;
          if (isApi(u)) r.clone().text().then((t) => emit(u, t)).catch(() => {});
        } catch (e) { /* ignore */ }
      }).catch(() => {});
    } catch (e) { /* ignore */ }
    return p;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__scUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', function () {
      try {
        const u = this.responseURL || this.__scUrl || '';
        if (!isApi(u)) return;
        let t = '';
        if (this.responseType === '' || this.responseType === 'text') t = this.responseText;
        else if (this.responseType === 'json') t = JSON.stringify(this.response);
        emit(u, t);
      } catch (e) { /* ignore */ }
    });
    return origSend.apply(this, arguments);
  };
})();
