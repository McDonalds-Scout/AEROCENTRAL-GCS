const STORAGE_KEY = "nova-browser-state-v1";

const defaults = {
  tabs: [{ id: crypto.randomUUID(), title: "主页", url: "" }],
  activeTabId: null,
  history: [],
  bookmarks: [
    { title: "百度", url: "https://www.baidu.com" },
    { title: "必应", url: "https://www.bing.com" },
    { title: "GitHub", url: "https://github.com" }
  ]
};

const quickLinks = [
  { title: "百度", url: "https://www.baidu.com", icon: "B" },
  { title: "必应", url: "https://www.bing.com", icon: "S" },
  { title: "GitHub", url: "https://github.com", icon: "G" },
  { title: "MDN", url: "https://developer.mozilla.org", icon: "M" },
  { title: "知乎", url: "https://www.zhihu.com", icon: "Z" },
  { title: "哔哩哔哩", url: "https://www.bilibili.com", icon: "V" },
  { title: "淘宝", url: "https://www.taobao.com", icon: "T" },
  { title: "设置", url: "nova://settings", icon: "N" }
];

const els = {
  addressForm: document.querySelector("#addressForm"),
  addressInput: document.querySelector("#addressInput"),
  heroSearch: document.querySelector("#heroSearch"),
  heroInput: document.querySelector("#heroInput"),
  homePage: document.querySelector("#homePage"),
  webView: document.querySelector("#webView"),
  pageFrame: document.querySelector("#pageFrame"),
  securityDot: document.querySelector("#securityDot"),
  quickGrid: document.querySelector("#quickGrid"),
  historyList: document.querySelector("#historyList"),
  bookmarkList: document.querySelector("#bookmarkList"),
  tabsBtn: document.querySelector("#tabsBtn"),
  menuBtn: document.querySelector("#menuBtn"),
  closeMenuBtn: document.querySelector("#closeMenuBtn"),
  tabsSheet: document.querySelector("#tabsSheet"),
  menuSheet: document.querySelector("#menuSheet"),
  scrim: document.querySelector("#scrim"),
  tabList: document.querySelector("#tabList"),
  tabCount: document.querySelector("#tabCount"),
  newTabBtn: document.querySelector("#newTabBtn"),
  backBtn: document.querySelector("#backBtn"),
  forwardBtn: document.querySelector("#forwardBtn"),
  homeBtn: document.querySelector("#homeBtn"),
  reloadBtn: document.querySelector("#reloadBtn"),
  bookmarkBtn: document.querySelector("#bookmarkBtn"),
  openExternalBtn: document.querySelector("#openExternalBtn"),
  copyLinkBtn: document.querySelector("#copyLinkBtn"),
  clearHistoryBtn: document.querySelector("#clearHistoryBtn"),
  installHintBtn: document.querySelector("#installHintBtn"),
  toast: document.querySelector("#toast")
};

let state = loadState();
if (!state.activeTabId) {
  state.activeTabId = state.tabs[0].id;
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return { ...defaults, ...saved };
  } catch {
    return structuredClone(defaults);
  }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function activeTab() {
  return state.tabs.find((tab) => tab.id === state.activeTabId) || state.tabs[0];
}

function normalizeInput(value) {
  const text = value.trim();
  if (!text) return "";
  if (text.startsWith("nova://")) return text;

  const looksLikeUrl = /^(https?:\/\/|localhost(:\d+)?|(\d{1,3}\.){3}\d{1,3}|[\w-]+(\.[\w-]+)+)/i.test(text);
  if (looksLikeUrl) {
    return /^https?:\/\//i.test(text) ? text : `https://${text}`;
  }

  return `https://www.bing.com/search?q=${encodeURIComponent(text)}`;
}

function titleFromUrl(url) {
  if (!url) return "主页";
  if (url.startsWith("nova://")) return "Nova 设置";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function navigate(rawValue, options = {}) {
  const url = normalizeInput(rawValue);
  const tab = activeTab();
  tab.url = url;
  tab.title = titleFromUrl(url);
  tab.updatedAt = Date.now();

  if (url && !url.startsWith("nova://")) {
    state.history = [
      { title: tab.title, url, visitedAt: Date.now() },
      ...state.history.filter((item) => item.url !== url)
    ].slice(0, 20);
  }

  saveState();
  render();

  if (url && options.external) {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

function render() {
  const tab = activeTab();
  els.addressInput.value = tab.url || "";
  els.heroInput.value = "";
  els.tabCount.textContent = String(state.tabs.length);
  els.securityDot.classList.toggle("searching", Boolean(tab.url && tab.url.includes("/search?")));

  if (!tab.url || tab.url.startsWith("nova://")) {
    els.homePage.classList.remove("hidden");
    els.webView.classList.add("hidden");
    els.pageFrame.removeAttribute("src");
  } else {
    els.homePage.classList.add("hidden");
    els.webView.classList.remove("hidden");
    if (els.pageFrame.src !== tab.url) {
      els.pageFrame.src = tab.url;
    }
  }

  renderQuickLinks();
  renderHistory();
  renderBookmarks();
  renderTabs();
}

function renderQuickLinks() {
  els.quickGrid.innerHTML = quickLinks.map((link) => `
    <button class="quick-tile" type="button" data-url="${link.url}">
      <span class="quick-icon">${link.icon}</span>
      <span>${link.title}</span>
    </button>
  `).join("");
}

function renderHistory() {
  if (!state.history.length) {
    els.historyList.innerHTML = '<p class="empty-state">还没有访问记录。</p>';
    return;
  }
  els.historyList.innerHTML = state.history.slice(0, 5).map((item) => `
    <button class="list-item" type="button" data-url="${item.url}">
      <span>${item.title}<small>${item.url}</small></span>
      <span>›</span>
    </button>
  `).join("");
}

function renderBookmarks() {
  if (!state.bookmarks.length) {
    els.bookmarkList.innerHTML = '<p class="empty-state">收藏网页后会显示在这里。</p>';
    return;
  }
  els.bookmarkList.innerHTML = state.bookmarks.slice(0, 6).map((item) => `
    <button class="list-item" type="button" data-url="${item.url}">
      <span>${item.title}<small>${item.url}</small></span>
      <span>›</span>
    </button>
  `).join("");
}

function renderTabs() {
  els.tabList.innerHTML = state.tabs.map((tab) => `
    <div class="tab-item ${tab.id === state.activeTabId ? "active" : ""}" data-tab-id="${tab.id}">
      <button type="button" class="tab-select" data-tab-id="${tab.id}">
        <span>${tab.title}<small>${tab.url || "主页"}</small></span>
      </button>
      <button class="tab-close" type="button" data-close-tab-id="${tab.id}" aria-label="关闭标签">×</button>
    </div>
  `).join("");
}

function addBookmark() {
  const tab = activeTab();
  if (!tab.url) {
    showToast("主页不需要收藏。");
    return;
  }

  const exists = state.bookmarks.some((item) => item.url === tab.url);
  if (exists) {
    state.bookmarks = state.bookmarks.filter((item) => item.url !== tab.url);
    showToast("已取消收藏。");
  } else {
    state.bookmarks.unshift({ title: tab.title, url: tab.url });
    showToast("已加入收藏。");
  }
  saveState();
  render();
}

function newTab() {
  const tab = { id: crypto.randomUUID(), title: "主页", url: "", updatedAt: Date.now() };
  state.tabs.unshift(tab);
  state.activeTabId = tab.id;
  saveState();
  closeSheets();
  render();
}

function closeTab(id) {
  if (state.tabs.length === 1) {
    navigate("");
    closeSheets();
    return;
  }

  state.tabs = state.tabs.filter((tab) => tab.id !== id);
  if (state.activeTabId === id) {
    state.activeTabId = state.tabs[0].id;
  }
  saveState();
  render();
}

function openSheet(sheet) {
  closeSheets();
  els.scrim.classList.remove("hidden");
  sheet.classList.remove("hidden");
}

function closeSheets() {
  els.scrim.classList.add("hidden");
  els.tabsSheet.classList.add("hidden");
  els.menuSheet.classList.add("hidden");
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.classList.add("hidden");
  }, 2200);
}

function openCurrentExternal() {
  const url = activeTab().url;
  if (!url) {
    showToast("当前是主页。");
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

els.addressForm.addEventListener("submit", (event) => {
  event.preventDefault();
  navigate(els.addressInput.value);
});

els.heroSearch.addEventListener("submit", (event) => {
  event.preventDefault();
  navigate(els.heroInput.value);
});

els.quickGrid.addEventListener("click", (event) => {
  const button = event.target.closest("[data-url]");
  if (button) navigate(button.dataset.url);
});

els.historyList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-url]");
  if (button) navigate(button.dataset.url);
});

els.bookmarkList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-url]");
  if (button) navigate(button.dataset.url);
});

els.tabsBtn.addEventListener("click", () => openSheet(els.tabsSheet));
els.menuBtn.addEventListener("click", () => openSheet(els.menuSheet));
els.closeMenuBtn.addEventListener("click", closeSheets);
els.scrim.addEventListener("click", closeSheets);
els.newTabBtn.addEventListener("click", newTab);

els.tabList.addEventListener("click", (event) => {
  const closeButton = event.target.closest("[data-close-tab-id]");
  if (closeButton) {
    closeTab(closeButton.dataset.closeTabId);
    return;
  }

  const selectButton = event.target.closest("[data-tab-id]");
  if (selectButton) {
    state.activeTabId = selectButton.dataset.tabId;
    saveState();
    closeSheets();
    render();
  }
});

els.homeBtn.addEventListener("click", () => navigate(""));
els.reloadBtn.addEventListener("click", () => {
  if (activeTab().url) {
    els.pageFrame.src = activeTab().url;
    showToast("已刷新预览。");
  }
});
els.bookmarkBtn.addEventListener("click", addBookmark);
els.openExternalBtn.addEventListener("click", openCurrentExternal);
els.backBtn.addEventListener("click", () => history.back());
els.forwardBtn.addEventListener("click", () => history.forward());

els.copyLinkBtn.addEventListener("click", async () => {
  const url = activeTab().url;
  if (!url) {
    showToast("当前没有可复制的链接。");
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    showToast("链接已复制。");
  } catch {
    showToast(url);
  }
});

els.clearHistoryBtn.addEventListener("click", () => {
  state.history = [];
  saveState();
  render();
  showToast("历史记录已清空。");
});

els.installHintBtn.addEventListener("click", () => {
  showToast("安卓浏览器菜单中选择添加到主屏幕。");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("service-worker.js").catch(() => {});
  });
}

render();
