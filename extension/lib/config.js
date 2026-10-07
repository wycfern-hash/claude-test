// 網址設定集中在這裡（測試時會換成本機假網站）。
(function (root) {
  root.SC = root.SC || {};
  root.SC.config = {
    shopeeBase: 'https://shopee.tw',
    affiliateBase: 'https://affiliate.shopee.tw',
    threadsHome: 'https://www.threads.com/',
    threadsSearch: 'https://www.threads.com/search?q={q}&serp_type=default',
    customLinkPath: '/offer/custom_link',
    quietMs: 7000,        // 捲到底後，多久沒有新商品就算讀完（限時特賣）
    searchQuietMs: 4000,  // 同上（搜尋頁，頁面短，等短一點）
  };
})(typeof self !== 'undefined' ? self : this);
