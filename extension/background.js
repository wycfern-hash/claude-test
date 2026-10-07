// 點工具列圖示 → 開啟（或切回）小幫手頁面。
chrome.action.onClicked.addListener(async () => {
  const url = chrome.runtime.getURL('dashboard.html');
  const tabs = await chrome.tabs.query({ url });
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
});
