// 分潤後台「自訂連結」的輔助：取短連結、組 GraphQL 請求、解析回應。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.SC = root.SC || {}; Object.assign(root.SC, api); }
})(typeof self !== 'undefined' ? self : this, function () {
  const SHORT_RE = /https?:\/\/(?:s\.shopee\.tw|shp\.ee)\/[A-Za-z0-9_-]+/;
  const QUERY = 'query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){ ' +
    'batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){ shortLink longLink failCode } }';

  const findShortLink = (text) => { const m = SHORT_RE.exec(text || ''); return m ? m[0] : null; };

  function buildBatchBody(urls, subId) {
    return {
      operationName: 'batchGetCustomLink', query: QUERY,
      variables: {
        linkParams: urls.map((u) => ({ originalLink: u, advancedLinkParams: subId ? { subId1: subId } : {} })),
        sourceCaller: 'CUSTOM_LINK_CALLER',
      },
    };
  }
  // 回傳 [{url, short, err}]；格式不符回 null（呼叫端改走網頁操作的備援）
  function parseBatchResponse(json, urls) {
    const arr = json && json.data && json.data.batchCustomLink;
    if (!Array.isArray(arr)) return null;
    return urls.map((u, i) => {
      const r = arr[i] || {};
      const short = findShortLink(r.shortLink || '');
      return { url: u, short, err: short ? '' : (r.failCode ? '失敗代碼 ' + r.failCode : '沒有回傳短連結') };
    });
  }
  return { SHORT_RE, findShortLink, buildBatchBody, parseBatchResponse };
});
