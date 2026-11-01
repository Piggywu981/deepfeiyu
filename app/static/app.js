/* Deep肥鱼 · 微信智能体控制台（多账号） */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

function storageGet(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 隐私模式或存储被禁用时忽略，不影响当前会话 */
  }
}

const state = {
  page: "overview",
  status: null,
  stats: null,
  chats: [],
  accounts: [],
  account: "",
  activeChat: null,
  chat: null,
  messages: [],
  settings: null,
  editingAccount: "",
  policies: {},
  ws: null,
  wsRetry: 0,
  shuttingDown: false,
  selectedChats: new Set(),
  selectMode: false,
  autoTools: [],
  autoThink: "",
  autoLiveEl: null,
  emojis: [],
  emojiTab: "builtin",
  wallFilter: { tag: "", q: "" },
  wallEmojiId: null,
  wallSelMode: false,
  wallSel: new Set(),
  wallRetagRunning: false,
  wallRetagStop: false,
  emojiHasMore: false,
  wallShowAllTags: false,
  tagLocks: new Set(),
  memories: [],
  memoryPage: {
    items: [],
    profiles: [],
    stats: null,
    tab: "stream",
    layer: "",
    kind: "",
    pinnedOnly: false,
    selMode: false,
    sel: new Set(),
  },
  capabilities: null,
  growth: {
    status: null,
    summary: "",
    traits: [],
    journal: [],
    goals: [],
    proposals: [],
    events: [],
    tab: "self",
  },
  momentsPage: { items: [], total: 0, limit: 30, keyword: "" },
  personaPresets: [],
  eula: {
    lang: storageGet("df_eula_lang") === "en" ? "en" : "zh",
    cache: {},
  },
  whale: { loaded: false, ok: false, balance: null, error: "", code: "", at: 0, fetching: false, timer: null },
};

const PAGE_META = {
  overview: ["概览", "微信账号与 AI 智能体的实时状态"],
  chats: ["会话", "实时消息流，可随时接管或交给 AI"],
  agent: ["智能体", "用自然语言直接操作选中的微信账号"],
  emojis: ["表情包", "已收集表情的预览墙：按标签筛选、发送、改标签"],
  memory: ["记忆", "三层长期记忆与人物档案：检索、置顶、整理、导入导出"],
  growth: ["成长", "自我总览、特质、日记与进化时间线"],
  mood: ["心情", "每轮判断涨跌、自然平复；挂饰在右下角，设置在这里"],
  proactive: ["主动搭话", "按概率在白名单会话里主动开口；默认关，开启后会真的发微信"],
  capabilities: ["能力", "技能、插件与 MCP：查看、启停、编辑配置"],
  moments: ["朋友圈", "好友动态与互动通知：浏览、点赞、评论"],
  settings: ["设置", "模型、策略、账号与通道"],
  usage: ["使用说明", "日常使用、页面导览、心情与排错；改完 docs/使用说明.md 刷新即见"],
  eula: ["最终用户许可协议", "使用本软件前请阅读；英文译本仅供参考，以中文版为准"],
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function getToken() {
  return localStorage.getItem("df_token") || "";
}

function mediaUrl(url) {
  const token = getToken();
  if (!token || !url) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const token = getToken();
  if (token) headers["X-Access-Token"] = token;
  if (state.account) headers["X-Account-Id"] = state.account;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    const t = prompt("该控制台已设置访问令牌，请输入：");
    if (t) {
      localStorage.setItem("df_token", t);
      return api(path, opts);
    }
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || `请求失败 (${res.status})`);
    err.status = res.status;   // 带上状态码，调用方才能按 404/409 分别提示
    throw err;
  }
  return data;
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  $("#toastWrap").appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .2s";
    setTimeout(() => el.remove(), 220);
  }, 3200);
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (d.toDateString() === now.toDateString()) return hm;
  const y = new Date(now.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return "昨天";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function fmtMoney(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toFixed(2) : "—";
}

/* 单轮成本通常远小于 1 元：按量级保留 2–4 位小数，避免整屏都是 ¥0.00 */
function fmtCost(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1) return v.toFixed(2);
  if (v >= 0.01) return v.toFixed(3);
  if (v > 0) return v.toFixed(4);
  return "0.00";
}

function billCostTitle(tokens) {
  const parts = ["AI 调用成本（DeepSeek 按 ai.price_*、MiMo 按内置牌价估算，与挂件账本同源）"];
  if (tokens && (tokens.input !== undefined || tokens.output !== undefined)) {
    parts.push(`输入 ${Number(tokens.input) || 0} / 输出 ${Number(tokens.output) || 0} tokens`);
  }
  return parts.join(" · ");
}

const PALETTE = [
  ["#dbeafe", "#bfdbfe", "#1e40af"],
  ["#fee2e2", "#fecaca", "#b91c1c"],
  ["#dcfce7", "#bbf7d0", "#15803d"],
  ["#fef3c7", "#fde68a", "#92400e"],
  ["#ede9fe", "#ddd6fe", "#5b21b6"],
  ["#cffafe", "#a5f3fc", "#0e7490"],
  ["#fce7f3", "#fbcfe8", "#9d174d"],
];

const MSG_TYPE_LABELS = {
  image: "图片",
  voice: "语音",
  video: "视频",
  emoji: "表情",
  location: "位置",
  link: "链接",
  system: "系统消息",
  pat: "拍一拍",
};

/* 微信内置表情：发送走普通文本（微信端原生渲染），控制台用 Unicode 近似显示 */
const EMOJI_MAP = {
  微笑: "😊", 撇嘴: "😒", 色: "😍", 发呆: "😶", 得意: "😎", 流泪: "😢",
  害羞: "😳", 闭嘴: "🤐", 睡: "😴", 大哭: "😭", 尴尬: "😅", 发怒: "😠",
  调皮: "😜", 呲牙: "😁", 惊讶: "😮", 难过: "🙁", 囧: "😵", 抓狂: "🤯",
  吐: "🤮", 偷笑: "🤭", 可爱: "🥰", 白眼: "🙄", 傲慢: "😤", 困: "😪",
  惊恐: "😱", 流汗: "😅", 憨笑: "🤣", 悠闲: "😇", 奋斗: "💪", 咒骂: "🤬",
  疑问: "❓", 嘘: "🤫", 晕: "😵‍💫", 折磨: "😖", 衰: "😞", 骷髅: "💀",
  敲打: "👊", 再见: "👋", 擦汗: "😓", 抠鼻: "👃", 鼓掌: "👏", 坏笑: "😏",
  右哼哼: "😒", 鄙视: "👎", 委屈: "🥺", 快哭了: "🥹", 阴险: "🤏", 亲亲: "😘",
  可怜: "🥹", 苦涩: "☹️", 裂开: "😱", 菜刀: "🔪", 西瓜: "🍉", 啤酒: "🍺",
  篮球: "🏀", 乒乓: "🏓", 咖啡: "☕", 饭: "🍚", 猪头: "🐷", 玫瑰: "🌹",
  凋谢: "🥀", 嘴唇: "👄", 爱心: "❤️", 心碎: "💔", 蛋糕: "🎂", 手枪: "🔫",
  炸弹: "💣", 刀: "🗡️", 足球: "⚽", 瓢虫: "🐞", 便便: "💩", 月亮: "🌙",
  太阳: "☀️", 庆祝: "🎉", 红包: "🧧", 发: "🧧", 拥抱: "🤗", 强: "👍",
  弱: "👎", 握手: "🤝", 胜利: "✌️", 抱拳: "🙏", 勾引: "👉", 拳头: "✊",
  OK: "👌", 合十: "🙏", 啧啧: "😝", 加油: "💪", 转圈: "🌀", 吃瓜: "🍉",
  哈欠: "🥱", 汗颜: "😅", 冒泡: "💭", 苦中作乐: "😂", 我看不下去了: "🙈",
  打脸: "🤚", 保守秘密: "🤐", 靠: "💢", 咖啡面: "🍜", 蛋: "🥚", 元宝: "🪙",
  转发: "🔁", 发财: "💰", 貔貅: "🐉", 快门: "📸", 求红包: "🧧",
};

/* 把文本里的 [微笑] 之类的内置表情名换成 Unicode 表情 */
function renderTextEmojis(escaped) {
  return escaped.replace(/\[([^\[\]]{1,8})\]/g, (raw, name) => {
    const ch = EMOJI_MAP[name];
    return ch ? `<span class="we-emoji" title="${raw}">${ch}</span>` : raw;
  });
}

function emojiProxyUrl(info, download) {
  let p = `/api/emoji/proxy?url=${encodeURIComponent(info.url || "")}`;
  if (info.md5) p += `&md5=${encodeURIComponent(info.md5)}`;
  if (download) p += "&download=1";
  return mediaUrl(p);
}

function avatarHtml(name, isGroup) {
  const s = name || "?";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
  const p = PALETTE[h % PALETTE.length];
  const txt = s.slice(0, 1).toUpperCase();
  return `<div class="avatar ${isGroup ? "group" : ""}" style="--c1:${p[0]};--c2:${p[1]};--c3:${p[2]}" title="${esc(s)}">${esc(txt)}</div>`;
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

function go(page) {
  state.page = page;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  const meta = PAGE_META[page] || ["", ""];
  $("#pageTitle").textContent = meta[0];
  $("#pageDesc").textContent = meta[1];
  if (page === "chats") loadChats();
  if (page === "settings") {
    loadSettings();
    loadAccounts();
    loadDiagnose();
    loadPersonaPresets();
  }
  if (page === "overview") {
    loadStats();
    loadEvents();
  }
  if (page === "agent") loadAgentPage();
  if (page === "memory") loadMemoryPage();
  if (page === "growth") loadGrowthPage();
  if (page === "mood") loadMoodPage();
  if (page === "proactive") loadProactivePage();
  if (page === "pet") loadPetPage();
  if (page === "capabilities") loadCapabilitiesPage();
  if (page === "moments") loadMomentsPage();
  if (page === "eula") loadEulaPage();
  if (page === "usage") loadUsagePage();
  syncWhalePolling();
}
window.go = go;

/* ---------------------------------------------------------------- 鲸鱼账单 */
function whaleBalanceChip() {
  const w = state.whale;
  if (!w.loaded) {
    return '<span class="bill-chip muted" title="正在读取 DeepSeek 余额">余额 …</span>';
  }
  if (w.ok) {
    const at = w.at ? ` · 更新于 ${fmtClock(w.at)}` : "";
    return `<button type="button" class="bill-chip" title="DeepSeek 账户余额${esc(at)} · 点击刷新" onclick="refreshWhaleBalance(true)">余额 ¥${fmtMoney(w.balance)}</button>`;
  }
  return `<button type="button" class="bill-chip muted" title="${esc(w.error || "余额不可用")} · 点击重试" onclick="refreshWhaleBalance(true)">余额 —</button>`;
}

function billCostChip(label, cost, tokens) {
  if (cost === null || cost === undefined || !Number.isFinite(Number(cost))) {
    return `<span class="bill-chip muted" title="当前会话还没有可用的本轮用量">${label} —</span>`;
  }
  return `<span class="bill-chip cost" title="${esc(billCostTitle(tokens))}">${label} ¥${fmtCost(cost)}</span>`;
}

function lastAgentMsgCost() {
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const m = state.messages[i];
    if (!m || !m.is_self || m.source !== "agent") continue;
    // 表情/图片是正文之后的附加消息，成本统一记在文字回复上
    if (m.msg_type && m.msg_type !== "text") continue;
    // 只看最近一条 AI 文字回复：它没带用量就显示「—」，不能拿更早一轮冒充上轮
    if (m.meta && m.meta.cost !== undefined && m.meta.cost !== null) {
      return { cost: Number(m.meta.cost), tokens: m.meta.tokens || null };
    }
    return null;
  }
  return null;
}

function renderWhaleBills() {
  const threadEl = $("#threadBill");
  if (threadEl) {
    if (state.chat && state.activeChat) {
      const last = lastAgentMsgCost();
      threadEl.innerHTML =
        whaleBalanceChip() +
        billCostChip("上轮", last ? last.cost : null, last ? last.tokens : null);
      threadEl.hidden = false;
    } else {
      threadEl.hidden = true;
    }
  }
  const agentEl = $("#agentBill");
  if (agentEl) {
    agentEl.innerHTML = whaleBalanceChip() + billCostChip("上轮", agentState.lastCost, agentState.lastTokens);
    agentEl.hidden = false;
  }
}

async function refreshWhaleBalance(force = false) {
  const w = state.whale;
  if (w.fetching) return;
  w.fetching = true;
  try {
    const body = await api(`/dsh-whale/balance.json${force ? "?refresh=1" : ""}`);
    w.loaded = true;
    const total = body && body.totalBalance;
    w.ok =
      !!(body && body.ok) &&
      total !== null &&
      total !== undefined &&
      Number.isFinite(Number(total));
    w.balance = w.ok ? Number(total) : null;
    w.error = w.ok ? "" : String((body && (body.error || body.code)) || "余额不可用");
    w.code = String((body && body.code) || "");
    w.at = w.ok ? Date.now() / 1000 : 0;
  } catch (e) {
    w.loaded = true;
    w.ok = false;
    w.balance = null;
    w.error = e.message || "余额请求失败";
  } finally {
    w.fetching = false;
    renderWhaleBills();
  }
}
window.refreshWhaleBalance = refreshWhaleBalance;

function syncWhalePolling() {
  const active = state.page === "chats" || state.page === "agent";
  if (!active) {
    if (state.whale.timer) {
      clearInterval(state.whale.timer);
      state.whale.timer = null;
    }
    return;
  }
  renderWhaleBills();
  if (!state.whale.timer) {
    state.whale.timer = setInterval(() => {
      if (state.page === "chats" || state.page === "agent") refreshWhaleBalance();
    }, 60000);
  }
  if (!state.whale.fetching) refreshWhaleBalance();
}

function renderAccountSwitcher() {
  const acc = state.accounts.find((a) => a.id === state.account) || state.accounts[0];
  if (acc) {
    state.account = acc.id;
    $("#accName").textContent = acc.name;
    $("#accAvatar").textContent = (acc.name || "?").slice(0, 1);
    $("#accSub").textContent = acc.connected ? acc.display_name || "已连接" : acc.error || "未连接";
  }
  $("#accMenu").innerHTML =
    state.accounts
      .map(
        (a) => `<button class="acc-item ${a.id === state.account ? "active" : ""}" data-id="${esc(a.id)}">
        <span class="acc-avatar">${esc((a.name || "?").slice(0, 1))}</span>
        <span class="acc-name">${esc(a.name)}</span>
        <span class="acc-dot ${a.connected ? "on" : ""}" title="${a.connected ? "已连接" : esc(a.error || "未连接")}"></span>
      </button>`
      )
      .join("") +
    `<div class="acc-sep"></div>
    <button class="acc-manage" id="accRestart">↻ 重新连接当前通道</button>
    <button class="acc-manage" id="accManage">＋ 管理账号 / 新增</button>`;

  $("#accMenu").querySelectorAll(".acc-item").forEach((el) =>
    el.addEventListener("click", () => switchAccount(el.dataset.id))
  );
  const manage = $("#accManage");
  if (manage)
    manage.addEventListener("click", () => {
      $("#accMenu").hidden = true;
      go("settings");
      setTimeout(() => $("#accTabs")?.scrollIntoView({ behavior: "smooth", block: "center" }), 250);
    });
  const restart = $("#accRestart");
  if (restart)
    restart.addEventListener("click", () => {
      $("#accMenu").hidden = true;
      restartDriver();
    });

  const agentSub = $("#agentSub");
  if (agentSub && acc) renderAgentHeader();
}

/* accounts 全程必须是数组（renderStatus/renderAgentHeader 等都直接 .find），响应结构异常时在这里规整兜底 */
function normalizeAccounts(v) {
  if (Array.isArray(v)) return v;
  if (v && typeof v === "object" && Array.isArray(v.accounts)) return v.accounts;
  return [];
}

async function loadAccounts() {
  try {
    state.accounts = normalizeAccounts(await api("/api/accounts"));
    if (!state.account) {
      const active = state.accounts.find((a) => a.active);
      state.account = active ? active.id : (state.accounts[0] || {}).id || "";
    }
    renderAccountSwitcher();
    renderAccTabs();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function switchAccount(id) {
  if (id === state.account) {
    $("#accMenu").hidden = true;
    return;
  }
  const acc = state.accounts.find((a) => a.id === id);
  try {
    await api(`/api/accounts/${encodeURIComponent(id)}/activate`, { method: "POST" });
    state.account = id;
    state.activeChat = null;
    state.chat = null;
    state.messages = [];
    state.selectedChats.clear();
    state.selectMode = false;
    syncSelectModeUI();
    state.editingAccount = id;
    loadEmojis();
    $(".chats-layout").classList.remove("show-thread");
    $("#thread").hidden = true;
    $("#threadEmpty").hidden = false;
    resetAgentState();
    $("#accMenu").hidden = true;
    await Promise.all([loadAccounts(), loadStatus(), loadChats(), loadStats(), loadEvents()]);
    fillAccountForm(id);
    if (state.page === "agent") loadAgentPage();
    if (state.page === "memory") {
      if (state.memoryPage.selMode) setMemSelMode(false);
      loadMemoryPage();
    }
    if (state.page === "moments") loadMomentsPage();
    if (state.page === "mood") loadMoodPage();
    if (state.page === "proactive") loadProactivePage();
    if (state.page === "pet") loadPetPage();
    toast(`已切换到「${acc ? acc.name : id}」`, "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderStatus(st) {
  state.status = st;
  const connected = st.connected;
  const aiOk = st.ai && st.ai.configured;

  const pill = $("#pillConn");
  pill.className = "pill " + (connected ? "ok" : "bad");
  pill.innerHTML = `<i></i>${connected ? "微信已连接" : "微信未连接"}`;

  const aiPill = $("#pillAi");
  aiPill.className = "pill " + (aiOk ? "ok" : "warn");
  aiPill.innerHTML = `<i></i>${aiOk ? "AI " + esc(st.ai.model) : "AI 未配置"}`;

  $("#sideDriver").textContent = st.display_name || st.driver || "—";
  $("#sideDriverSub").textContent = connected
    ? st.account || st.detail || "运行中"
    : st.error || st.detail || "未连接";
  $("#capDriver").textContent = st.display_name || "—";

  const caps = st.capabilities || [];
  $("#capList").innerHTML =
    caps
      .map(
        (c) => `<div class="cap-item"><span class="cap-ico ${c.ok ? "yes" : "no"}">${c.ok ? "✓" : "–"}</span>
      <span>${esc(c.label)}</span><span class="spacer"></span>
      <span style="font-size:11px;color:${c.ok ? "#048a45" : "#b3bcc9"}">${c.ok ? "可用" : "不支持"}</span></div>`
      )
      .join("") || '<div class="empty-mini">暂无能力信息</div>';

  $("#capNote").textContent =
    st.driver === "wcferry"
      ? "本地 Hook 直连微信客户端，消息与通讯录都在本机处理。"
      : st.driver === "webhook"
      ? "通过外部网关收发消息，入站需带 account 或 token 以区分账号。"
      : st.driver === "weixin4"
      ? "微信 4.x：数据库解密后台收消息，发送时激活窗口执行前台 RPA。"
      : "";

  const accName = st.account_name || "当前账号";
  $("#heroState").textContent = connected
    ? `${accName} · ${st.display_name} 运行中 · 已稳定 ${Math.max(1, Math.round((st.uptime || 0) / 60))} 分钟`
    : `账号「${accName}」通道未连接`;

  const banner = $("#banner");
  if (!aiOk) {
    banner.hidden = false;
    $("#bannerTitle").textContent = "尚未配置 DeepSeek API Key";
    $("#bannerDesc").textContent = "自动回复与智能体需要模型支持，填入 Key 后立即生效。";
  } else if (!connected) {
    banner.hidden = false;
    $("#bannerTitle").textContent = `账号「${accName}」通道未连接`;
    $("#bannerDesc").textContent = st.error || "请检查微信客户端与驱动配置。";
  } else {
    banner.hidden = true;
  }

  const autoOn = st.auto && st.auto.enabled;
  $("#autoTag").textContent = autoOn ? "自动回复已开启" : "自动回复已关闭";
  $("#autoTag").style.color = autoOn ? "#048a45" : "";

  const acc = state.accounts.find((a) => a.id === st.account_id);
  if (acc) {
    acc.connected = connected;
    acc.display_name = st.display_name;
    acc.error = st.error || "";
    renderAccountSwitcher();
  }
}

async function loadStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    state.stats = s;
    $("#statIn").textContent = s.today.inbound;
    $("#statOut").textContent = s.today.outbound;
    $("#statAi").textContent = s.today.ai;
    $("#statChats").textContent = s.today.chats;
    if (state.chats.length) $("#chatCount").textContent = state.chats.length;
    const u = s.usage || {};
    $("#statAiFoot").textContent = u.calls
      ? `累计 ${u.calls} 次调用 · ${(u.prompt_tokens + u.completion_tokens).toLocaleString()} tokens`
      : "今日自动处理";
    renderChart(s.daily || []);
    if (s.driver) renderStatus(s.driver);
  } catch (e) {
    /* ignore */
  }
}

function renderChart(days) {
  const max = Math.max(1, ...days.map((d) => Math.max(d.inbound, d.ai)));
  $("#chart").innerHTML = days
    .map((d) => {
      const hi = Math.max(3, Math.round((d.inbound / max) * 126));
      const ha = Math.max(3, Math.round((d.ai / max) * 126));
      return `<div class="chart-day" title="${d.day} 收到 ${d.inbound} · AI ${d.ai}">
        <div class="chart-bars">
          <div class="chart-bar in" style="height:${hi}px"></div>
          <div class="chart-bar ai" style="height:${ha}px"></div>
        </div>
        <div class="chart-label">${esc(d.label)}</div>
      </div>`;
    })
    .join("");
}

async function loadEvents() {
  try {
    const list = await api("/api/events?limit=12");
    $("#eventList").innerHTML =
      list
        .map(
          (e) => `<div class="event-item">
        <span class="event-dot ${esc(e.level)}"></span>
        <div class="event-main">
          <div class="event-title">${esc(e.title)}</div>
          ${e.detail ? `<div class="event-detail">${esc(e.detail)}</div>` : ""}
        </div>
        <div class="event-time">${fmtClock(e.ts)}</div>
      </div>`
        )
        .join("") || '<div class="empty-mini">暂无动态</div>';
  } catch (e) {
    /* ignore */
  }
}

async function loadChats() {
  try {
    const list = await api("/api/chats");
    state.chats = list;
    // 丢弃已不存在于当前账号的选中项，避免批量操作误伤
    const alive = new Set(list.map((c) => c.uid));
    [...state.selectedChats].forEach((uid) => {
      if (!alive.has(uid)) state.selectedChats.delete(uid);
    });
    $("#chatCount").textContent = list.length;
    renderChats();
    // 打开中的线程头部要跟着列表数据走：人工接管发生/解除时徽标和恢复按钮才会即时变化
    if (state.chat && state.activeChat) {
      const fresh = list.find((c) => c.uid === state.activeChat);
      if (fresh && (fresh.paused !== state.chat.paused || fresh.ai_enabled !== state.chat.ai_enabled)) {
        state.chat.paused = fresh.paused;
        state.chat.ai_enabled = fresh.ai_enabled;
        renderThreadHead();
      }
    }
  } catch (e) {
    toast(e.message, "err");
  }
}

/* 当前搜索条件下可见的会话 */
function visibleChats() {
  const kw = ($("#chatSearch").value || "").trim().toLowerCase();
  return state.chats.filter((c) => !kw || (c.name || "").toLowerCase().includes(kw));
}

function renderChats() {
  const list = visibleChats();
  $("#chatList").innerHTML =
    list
      .map((c) => {
        const active = state.activeChat === c.uid ? "active" : "";
        const selected = state.selectMode && state.selectedChats.has(c.uid) ? "selected" : "";
        const badge = c.unread ? `<span class="ci-badge">${c.unread > 99 ? "99+" : c.unread}</span>` : "";
        const ai = c.ai_enabled
          ? c.paused
            ? `<span class="ci-paused" data-uid="${esc(c.uid)}" title="人工接管中，点击恢复 AI 自动回复">接管中</span>`
            : '<span class="ci-ai">AI</span>'
          : "";
        return `<div class="chat-item ${active} ${selected}" data-uid="${esc(c.uid)}">
        ${avatarHtml(c.name, c.is_group)}
        <div class="ci-main">
          <div class="ci-row"><span class="ci-name">${esc(c.name)}</span><span class="ci-time">${fmtTime(c.last_ts)}</span></div>
          <div class="ci-row"><span class="ci-last">${esc(c.last_message || "暂无消息")}</span>${ai}${badge}</div>
        </div>
      </div>`;
      })
      .join("") || '<div class="empty-mini" style="padding:24px 12px;text-align:center">没有匹配的会话</div>';
  updateBulkBar();
}

/* 多选模式开关 */
function setSelectMode(on) {
  state.selectMode = on;
  if (!on) state.selectedChats.clear();
  syncSelectModeUI();
  renderChats();
}

function syncSelectModeUI() {
  const tools = $("#bulkTools");
  if (tools) tools.hidden = !state.selectMode;
  const btn = $("#btnSelectMode");
  if (btn) {
    btn.textContent = state.selectMode ? "退出多选" : "多选";
    btn.classList.toggle("active", state.selectMode);
  }
}

function updateBulkBar() {
  const el = $("#chatSelCount");
  if (el) el.textContent = `已选 ${state.selectedChats.size}`;
  syncSelAll();
}

/* 全选按钮：根据当前可见列表的选中情况切换文案 */
function syncSelAll() {
  const btn = $("#btnChatSelAll");
  if (!btn) return;
  const visible = visibleChats();
  const all = visible.length > 0 && visible.every((c) => state.selectedChats.has(c.uid));
  btn.textContent = all ? "取消全选" : "全选";
  btn.disabled = visible.length === 0;
}

async function bulkSetAI(enabled) {
  const uids = [...state.selectedChats];
  if (!uids.length) {
    toast("请先勾选会话", "err");
    return;
  }
  try {
    const r = await api("/api/chats/batch-ai", {
      method: "POST",
      body: JSON.stringify({ uids, enabled }),
    });
    toast(`已把 ${r.changed} 个会话设为${enabled ? " AI 自动回复" : "人工接管"}`, "ok");
    setSelectMode(false);
    await loadChats();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function selectChat(uid) {
  state.activeChat = uid;
  state.autoTools = [];
  state.autoThink = "";
  state.autoLiveEl = null;
  $(".chats-layout").classList.add("show-thread");
  renderChats();
  $("#threadEmpty").hidden = true;
  $("#thread").hidden = false;
  try {
    const data = await api(`/api/chats/${encodeURIComponent(uid)}/messages?limit=100`);
    state.chat = data.chat;
    state.messages = data.messages;
    renderThread();
    loadChats();
  } catch (e) {
    toast(e.message, "err");
  }
}

/* 窄屏：从对话返回会话列表 */
function closeThread() {
  state.activeChat = null;
  state.autoTools = [];
  state.autoThink = "";
  state.autoLiveEl = null;
  $(".chats-layout").classList.remove("show-thread");
  $("#thread").hidden = true;
  $("#threadEmpty").hidden = false;
  renderChats();
}

function appendAutoThinkDelta(text) {
  const wrap = state.autoLiveEl;
  const body = wrap && wrap.isConnected ? wrap.querySelector(".auto-think-body") : null;
  if (!body) {
    renderThread();
    return;
  }
  body.appendChild(document.createTextNode(text));
  scrollBottom($("#messages"));
}

function toolCardHtml(t) {
  const ok = t.ok;
  const hasResult = t.result !== undefined && t.result !== null && t.result !== "";
  const args = JSON.stringify(t.args || {}, null, 2);
  const result = hasResult
    ? typeof t.result === "string"
      ? t.result
      : JSON.stringify(t.result, null, 2)
    : "";
  return `<details class="tool-card"><summary>
      <span class="tool-ico">⚙</span>
      <span class="tool-name">${esc(t.name || "")}</span>
      <span class="tool-status ${ok === true ? "ok" : ok === false ? "err" : ""}">${ok === true ? "成功" : ok === false ? "失败" : "运行中…"}</span>
    </summary>
    <div class="tool-body">
      <div class="tool-label">参数</div>
      <pre class="tool-args">${esc(args)}</pre>
      ${hasResult ? `<div class="tool-label">结果</div><pre class="tool-result">${esc(result)}</pre>` : ""}
    </div>
  </details>`;
}

function renderThreadHead() {
  const c = state.chat;
  if (!c) return;
  $("#threadName").textContent = c.name;
  const policy = state.policies[c.uid];
  const base = c.is_group ? "群聊" : "私聊";
  const aiState = c.ai_enabled ? (c.paused ? "人工接管中" : "AI 自动回复") : "AI 已关闭";
  $("#threadSub").textContent = policy ? `${base} · ${aiState} · AI 判断：${policy}` : `${base} · ${aiState}`;
  $("#chatAiToggle").checked = !!c.ai_enabled;
  $("#pauseBadge").hidden = !c.paused;
  $("#btnResume").hidden = !c.paused;
  renderWhaleBills();
}

function renderThread() {
  const c = state.chat;
  if (!c) return;
  $("#threadAvatar").outerHTML = avatarHtml(c.name, c.is_group).replace(
    'class="avatar',
    'id="threadAvatar" class="avatar'
  );
  renderThreadHead();

  let threadHtml =
    state.messages
      .map((m) => {
        if (m.msg_type === "pat") {
          // 拍一拍是提示不是对话：居中轻量展示，不占左右气泡
          return `<div class="msg-pat">${esc(m.content || "拍了拍")}<span class="msg-pat-time">${fmtClock(m.ts)}</span></div>`;
        }
        const out = m.is_self;
        const sender = !out && m.is_group ? `<div class="msg-sender">${esc(m.sender_name || m.chat_name)}</div>` : "";
        const aiTag = out && m.source === "agent" ? '<span class="ai-tag">AI</span>' : "";
        const billed =
          out && m.source === "agent" && m.meta && m.meta.cost !== undefined && m.meta.cost !== null;
        const costTag = billed
          ? `<span class="msg-cost" title="${esc(billCostTitle(m.meta.tokens))}">¥${fmtCost(m.meta.cost)}</span>`
          : "";
        const reasoning = out && m.meta && m.meta.reasoning ? String(m.meta.reasoning) : "";
        const think = reasoning
          ? `<details class="msg-think" open><summary>思考过程<em>${reasoning.length} 字</em></summary><div class="think-body">${esc(reasoning)}</div></details>`
          : "";
        const tools = out && m.meta && Array.isArray(m.meta.tools) ? m.meta.tools : [];
        const toolsBlock = tools.length
          ? `<details class="msg-think msg-tools"><summary>工具调用<em>${tools.length} 次</em></summary><div class="think-body">${tools.map(toolCardHtml).join("")}</div></details>`
          : "";
        const typeLabel = MSG_TYPE_LABELS[m.msg_type] || m.msg_type;
        const imageSrc =
          m.msg_type === "image" && m.id
            ? `/api/chats/${encodeURIComponent(state.activeChat)}/messages/${encodeURIComponent(m.id)}/image`
            : "";
        let text;
        if (imageSrc) {
          text = `<a class="msg-image-link" href="${imageSrc}" target="_blank" rel="noopener"><img class="msg-image" src="${imageSrc}" alt="图片" loading="lazy" onerror="this.closest('.bubble').textContent='[图片]'"></a>`;
        } else if (m.msg_type === "emoji" && m.meta && m.meta.emoji && m.meta.emoji.url) {
          const info = m.meta.emoji;
          const src = emojiProxyUrl(info);
          const faved = state.emojis.some((e) => e.url === info.url);
          text = `<div class="msg-emoji-wrap">
            <img class="msg-emoji" src="${src}" alt="表情" loading="lazy"
                 onerror="this.closest('.msg-emoji-wrap').replaceWith(document.createTextNode('[表情]'))">
            <div class="emoji-actions">
              <button type="button" class="${faved ? "faved" : ""}" title="${faved ? "已收藏" : "收藏"}"
                      onclick="favEmoji('${esc(m.id)}')">★</button>
              <a href="${emojiProxyUrl(info, true)}" download="emoji.gif" title="下载">⬇</a>
            </div>
          </div>`;
        } else if ((m.msg_type === "link" || m.msg_type === "text") && m.meta && m.meta.refer) {
          // 引用消息：回复文本 + 引用块；被引用的是图片/表情时直接渲染原图
          const r = m.meta.refer;
          const reply = (m.content || "").replace(/\s*\[引用[^\n]*\]\s*$/, "").trim();
          const refId = r.msg_id || m.id;
          const refImg =
            String(r.type) === "3" && refId
              ? `/api/chats/${encodeURIComponent(state.activeChat)}/messages/${encodeURIComponent(refId)}/image`
              : "";
          const refEmoji = r.emoji && r.emoji.url ? emojiProxyUrl(r.emoji) : "";
          const quoteBody = refImg
            ? `<a href="${refImg}" target="_blank" rel="noopener"><img class="msg-quote-img" src="${refImg}" alt="引用图片" loading="lazy" onerror="this.replaceWith(document.createTextNode('[图片]'))"></a>`
            : refEmoji
            ? `<a href="${refEmoji}" target="_blank" rel="noopener"><img class="msg-quote-img" src="${refEmoji}" alt="引用表情" loading="lazy" onerror="this.replaceWith(document.createTextNode('[表情]'))"></a>`
            : esc(r.summary || "");
          text = `${renderTextEmojis(esc(reply))}<div class="msg-quote"><div class="msg-quote-name">${esc(r.displayname || "引用消息")}</div><div class="msg-quote-body">${quoteBody}</div></div>`;
        } else if (m.msg_type === "text") {
          text = renderTextEmojis(esc(m.content || ""));
        } else {
          text = esc(m.content || `[${typeLabel}]`);
        }
        const revokedTag = m.is_revoked ? '<span class="revoked-tag">已撤回</span>' : "";
        return `<div class="msg ${out ? "out" : "in"}">
          ${sender}
          ${think}${toolsBlock}
          <div class="bubble${m.is_revoked ? " is-revoked" : ""}">${text}</div>
          <div class="msg-meta">${aiTag}${costTag}${revokedTag}<span>${fmtClock(m.ts)}</span></div>
        </div>`;
      })
      .join("") || '<div class="empty-mini" style="text-align:center;padding:40px">还没有消息</div>';
  if (state.autoThink || state.autoTools.length) {
    const liveThink = state.autoThink
      ? `<details class="msg-think" open><summary>思考中…</summary><div class="think-body auto-think-body">${esc(state.autoThink)}</div></details>`
      : "";
    const liveTools = state.autoTools.length
      ? `<details class="msg-think" open><summary>AI 正在调用工具<em>${state.autoTools.length}</em></summary><div class="think-body">${state.autoTools.map(toolCardHtml).join("")}</div></details>`
      : "";
    threadHtml += `<div class="msg out auto-live-wrap">${liveThink}${liveTools}</div>`;
  }
  $("#messages").innerHTML = threadHtml;
  state.autoLiveEl = $("#messages .auto-live-wrap");
  scrollBottom($("#messages"));
}

function scrollBottom(el) {
  if (!el) return;
  requestAnimationFrame(() => (el.scrollTop = el.scrollHeight));
}

async function sendChat() {
  const input = $("#composer");
  const text = input.value.trim();
  if (!text || !state.activeChat) return;
  input.value = "";
  autoGrow(input);
  try {
    await api(`/api/chats/${encodeURIComponent(state.activeChat)}/send`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const data = await api(`/api/chats/${encodeURIComponent(state.activeChat)}/messages?limit=100`);
    state.messages = data.messages;
    state.chat = data.chat;
    renderThread();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function resumeChat(uid) {
  const target = uid || state.activeChat;
  if (!target) return;
  await api(`/api/chats/${encodeURIComponent(target)}/resume`, { method: "POST" });
  if (target === state.activeChat) await selectChat(target);
  else loadChats();
  toast("已恢复 AI 自动回复", "ok");
}

async function toggleChatAi(enabled) {
  if (!state.activeChat) return;
  await api(`/api/chats/${encodeURIComponent(state.activeChat)}/ai`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  if (state.chat) state.chat.ai_enabled = enabled;
  renderThread();
  toast(enabled ? "已开启该会话的 AI 回复" : "已关闭该会话的 AI 回复");
}

/* ---------------------------------------------------------------- 表情 */
const EMOJI_PAGE = 200; // 表情墙分页大小：首屏只拉一页，点「加载更多」翻页

async function loadEmojis() {
  try {
    state.emojis = await api(`/api/emojis?limit=${EMOJI_PAGE}`);
    state.emojiHasMore = state.emojis.length >= EMOJI_PAGE;
  } catch (e) {
    state.emojis = [];
    state.emojiHasMore = false;
  }
  await loadTagLocks();
  const alive = new Set(state.emojis.map((e) => e.id));
  for (const id of [...state.wallSel]) if (!alive.has(id)) state.wallSel.delete(id);
  renderEmojiPanel();
  renderEmojiWall();
}

async function loadTagLocks() {
  try {
    const r = await api("/api/emoji/tag-locks");
    state.tagLocks = new Set(r.tags || []);
  } catch {
    state.tagLocks = new Set();
  }
}

async function loadMoreEmojis() {
  const btn = $("#btnWallMore");
  if (btn) { btn.disabled = true; btn.textContent = "加载中…"; }
  try {
    const more = await api(`/api/emojis?limit=${EMOJI_PAGE}&offset=${state.emojis.length}`);
    const known = new Set(state.emojis.map((e) => e.id));
    const fresh = more.filter((e) => !known.has(e.id)); // 服务端不认 offset 时防重复
    state.emojis = state.emojis.concat(fresh);
    state.emojiHasMore = fresh.length > 0 && more.length >= EMOJI_PAGE;
  } catch (e) {
    toast(e.message, "err");
  } finally {
    if (btn) { btn.disabled = false; }
  }
  renderEmojiWall();
}

async function favEmoji(mid) {
  const m = state.messages.find((x) => x.id === mid);
  const info = m && m.meta && m.meta.emoji;
  if (!info || !info.url) return;
  if (state.emojis.some((e) => e.url === info.url || (info.md5 && e.md5 === info.md5))) {
    toast("已在收藏里");
    return;
  }
  try {
    const r = await api("/api/emojis", {
      method: "POST",
      body: JSON.stringify({
        url: info.url,
        md5: info.md5 || "",
        width: info.width || 0,
        height: info.height || 0,
      }),
    });
    if (r.emoji) {
      // 后端按 md5 去重：返回的可能是已存在的行，替换而不是追加，避免面板重复
      state.emojis = state.emojis.filter((x) => String(x.id) !== String(r.emoji.id));
      state.emojis.unshift(r.emoji);
    }
    renderThread();
    renderEmojiPanel();
    toast("已收藏表情", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function sendFavEmoji(eid) {
  if (!state.activeChat) {
    toast("先选择一个会话", "err");
    return;
  }
  const e = state.emojis.find((x) => String(x.id) === String(eid));
  if (!e) return;
  try {
    await api(`/api/chats/${encodeURIComponent(state.activeChat)}/send-emoji`, {
      method: "POST",
      body: JSON.stringify({
        url: e.url,
        md5: e.md5 || "",
        width: e.width || 0,
        height: e.height || 0,
      }),
    });
    const data = await api(`/api/chats/${encodeURIComponent(state.activeChat)}/messages?limit=100`);
    state.messages = data.messages;
    state.chat = data.chat;
    renderThread();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function removeFavEmoji(eid) {
  try {
    await api(`/api/emojis/${encodeURIComponent(eid)}`, { method: "DELETE" });
    state.emojis = state.emojis.filter((x) => String(x.id) !== String(eid));
    renderThread();
    renderEmojiPanel();
    toast("已从收藏移除");
  } catch (e) {
    toast(e.message, "err");
  }
}

function toggleEmojiPanel() {
  const panel = $("#emojiPanel");
  if (!panel) return;
  panel.hidden = !panel.hidden;
  if (!panel.hidden) renderEmojiPanel();
}

function closeEmojiPanel() {
  const panel = $("#emojiPanel");
  if (panel) panel.hidden = true;
}

function renderEmojiPanel() {
  const grid = $("#emojiGrid");
  if (!grid) return;
  if (state.emojiTab === "builtin") {
    grid.innerHTML = Object.entries(EMOJI_MAP)
      .map(
        ([name, ch]) =>
          `<button type="button" class="emoji-item" data-name="${esc(name)}" title="[${esc(name)}]">${ch}</button>`
      )
      .join("");
    return;
  }
  if (state.emojiTab === "tagged") {
    const tagged = state.emojis.filter((e) => e.tags);
    grid.innerHTML = tagged.length
      ? tagged
          .map((e) => {
            const title = [e.tags, e.description].filter(Boolean).join(" | ") || "点击发送";
            return `<div class="emoji-item fav" data-eid="${e.id}" title="${esc(title)}">
              <img src="${emojiProxyUrl(e)}" alt="表情" loading="lazy">
            </div>`;
          })
          .join("")
      : '<div class="emoji-empty">还没有已打标的表情<br>到「表情包」页查看或手动打标</div>';
    return;
  }
  grid.innerHTML = state.emojis.length
    ? state.emojis
        .map((e) => {
          const tags = (e.tags || "").split(",").filter(Boolean).slice(0, 3).join(" · ");
          const title = [e.tags, e.description].filter(Boolean).join(" | ") || "点击发送";
          return `<div class="emoji-item fav" data-eid="${e.id}" title="${esc(title)}">
            <img src="${emojiProxyUrl(e)}" alt="表情" loading="lazy">
            <button type="button" class="emoji-del" data-del="${e.id}" title="移除">×</button>
            ${tags ? `<div class="emoji-tags">${esc(tags)}</div>` : ""}
          </div>`;
        })
        .join("")
    : '<div class="emoji-empty">还没有收藏<br>聊天点 ★ 收藏，别人发的会自动入库</div>';
}

function insertBuiltinEmoji(name) {
  const input = $("#composer");
  if (!input) return;
  input.value = (input.value || "") + `[${name}]`;
  input.focus();
  autoGrow(input);
}

function welcomeHtml() {
  return `<div class="agent-welcome" id="agentWelcome">
      <h3>今天想让肥鱼游去哪儿？</h3>
      <p>肥鱼可以直接潜入当前选中的微信账号：查会话、读上下文、发消息。</p>
      <div class="suggests">
        <button onclick="askAgent('帮我看看今天有哪些未回复的消息，列出来', [], '捞一捞没回的消息')">捞一捞没回的消息</button>
        <button onclick="askAgent('把今天群里的讨论总结一下', [], '吐个群聊总结')">吐个群聊总结</button>
        <button onclick="askAgent('给联系人发消息说我会晚到十分钟', [], '甩尾替我发条消息')">甩尾替我发条消息</button>
        <button onclick="askAgent('搜索最近提到“合同”的消息', [], '潜水搜聊天记录')">潜水搜聊天记录</button>
        <button onclick="askAgent('查一下 DeepSeek 余额和今天已经花了多少', [], '看看余额')">看看余额</button>
        <button onclick="askAgent('上一轮对话花了多少钱？', [], '查上轮话费')">查上轮话费</button>
      </div>
    </div>`;
}

const AGENT_PREFS = {
  model: () => `df_agent_model_${state.account || "default"}`,
  effort: () => `df_agent_effort_${state.account || "default"}`,
  conv: () => `df_agent_conv_${state.account || "default"}`,
};

const DEFAULT_AGENT_EFFORTS = [
  { value: "auto", label: "自动", desc: "不额外传参，由模型决定" },
  { value: "off", label: "关闭", desc: "尝试关闭思考" },
  { value: "low", label: "快速", desc: "较轻量的思考" },
  { value: "medium", label: "标准", desc: "均衡" },
  { value: "high", label: "深入", desc: "更充分的推理" },
];

const AGENT_STATUS = {
  idle: "就绪",
  thinking: "思考中",
  answering: "回答中",
  tool: "执行工具",
  stopped: "已停止",
  error: "出错",
};

const CHECK_SVG =
  '<svg class="pop-check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const agentState = {
  conversations: [],
  convId: null,
  convTitle: "",
  busy: false,
  abort: null,
  runId: 0,
  stopRequested: false,
  error: false,
  model: "",
  effort: "auto",
  prefModel: "",
  prefEffort: "auto",
  models: [],
  currentModel: "",
  liveModels: false,
  initialized: false,
  customMode: false,
  think: null,
  thinkClock: null,
  thinkStarted: 0,
  answerStarted: false,
  attachments: [],
  uploading: false,
  lastCost: null,
  lastTokens: null,
};

let agentRunSeq = 0;
let agentScrollQueued = false;

function scrollAgentBottom() {
  if (agentScrollQueued) return;
  agentScrollQueued = true;
  requestAnimationFrame(() => {
    agentScrollQueued = false;
    const el = $("#agentBody");
    if (el) el.scrollTop = el.scrollHeight;
  });
}

function setAgentState(kind) {
  const el = $("#agentState");
  if (!el) return;
  if (!AGENT_STATUS[kind]) kind = "idle";
  el.className = "agent-state " + kind;
  el.innerHTML = `<i></i>${AGENT_STATUS[kind]}`;
}

function renderAgentHeader() {
  const nameEl = $("#agentName");
  const subEl = $("#agentSub");
  if (!nameEl || !subEl) return;
  const acc = state.accounts.find((a) => a.id === state.account);
  const accName = acc ? acc.name : "";
  const title = agentState.convId ? agentState.convTitle || "对话" : "";
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  if (narrow) {
    nameEl.textContent = title || "Deep肥鱼 Agent";
    subEl.textContent = `当前账号：${accName} · 工具调用已开启`;
  } else {
    nameEl.textContent = "Deep肥鱼 Agent";
    subEl.textContent = title
      ? `对话：${title} · 工具调用已开启`
      : `当前账号：${accName} · 工具调用已开启`;
  }
  nameEl.title = title || "Deep肥鱼 Agent";
  renderWhaleBills();
}

function updateAgentSendButton() {
  const btn = $("#btnAgentSend");
  if (!btn) return;
  const busy = agentState.busy;
  const hasContent = !!($("#agentInput")?.value.trim() || agentState.attachments.length);
  btn.classList.toggle("stopping", busy);
  btn.disabled = !busy && (!hasContent || agentState.uploading);
  const label = busy ? "停止回答" : "发送";
  btn.title = label;
  btn.setAttribute("aria-label", label);
}

function visionEnabled() {
  return !!state.settings?.ai?.vision_enabled;
}

function syncAgentImageButton() {
  const btn = $("#btnAgentImage");
  if (!btn) return;
  btn.disabled = !visionEnabled();
  btn.title = visionEnabled() ? "添加图片" : "先在「设置 → 模型」开启识图";
}

function clearAgentAttachments() {
  agentState.attachments.forEach((a) => {
    if (a.preview) URL.revokeObjectURL(a.preview);
  });
  agentState.attachments = [];
  renderAgentAttachments();
}

function renderAgentAttachments() {
  const box = $("#agentAttachments");
  if (!box) return;
  box.hidden = !agentState.attachments.length;
  box.innerHTML = "";
  agentState.attachments.forEach((a, idx) => {
    const item = document.createElement("div");
    item.className = "agent-attach";
    const img = document.createElement("img");
    img.src = a.preview;
    img.alt = a.name || "图片";
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "×";
    del.title = "移除";
    del.addEventListener("click", () => {
      URL.revokeObjectURL(a.preview);
      agentState.attachments.splice(idx, 1);
      renderAgentAttachments();
      updateAgentSendButton();
    });
    item.append(img, del);
    box.appendChild(item);
  });
  updateAgentSendButton();
}

function compressImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const max = 1600;
      const scale = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.max(1, Math.round(img.naturalWidth * scale));
      const h = Math.max(1, Math.round(img.naturalHeight * scale));
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("图片处理失败"))), "image/jpeg", 0.86);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("无法读取图片"));
    };
    img.src = url;
  });
}

async function addAgentImages(files) {
  if (!visionEnabled()) {
    toast("先在「设置 → 模型」开启识图", "err");
    return;
  }
  const room = 4 - agentState.attachments.length;
  if (room <= 0) {
    toast("最多 4 张图片");
    return;
  }
  for (const file of Array.from(files).slice(0, room)) {
    if (!file.type.startsWith("image/")) continue;
    try {
      const blob = await compressImage(file);
      agentState.attachments.push({
        blob,
        preview: URL.createObjectURL(blob),
        name: (file.name || "image").replace(/\.[^.]+$/, "") + ".jpg",
      });
    } catch (e) {
      toast(e.message, "err");
    }
  }
  renderAgentAttachments();
}

async function uploadAgentAttachments(items) {
  const out = [];
  for (const item of items) {
    const fd = new FormData();
    fd.append("file", item.blob, item.name || "image.jpg");
    const headers = {};
    const token = getToken();
    if (token) headers["X-Access-Token"] = token;
    if (state.account) headers["X-Account-Id"] = state.account;
    const res = await fetch("/api/agent/upload", { method: "POST", headers, body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `上传失败 (${res.status})`);
    out.push(data.url);
  }
  return out;
}

function effectiveModelId() {  return agentState.model || agentState.currentModel || state.settings?.ai?.model || "";
}

function fallbackEfforts(modelId) {
  const id = String(modelId || "").toLowerCase();
  if (id.startsWith("deepseek")) {
    return [
      { value: "auto", label: "自动", desc: "跟随模型默认设置" },
      { value: "off", label: "关闭", desc: "尽量不输出思考过程" },
      { value: "low", label: "快速", desc: "较轻量的思考" },
      { value: "high", label: "深入", desc: "更充分的推理" },
      { value: "max", label: "最深", desc: "模型允许的最大思考量" },
    ];
  }
  if (id.startsWith("mimo")) {
    return [
      { value: "auto", label: "自动", desc: "跟随模型默认设置" },
      { value: "off", label: "关闭", desc: "不启用深度思考" },
      { value: "on", label: "开启", desc: "启用深度思考" },
    ];
  }
  return DEFAULT_AGENT_EFFORTS;
}

function modelEfforts(modelId) {
  const id = modelId || agentState.currentModel;
  const found = agentState.models.find((m) => m.id === id);
  return found && Array.isArray(found.efforts) && found.efforts.length ? found.efforts : fallbackEfforts(id);
}

function renderAgentPickers() {
  const modelLabel = $("#modelPickerLabel");
  const effortLabel = $("#effortPickerLabel");
  if (!modelLabel || !effortLabel) return;
  const effective = effectiveModelId();
  modelLabel.textContent = effective || "选择模型";
  modelLabel.title = effective;
  const options = modelEfforts(effective);
  if (!options.some((o) => o.value === agentState.effort)) agentState.effort = "auto";
  const current = options.find((o) => o.value === agentState.effort) || options[0];
  effortLabel.textContent = `思考 · ${current.label}`;
  syncAgentImageButton();
  if ($("#modelPop") && !$("#modelPop").hidden) renderModelPop();
  if ($("#effortPop") && !$("#effortPop").hidden) renderEffortPop();
}

function renderModelPop() {
  const pop = $("#modelPop");
  if (!pop) return;
  const effective = effectiveModelId();
  const ids = [];
  if (agentState.currentModel) ids.push(agentState.currentModel);
  agentState.models.forEach((m) => {
    if (m.id && !ids.includes(m.id)) ids.push(m.id);
  });
  if (agentState.model && !ids.includes(agentState.model)) ids.push(agentState.model);
  const rows = ids
    .map(
      (id) => `<button class="pop-item ${id === effective ? "active" : ""}" data-model="${esc(id)}" role="option" aria-selected="${id === effective}">
        <span class="pop-main"><span class="pop-name">${esc(id)}${id === agentState.currentModel ? '<span class="pop-tag">默认</span>' : ""}</span></span>
        ${id === effective ? CHECK_SVG : ""}
      </button>`
    )
    .join("");
  pop.innerHTML = `<div class="pop-title">选择模型</div>
    ${rows || '<div class="pop-note" style="border:0;margin:0">暂无可选模型，可直接输入模型 ID</div>'}
    ${
      agentState.customMode
        ? `<form class="pop-custom-form" id="modelCustomForm"><input id="modelCustomInput" placeholder="输入模型 ID" value="${esc(agentState.model || "")}" autocomplete="off" /><button type="submit">确定</button></form>`
        : `<button class="pop-item" data-custom="1"><span class="pop-main"><span class="pop-name">自定义模型…</span><span class="pop-desc">手动填写服务商支持的模型 ID</span></span></button>`
    }
    <div class="pop-note">仅影响当前对话，自动回复仍用设置里的模型。<br>${agentState.liveModels ? "列表来自服务商 /models" : "未读取到服务商列表，显示默认项"}</div>`;
  if (agentState.customMode) setTimeout(() => $("#modelCustomInput")?.focus(), 0);
}

function renderEffortPop() {
  const pop = $("#effortPop");
  if (!pop) return;
  const effective = effectiveModelId();
  const options = modelEfforts(effective);
  pop.innerHTML =
    `<div class="pop-title">思考强度</div>` +
    options
      .map(
        (o) => `<button class="pop-item ${o.value === agentState.effort ? "active" : ""}" data-effort="${esc(o.value)}" role="option" aria-selected="${o.value === agentState.effort}">
        <span class="pop-main"><span class="pop-name">${esc(o.label)}</span><span class="pop-desc">${esc(o.desc || "")}</span></span>
        ${o.value === agentState.effort ? CHECK_SVG : ""}
      </button>`
      )
      .join("") +
    `<div class="pop-note">当前模型：${esc(effective || "未配置")}<br>自动表示不额外传参，仅影响当前对话。</div>`;
}

function openPicker(which) {
  const isModel = which === "model";
  const pop = isModel ? $("#modelPop") : $("#effortPop");
  const btn = isModel ? $("#btnModelPicker") : $("#btnEffortPicker");
  const otherPop = isModel ? $("#effortPop") : $("#modelPop");
  const otherBtn = isModel ? $("#btnEffortPicker") : $("#btnModelPicker");
  if (!pop || !btn) return;
  const willOpen = pop.hidden;
  if (otherPop) otherPop.hidden = true;
  if (otherBtn) {
    otherBtn.classList.remove("active");
    otherBtn.setAttribute("aria-expanded", "false");
  }
  if (isModel) renderModelPop();
  else renderEffortPop();
  pop.hidden = !willOpen;
  btn.classList.toggle("active", willOpen);
  btn.setAttribute("aria-expanded", String(willOpen));
  const scrim = $("#pickerScrim");
  if (scrim) scrim.classList.toggle("show", willOpen);
}

function closePickers() {
  [$("#modelPop"), $("#effortPop")].forEach((p) => {
    if (p) p.hidden = true;
  });
  [$("#btnModelPicker"), $("#btnEffortPicker")].forEach((b) => {
    if (!b) return;
    b.classList.remove("active");
    b.setAttribute("aria-expanded", "false");
  });
  const scrim = $("#pickerScrim");
  if (scrim) scrim.classList.remove("show");
  agentState.customMode = false;
}

function setAgentModel(id) {
  const value = String(id || "").trim();
  agentState.model = value;
  agentState.prefModel = value;
  localStorage.setItem(AGENT_PREFS.model(), value);
  if (!modelEfforts(value).some((o) => o.value === agentState.effort)) {
    agentState.effort = "auto";
    agentState.prefEffort = "auto";
    localStorage.setItem(AGENT_PREFS.effort(), "auto");
  }
  agentState.customMode = false;
  renderAgentPickers();
  closePickers();
}

function setAgentEffort(value) {
  agentState.effort = value || "auto";
  agentState.prefEffort = agentState.effort;
  localStorage.setItem(AGENT_PREFS.effort(), agentState.effort);
  renderAgentPickers();
  closePickers();
}

async function loadAgentModels() {
  try {
    const r = await api("/api/agent/models");
    agentState.currentModel = r.current || "";
    agentState.models = r.models || [];
    agentState.liveModels = !!r.live;
  } catch (e) {
    agentState.currentModel = state.settings?.ai?.model || agentState.currentModel;
    if (!agentState.models.length && agentState.currentModel) {
      agentState.models = [{ id: agentState.currentModel, efforts: DEFAULT_AGENT_EFFORTS }];
    }
  }
  renderAgentPickers();
}

function renderAgentConversations() {
  const list = $("#agentConvList");
  if (!list) return;
  if (!agentState.conversations.length) {
    list.innerHTML = '<div class="ah-empty">还没有对话<br><span>发出第一条指令后会出现在这里</span></div>';
    return;
  }
  list.innerHTML = agentState.conversations
    .map((c) => {
      const active = c.id === agentState.convId ? " active" : "";
      const preview = (c.preview || "等待回复").replace(/\s+/g, " ").slice(0, 28);
      return `<div class="conv-item${active}" data-cid="${esc(c.id)}" role="button" tabindex="0" title="${esc(c.title || "新对话")}">
        <div class="conv-title">${esc(c.title || "新对话")}</div>
        <div class="conv-sub"><span class="conv-preview">${esc(preview)}</span><span class="conv-time">${fmtTime(c.updated_at)}</span></div>
        <button class="conv-del" data-del="${esc(c.id)}" title="删除对话" aria-label="删除对话">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
        </button>
      </div>`;
    })
    .join("");
}

async function loadAgentConversations() {
  try {
    agentState.conversations = await api("/api/agent/conversations");
    renderAgentConversations();
  } catch (e) {
    /* 保留旧列表，避免流式过程中偶发失败把侧栏清空 */
  }
}

function openAgentHistory() {
  $("#agentLayout")?.classList.add("show-history");
  const scrim = $("#agentScrim");
  if (scrim) scrim.hidden = false;
}

function closeAgentHistory() {
  $("#agentLayout")?.classList.remove("show-history");
  const scrim = $("#agentScrim");
  if (scrim) scrim.hidden = true;
}

function newAgentConversation(opts = {}) {
  if (agentState.busy) {
    toast("请先停止当前回答");
    return;
  }
  agentState.convId = null;
  agentState.convTitle = "";
  agentState.model = agentState.prefModel || "";
  agentState.effort = agentState.prefEffort || "auto";
  agentState.customMode = false;
  agentState.uploading = false;
  agentState.lastCost = null;
  agentState.lastTokens = null;
  clearAgentAttachments();
  localStorage.removeItem(AGENT_PREFS.conv());
  const body = $("#agentBody");
  if (body) body.innerHTML = welcomeHtml();
  setAgentState("idle");
  renderAgentConversations();
  renderAgentPickers();
  renderAgentHeader();
  updateAgentSendButton();
  closeAgentHistory();
  if (!opts.silent) $("#agentInput")?.focus();
}

async function openAgentConversation(cid, opts = {}) {
  if (!cid) return;
  if (agentState.busy) {
    toast("请先停止当前回答");
    return;
  }
  if (cid === agentState.convId && !opts.force) {
    closeAgentHistory();
    return;
  }
  clearAgentAttachments();
  try {
    const data = await api(`/api/agent/conversations/${encodeURIComponent(cid)}`);
    agentState.convId = data.conversation.id;
    agentState.convTitle = data.conversation.title || "";
    agentState.model = data.conversation.model || "";
    agentState.effort = data.conversation.effort || "auto";
    if (!modelEfforts(agentState.model).some((o) => o.value === agentState.effort)) agentState.effort = "auto";
    localStorage.setItem(AGENT_PREFS.conv(), agentState.convId);
    renderAgentTurns(data.turns || []);
    setAgentState("idle");
    renderAgentConversations();
    renderAgentPickers();
    renderAgentHeader();
    updateAgentSendButton();
    closeAgentHistory();
  } catch (e) {
    toast(e.message, "err");
    if (String(e.message).includes("不存在")) {
      localStorage.removeItem(AGENT_PREFS.conv());
      newAgentConversation({ silent: true });
      loadAgentConversations();
    }
  }
}

async function deleteAgentConversation(cid) {
  if (agentState.busy) {
    toast("请先停止当前回答");
    return;
  }
  if (!confirm("删除这段对话？删除后无法恢复。")) return;
  try {
    await api(`/api/agent/conversations/${encodeURIComponent(cid)}`, { method: "DELETE" });
    const deletingCurrent = cid === agentState.convId;
    await loadAgentConversations();
    if (deletingCurrent) {
      localStorage.removeItem(AGENT_PREFS.conv());
      newAgentConversation({ silent: true });
    }
    toast("对话已删除");
  } catch (e) {
    toast(e.message, "err");
  }
}

function buildNote(text, cls = "") {
  const el = document.createElement("div");
  el.className = "a-note " + cls;
  el.textContent = text;
  return el;
}

function buildCostNote(cost, tokens) {
  const el = document.createElement("div");
  el.className = "a-note cost";
  el.textContent = `本轮消耗 ¥${fmtCost(cost)}`;
  el.title = billCostTitle(tokens);
  return el;
}

function buildUserMsg(text, images = []) {
  const wrap = document.createElement("div");
  wrap.className = "a-msg user";
  wrap.innerHTML = `<div class="a-avatar">我</div><div class="a-content"><div class="a-bubble"></div></div>`;
  wrap.querySelector(".a-bubble").textContent = text || "";
  if (images && images.length) {
    const grid = document.createElement("div");
    grid.className = "a-images";
    images.forEach((src) => {
      const img = document.createElement("img");
      img.src = mediaUrl(src);
      img.alt = "图片";
      img.loading = "lazy";
      grid.appendChild(img);
    });
    wrap.querySelector(".a-content").appendChild(grid);
  }
  return wrap;
}

function buildAssistantMsg(turn) {
  const wrap = document.createElement("div");
  wrap.className = "a-msg";
  wrap.innerHTML = `<div class="a-avatar">AI</div><div class="a-content"></div>`;
  const content = wrap.querySelector(".a-content");
  if (turn.reasoning) content.appendChild(buildThinkCard(turn.reasoning, { open: true }));
  (turn.tools || []).forEach((t) => {
    content.appendChild(buildToolCard(t.name, t.args, t.ok === null || t.ok === undefined ? null : t.result, t.ok));
  });
  if (turn.content) {
    const bubble = document.createElement("div");
    bubble.className = "a-bubble";
    bubble.textContent = turn.content;
    content.appendChild(bubble);
  }
  if (turn.meta && turn.meta.stopped) content.appendChild(buildNote("已停止生成"));
  if (turn.meta && turn.meta.note) content.appendChild(buildNote(turn.meta.note, "warn"));
  if (turn.meta && turn.meta.error) content.appendChild(buildNote("出错了：" + turn.meta.error, "err"));
  if (turn.meta && turn.meta.cost !== undefined && turn.meta.cost !== null) {
    content.appendChild(buildCostNote(turn.meta.cost, turn.meta.tokens));
  }
  if (!content.children.length) {
    const bubble = document.createElement("div");
    bubble.className = "a-bubble";
    bubble.textContent = "（没有返回内容）";
    content.appendChild(bubble);
  }
  return wrap;
}

function renderAgentTurns(turns) {
  const body = $("#agentBody");
  if (!body) return;
  const list = Array.isArray(turns) ? turns : [];
  const last = [...list].reverse().find((turn) => turn.role === "assistant");
  const lastMeta = last && last.meta && last.meta.cost !== undefined && last.meta.cost !== null
    ? last.meta
    : null;
  agentState.lastCost = lastMeta ? Number(lastMeta.cost) : null;
  agentState.lastTokens = lastMeta ? lastMeta.tokens || null : null;
  renderWhaleBills();
  if (!list.length) {
    body.innerHTML = welcomeHtml();
    return;
  }
  body.innerHTML = "";
  list.forEach((turn) => {
    if (turn.role === "user") body.appendChild(buildUserMsg(turn.meta?.display_text || turn.content, turn.meta?.images || []));
    else if (turn.role === "assistant") body.appendChild(buildAssistantMsg(turn));
  });
  scrollAgentBottom();
}

function appendAgentUser(text, images = []) {
  $("#agentWelcome")?.remove();
  const wrap = buildUserMsg(text, images);
  $("#agentBody").appendChild(wrap);
  scrollAgentBottom();
}

function createLiveAssistant() {
  $("#agentWelcome")?.remove();
  const wrap = document.createElement("div");
  wrap.className = "a-msg";
  wrap.innerHTML = `<div class="a-avatar">AI</div><div class="a-content"><div class="a-bubble typing" hidden></div></div>`;
  $("#agentBody").appendChild(wrap);
  scrollAgentBottom();
  return { wrap, content: wrap.querySelector(".a-content"), bubble: wrap.querySelector(".a-bubble") };
}

function buildThinkCard(text, opts = {}) {
  const details = document.createElement("details");
  details.className = "think-card" + (opts.live ? " live" : "");
  details.open = !!opts.open;
  details.innerHTML = `<summary>
      <span class="think-ico"><svg viewBox="0 0 24 24"><path d="M12 2l1.8 4.6L18.5 8.4l-4.7 1.8L12 15l-1.8-4.8L5.5 8.4l4.7-1.8L12 2z"/></svg></span>
      <span class="think-title">${opts.live ? "思考中…" : "思考过程"}</span>
      <span class="think-time"></span>
      <svg class="think-chev" viewBox="0 0 24 24"><path d="M7 10l5 5 5-5z"/></svg>
    </summary>
    <div class="think-body"></div>`;
  details.querySelector(".think-body").textContent = text || "";
  if (opts.time) details.querySelector(".think-time").textContent = opts.time;
  details.querySelector("summary").addEventListener("click", () => {
    details.dataset.touched = "1";
  });
  return details;
}

function ensureLiveThink(content, bubble) {
  if (agentState.think) return agentState.think;
  const details = buildThinkCard("", { live: true, open: true });
  content.insertBefore(details, bubble);
  agentState.think = details;
  agentState.thinkStarted = performance.now();
  const timeEl = details.querySelector(".think-time");
  agentState.thinkClock = setInterval(() => {
    if (!agentState.think) return;
    timeEl.textContent = `${Math.max(1, Math.round((performance.now() - agentState.thinkStarted) / 1000))}s`;
  }, 500);
  return details;
}

function finalizeLiveThink(keepOpen = false) {
  const details = agentState.think;
  if (!details) return;
  if (agentState.thinkClock) {
    clearInterval(agentState.thinkClock);
    agentState.thinkClock = null;
  }
  if (details.dataset.done === "1") return;
  details.dataset.done = "1";
  details.classList.remove("live");
  const secs = agentState.thinkStarted ? (performance.now() - agentState.thinkStarted) / 1000 : 0;
  const timeText = secs >= 1 ? `已思考 ${Math.round(secs)} 秒` : secs > 0 ? `已思考 ${secs.toFixed(1)} 秒` : "已思考";
  details.querySelector(".think-title").textContent = "思考过程";
  details.querySelector(".think-time").textContent = timeText;
  // 思考结束后默认保持展开，除非用户在思考过程中手动点过折叠
  if (!details.dataset.touched) details.open = true;
}

function buildToolCard(name, args, result, ok) {
  const hasResult = result !== undefined && result !== null;
  const details = document.createElement("details");
  details.className = "tool-card";
  details.innerHTML = `<summary>
      <span class="tool-ico">⚙</span>
      <span class="tool-name">${esc(name)}</span>
      <span class="tool-status ${ok === true ? "ok" : ok === false ? "err" : ""}">${ok === true ? "成功" : ok === false ? "失败" : "运行中…"}</span>
    </summary>
    <div class="tool-body">
      <div class="tool-label">参数</div>
      <pre class="tool-args"></pre>
      <div class="tool-label result-label" ${hasResult ? "" : "hidden"}>结果</div>
      <pre class="tool-result" ${hasResult ? "" : "hidden"}></pre>
    </div>`;
  details.querySelector(".tool-args").textContent = JSON.stringify(args || {}, null, 2);
  if (hasResult) {
    details.querySelector(".tool-result").textContent =
      typeof result === "string" ? result : JSON.stringify(result, null, 2);
  }
  return details;
}

function toolDone(details, ok, result) {
  const st = details.querySelector(".tool-status");
  st.textContent = ok ? "成功" : "失败";
  st.className = "tool-status " + (ok ? "ok" : "err");
  const label = details.querySelector(".result-label");
  const pre = details.querySelector(".tool-result");
  label.hidden = false;
  pre.hidden = false;
  pre.textContent = typeof result === "string" ? result : JSON.stringify(result, null, 2);
}

async function askAgent(text, images = [], displayText = "") {
  text = String(text || "").trim();
  images = Array.isArray(images) ? images : [];
  if (!text && !images.length) return;
  if (agentState.busy) {
    toast("智能体正在处理上一条指令");
    return;
  }
  if (!state.settings?.ai?.configured) {
    toast("请先在设置中配置 API Key", "err");
    go("settings");
    return;
  }
  const runId = ++agentRunSeq;
  agentState.runId = runId;
  agentState.busy = true;
  agentState.stopRequested = false;
  agentState.error = false;
  agentState.answerStarted = false;
  agentState.think = null;
  agentState.thinkStarted = 0;
  if (agentState.thinkClock) {
    clearInterval(agentState.thinkClock);
    agentState.thinkClock = null;
  }
  appendAgentUser(String(displayText || text).trim() || "看看这张图", images);
  const live = createLiveAssistant();
  const abort = new AbortController();
  agentState.abort = abort;
  setAgentState("thinking");
  updateAgentSendButton();

  let answer = "";
  let turnUsage = null;
  let turnFinished = false;
  const tools = new Map();

  try {
    const headers = { "Content-Type": "application/json" };
    if (getToken()) headers["X-Access-Token"] = getToken();
    if (state.account) headers["X-Account-Id"] = state.account;
    const res = await fetch("/api/agent/chat", {
      method: "POST",
      headers,
      signal: abort.signal,
      body: JSON.stringify({
        text,
        images,
        display_text: displayText || undefined,
        conversation_id: agentState.convId || undefined,
        model: agentState.model || undefined,
        effort: agentState.effort || "auto",
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `请求失败 ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (agentState.runId !== runId) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (agentState.runId !== runId) break;
        if (evt.type === "meta") {
          if (evt.conversation_id) {
            agentState.convId = evt.conversation_id;
            localStorage.setItem(AGENT_PREFS.conv(), evt.conversation_id);
          }
          agentState.convTitle = evt.title || agentState.convTitle;
          renderAgentHeader();
          loadAgentConversations();
        } else if (evt.type === "reasoning") {
          const card = ensureLiveThink(live.content, live.bubble);
          const body = card.querySelector(".think-body");
          body.textContent += evt.text || "";
          if (card.open) body.scrollTop = body.scrollHeight;
          if (!agentState.answerStarted) setAgentState("thinking");
          scrollAgentBottom();
        } else if (evt.type === "delta") {
          if (!agentState.answerStarted) {
            agentState.answerStarted = true;
            finalizeLiveThink(false);
            setAgentState("answering");
          }
          answer += evt.text || "";
          live.bubble.hidden = false;
          live.bubble.textContent = answer;
          scrollAgentBottom();
        } else if (evt.type === "tool_start") {
          setAgentState("tool");
          const card = buildToolCard(evt.name, evt.args, null, null);
          live.content.insertBefore(card, live.bubble);
          tools.set(evt.id, card);
          scrollAgentBottom();
        } else if (evt.type === "tool_end") {
          const card = tools.get(evt.id);
          if (card) toolDone(card, evt.ok, evt.result);
          setAgentState(answer ? "answering" : "thinking");
        } else if (evt.type === "note") {
          live.content.insertBefore(buildNote(evt.text, "warn"), live.bubble);
          scrollAgentBottom();
        } else if (evt.type === "done") {
          turnFinished = true;
          turnUsage = evt.usage && evt.usage.measured ? evt.usage : null;
        } else if (evt.type === "error") {
          agentState.error = true;
          turnFinished = true;
          turnUsage = evt.usage && evt.usage.measured ? evt.usage : null;
          finalizeLiveThink(true);
          live.content.appendChild(buildNote(evt.message || "模型返回错误", "err"));
          setAgentState("error");
          toast(evt.message || "模型返回错误", "err");
        }
      }
      if (agentState.runId !== runId) break;
    }
  } catch (e) {
    if (agentState.runId !== runId) return;
    if (e.name === "AbortError") {
      agentState.stopRequested = true;
    } else {
      agentState.error = true;
      finalizeLiveThink(true);
      live.content.appendChild(buildNote("出错了：" + e.message, "err"));
      setAgentState("error");
      toast(e.message, "err");
    }
  } finally {
    if (agentState.runId !== runId) return;
    finalizeLiveThink(agentState.stopRequested && !agentState.answerStarted);
    live.bubble.classList.remove("typing");
    if (agentState.stopRequested) {
      live.content.appendChild(buildNote("已停止生成"));
      setAgentState("stopped");
    } else if (!agentState.error) {
      setAgentState("idle");
    }
    if (!answer) {
      const hasTools = !!live.content.querySelector(".tool-card");
      if (agentState.stopRequested || agentState.error || hasTools || agentState.think) {
        live.bubble.hidden = true;
      } else {
        live.bubble.hidden = false;
        live.bubble.textContent = "（没有返回内容）";
      }
    }
    if (turnFinished) {
      const hasCost = turnUsage && turnUsage.cost !== null && turnUsage.cost !== undefined;
      agentState.lastCost = hasCost ? Number(turnUsage.cost) : null;
      agentState.lastTokens = turnUsage
        ? {
            input: Number(turnUsage.prompt_tokens) || 0,
            output: Number(turnUsage.completion_tokens) || 0,
          }
        : null;
      if (hasCost) live.content.appendChild(buildCostNote(agentState.lastCost, agentState.lastTokens));
      renderWhaleBills();
      refreshWhaleBalance();
    }
    agentState.busy = false;
    agentState.abort = null;
    updateAgentSendButton();
    loadAgentConversations();
  }
}
window.askAgent = askAgent;

function stopAgent() {
  if (!agentState.busy || !agentState.abort) return;
  agentState.stopRequested = true;
  agentState.abort.abort();
}

async function loadAgentPage() {
  if (!agentState.initialized) {
    agentState.initialized = true;
    agentState.prefModel = localStorage.getItem(AGENT_PREFS.model()) || "";
    agentState.prefEffort = localStorage.getItem(AGENT_PREFS.effort()) || "auto";
    agentState.model = agentState.prefModel;
    agentState.effort = agentState.prefEffort;
    if (!agentState.models.length) loadAgentModels();
    await loadAgentConversations();
    const saved = localStorage.getItem(AGENT_PREFS.conv()) || "";
    if (saved && agentState.conversations.some((c) => c.id === saved)) {
      await openAgentConversation(saved, { silent: true });
    } else {
      newAgentConversation({ silent: true });
    }
  } else {
    loadAgentConversations();
  }
}

function resetAgentState() {
  if (agentState.abort) {
    try {
      agentState.abort.abort();
    } catch {
      /* ignore */
    }
  }
  if (agentState.thinkClock) clearInterval(agentState.thinkClock);
  agentState.abort = null;
  agentState.runId = 0;
  agentState.busy = false;
  agentState.stopRequested = false;
  agentState.error = false;
  agentState.conversations = [];
  agentState.convId = null;
  agentState.convTitle = "";
  agentState.model = "";
  agentState.effort = "auto";
  agentState.prefModel = "";
  agentState.prefEffort = "auto";
  agentState.initialized = false;
  agentState.customMode = false;
  agentState.think = null;
  agentState.thinkClock = null;
  agentState.thinkStarted = 0;
  agentState.answerStarted = false;
  agentState.uploading = false;
  agentState.lastCost = null;
  agentState.lastTokens = null;
  clearAgentAttachments();
  const body = $("#agentBody");
  if (body) body.innerHTML = welcomeHtml();
  closePickers();
  closeAgentHistory();
  renderAgentConversations();
  renderAgentHeader();
  setAgentState("idle");
  updateAgentSendButton();
}

/* ---- 模型配置（profile）：一档 = Base URL + 模型名 + 自己那把 .env 密钥 ---- */
function activeProfile(ai) {
  const list = (ai && ai.profiles) || [];
  return list.find((p) => p.id === ai.active_profile) || list[0] || null;
}

function renderModelProfiles(ai) {
  const box = $("#modelProfiles");
  if (!box) return;
  const list = ai.profiles || [];
  if (!list.length) {
    box.innerHTML = `<span class="chip on">${esc(ai.model || "默认")}</span>`;
    const hint = $("#fProfileHint");
    if (hint) hint.textContent = "没读到模型配置档，直接改下方地址与模型名即可";
    return;
  }
  const active = ai.active_profile || list[0].id;
  box.innerHTML = list
    .map(
      (p) =>
        `<button type="button" class="chip ${p.id === active ? "on" : p.configured ? "" : "warn"}"
          data-profile="${esc(p.id)}" title="${esc(
          [p.description, p.base_url, p.model, `密钥：.env 的 ${p.key_env || "DEEPSEEK_API_KEY"}`]
            .filter(Boolean)
            .join(" · ")
        )}">${esc(p.name || p.id)}${p.configured ? "" : " ·未填 Key"}</button>`
    )
    .join("");
  const prof = list.find((p) => p.id === active) || list[0];
  const env = prof.key_env || "DEEPSEEK_API_KEY";
  const set = (id, text) => {
    const el = $(id);
    if (el) el.textContent = text;
  };
  set("#fProfileHint", `${prof.name || prof.id}：${prof.description || ""}（密钥读 .env 的 ${env}）`);
  set("#fApiKeyHint", `只写入项目根 .env 的 ${env}，不进 data/config.json`);
  set("#fModelHint", "这一档实际发给服务商的模型名，改了只覆盖这一档，不影响其他档");
}

async function switchModelProfile(id) {
  const ai = (state.settings && state.settings.ai) || {};
  if (!id || id === ai.active_profile) return;
  const base = (($("#fBaseUrl") || {}).value || "").trim();
  const model = (($("#fModel") || {}).value || "").trim();
  try {
    // 表单里没保存的地址/模型名先落回切换前的档，再切档，免得手改的内容被吞掉
    if ((base && base !== ai.base_url) || (model && model !== ai.model)) {
      const flush = { ai: {} };
      if (base) flush.ai.base_url = base;
      if (model) flush.ai.model = model;
      await api("/api/settings", { method: "PUT", body: JSON.stringify(flush) });
    }
    const res = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ ai: { active_profile: id } }),
    });
    fillSettings(res.settings);
    await loadStatus();
    loadAgentModels();
    const p = (res.settings.ai.profiles || []).find((x) => x.id === id) || {};
    toast(
      p.configured ? `已切到「${p.name || id}」` : `已切到「${p.name || id}」，这一档还没填 API Key`,
      p.configured ? "ok" : "warn"
    );
  } catch (e) {
    toast(e.message, "err");
  }
}

function fillSettings(s) {
  state.settings = s;
  $("#fBaseUrl").value = s.ai.base_url || "";
  $("#fApiKey").value = "";
  $("#fApiKey").placeholder = s.ai.configured ? s.ai.api_key : "sk-…";
  $("#fModel").value = s.ai.model || "";
  $("#fTemp").value = s.ai.temperature ?? 0.7;
  $("#fVisionEnabled").checked = !!s.ai.vision_enabled;
  $("#fWebEnabled").checked = s.ai.web_enabled !== false;
  $("#fMemoryEnabled").checked = s.ai.memory_enabled !== false;
  const mem = s.memory || {};
  $("#fMemoryProfile").checked = mem.profile_enabled !== false;
  $("#fMemoryContextChars").value = mem.context_chars ?? 1200;
  $("#fMemoryMaxItems").value = mem.max_items ?? 24;
  $("#fMemoryCompressAfter").value = mem.compress_after_messages ?? 24;
  const emb = mem.embedding || {};
  $("#fEmbeddingEnabled").checked = !!emb.enabled;
  $("#fEmbeddingBaseUrl").value = emb.base_url || "";
  $("#fEmbeddingModel").value = emb.model || "";
  $("#fEmbeddingKeyEnv").value = emb.key_env || "EMBEDDING_API_KEY";
  $("#fEmojiWithReply").checked = s.ai.emoji_with_reply !== false;
  $("#fEmojiAutoCollect").checked = s.ai.emoji_auto_collect !== false;
  $("#fWikiEmojiOnly").checked = !!s.ai.wiki_emoji_only;
  $("#fChuanziMode").checked = !!s.ai.chuanzi_mode;
  $("#fChuanziSiteEmoji").checked = !!s.ai.chuanzi_site_emoji;
  const cross = s.cross || {};
  $("#fCrossEnabled").checked = cross.enabled !== false;
  $("#fCrossRelay").checked = cross.relay !== false;
  $("#fCrossVisit").checked = cross.visit !== false;
  $("#fCrossDaily").value = cross.daily_limit ?? 30;
  $("#fCrossVisitGap").value = Math.round((cross.visit_gap_sec ?? 600) / 60);
  syncAgentImageButton();
  $("#fPrompt").value = s.ai.system_prompt || "";
  $("#fPersonaName").value = s.ai.persona_name || "";
  const prof = activeProfile(s.ai);
  renderModelProfiles(s.ai);
  $("#modelTag").textContent =
    `${prof ? prof.name || prof.id : s.ai.model || "模型"} · ${s.ai.configured ? "已配置" : "未配置"}`;

  $("#fAutoEnabled").checked = !!s.auto.enabled;
  $("#fDefaultAi").checked = !!s.auto.default_ai_on;
  $("#fGroupAt").checked = !!s.auto.group_need_at;
  $("#fEmojiReply").checked = s.auto.emoji_reply !== false;
  $("#fPatReply").checked = s.auto.pat_reply !== false;
  $("#fInstantReply").checked = !!s.auto.instant_reply;
  $("#fOnlyWl").checked = !!s.auto.only_whitelist;
  $("#fWhitelist").value = (s.auto.whitelist || []).join("\n");
  $("#fBlacklist").value = (s.auto.blacklist || []).join("\n");
  $("#fInterval").value = s.auto.min_interval_sec ?? 3;
  $("#fDaily").value = s.auto.daily_limit ?? 300;
  $("#fTakeover").value = s.auto.takeover_pause_min ?? 10;
  $("#fPrefix").value = s.auto.prefix || "";
  $("#fReplyTimeout").value = s.auto.reply_timeout_sec ?? 60;
  $("#fTimeoutFallback").value = s.auto.timeout_fallback || "";
  $("#fSplitReply").checked = !!s.auto.split_reply;
  $("#fAccessToken").value = s.server.access_token || "";
}

async function loadSettings() {
  try {
    fillSettings(await api("/api/settings"));
  } catch (e) {
    toast(e.message, "err");
  }
}

function lines(v) {
  return v
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
}

async function saveSettings() {
  const patch = {
    ai: {
      base_url: $("#fBaseUrl").value.trim(),
      api_key: $("#fApiKey").value.trim(),
      model: $("#fModel").value.trim(),
      temperature: parseFloat($("#fTemp").value) || 0.7,
      vision_enabled: $("#fVisionEnabled").checked,
      web_enabled: $("#fWebEnabled").checked,
      memory_enabled: $("#fMemoryEnabled").checked,
      emoji_with_reply: $("#fEmojiWithReply").checked,
      emoji_auto_collect: $("#fEmojiAutoCollect").checked,
      wiki_emoji_only: $("#fWikiEmojiOnly").checked,
      chuanzi_mode: $("#fChuanziMode").checked,
      chuanzi_site_emoji: $("#fChuanziSiteEmoji").checked,
      system_prompt: $("#fPrompt").value.trim(),
      persona_name: $("#fPersonaName").value.trim(),
    },
    memory: {
      profile_enabled: $("#fMemoryProfile").checked,
      context_chars: parseInt($("#fMemoryContextChars").value) || 1200,
      max_items: parseInt($("#fMemoryMaxItems").value) || 24,
      compress_after_messages: parseInt($("#fMemoryCompressAfter").value) || 24,
      embedding: {
        enabled: $("#fEmbeddingEnabled").checked,
        base_url: $("#fEmbeddingBaseUrl").value.trim(),
        model: $("#fEmbeddingModel").value.trim(),
        key_env: $("#fEmbeddingKeyEnv").value.trim() || "EMBEDDING_API_KEY",
      },
    },
    cross: {
      enabled: $("#fCrossEnabled").checked,
      relay: $("#fCrossRelay").checked,
      visit: $("#fCrossVisit").checked,
      daily_limit: parseInt($("#fCrossDaily").value) || 0,
      visit_gap_sec: (parseInt($("#fCrossVisitGap").value) || 0) * 60,
    },
    auto: {
      enabled: $("#fAutoEnabled").checked,
      default_ai_on: $("#fDefaultAi").checked,
      group_need_at: $("#fGroupAt").checked,
      emoji_reply: $("#fEmojiReply").checked,
      pat_reply: $("#fPatReply").checked,
      instant_reply: $("#fInstantReply").checked,
      only_whitelist: $("#fOnlyWl").checked,
      whitelist: lines($("#fWhitelist").value),
      blacklist: lines($("#fBlacklist").value),
      min_interval_sec: parseInt($("#fInterval").value) || 0,
      daily_limit: parseInt($("#fDaily").value) || 0,
      takeover_pause_min: parseInt($("#fTakeover").value) || 0,
      prefix: $("#fPrefix").value,
      reply_timeout_sec: parseFloat($("#fReplyTimeout").value) || 0,
      timeout_fallback: $("#fTimeoutFallback").value.trim(),
      split_reply: $("#fSplitReply").checked,
    },
    server: { access_token: $("#fAccessToken").value.trim() },
  };
  try {
    const res = await api("/api/settings", { method: "PUT", body: JSON.stringify(patch) });
    fillSettings(res.settings);
    await loadStatus();
    loadAgentModels();
    toast("设置已保存", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.saveSettings = saveSettings;

async function testAi() {
  const el = $("#testResult");
  el.className = "test-result";
  el.textContent = "正在测试…";
  try {
    const res = await api("/api/settings/test-ai", {
      method: "POST",
      body: JSON.stringify({
        base_url: $("#fBaseUrl").value.trim(),
        api_key: $("#fApiKey").value.trim() || undefined,
        model: $("#fModel").value.trim(),
      }),
    });
    el.className = "test-result ok";
    el.textContent = `连接正常 · ${res.latency_ms}ms · ${res.reply || "ok"}`;
    loadSettings();
    loadStatus();
  } catch (e) {
    el.className = "test-result err";
    el.textContent = e.message;
  }
}

/* ---- 人格预设：data/personas/*.json，可应用到全局或填入账号表单 ---- */
async function loadPersonaPresets(keep) {
  try {
    const res = await api("/api/personas");
    state.personaPresets = res.personas || [];
  } catch (e) {
    state.personaPresets = [];
  }
  const sel = $("#fPersonaPreset");
  const cur = keep || sel.value;
  const opts = ['<option value="">— 选择已保存的人格 —</option>'];
  for (const p of state.personaPresets) {
    const wiki = p.source === "ai-moe-wiki" ? "·图集" : "";
    const label =
      (p.persona_name && p.persona_name !== p.name ? `${p.name}（${p.persona_name}）` : p.name) + wiki;
    opts.push(`<option value="${esc(p.name)}">${esc(label)}</option>`);
  }
  sel.innerHTML = opts.join("");
  sel.value = cur && state.personaPresets.some((p) => p.name === cur) ? cur : "";
  $("#personaTag").textContent = `${state.personaPresets.length} 份`;
}

function currentPreset() {
  const name = $("#fPersonaPreset").value;
  return state.personaPresets.find((p) => p.name === name) || null;
}

function requirePreset() {
  const p = currentPreset();
  if (!p) toast("先在上方选择一个人格预设", "err");
  return p;
}

async function applyPresetGlobal() {
  const p = requirePreset();
  if (!p) return;
  $("#fPrompt").value = p.system_prompt || "";
  $("#fPersonaName").value = p.persona_name || "";
  await saveSettings();
}

async function applyPresetAccount() {
  const p = requirePreset();
  if (!p) return;
  $("#aPersonaName").value = p.persona_name || "";
  $("#aPersonaPrompt").value = p.system_prompt || "";
  toast("已填入账号表单，点「保存账号」后生效", "ok");
}

async function savePresetFrom(source) {
  const isGlobal = source === "global";
  const systemPrompt = (isGlobal ? $("#fPrompt").value : $("#aPersonaPrompt").value).trim();
  const personaName = (isGlobal ? $("#fPersonaName").value : $("#aPersonaName").value).trim();
  if (!systemPrompt && !personaName) {
    return toast(isGlobal ? "全局人设还是空的，没有可保存的内容" : "当前账号没有专属人设，先在「账号与通道」里填一点", "err");
  }
  const def = personaName || "新人格";
  const name = prompt("保存为人格文件，起个名字（存到 data/personas/，重名覆盖）：", def);
  if (!name || !name.trim()) return;
  try {
    await api("/api/personas", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), persona_name: personaName, system_prompt: systemPrompt }),
    });
    await loadPersonaPresets(name.trim());
    toast(`人格「${name.trim()}」已保存`, "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function deletePreset() {
  const p = requirePreset();
  if (!p) return;
  if (!confirm(`删除人格文件「${p.name}」？删除后不可恢复。`)) return;
  try {
    await api(`/api/personas/${encodeURIComponent(p.name)}`, { method: "DELETE" });
    await loadPersonaPresets();
    toast("人格文件已删除", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function syncWikiPersonas() {
  try {
    const res = await api("/api/personas/sync_wiki", { method: "POST" });
    await loadPersonaPresets();
    const skipped = (res.skipped || []).length ? `，跳过自建 ${res.skipped.length} 份` : "";
    toast(`图集预设已同步：新建 ${res.created} 份，更新 ${res.updated} 份${skipped}`, "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderAccTabs() {
  const tabs = $("#accTabs");
  if (!tabs) return;
  if (!state.editingAccount) {
    state.editingAccount = state.account || (state.accounts[0] || {}).id || "";
  }
  tabs.innerHTML = state.accounts
    .map(
      (a) => `<button class="acc-tab ${a.id === state.editingAccount ? "active" : ""}" data-id="${esc(a.id)}">
        <span class="acc-dot ${a.connected ? "on" : ""}"></span>${esc(a.name)}
      </button>`
    )
    .join("");
  tabs.querySelectorAll(".acc-tab").forEach((el) =>
    el.addEventListener("click", () => fillAccountForm(el.dataset.id))
  );
}

function fillAccountForm(id) {
  const a = state.accounts.find((x) => x.id === id);
  if (!a) return;
  state.editingAccount = id;
  $("#aName").value = a.name || "";
  $("#aNickname").value = a.nickname || "";
  $("#aPersonaName").value = (a.persona && a.persona.persona_name) || "";
  $("#aPersonaPrompt").value = (a.persona && a.persona.system_prompt) || "";
  $("#aDriver").value = a.driver || "auto";
  $("#aEnabled").value = a.enabled ? "1" : "0";
  $("#aHost").value = (a.wcferry && a.wcferry.host) || "127.0.0.1";
  $("#aPort").value = (a.wcferry && a.wcferry.port) || 10086;
  // 账号目录用状态变量兜底：下拉 options 未加载完时 select.value 会静默丢失，
  // 保存必须以 state 为准，避免把已配置的账号清空
  state.editingWeixin4Dir = (a.weixin4 && a.weixin4.account_dir) || "";
  const wx4Sel = $("#aWx4Dir");
  if (wx4Sel) {
    wx4Sel.value = state.editingWeixin4Dir;
    wx4Sel.onchange = () => {
      state.editingWeixin4Dir = wx4Sel.value;
    };
  }
  $("#aCallback").value = (a.webhook && a.webhook.callback_url) || "";
  $("#aToken").value = (a.webhook && a.webhook.token) || "";
  updateDriverFields();
  renderAccTabs();
  loadWeixin4Accounts();
  loadMemories();
}

/* ---- 微信 4.x 账号下拉（昵称 + 微信号 + 在线状态） ---- */
let wx4Loading = null;
function loadWeixin4Accounts() {
  const sel = $("#aWx4Dir");
  if (!sel) return;
  if (!wx4Loading) {
    wx4Loading = api("/api/weixin4/accounts")
      .then((r) => {
        const hint = $("#aWx4Hint");
        if (!r.ok && r.error) {
          if (hint) hint.textContent = r.error;
          return;
        }
        const cur = state.editingWeixin4Dir || "";
        const opts = ['<option value="">自动选择（按最近活跃）</option>'];
        for (const a of r.accounts || []) {
          const label =
            (a.nickname ? `${a.nickname}（${a.wxid}）` : `${a.wxid}（未读取到昵称）`) +
            (a.online ? " · 在线" : a.readable ? " · 离线" : " · 未使用");
          opts.push(`<option value="${esc(a.account_dir)}">${esc(label)}</option>`);
        }
        if (cur && !(r.accounts || []).some((x) => x.account_dir === cur)) {
          opts.push(`<option value="${esc(cur)}">${esc(cur)}（不在列表）</option>`);
        }
        sel.innerHTML = opts.join("");
        sel.value = cur;
        if (hint && r.ok) {
          const on = (r.accounts || []).filter((x) => x.online).length;
          hint.textContent = `检测到 ${r.accounts.length} 个账号，${on} 个在线`;
        }
      })
      .catch(() => {
        const hint = $("#aWx4Hint");
        if (hint) hint.textContent = "账号列表加载失败";
      })
      .finally(() => {
        wx4Loading = null;
      });
  } else {
    wx4Loading.then(() => {
      const s = $("#aWx4Dir");
      const cur = state.editingWeixin4Dir || "";
      if (s && [...s.options].some((o) => o.value === cur)) s.value = cur;
    });
  }
}

function updateDriverFields() {
  const d = $("#aDriver").value;
  $$("[data-driver]").forEach((el) => {
    // auto 在 4.x 机器上会解析为 weixin4，账号选择对 auto 同样生效
    el.hidden = !(el.dataset.driver === d || (d === "auto" && el.dataset.driver === "weixin4"));
  });
}

async function saveAccount() {
  const id = state.editingAccount;
  if (!id) return;
  const body = {
    name: $("#aName").value.trim() || "未命名账号",
    nickname: $("#aNickname").value.trim() || "Deep肥鱼",
    persona: {
      persona_name: $("#aPersonaName").value.trim(),
      system_prompt: $("#aPersonaPrompt").value.trim(),
    },
    driver: $("#aDriver").value,
    enabled: $("#aEnabled").value === "1",
    wcferry: { host: $("#aHost").value.trim() || "127.0.0.1", port: parseInt($("#aPort").value) || 10086 },
    weixin4: { account_dir: state.editingWeixin4Dir || "" },
    webhook: { callback_url: $("#aCallback").value.trim(), token: $("#aToken").value.trim() },
  };
  try {
    await api(`/api/accounts/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
    await loadAccounts();
    await loadSettings();
    toast("账号已保存，通道重启中（约 10-30 秒）", "ok");
    // 通道重启放后台了，稍后刷新状态与会话
    setTimeout(async () => {
      await loadStatus();
      await loadChats();
    }, 8000);
  } catch (e) {
    toast(e.message, "err");
  }
}

async function addAccount() {
  try {
    const res = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({
        name: `账号 ${state.accounts.length + 1}`,
        nickname: "Deep肥鱼",
        driver: "auto",
        enabled: true,
      }),
    });
    await loadAccounts();
    fillAccountForm(res.account.id);
    toast("已新增账号，配置后可在左上角切换", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function deleteAccount() {
  const id = state.editingAccount;
  if (!id) return;
  if (!confirm("删除账号只会移除本应用的账号配置，本地消息记录仍保留在 data/agent.db，不影响微信本身。确定删除？")) return;
  try {
    const res = await api(`/api/accounts/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.account = res.active_account || "";
    state.editingAccount = state.account;
    resetAgentState();
    await loadAccounts();
    await loadStatus();
    await loadChats();
    fillAccountForm(state.editingAccount);
    if (state.page === "agent") loadAgentPage();
    toast("账号已删除");
  } catch (e) {
    toast(e.message, "err");
  }
}

/* ---- 长期记忆管理 ---- */
const KIND_LABEL = { fact: "事实", preference: "偏好", event: "事件", relationship: "关系" };
const LAYER_LABEL = { core: "长期", episodic: "往事", session: "摘要" };

async function loadMemories() {
  const id = state.editingAccount;
  if (!id || !$("#memList")) return;
  try {
    const res = await api(`/api/accounts/${encodeURIComponent(id)}/memories`);
    state.memories = res.items || [];
    renderMemories();
  } catch (e) {
    state.memories = [];
    renderMemories();
  }
}

function renderMemories() {
  const box = $("#memList");
  if (!box) return;
  const items = state.memories || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty-mini">还没有记忆，聊几句试试，或在下方手动添加</div>';
    return;
  }
  box.innerHTML = items
    .map(
      (m) => `<div class="mem-item">
        <div class="mem-content">
          ${esc(m.content)}
          <div class="mem-meta">
            ${KIND_LABEL[m.kind] ? `<span class="tag">${KIND_LABEL[m.kind]}</span>` : ""}
            ${m.chat_uid ? '<span class="tag chat">仅该会话</span>' : '<span class="tag">全局</span>'}
            ${m.importance > 2 ? '<span class="tag">重要</span>' : ""}
          </div>
        </div>
        <div class="mem-ops">
          <button class="mini-btn" onclick="editMemory(${m.id})">改</button>
          <button class="mini-btn mini-btn-danger" onclick="deleteMemory(${m.id})">删</button>
        </div>
      </div>`
    )
    .join("");
}

async function addMemory() {
  const input = $("#fMemContent");
  const content = (input.value || "").trim();
  if (!content) {
    toast("先写点要记的内容", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(state.editingAccount)}/memories`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    input.value = "";
    await loadMemories();
    toast("已添加记忆", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.addMemory = addMemory;

async function editMemory(id) {
  const cur = (state.memories || []).find((m) => m.id === id);
  const next = prompt("修改这条记忆：", cur ? cur.content : "");
  if (next === null) return;
  const content = next.trim();
  if (!content) {
    toast("内容不能为空", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(state.editingAccount)}/memories/${id}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    });
    await loadMemories();
    toast("已更新", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.editMemory = editMemory;

async function deleteMemory(id) {
  if (!confirm("让 AI 忘掉这条记忆？")) return;
  try {
    await api(`/api/accounts/${encodeURIComponent(state.editingAccount)}/memories/${id}`, {
      method: "DELETE",
    });
    await loadMemories();
    toast("已删除");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.deleteMemory = deleteMemory;

/* ---- 记忆页（侧栏「记忆」）：记忆流 + 人物档案，跟随顶部账号切换 ---- */
function memFilterParams() {
  const p = new URLSearchParams();
  const kw = ($("#memPageSearch")?.value || "").trim();
  const layer = $("#memLayerFilter")?.value || "";
  const kind = $("#memKindFilter")?.value || "";
  const pinned = !!$("#memPinnedOnly")?.checked;
  if (kw) p.set("keyword", kw);
  if (layer) p.set("layer", layer);
  if (kind) p.set("kind", kind);
  if (pinned) p.set("pinned", "true");
  state.memoryPage.layer = layer;
  state.memoryPage.kind = kind;
  state.memoryPage.pinnedOnly = pinned;
  return p;
}

async function loadMemoryPage() {
  const list = $("#memPageList");
  if (!list) return;
  if (state.memoryPage.tab === "profiles") {
    await loadMemoryProfiles();
    return;
  }
  const params = memFilterParams();
  try {
    const res = await api(
      `/api/accounts/${encodeURIComponent(state.account)}/memories?${params.toString()}`
    );
    state.memoryPage.items = res.items || [];
    state.memoryPage.stats = res.stats || null;
    const alive = new Set(state.memoryPage.items.map((m) => m.id));
    for (const id of [...state.memoryPage.sel]) if (!alive.has(id)) state.memoryPage.sel.delete(id);
  } catch (e) {
    state.memoryPage.items = [];
    toast(e.message, "err");
  }
  renderMemoryPage();
}
window.loadMemoryPage = loadMemoryPage;

function renderMemStats() {
  const box = $("#memStats");
  if (!box) return;
  const s = state.memoryPage.stats || {};
  const total = Number(s.total || 0);
  const parts = [
    ["全部", total],
    ["长期", Number(s.core || 0)],
    ["往事", Number(s.episodic || 0)],
    ["摘要", Number(s.session || 0)],
    ["置顶", Number(s.pinned || 0)],
    ["档案", Number(s.profiles || 0)],
  ];
  box.innerHTML = parts
    .map(([label, value]) => `<span class="mem-stat"><b>${value}</b>${label}</span>`)
    .join("");
}

function renderMemoryPage() {
  const box = $("#memPageList");
  if (!box) return;
  const items = state.memoryPage.items || [];
  const selMode = state.memoryPage.selMode;
  box.classList.toggle("multi", selMode);
  renderMemStats();
  const count = $("#memPageCount");
  if (count) count.textContent = `${items.length} 条`;
  if (!items.length) {
    const hasFilter =
      ($("#memPageSearch")?.value || "").trim() ||
      state.memoryPage.layer ||
      state.memoryPage.kind ||
      state.memoryPage.pinnedOnly;
    box.innerHTML = `<div class="empty-mini">${
      hasFilter ? "没有匹配的记忆" : "还没有记忆，聊几句试试，或在下方手动添加"
    }</div>`;
    updateMemSelBar();
    return;
  }
  box.innerHTML = items
    .map((m) => {
      const when = m.updated_at
        ? new Date(m.updated_at * 1000).toLocaleString("zh-CN", { hour12: false })
        : "";
      const picked = selMode && state.memoryPage.sel.has(m.id);
      return `<div class="mem-item ${picked ? "picked" : ""}" data-mid="${m.id}">
        ${selMode ? `<i class="mem-check">${picked ? "✓" : ""}</i>` : ""}
        <div class="mem-content">
          ${esc(m.content)}
          <div class="mem-meta">
            <span class="tag layer-${esc(m.layer || "core")}">${LAYER_LABEL[m.layer] || "长期"}</span>
            ${KIND_LABEL[m.kind] ? `<span class="tag">${KIND_LABEL[m.kind]}</span>` : ""}
            ${m.chat_uid ? '<span class="tag chat">仅该会话</span>' : '<span class="tag">全局</span>'}
            ${m.pinned ? '<span class="tag pin">置顶</span>' : ""}
            ${m.importance > 2 ? '<span class="tag">重要</span>' : ""}
            ${m.source && m.source !== "manual" ? `<span class="tag">${esc(m.source)}</span>` : ""}
            ${when ? `<span class="tag">${esc(when)}</span>` : ""}
          </div>
        </div>
        ${selMode ? "" : `<div class="mem-ops">
          <button class="mini-btn" onclick="togglePageMemoryPin(${m.id}, ${m.pinned ? "false" : "true"})" title="置顶/取消置顶">${m.pinned ? "取消置顶" : "置顶"}</button>
          <button class="mini-btn" onclick="cyclePageMemoryLayer(${m.id})" title="切换层级">层</button>
          <button class="mini-btn" onclick="editPageMemory(${m.id})">改</button>
          <button class="mini-btn mini-btn-danger" onclick="deletePageMemory(${m.id})">删</button>
        </div>`}
      </div>`;
    })
    .join("");
  updateMemSelBar();
}

async function loadMemoryProfiles() {
  const box = $("#memProfileList");
  if (!box) return;
  box.innerHTML = `<div class="empty-mini">加载中…</div>`;
  try {
    const res = await api(`/api/accounts/${encodeURIComponent(state.account)}/memory-profiles`);
    state.memoryPage.profiles = res.items || [];
    state.memoryPage.stats = res.stats || state.memoryPage.stats;
  } catch (e) {
    state.memoryPage.profiles = [];
    toast(e.message, "err");
  }
  renderMemoryProfiles();
}
window.loadMemoryProfiles = loadMemoryProfiles;

function renderMemoryProfiles() {
  const box = $("#memProfileList");
  if (!box) return;
  const items = state.memoryPage.profiles || [];
  const count = $("#memPageCount");
  if (count) count.textContent = `${items.length} 份档案`;
  renderMemStats();
  if (!items.length) {
    box.innerHTML = `<div class="empty-mini">还没有人物档案；聊几轮后点「整理记忆」生成。</div>`;
    return;
  }
  box.innerHTML = items
    .map((p) => {
      const name = p.chat_uid ? (p.chat_name || p.chat_uid) : "全局档案";
      const when = p.updated_at
        ? new Date(p.updated_at * 1000).toLocaleString("zh-CN", { hour12: false })
        : "";
      const fields = [
        ["关系", p.relationship],
        ["偏好", p.preferences],
        ["雷区", p.taboos],
        ["最近", p.recent_topics],
      ].filter(([, v]) => v);
      return `<div class="mem-profile" data-uid="${esc(p.chat_uid || "")}">
        <div class="mem-profile-head">
          <strong>${esc(name)}</strong>
          ${
            p.chat_uid
              ? p.is_group
                ? '<span class="tag">群聊</span>'
                : '<span class="tag">私聊</span>'
              : '<span class="tag">全局</span>'
          }
          ${when ? `<span class="tag">${esc(when)}</span>` : ""}
          <div class="spacer"></div>
          ${p.chat_uid ? `<button class="mini-btn" data-rebuild-profile="${esc(p.chat_uid)}">重算</button>` : ""}
        </div>
        ${p.summary ? `<div class="mem-profile-summary">${esc(p.summary)}</div>` : ""}
        ${fields
          .map(
            ([label, value]) =>
              `<div class="mem-profile-row"><span>${label}</span><em>${esc(value)}</em></div>`
          )
          .join("")}
      </div>`;
    })
    .join("");
}

function switchMemTab(tab) {
  state.memoryPage.tab = tab === "profiles" ? "profiles" : "stream";
  $$(".mem-tabs .chip").forEach((b) =>
    b.classList.toggle("active", b.dataset.memTab === state.memoryPage.tab)
  );
  $("#memStreamPane").hidden = state.memoryPage.tab !== "stream";
  $("#memProfilesPane").hidden = state.memoryPage.tab !== "profiles";
  loadMemoryPage();
}
window.switchMemTab = switchMemTab;

async function togglePageMemoryPin(id, pinned) {
  try {
    await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/${id}`, {
      method: "PUT",
      body: JSON.stringify({ pinned: !!pinned }),
    });
    await loadMemoryPage();
  } catch (e) {
    toast(e.message, "err");
  }
}
window.togglePageMemoryPin = togglePageMemoryPin;

async function cyclePageMemoryLayer(id) {
  const cur = (state.memoryPage.items || []).find((m) => m.id === id);
  const order = ["core", "episodic", "session"];
  const next = order[(order.indexOf(cur?.layer || "core") + 1) % order.length];
  try {
    await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/${id}`, {
      method: "PUT",
      body: JSON.stringify({ layer: next }),
    });
    await loadMemoryPage();
    toast(`已切到${LAYER_LABEL[next] || next}`, "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.cyclePageMemoryLayer = cyclePageMemoryLayer;

async function rebuildMemoryProfile(chatUid) {
  try {
    await api(
      `/api/accounts/${encodeURIComponent(state.account)}/memory-profiles/${encodeURIComponent(chatUid)}/rebuild`,
      { method: "POST" }
    );
    await loadMemoryProfiles();
    toast("档案已重算", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.rebuildMemoryProfile = rebuildMemoryProfile;

async function consolidateMemories() {
  if (!confirm("整理记忆：合并重复、清理过期摘要、衰减长期未用记忆，并重算人物档案？")) return;
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/consolidate`, {
      method: "POST",
    });
    toast(`整理完成：合并 ${r.merged || 0}，清理 ${r.expired || 0}，档案 ${r.profiles || 0}`, "ok");
    await loadMemoryPage();
  } catch (e) {
    toast(e.message, "err");
  }
}
window.consolidateMemories = consolidateMemories;

async function reindexMemories() {
  try {
    const r = await api(
      `/api/accounts/${encodeURIComponent(state.account)}/memories/reindex?force=1`,
      { method: "POST" }
    );
    if (r.ok) toast(`向量索引已重建：${r.updated}/${r.total} 条`, "ok");
    else toast(r.error || "重建失败", "err");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.reindexMemories = reindexMemories;

function downloadJson(name, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
}

function exportMemories(items = null) {
  const rows = items || state.memoryPage.items || [];
  if (!rows.length) {
    toast("当前没有可导出的记忆", "err");
    return;
  }
  downloadJson(`memories-${state.account}-${new Date().toISOString().slice(0, 10)}.json`, {
    version: 2,
    account: state.account,
    exported_at: new Date().toISOString(),
    items: rows,
  });
  toast(`已导出 ${rows.length} 条记忆`, "ok");
}
window.exportMemories = exportMemories;

async function importMemoriesFile(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const items = Array.isArray(data) ? data : data.items || [];
    if (!items.length) throw new Error("文件里没有 items");
    const r = await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/import`, {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    toast(`导入完成：新增 ${r.added} 条`, "ok");
    await loadMemoryPage();
  } catch (e) {
    toast(`导入失败：${e.message}`, "err");
  }
}
window.importMemoriesFile = importMemoriesFile;

async function addPageMemory() {
  const input = $("#fPageMemContent");
  const content = (input.value || "").trim();
  if (!content) {
    toast("先写点要记的内容", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(state.account)}/memories`, {
      method: "POST",
      body: JSON.stringify({
        content,
        layer: $("#fPageMemLayer")?.value || "core",
        importance: parseInt($("#fPageMemImportance")?.value || "2", 10),
      }),
    });
    input.value = "";
    await loadMemoryPage();
    toast("已添加记忆", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.addPageMemory = addPageMemory;

async function editPageMemory(id) {
  const cur = (state.memoryPage.items || []).find((m) => m.id === id);
  const next = prompt("修改这条记忆：", cur ? cur.content : "");
  if (next === null) return;
  const content = next.trim();
  if (!content) {
    toast("内容不能为空", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/${id}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    });
    await loadMemoryPage();
    toast("已更新", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.editPageMemory = editPageMemory;

async function deletePageMemory(id) {
  if (!confirm("让 AI 忘掉这条记忆？")) return;
  try {
    await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/${id}`, {
      method: "DELETE",
    });
    await loadMemoryPage();
    toast("已删除");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.deletePageMemory = deletePageMemory;

/* ---- 记忆页多选批处理：批删 / 批量重要标记 / 导出 txt ---- */
function setMemSelMode(on) {
  state.memoryPage.selMode = !!on;
  if (!on) state.memoryPage.sel.clear();
  $("#btnMemMulti").textContent = on ? "退出多选" : "多选";
  $("#memSelBar").hidden = !on;
  renderMemoryPage();
}

function memSelToggle(mid) {
  if (state.memoryPage.sel.has(mid)) state.memoryPage.sel.delete(mid);
  else state.memoryPage.sel.add(mid);
  renderMemoryPage();
}

function memSelAll() {
  const items = state.memoryPage.items || [];
  if (!items.length) return;
  const allPicked = items.every((m) => state.memoryPage.sel.has(m.id));
  for (const m of items) {
    if (allPicked) state.memoryPage.sel.delete(m.id);
    else state.memoryPage.sel.add(m.id);
  }
  renderMemoryPage();
}

function updateMemSelBar() {
  const bar = $("#memSelBar");
  if (!bar || bar.hidden) return;
  $("#memSelCount").textContent = `已选 ${state.memoryPage.sel.size}`;
  for (const id of ["btnBatchMemImportance", "btnBatchMemExport", "btnBatchMemDelete"]) {
    $(`#${id}`).disabled = !state.memoryPage.sel.size;
  }
}

function memSelItems() {
  const sel = state.memoryPage.sel;
  return (state.memoryPage.items || []).filter((m) => sel.has(m.id));
}

async function memBatchDelete() {
  const ids = [...state.memoryPage.sel];
  if (!ids.length) return;
  if (!confirm(`确定忘掉选中的 ${ids.length} 条记忆吗？删除后不可恢复。`)) return;
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/batch-delete`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    state.memoryPage.sel.clear();
    await loadMemoryPage();
    toast(`已删除 ${r.deleted} 条记忆`, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function memBatchImportance() {
  const ids = [...state.memoryPage.sel];
  if (!ids.length) return;
  const picked = memSelItems();
  const allImportant = picked.length > 0 && picked.every((m) => m.importance > 2);
  const target = allImportant ? 2 : 3;
  const action = allImportant ? "取消重要" : "设为重要";
  if (!confirm(`对选中的 ${ids.length} 条记忆${action}？`)) return;
  const btn = $("#btnBatchMemImportance");
  const orig = btn.textContent;
  btn.disabled = true;
  let ok = 0, fail = 0;
  for (let i = 0; i < ids.length; i++) {
    btn.textContent = `${action} ${i + 1}/${ids.length}`;
    try {
      await api(`/api/accounts/${encodeURIComponent(state.account)}/memories/${ids[i]}`, {
        method: "PUT",
        body: JSON.stringify({ importance: target }),
      });
      ok++;
    } catch {
      fail++;
    }
  }
  btn.disabled = false;
  btn.textContent = orig;
  await loadMemoryPage();
  toast(`${action}完成：成功 ${ok}${fail ? `，失败 ${fail}` : ""}`, ok ? "ok" : "err");
}

function memBatchExport() {
  const picked = memSelItems();
  if (!picked.length) return;
  exportMemories(picked);
}

/* ---- 能力页（侧栏「能力」）：技能 / 插件 / MCP ---- */
function attr(s) {
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function capStat(label, value) {
  return `<span class="mem-stat"><b>${Number(value || 0)}</b>${esc(label)}</span>`;
}

function renderCapabilities(st) {
  state.capabilities = st;
  const tag = $("#capTag");
  if (tag) tag.textContent = st.enabled ? "已启用" : "已关闭";
  const stats = [
    ["技能", st.skills?.count || 0],
    ["插件", st.plugins?.count || 0],
    ["MCP", st.mcp?.count || 0],
    ["工具", (st.tools || []).length],
  ];
  $("#capStats").innerHTML = stats.map(([k, v]) => capStat(k, v)).join("");

  const mcpItems = st.mcp?.items || [];
  $("#capMcpCount").textContent = `${mcpItems.length} 个`;
  $("#capMcpList").innerHTML = mcpItems.length
    ? mcpItems
        .map((m) => {
          const tools = m.tools || [];
          return `<div class="cap-item">
            <div class="cap-item-head">
              <span class="cap-dot ${m.connected ? "on" : "off"}"></span>
              <strong>${esc(m.name)}</strong>
              <span class="tag">${esc(m.transport || "stdio")}</span>
              ${m.auto_reply ? '<span class="tag pin">自动回复</span>' : ""}
              <div class="spacer"></div>
              <button class="mini-btn" data-mcp-test="${attr(m.name)}">测试</button>
            </div>
            <div class="cap-meta">
              ${m.connected ? "已连接" : "未连接"} · ${tools.length} 个工具
              ${m.error ? ` · <span class="cap-err">${esc(m.error)}</span>` : ""}
            </div>
            ${tools.length ? `<div class="cap-chips">${tools.map((t) => `<span class="tag">${esc(t.name)}</span>`).join("")}</div>` : ""}
          </div>`;
        })
        .join("")
    : `<div class="empty-mini">还没有 MCP 服务器。把配置写进下面的 JSON，保存后会立即重连。</div>`;

  const skills = st.skills?.items || [];
  $("#capSkillCount").textContent = `${skills.length} 个`;
  $("#capSkillList").innerHTML = skills.length
    ? skills
        .map(
          (s) => `<div class="cap-item">
            <div class="cap-item-head">
              <strong>${esc(s.name)}</strong>
              <div class="spacer"></div>
              <button class="mini-btn" data-skill-view="${attr(s.name)}">查看正文</button>
            </div>
            <div class="cap-meta">${esc(s.description || "")}</div>
            <div class="cap-path">${esc(s.path || "")}</div>
          </div>`
        )
        .join("")
    : `<div class="empty-mini">还没有技能。在 skills/ 或 data/skills/ 下放一个 SKILL.md 即可。</div>`;

  const plugins = st.plugins?.items || [];
  $("#capPluginCount").textContent = `${plugins.length} 个`;
  $("#capPluginList").innerHTML = plugins.length
    ? plugins
        .map((p) => {
          const tools = p.tools || [];
          return `<div class="cap-item">
            <div class="cap-item-head">
              <strong>${esc(p.name)}</strong>
              ${p.version ? `<span class="tag">v${esc(p.version)}</span>` : ""}
              ${p.trusted ? '<span class="tag pin">trusted</span>' : ""}
              ${p.auto_reply ? '<span class="tag">自动回复</span>' : ""}
              <div class="spacer"></div>
            </div>
            <div class="cap-meta">${esc(p.description || "")}${p.error ? ` · <span class="cap-err">${esc(p.error)}</span>` : ""}</div>
            <div class="cap-path">${esc(p.path || "")}</div>
            ${tools.length ? `<div class="cap-chips">${tools.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
          </div>`;
        })
        .join("")
    : `<div class="empty-mini">还没有插件。在 plugins/ 或 data/plugins/ 下放 plugin.json 即可。</div>`;

  const tools = st.tools || [];
  $("#capTools").innerHTML = tools.length
    ? `<details><summary>当前挂载 ${tools.length} 个能力工具</summary><div class="cap-tool-grid">${tools
        .map(
          (t) =>
            `<div class="cap-tool"><b>${esc(t.name)}</b><span>${esc(t.description || "")}</span></div>`
        )
        .join("")}</div></details>`
    : "";
}

function fillCapabilitySwitches(st) {
  $("#capEnabled").checked = !!st.enabled;
  $("#capConsole").checked = !!st.console_enabled;
  $("#capAuto").checked = !!st.auto_reply_enabled;
  $("#capSkillsPrompt").checked = st.skills_in_prompt !== false;
}

async function loadCapabilitiesPage() {
  if (!$("#capStats")) return;
  try {
    const [st, cfg] = await Promise.all([
      api("/api/capabilities"),
      api("/api/capabilities/mcp-config"),
    ]);
    fillCapabilitySwitches(st);
    renderCapabilities(st);
    loadBiliCard();
    $("#capMcpConfig").value = cfg.text || "";
    $("#capMcpPath").textContent = cfg.path || "data/mcp.json";
    $("#capMcpPath").title = cfg.path || "";
  } catch (e) {
    toast(e.message, "err");
  }
}
window.loadCapabilitiesPage = loadCapabilitiesPage;

async function saveCapabilitySettings(patch) {
  try {
    const st = await api("/api/capabilities/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    });
    fillCapabilitySwitches(st);
    renderCapabilities(st);
    toast("能力设置已保存", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function reloadCapabilities() {
  try {
    const st = await api("/api/capabilities/reload", { method: "POST" });
    fillCapabilitySwitches(st);
    renderCapabilities(st);
    toast("能力已重新加载", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.reloadCapabilities = reloadCapabilities;

async function saveMcpConfig() {
  const text = $("#capMcpConfig").value || "";
  try {
    JSON.parse(text || "{}");
  } catch (e) {
    toast(`JSON 不合法：${e.message}`, "err");
    return;
  }
  try {
    const st = await api("/api/capabilities/mcp-config", {
      method: "PUT",
      body: JSON.stringify({ text }),
    });
    fillCapabilitySwitches(st);
    renderCapabilities(st);
    toast("MCP 配置已保存并重载", "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}
window.saveMcpConfig = saveMcpConfig;

async function testMcpServer(name) {
  try {
    const r = await api(`/api/capabilities/mcp/${encodeURIComponent(name)}/test`, {
      method: "POST",
    });
    if (r.ok) toast(`MCP「${name}」连接正常`, "ok");
    else toast(`MCP「${name}」未连接：${r.status?.error || "未知原因"}`, "err");
    await loadCapabilitiesPage();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function viewSkill(name) {
  try {
    const s = await api(`/api/capabilities/skills/${encodeURIComponent(name)}`);
    const box = $("#capSkillViewer");
    box.hidden = false;
    box.textContent = `# ${s.name}\n${s.path}\n\n${s.body || ""}`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadBiliCard() {
  if (!$("#biliCard")) return;
  try {
    const [st, cfg, runs, chats] = await Promise.all([
      api("/api/bili/status"),
      api("/api/bili/config"),
      api("/api/bili/patrol/runs?limit=10"),
      api("/api/chats"),
    ]);
    renderBili(st, cfg, runs.items || [], chats || []);
  } catch (e) {
    const hint = $("#biliHint");
    if (hint) hint.textContent = "B 站未就绪：" + e.message;
  }
}
window.loadBiliCard = loadBiliCard;

function renderBili(st, cfg, runs, chats) {
  const setup = st.setup || {};
  const credState = st.credential_state || {};
  const emptyCred = credState.exists && !credState.has_sessdata;
  $("#biliTag").textContent = setup.phase || (setup.installed ? "已安装" : "未安装");
  $("#biliHint").textContent = setup.installed
    ? (st.credential?.logged_in
        ? `已登录：${st.credential.username || st.credential.uid || "B站"}`
        : (emptyCred ? "上次登录保存的凭据为空，请重新扫码。" : "运行环境已装，尚未登录。"))
    : "B 站工具默认关闭。先点「安装 / 修复」创建独立运行环境。";
  $("#biliBody").hidden = !setup.installed;
  $("#biliStats").innerHTML = [
    ["MCP", st.mcp?.connected ? "已连接" : "未连接"],
    ["工具", String((st.tools || []).length)],
    ["登录", st.credential?.logged_in ? "已登录" : (emptyCred ? "凭据为空" : "未登录")],
  ].map(([k, v]) => `<span><b>${k}</b>${esc(v)}</span>`).join("");
  $("#biliEnabled").checked = !!cfg.enabled;
  const box = $("#biliLoginBox");
  if (st.credential?.logged_in) {
    box.innerHTML = `<div class="cap-item"><strong>${esc(st.credential.username || "B站")}</strong><span class="muted">UID ${esc(st.credential.uid || "")}</span></div>`;
  } else {
    box.innerHTML = `<button class="btn" onclick="biliLoginStart()">扫码登录 B 站</button><div id="biliQr"></div>`;
  }
  const p = cfg.patrol || {};
  $("#biliKeywords").value = p.keywords || "";
  $("#biliInterval").value = p.interval_min || 360;
  $("#biliDaily").value = p.daily_max ?? 3;
  $("#biliPerScan").value = p.per_scan || 1;
  $("#biliDelivery").value = p.delivery || "console";
  $("#biliPatrolOn").checked = !!p.enabled;
  const select = $("#biliTarget");
  select.innerHTML = `<option value="">不投递</option>` + (chats || []).map((c) =>
    `<option value="${attr(c.uid)}">${esc(c.name || c.uid)}</option>`
  ).join("");
  select.value = p.target_chat_uid || "";
  $("#biliRuns").innerHTML = runs.length
    ? runs.map((r) => `<div class="cap-item"><strong>#${r.id} · ${esc(r.status)}</strong><span class="muted">${esc(r.trigger)} · ${esc(r.report_path || r.error || "")}</span><button class="link-btn" style="margin-left:auto" onclick="openBiliPatrolLog(${Number(r.id)})">日志</button></div>`).join("")
    : `<div class="empty-mini">还没有巡逻记录。</div>`;
}

let biliSetupTimer = null;
let biliSetupOffset = 0;
let biliSetupText = "";

function biliTerminalNearBottom() {
  const term = $("#biliSetupTerm");
  return !term || term.scrollHeight - term.scrollTop - term.clientHeight < 36;
}

function appendBiliSetupLog(text) {
  if (!text) return;
  const term = $("#biliSetupTerm");
  if (!term) return;
  const wasNearBottom = biliTerminalNearBottom();
  biliSetupText += text;
  const lines = biliSetupText.split(/\r?\n/);
  if (lines.length > 5000) biliSetupText = lines.slice(-5000).join("\n");
  term.textContent = biliSetupText;
  if (wasNearBottom) term.scrollTop = term.scrollHeight;
}

function setBiliSetupStatus(text, type = "") {
  const status = $("#biliSetupStatus");
  if (!status) return;
  status.textContent = text;
  status.style.color = type === "err" ? "var(--red)" : type === "ok" ? "var(--green-d)" : "";
}

function finishBiliSetup() {
  if (biliSetupTimer) {
    clearInterval(biliSetupTimer);
    biliSetupTimer = null;
  }
  const btn = $("#biliSetupBtn");
  if (btn) {
    btn.disabled = false;
    btn.textContent = "安装 / 修复";
  }
}

async function pollBiliSetupLog() {
  try {
    const r = await api(`/api/bili/setup/log?offset=${biliSetupOffset}`);
    biliSetupOffset = Number(r.offset || biliSetupOffset);
    if (r.reset) biliSetupText = "";
    appendBiliSetupLog(r.text || "");
    setBiliSetupStatus(`当前阶段：${r.phase || "running"}`);
    if (r.done) {
      if (r.phase === "ok") {
        appendBiliSetupLog("\n✓ B 站运行环境安装完成，MCP 已重新加载\n");
        setBiliSetupStatus("安装完成", "ok");
        toast("B 站安装完成", "ok");
      } else if (r.phase === "error") {
        appendBiliSetupLog(`\n✗ ${r.error || "安装失败"}\n`);
        setBiliSetupStatus("安装失败", "err");
        toast(r.error || "B 站安装失败", "err");
      } else {
        appendBiliSetupLog("\n— 本轮安装任务结束 —\n");
        setBiliSetupStatus("已结束");
      }
      finishBiliSetup();
      await loadBiliCard();
    }
  } catch (e) {
    appendBiliSetupLog(`\n✗ 读取安装日志失败：${e.message}\n`);
    setBiliSetupStatus("日志读取失败", "err");
    finishBiliSetup();
  }
}

async function biliSetup() {
  const mask = $("#biliSetupMask");
  const term = $("#biliSetupTerm");
  if (!mask || !term) return;
  mask.hidden = false;
  term.textContent = "";
  biliSetupText = "";
  biliSetupOffset = 0;
  finishBiliSetup();
  setBiliSetupStatus("正在启动安装任务…");
  const btn = $("#biliSetupBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "安装中…";
  }
  appendBiliSetupLog(`$ B 站安装任务 · ${new Date().toLocaleString()}\n\n`);
  try {
    const started = await api("/api/bili/setup", { method: "POST" });
    biliSetupOffset = Number(started.log_offset || 0);
    if (!started.ok && started.error) appendBiliSetupLog(`⚠ ${started.error}\n`);
    biliSetupTimer = setInterval(pollBiliSetupLog, 800);
  } catch (e) {
    appendBiliSetupLog(`✗ 安装任务启动失败：${e.message}\n`);
    setBiliSetupStatus("启动失败", "err");
    finishBiliSetup();
  }
}
window.biliSetup = biliSetup;

function closeBiliSetupTerminal() {
  finishBiliSetup();
  const mask = $("#biliSetupMask");
  if (mask) mask.hidden = true;
}
window.closeBiliSetupTerminal = closeBiliSetupTerminal;

async function copyBiliSetupLog() {
  try {
    await navigator.clipboard.writeText(biliSetupText);
    toast("安装日志已复制", "ok");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = biliSetupText;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("安装日志已复制", "ok");
  }
}
window.copyBiliSetupLog = copyBiliSetupLog;

let biliPatrolTimer = null;
let biliPatrolOffset = 0;
let biliPatrolText = "";
let biliPatrolRunId = 0;
let biliPatrolMode = "idle";
let biliPatrolRunning = false;
let biliPatrolWaitNew = false;
let biliPatrolBaseline = 0;

function biliPatrolNearBottom() {
  const term = $("#biliPatrolTerm");
  return !term || term.scrollHeight - term.scrollTop - term.clientHeight < 36;
}

function patrolLineClass(kind, text) {
  if (kind === "tool_result") return String(text).includes("✗") ? "pl-err" : "pl-ok";
  if (kind === "done") return String(text).includes("失败") ? "pl-err" : "pl-done";
  const map = {
    think: "pl-think",
    tool: "pl-tool",
    text: "pl-text",
    note: "pl-note",
    error: "pl-err",
    done: "pl-done",
    info: "pl-info",
  };
  return map[kind] || "pl-text";
}

function classifyPatrolLine(line) {
  if (!line) return "info";
  if (line.includes("思考：")) return "think";
  if (line.includes("调用 ")) return "tool";
  if (line.includes("✗") || line.includes("错误：") || line.includes("巡逻失败")) return "err";
  if (line.includes("✓")) return "ok";
  if (line.includes("输出：")) return "text";
  if (line.includes("巡逻完成")) return "done";
  if (line.includes("提示：")) return "note";
  return "info";
}

function appendBiliPatrolLine(kind, text) {
  if (!text) return;
  const term = $("#biliPatrolTerm");
  if (!term) return;
  const wasNearBottom = biliPatrolNearBottom();
  const div = document.createElement("div");
  div.className = `bili-patrol-line ${patrolLineClass(kind, text)}`;
  div.textContent = text;
  term.appendChild(div);
  while (term.childElementCount > 5000) term.removeChild(term.firstElementChild);
  biliPatrolText += text + "\n";
  if (biliPatrolText.length > 400000) biliPatrolText = biliPatrolText.slice(-300000);
  if (wasNearBottom) term.scrollTop = term.scrollHeight;
}

function appendBiliPatrolItem(item) {
  if (!item) return;
  const text = String(item.text || "");
  if (!text) return;
  if (item.kind === "file") {
    for (const line of text.split(/\r?\n/)) {
      if (line.trim()) appendBiliPatrolLine(classifyPatrolLine(line), line);
    }
    return;
  }
  appendBiliPatrolLine(item.kind || "info", `[${item.time || "--:--:--"}] ${text}`);
}

function setBiliPatrolStatus(text, type = "") {
  const status = $("#biliPatrolStatus");
  if (!status) return;
  status.textContent = text;
  status.style.color = type === "err" ? "var(--red)" : type === "ok" ? "var(--green-d)" : "";
}

function stopBiliPatrolTimer() {
  if (biliPatrolTimer) {
    clearInterval(biliPatrolTimer);
    biliPatrolTimer = null;
  }
}

async function openBiliPatrolLog(runId) {
  const mask = $("#biliPatrolMask");
  if (!mask) return;
  mask.hidden = false;
  stopBiliPatrolTimer();
  biliPatrolText = "";
  biliPatrolOffset = 0;
  biliPatrolRunId = 0;
  const term = $("#biliPatrolTerm");
  if (term) term.textContent = "";
  const title = $("#biliPatrolTitle");
  if (runId) {
    biliPatrolMode = "history";
    if (title) title.textContent = `B 站巡逻 #${runId}`;
    setBiliPatrolStatus("读取历史日志…");
    try {
      const r = await api(`/api/bili/patrol/runs/${encodeURIComponent(runId)}/events`);
      for (const item of r.items || []) appendBiliPatrolItem(item);
      setBiliPatrolStatus(r.error ? "失败" : "完成", r.error ? "err" : "ok");
    } catch (e) {
      setBiliPatrolStatus("读取失败", "err");
      appendBiliPatrolLine("error", `✗ 读取失败：${e.message}`);
    }
    return;
  }
  biliPatrolMode = "live";
  if (title) title.textContent = "B 站巡逻";
  setBiliPatrolStatus("等待巡逻…");
  await pollBiliPatrolLog();
  if (biliPatrolMode === "live" && !biliPatrolTimer) {
    biliPatrolTimer = setInterval(pollBiliPatrolLog, 800);
  }
}
window.openBiliPatrolLog = openBiliPatrolLog;

async function pollBiliPatrolLog() {
  if (biliPatrolMode !== "live") return;
  try {
    const r = await api(`/api/bili/patrol/active?after=${biliPatrolOffset}`);
    if (biliPatrolWaitNew) {
      if (!r.run_id || r.run_id === biliPatrolBaseline) {
        setBiliPatrolStatus("启动中…");
        return;
      }
      biliPatrolWaitNew = false;
    }
    if (!r.run_id) {
      if (biliPatrolRunning) {
        setBiliPatrolStatus("启动中…");
        return;
      }
      setBiliPatrolStatus("还没有巡逻记录");
      stopBiliPatrolTimer();
      return;
    }
    if (r.run_id !== biliPatrolRunId) {
      biliPatrolRunId = r.run_id;
      biliPatrolOffset = 0;
      biliPatrolText = "";
      const term = $("#biliPatrolTerm");
      if (term) term.textContent = "";
    }
    for (const item of r.items || []) {
      appendBiliPatrolItem(item);
      biliPatrolOffset = Math.max(biliPatrolOffset, Number(item.seq || 0));
    }
    if (r.done) {
      stopBiliPatrolTimer();
      setBiliPatrolStatus(r.error ? "失败" : "完成", r.error ? "err" : "ok");
      await loadBiliCard();
    } else {
      setBiliPatrolStatus("运行中…");
    }
  } catch (e) {
    stopBiliPatrolTimer();
    setBiliPatrolStatus("日志读取失败", "err");
    appendBiliPatrolLine("error", `✗ 日志读取失败：${e.message}`);
  }
}

function closeBiliPatrolLog() {
  stopBiliPatrolTimer();
  biliPatrolMode = "idle";
  biliPatrolWaitNew = false;
  const mask = $("#biliPatrolMask");
  if (mask) mask.hidden = true;
}
window.closeBiliPatrolLog = closeBiliPatrolLog;

async function copyBiliPatrolLog() {
  try {
    await navigator.clipboard.writeText(biliPatrolText);
    toast("巡逻日志已复制", "ok");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = biliPatrolText;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("巡逻日志已复制", "ok");
  }
}
window.copyBiliPatrolLog = copyBiliPatrolLog;

async function biliLoginStart() {
  try {
    const d = await api("/api/bili/login/start", { method: "POST" });
    if (!d || d.error || d.ok === false) {
      toast((d && d.error) || "B 站登录二维码生成失败", "err");
      return;
    }
    if (d.qrcode_image) {
      $("#biliQr").innerHTML = `<img src="${attr(d.qrcode_image)}" alt="B站登录二维码" style="width:160px;height:auto;margin-top:10px" />`;
    }
    if (d.message) toast(d.message, d.qrcode_image ? "ok" : "err");
    const timer = setInterval(async () => {
      try {
        const r = await api("/api/bili/login/poll");
        if (r.error || r.ok === false) {
          clearInterval(timer);
          toast(r.error || "B 站登录状态查询失败", "err");
          return;
        }
        if (["done", "timeout", "no_session", "already_logged_in", "error"].includes(r.status)) {
          clearInterval(timer);
          toast(r.message || r.status, r.status === "done" ? "ok" : "err");
          await loadBiliCard();
        }
      } catch (e) { clearInterval(timer); toast(e.message, "err"); }
    }, 3000);
  } catch (e) { toast(e.message, "err"); }
}

async function saveBiliEnabled(on) {
  try {
    await api("/api/bili/config", { method: "POST", body: JSON.stringify({ enabled: on }) });
    toast(on ? "B 站只读工具已启用" : "B 站只读工具已停用", "ok");
    await loadBiliCard();
  } catch (e) { toast(e.message, "err"); await loadBiliCard(); }
}

async function saveBiliPatrol() {
  const patch = {
    enabled: $("#biliPatrolOn").checked,
    keywords: $("#biliKeywords").value.trim(),
    interval_min: Number($("#biliInterval").value) || 360,
    daily_max: Number($("#biliDaily").value),
    per_scan: Number($("#biliPerScan").value) || 1,
    delivery: $("#biliDelivery").value,
    target_chat_uid: $("#biliTarget").value,
  };
  try {
    await api("/api/bili/config", { method: "POST", body: JSON.stringify({ patrol: patch }) });
    toast("B 站巡逻设置已保存", "ok");
    await loadBiliCard();
  } catch (e) { toast(e.message, "err"); }
}

async function runBiliPatrol() {
  if (biliPatrolRunning) {
    toast("正在巡逻，稍等", "err");
    return;
  }
  biliPatrolRunning = true;
  await openBiliPatrolLog();
  stopBiliPatrolTimer();
  biliPatrolWaitNew = true;
  biliPatrolBaseline = biliPatrolRunId;
  biliPatrolText = "";
  biliPatrolOffset = 0;
  const term = $("#biliPatrolTerm");
  if (term) term.textContent = "";
  setBiliPatrolStatus("启动中…");
  biliPatrolTimer = setInterval(pollBiliPatrolLog, 800);
  try {
    const r = await api("/api/bili/patrol/run", { method: "POST" });
    toast(r.ok ? "巡逻完成" : (r.error || "巡逻失败"), r.ok ? "ok" : "err");
    await loadBiliCard();
  } catch (e) {
    toast(e.message, "err");
  } finally {
    biliPatrolRunning = false;
    biliPatrolWaitNew = false;
  }
}

/* ---- 朋友圈页（侧栏「朋友圈」）：动态列表 + 互动通知；点赞/评论走微信窗口，耗时较长 ---- */
function bumpMomentBadge(count) {
  const badge = $("#momentBadge");
  if (!badge) return;
  if (typeof count !== "number") {
    api("/api/moments")
      .then((res) => {
        const n = (res && res.unread_interactions) || 0;
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.hidden = !n;
      })
      .catch(() => {});
    return;
  }
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.hidden = !count;
}

function momentMediaUrl(feedId, index, kind = "image") {
  return mediaUrl(`/api/moments/${encodeURIComponent(feedId)}/media/${index}?kind=${kind}`);
}

function momentRelTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  if (d.toDateString() === new Date().toDateString()) return fmtClock(ts);
  if (d.getFullYear() === new Date().getFullYear())
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function renderMomentFeed(m) {
  const name = m.is_self ? "我" : m.nickname || m.username || "?";
  const imgs = (m.images || []).slice(0, 9);
  const vids = m.videos || [];
  const likes = (m.likes || []).map((l) => l.nickname || l.username).filter(Boolean);
  const comments = (m.comments || []).filter((c) => c.username || c.nickname);
  const imgGrid = imgs.length
    ? `<div class="moment-grid n${Math.min(imgs.length, 9)}">` +
      imgs
        .map(
          (_img, i) =>
            `<img loading="lazy" src="${momentMediaUrl(m.feed_id, i)}" onclick="window.open(this.src)" onerror="this.style.display='none'">`
        )
        .join("") +
      `</div>`
    : "";
  const vidTag = vids.length
    ? `<div class="moment-media-note">🎬 视频 ×${vids.length}（暂不支持在线播放）</div>`
    : "";
  return `<div class="moment-feed" id="moment-${esc(m.feed_id)}">
    ${avatarHtml(name)}
    <div class="moment-body">
      <div class="moment-head">
        <span class="moment-nick">${esc(name)}</span>
        <span class="moment-time">${esc(momentRelTime(m.create_time))}</span>
      </div>
      ${m.text ? `<div class="moment-text">${esc(m.text)}</div>` : ""}
      ${imgGrid}
      ${vidTag}
      ${m.location ? `<div class="moment-loc">📍 ${esc(m.location)}</div>` : ""}
      ${
        likes.length || comments.length
          ? `<div class="moment-social">
              ${likes.length ? `<div class="moment-likes">👍 ${esc(likes.join("、"))}</div>` : ""}
              ${comments
                .map(
                  (c) =>
                    `<div class="moment-comment-line"><b>${esc(c.nickname || c.username)}</b>：${esc(c.content || "")}</div>`
                )
                .join("")}
            </div>`
          : ""
      }
      <div class="moment-ops">
        <button class="mini-btn" onclick="momentLike('${esc(m.feed_id)}')">赞</button>
        <button class="mini-btn" onclick="momentFocusComment('${esc(m.feed_id)}')">评论</button>
      </div>
      <div class="moment-comment-box" hidden>
        <input placeholder="写评论…" />
        <button class="btn">发送</button>
      </div>
    </div>
  </div>`;
}

function renderMomentsStatus(res) {
  const st = res.status;
  const tag = $("#momentsSyncTag");
  const who = st && st.account_name ? `${st.account_name} · ` : "";
  if (tag)
    tag.textContent = st && st.last_sync
      ? `${who}同步于 ${momentRelTime(st.last_sync)}${st.error ? " · 出错" : ""}`
      : st
        ? `${who}待首次同步`
        : "通道不支持";
  const countTag = $("#momentsCountTag");
  if (countTag) countTag.textContent = `${res.total ?? 0} 条`;
  bumpMomentBadge(res.unread_interactions || 0);
}

async function loadMomentInteractions() {
  const box = $("#momentInterList");
  if (!box) return;
  try {
    const rows = await api("/api/moments/interactions?limit=20");
    if (!rows.length) {
      box.innerHTML = '<div class="empty-mini">还没有互动通知：有人赞或评论你的朋友圈时会出现在这里</div>';
      return;
    }
    box.innerHTML = rows
      .map(
        (r) => `<div class="moment-inter-item ${r.seen ? "" : "unread"}">
          ${avatarHtml(r.from_nickname || r.from_username)}
          <div>
            <div>${
              r.type === 1
                ? `<b>${esc(r.from_nickname || r.from_username)}</b> 赞了你的朋友圈`
                : `<b>${esc(r.from_nickname || r.from_username)}</b> 评论：${esc(r.content || "")}`
            }</div>
            <span class="moment-time">${esc(momentRelTime(r.create_time))}${r.relative_me ? " · 与我相关" : ""}</span>
          </div>
        </div>`
      )
      .join("");
  } catch (e) {
    box.innerHTML = `<div class="empty-mini">${esc(e.message)}</div>`;
  }
}

async function loadMomentsPage() {
  const list = $("#momentsList");
  if (!list) return;
  const kw = ($("#momentsSearch")?.value || "").trim();
  state.momentsPage.keyword = kw;
  try {
    const res = await api(
      `/api/moments?keyword=${encodeURIComponent(kw)}&limit=${state.momentsPage.limit}&offset=0`
    );
    state.momentsPage.items = res.items || [];
    state.momentsPage.total = res.total || 0;
    renderMomentsStatus(res);
    if (!state.momentsPage.items.length) {
      list.innerHTML = `<div class="empty-mini">${
        res.status ? "还没有同步到动态，点上方「立即同步」试试" : "当前通道不支持朋友圈（仅微信 4.x 数据库通道）"
      }</div>`;
    } else {
      list.innerHTML = state.momentsPage.items.map(renderMomentFeed).join("");
    }
    const more = $("#momentsMoreBtn");
    if (more) more.hidden = state.momentsPage.items.length >= state.momentsPage.total;
    loadMomentInteractions();
    api("/api/moments/interactions/seen", { method: "POST" }).catch(() => {});
  } catch (e) {
    list.innerHTML = `<div class="empty-mini">${esc(e.message)}</div>`;
    toast(e.message, "err");
  }
}
window.loadMomentsPage = loadMomentsPage;

async function loadMoreMoments() {
  const list = $("#momentsList");
  if (!list) return;
  const offset = state.momentsPage.items.length;
  try {
    const res = await api(
      `/api/moments?keyword=${encodeURIComponent(state.momentsPage.keyword)}&limit=${state.momentsPage.limit}&offset=${offset}`
    );
    const fresh = (res.items || []).filter(
      (m) => !state.momentsPage.items.some((x) => x.feed_id === m.feed_id)
    );
    state.momentsPage.items.push(...fresh);
    list.insertAdjacentHTML("beforeend", fresh.map(renderMomentFeed).join(""));
    const more = $("#momentsMoreBtn");
    if (more) more.hidden = state.momentsPage.items.length >= (res.total || 0);
  } catch (e) {
    toast(e.message, "err");
  }
}
window.loadMoreMoments = loadMoreMoments;

async function syncMoments() {
  const tag = $("#momentsSyncTag");
  if (tag) tag.textContent = "正在打开微信朋友圈拉取…";
  try {
    // deep=true：先让微信客户端拉一次最新时间线（纯读本地库只能看到上次打开时的缓存）
    const res = await api("/api/moments/sync", {
      method: "POST",
      body: JSON.stringify({ deep: true }),
    });
    const uiNote = res.ui_opened
      ? ""
      : res.ui_error
        ? `（打开微信朋友圈失败：${res.ui_error}，本次只同步了本地库）`
        : "";
    toast(
      `已同步：新增 ${res.new_feeds} 条动态、${res.new_interactions} 条互动${uiNote}`,
      res.ui_error ? "warn" : "ok"
    );
    await loadMomentsPage();
  } catch (e) {
    toast(e.message, "err");
    if (tag) tag.textContent = "同步失败";
  }
}
window.syncMoments = syncMoments;

function momentBusy(feedId, on, label) {
  const feed = document.getElementById(`moment-${feedId}`);
  if (!feed) return;
  feed.querySelectorAll(".moment-ops .mini-btn").forEach((b) => {
    b.disabled = on;
    if (on && b.textContent === "赞") b.textContent = label || "操作中…";
  });
}

async function momentLike(feedId) {
  if (!confirm("给这条动态点赞？（会打开微信窗口操作，可能需要十几秒到一分钟）")) return;
  momentBusy(feedId, true, "点赞中…");
  try {
    await api(`/api/moments/${encodeURIComponent(feedId)}/like`, { method: "POST" });
    toast("已点赞", "ok");
  } catch (e) {
    toast(e.message, "err");
  } finally {
    momentBusy(feedId, false);
    loadMomentsPage();
  }
}
window.momentLike = momentLike;

function momentFocusComment(feedId) {
  const feed = document.getElementById(`moment-${feedId}`);
  if (!feed) return;
  const box = feed.querySelector(".moment-comment-box");
  if (!box) return;
  box.hidden = !box.hidden;
  if (!box.hidden) box.querySelector("input")?.focus();
  const send = box.querySelector("button");
  if (send && !send.dataset.bound) {
    send.dataset.bound = "1";
    send.addEventListener("click", () => momentComment(feedId, box));
  }
}
window.momentFocusComment = momentFocusComment;

async function momentComment(feedId, box) {
  const input = box.querySelector("input");
  const content = (input?.value || "").trim();
  if (!content) {
    toast("先写评论内容", "err");
    return;
  }
  if (!confirm("发送这条评论？（会打开微信窗口操作，可能需要十几秒到一分钟）")) return;
  const send = box.querySelector("button");
  if (send) send.disabled = true;
  try {
    await api(`/api/moments/${encodeURIComponent(feedId)}/comment`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    toast("评论已发送", "ok");
    if (input) input.value = "";
    box.hidden = true;
  } catch (e) {
    toast(e.message, "err");
  } finally {
    if (send) send.disabled = false;
    loadMomentsPage();
  }
}
window.momentComment = momentComment;

async function loadDiagnose() {
  try {
    const [d, inst] = await Promise.all([api("/api/diagnose"), api("/api/instances")]);
    $("#diagList").innerHTML = d.checks
      .map(
        (c) => `<div class="diag-item">
        <span class="diag-ico ${c.status}">${c.status === "ok" ? "✓" : c.status === "warn" ? "!" : "×"}</span>
        <div class="diag-main">
          <div class="diag-label">${esc(c.label)}</div>
          <div class="diag-value">${esc(c.value)}</div>
          ${c.hint ? `<div class="diag-hint">${esc(c.hint)}</div>` : ""}
        </div>
      </div>`
      )
      .join("");

    const wins = inst.windows || [];
    const allLocked = wins.length > 0 && wins.every((w) => !w.hookable);
    $("#instanceList").innerHTML =
      wins
        .map((w) => {
          const ver = w.version || "未知版本";
          const badge = w.hookable
            ? '<span class="inst-badge ok">3.9.x 可 Hook</span>'
            : '<span class="inst-badge no">4.x 不可 Hook</span>';
          return `<div class="instance-item">
          <div class="instance-main">
            <div class="instance-title">${esc(w.title || "微信")} <span style="color:#8a94a6;font-weight:400">PID ${w.pid}</span></div>
            <div class="instance-sub">${esc(ver)} · 窗口位置 ${w.rect[0]},${w.rect[1]} · 尺寸 ${w.rect[2]}×${w.rect[3]}</div>
          </div>
          ${badge}</div>`;
        })
        .join("") ||
      `<div class="empty-mini">未检测到微信窗口${inst.count === 0 ? "，请先登录微信" : ""}</div>`;

    if (allLocked) {
      $("#instanceList").innerHTML += `<div class="instance-note">
        检测到 ${wins.length} 个微信 4.x 窗口：本地 Hook 无法绑定。双开场景下，请给每个账号分别配置
        <b>Webhook 网关</b>（各用各的回调地址 / Token），或安装 3.9.10.27 双开后为每个实例配置不同的 Hook 端口。
      </div>`;
    }
  } catch (e) {
    console.error("loadDiagnose failed:", e);
  }
}
window.loadDiagnose = loadDiagnose;

async function restartDriver() {
  toast("正在重启通道…");
  try {
    const r = await api("/api/driver/restart", { method: "POST", body: JSON.stringify({}) });
    await loadStatus();
    await loadAccounts();
    toast(r.ok ? "通道已连接" : "通道未连接：" + (r.status?.error || ""), r.ok ? "ok" : "err");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function shutdownServer() {
  if (!confirm("关闭后控制台与微信通道都会停止，需要重新运行 run.bat 才能恢复。确定关闭？")) return;
  const btn = $("#btnShutdown");
  if (btn) btn.disabled = true;
  try {
    await api("/api/server/shutdown", {
      method: "POST",
      body: JSON.stringify({ reason: "控制台一键关闭" }),
    });
    state.shuttingDown = true;
    const mask = $("#shutdownMask");
    if (mask) mask.hidden = false;
    toast("数据已保存，服务正在关闭…", "ok");
  } catch (e) {
    if (btn) btn.disabled = false;
    toast(e.message, "err");
  }
}

async function restartServer() {
  if (!confirm("重启会短暂中断微信通道（约 10-20 秒），之后自动恢复。确定重启？")) return;
  const btn = $("#btnReboot");
  if (btn) btn.disabled = true;
  try {
    await api("/api/server/restart", {
      method: "POST",
      body: JSON.stringify({ reason: "控制台一键重启" }),
    });
    toast("数据已保存，服务重启中，恢复后页面自动刷新…", "ok");
    pollRestart();
  } catch (e) {
    if (btn) btn.disabled = false;
    toast(e.message, "err");
  }
}

async function pollRestart() {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      await api("/api/health");
      location.reload();
      return;
    } catch {
      /* 服务还没起来，继续等 */
    }
  }
  toast("重启超时，请检查服务是否已恢复，或手动运行 run.bat", "err");
}

function renderVersion(r) {
  const verLabel = $("#verLabel");
  const sideVer = $(".side-ver");
  const tag = $("#updateTag");
  const main = $("#updateMain");
  const sub = $("#updateSub");
  const actions = $("#updateActions");
  if (!verLabel) return;
  sideVer?.classList.toggle("ver-outdated", !!r.has_update);
  if (!r.git_repo) {
    verLabel.textContent = "v" + (r.version || "—");
    if (tag) tag.textContent = "不适用";
    if (main) main.textContent = `当前 v${r.version || "未知"}（解压安装的副本不做在线比对）`;
    if (sub) sub.textContent = "未通过 git 克隆，无法在线检查更新，请重新下载仓库后解压覆盖";
    if (actions) actions.hidden = true;
    return;
  }
  verLabel.textContent =
    "v" + (r.version || "—") + (r.local_short ? " · " + r.local_short : "") + (r.has_update ? " · 有新版" : "");
  if (r.error) {
    if (tag) tag.textContent = "检查失败";
    if (main) main.textContent = "检查更新失败";
    if (sub) sub.textContent = r.error;
    if (actions) actions.hidden = true;
    return;
  }
  if (r.has_update) {
    if (tag) {
      tag.textContent = "有新版本";
      tag.classList.add("ver-tag-outdated");
    }
    if (main) {
      main.textContent = `发现新版本 ${r.remote_short}（当前 v${r.version || "未知"} · ${r.local_short}）`;
      main.classList.add("ver-outdated-text");
    }
    if (sub) sub.textContent = "在项目目录运行 git pull 更新，然后重启服务即可生效";
    if (actions) {
      const link = $("#updateRepoLink");
      if (link) link.href = r.repo_url || "#";
      actions.hidden = false;
    }
    return;
  }
  if (tag) {
    tag.textContent = "已是最新";
    tag.classList.remove("ver-tag-outdated");
  }
  if (main) {
    main.textContent = `当前已是最新版本（v${r.version || "未知"} · ${r.local_short}）`;
    main.classList.remove("ver-outdated-text");
  }
  if (sub) sub.textContent = "与所选远端仓库的默认分支对比，检测是否有新版本";
  if (actions) actions.hidden = true;
}

async function checkUpdate(manual = true) {
  const btns = $$(".btn-check-update");
  if (btns.some((b) => b.disabled)) return;
  btns.forEach((b) => {
    b.disabled = true;
    b.dataset.label = b.textContent;
    b.textContent = "检查中…";
  });
  try {
    const r = await api("/api/update/check?force=1");
    renderVersion(r);
    if (!manual) return;
    if (!r.git_repo) toast("当前目录不是 git 安装，无法在线检查更新", "err");
    else if (r.error) toast("检查更新失败：" + r.error, "err");
    else if (r.has_update) toast(`发现新版本 ${r.remote_short}，运行 git pull 更新后重启服务即可`, "ok");
    else toast("当前已是最新版本", "ok");
  } catch (e) {
    if (manual) toast(e.message, "err");
  } finally {
    btns.forEach((b) => {
      b.disabled = false;
      if (b.dataset.label) b.textContent = b.dataset.label;
    });
  }
}
window.checkUpdate = checkUpdate;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(withToken(`${proto}://${location.host}/ws`));
  state.ws = ws;
  ws.onopen = () => {
    state.wsRetry = 0;
  };
  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleEvent(msg);
  };
  ws.onclose = () => {
    if (state.shuttingDown) return;
    state.wsRetry = Math.min(state.wsRetry + 1, 6);
    setTimeout(connectWs, 800 * state.wsRetry);
  };
}

const refreshChats = debounce(loadChats, 350);

function handleEvent(evt) {
  const data = evt.data || {};
  switch (evt.type) {
    case "hello":
      if (evt.accounts) state.accounts = normalizeAccounts(evt.accounts);
      if (evt.data) renderStatus(evt.data);
      if (evt.accounts) {
        renderAccountSwitcher();
        renderAccTabs();
      }
      break;
    case "status":
      if (!data.account_id || data.account_id === state.account) renderStatus(data);
      break;
    case "chats_dirty":
      if (!data.account_id || data.account_id === state.account) refreshChats();
      break;
    case "emojis_dirty":
      if (!data.account_id || data.account_id === state.account) loadEmojis();
      break;
    case "growth":
      if (!data.account_id || data.account_id === state.account) {
        if (state.page === "growth") loadGrowthPage();
        else loadGrowthStatus();
      }
      break;
    case "moments_dirty":
      if (!data.account_id || data.account_id === state.account) {
        if (state.page === "moments") loadMomentsPage();
      }
      break;
    case "moment_interaction":
      if (!data.account_id || data.account_id === state.account) {
        const item = data.item || {};
        const who = item.from_nickname || "好友";
        toast(item.type === 1 ? `${who} 赞了你的朋友圈` : `${who} 评论了你的朋友圈：${item.content || ""}`);
        bumpMomentBadge();
        if (state.page === "moments") loadMomentsPage();
      }
      break;
    case "message": {
      if (data.account_id && data.account_id !== state.account) break;
      if (data.chat_uid === state.activeChat) {
        if (!state.messages.some((x) => x.id === data.id)) {
          if (data.is_self && data.source === "agent") {
            state.autoTools = [];
            state.autoThink = "";
          }
          state.messages.push(data);
          renderThread();
        }
      }
      refreshChats();
      if (data.is_self && data.source === "agent") refreshWhaleBalance();
      break;
    }
    case "agent_tool": {
      if (data.scope !== "auto") break;
      if (data.account_id && data.account_id !== state.account) break;
      if (data.chat_uid !== state.activeChat) break;
      if (data.type === "tool_start") {
        state.autoTools.push({
          id: data.id || `t${state.autoTools.length}`,
          name: data.name,
          args: data.args || {},
          ok: null,
          result: null,
        });
      } else if (data.type === "tool_end") {
        const t = state.autoTools.find((x) => x.id && x.id === data.id) || state.autoTools.find((x) => x.ok === null);
        if (t) {
          t.ok = !!data.ok;
          t.result = data.result;
        }
      } else if (data.type === "done") {
        state.autoTools = [];
        state.autoThink = "";
        refreshWhaleBalance();
      }
      renderThread();
      break;
    }
    case "agent_reasoning": {
      if (data.scope !== "auto") break;
      if (data.account_id && data.account_id !== state.account) break;
      if (data.chat_uid !== state.activeChat) break;
      state.autoThink += data.text || "";
      appendAutoThinkDelta(data.text || "");
      break;
    }
    case "policy": {
      if (data.account_id && data.account_id !== state.account) break;
      state.policies[data.chat_uid] = data.allowed ? "将自动回复" : data.reason;
      if (data.chat_uid === state.activeChat) renderThread();
      break;
    }
    case "accounts_dirty":
      loadAccounts();
      loadStatus();
      break;
    case "log":
      toast(data.title, data.level === "error" ? "err" : "");
      break;
    case "settings":
      fillSettings(data);
      loadAgentModels();
      break;
    default:
      break;
  }
}

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

/* 窄屏下输入框变窄，长提示语会被裁掉，换成短版 */
function syncPlaceholders() {
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  $("#composer").placeholder = narrow
    ? "输入消息，Enter 发送"
    : "输入消息，Enter 发送 / Shift + Enter 换行";
  $("#agentInput").placeholder = narrow
    ? "直接下指令，例如：总结今天的群聊"
    : "直接下指令，例如：把产品群今天的结论整理成三点发给王倩";
  renderAgentHeader();
}

function bind() {
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  $$("[data-eula-lang]").forEach((b) =>
    b.addEventListener("click", () => setEulaLang(b.dataset.eulaLang))
  );
  window.addEventListener("resize", debounce(syncPlaceholders, 150));
  $("#chatSearch").addEventListener("input", renderChats);
  $("#chatList").addEventListener("click", (e) => {
    const pausedTag = e.target.closest(".ci-paused");
    if (pausedTag) {
      // 「接管中」徽标：就地恢复该会话的 AI，不打开会话
      resumeChat(pausedTag.dataset.uid);
      return;
    }
    const item = e.target.closest(".chat-item");
    if (!item) return;
    const uid = item.dataset.uid;
    if (state.selectMode) {
      // 多选模式：点击卡片切换选中状态，不打开会话
      if (state.selectedChats.has(uid)) state.selectedChats.delete(uid);
      else state.selectedChats.add(uid);
      item.classList.toggle("selected", state.selectedChats.has(uid));
      updateBulkBar();
      return;
    }
    selectChat(uid);
  });
  $("#btnSelectMode").addEventListener("click", () => setSelectMode(!state.selectMode));
  $("#btnChatSelAll").addEventListener("click", () => {
    const visible = visibleChats();
    if (!visible.length) return;
    const all = visible.every((c) => state.selectedChats.has(c.uid));
    visible.forEach((c) => (all ? state.selectedChats.delete(c.uid) : state.selectedChats.add(c.uid)));
    $$("#chatList .chat-item").forEach((el) =>
      el.classList.toggle("selected", state.selectedChats.has(el.dataset.uid))
    );
    updateBulkBar();
  });
  $("#btnBulkManual").addEventListener("click", () => bulkSetAI(false));
  $("#btnBulkAI").addEventListener("click", () => bulkSetAI(true));
  $("#btnThreadBack").addEventListener("click", closeThread);
  $("#btnSend").addEventListener("click", sendChat);
  $("#btnEmoji").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleEmojiPanel();
  });
  $("#btnEmojiClose").addEventListener("click", closeEmojiPanel);
  $("#btnEmojiWall").addEventListener("click", () => {
    closeEmojiPanel();
    go("emojis");
  });
  $("#emojiPanel").addEventListener("click", (e) => {
    e.stopPropagation();
    const tab = e.target.closest(".emoji-tab");
    if (tab) {
      state.emojiTab = tab.dataset.tab;
      $$("#emojiPanel .emoji-tab").forEach((t) => t.classList.toggle("active", t === tab));
      renderEmojiPanel();
      return;
    }
    const del = e.target.closest("[data-del]");
    if (del) {
      removeFavEmoji(del.dataset.del);
      return;
    }
    const item = e.target.closest(".emoji-item");
    if (!item) return;
    if (item.dataset.name) insertBuiltinEmoji(item.dataset.name);
    else if (item.dataset.eid) sendFavEmoji(item.dataset.eid);
  });
  $("#composer").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("#composer").addEventListener("input", (e) => autoGrow(e.target));
  $("#chatAiToggle").addEventListener("change", (e) => toggleChatAi(e.target.checked));
  $("#btnResume").addEventListener("click", () => resumeChat());

  $("#btnAgentSend").addEventListener("click", async () => {
    if (agentState.busy) {
      stopAgent();
      return;
    }
    if (agentState.uploading) return;
    const input = $("#agentInput");
    const v = input.value.trim();
    const pending = agentState.attachments.slice();
    if (!v && !pending.length) return;
    let urls = [];
    if (pending.length) {
      agentState.uploading = true;
      updateAgentSendButton();
      try {
        urls = await uploadAgentAttachments(pending);
      } catch (e) {
        toast(e.message, "err");
        agentState.uploading = false;
        updateAgentSendButton();
        return;
      }
      agentState.uploading = false;
    }
    clearAgentAttachments();
    input.value = "";
    autoGrow(input);
    updateAgentSendButton();
    askAgent(v, urls);
  });
  $("#agentInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!agentState.busy) $("#btnAgentSend").click();
    }
  });
  $("#agentInput").addEventListener("input", (e) => {
    autoGrow(e.target);
    updateAgentSendButton();
  });
  $("#agentInput").addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
    if (!files.length) return;
    if (!visionEnabled()) {
      toast("先在「设置 → 模型」开启识图", "err");
      return;
    }
    e.preventDefault();
    addAgentImages(files);
  });
  $("#btnAgentImage").addEventListener("click", () => $("#agentImageInput").click());
  $("#agentImageInput").addEventListener("change", (e) => {
    addAgentImages(e.target.files || []);
    e.target.value = "";
  });

  $("#btnAgentNew").addEventListener("click", () => newAgentConversation());
  $("#btnAgentNewTop").addEventListener("click", () => newAgentConversation());
  $("#btnAgentHistory").addEventListener("click", openAgentHistory);
  $("#btnAgentHistoryClose").addEventListener("click", closeAgentHistory);
  $("#agentScrim").addEventListener("click", closeAgentHistory);

  $("#agentConvList").addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      deleteAgentConversation(del.dataset.del);
      return;
    }
    const item = e.target.closest(".conv-item");
    if (item) openAgentConversation(item.dataset.cid);
  });
  $("#agentConvList").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const item = e.target.closest(".conv-item");
    if (!item) return;
    e.preventDefault();
    openAgentConversation(item.dataset.cid);
  });

  $("#btnModelPicker").addEventListener("click", (e) => {
    e.stopPropagation();
    openPicker("model");
  });
  $("#btnEffortPicker").addEventListener("click", (e) => {
    e.stopPropagation();
    openPicker("effort");
  });
  $("#modelPop").addEventListener("click", (e) => {
    const modelBtn = e.target.closest("[data-model]");
    if (modelBtn) {
      e.stopPropagation();
      setAgentModel(modelBtn.dataset.model);
      return;
    }
    if (e.target.closest("[data-custom]")) {
      e.stopPropagation();
      agentState.customMode = true;
      renderModelPop();
    }
  });
  $("#modelPop").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = $("#modelCustomInput")?.value.trim();
    if (value) setAgentModel(value);
  });
  $("#effortPop").addEventListener("click", (e) => {
    const item = e.target.closest("[data-effort]");
    if (!item) return;
    e.stopPropagation();
    setAgentEffort(item.dataset.effort);
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".picker-wrap")) closePickers();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closePickers();
    closeAgentHistory();
  });

  $("#btnRestart").addEventListener("click", restartDriver);
  $("#btnShutdown").addEventListener("click", shutdownServer);
  $("#btnReboot").addEventListener("click", restartServer);
  $$(".btn-check-update").forEach((b) => b.addEventListener("click", () => checkUpdate(true)));
  $("#btnTestAi").addEventListener("click", testAi);
  $("#modelProfiles").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip && chip.dataset.profile) switchModelProfile(chip.dataset.profile);
  });
  $("#btnPresetGlobal").addEventListener("click", applyPresetGlobal);
  $("#btnPresetAccount").addEventListener("click", applyPresetAccount);
  $("#btnPresetDelete").addEventListener("click", deletePreset);
  $("#btnPresetSaveGlobal").addEventListener("click", () => savePresetFrom("global"));
  $("#btnPresetSaveAccount").addEventListener("click", () => savePresetFrom("account"));
  $("#btnSyncWikiPersonas").addEventListener("click", syncWikiPersonas);
  $("#btnAddAccount").addEventListener("click", addAccount);
  $("#btnSaveAccount").addEventListener("click", saveAccount);
  $("#btnDeleteAccount").addEventListener("click", deleteAccount);
  $("#aDriver").addEventListener("change", updateDriverFields);
  $("#memPageSearch").addEventListener("input", debounce(() => loadMemoryPage(), 300));
  $("#memPageSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadMemoryPage();
  });
  for (const id of ["#memLayerFilter", "#memKindFilter", "#memPinnedOnly"]) {
    $(id)?.addEventListener("change", () => loadMemoryPage());
  }
  $("#memImportFile")?.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) importMemoriesFile(file);
  });
  $("#memProfileList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-rebuild-profile]");
    if (btn) rebuildMemoryProfile(btn.dataset.rebuildProfile || "");
  });
  for (const [id, key] of [
    ["#capEnabled", "enabled"],
    ["#capConsole", "console_enabled"],
    ["#capAuto", "auto_reply_enabled"],
    ["#capSkillsPrompt", "skills_in_prompt"],
  ]) {
    $(id)?.addEventListener("change", (e) => saveCapabilitySettings({ [key]: e.target.checked }));
  }
  $("#capMcpList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mcp-test]");
    if (btn) testMcpServer(btn.dataset.mcpTest || "");
  });
  $("#capSkillList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-skill-view]");
    if (btn) viewSkill(btn.dataset.skillView || "");
  });
  $("#fPageMemContent").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addPageMemory();
  });
  $("#memPageList").addEventListener("click", (e) => {
    if (!state.memoryPage.selMode) return;
    const item = e.target.closest(".mem-item");
    if (!item || !item.dataset.mid) return;
    memSelToggle(Number(item.dataset.mid));
  });
  $("#btnMemMulti").addEventListener("click", () => setMemSelMode(!state.memoryPage.selMode));
  $("#btnMemSelAll").addEventListener("click", memSelAll);
  $("#btnMemSelNone").addEventListener("click", () => {
    state.memoryPage.sel.clear();
    renderMemoryPage();
  });
  $("#btnMemSelExit").addEventListener("click", () => setMemSelMode(false));
  $("#btnBatchMemDelete").addEventListener("click", memBatchDelete);
  $("#btnBatchMemImportance").addEventListener("click", memBatchImportance);
  $("#btnBatchMemExport").addEventListener("click", memBatchExport);
  $("#momentsSearch").addEventListener("input", debounce(() => loadMomentsPage(), 300));
  $("#momentsSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadMomentsPage();
  });
  bumpMomentBadge();

  $("#accCurrent").addEventListener("click", (e) => {
    e.stopPropagation();
    $("#accMenu").hidden = !$("#accMenu").hidden;
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".emoji-anchor")) closeEmojiPanel();
    if (!e.target.closest("#accountSwitch")) $("#accMenu").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closePickers();
    closeAgentHistory();
    closeEmojiPanel();
    closeWallDrawer();
    if (state.wallSelMode) setWallSelMode(false);
    if (state.memoryPage.selMode) setMemSelMode(false);
  });

  $("#wallSearch").addEventListener("input", debounce(() => {
    state.wallFilter.q = $("#wallSearch").value;
    renderEmojiWall();
  }, 200));
  $("#wallChips").addEventListener("click", (e) => {
    if (e.target.closest("#btnChipsAll")) {
      state.wallShowAllTags = !state.wallShowAllTags;
      renderWallChips();
      return;
    }
    const lock = e.target.closest(".chip-lock");
    if (lock) {
      e.stopPropagation();
      toggleTagLock(lock.dataset.lockTag);
      return;
    }
    const chip = e.target.closest(".chip");
    if (chip) wallFilterTag(chip.dataset.tag);
  });
  // 锁图标自身可聚焦，键盘触发要落到解锁逻辑；preventDefault 抑制父级 chip 的默认激活，避免同时切换筛选
  $("#wallChips").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const lock = e.target.closest(".chip-lock");
    if (!lock) return;
    e.preventDefault();
    toggleTagLock(lock.dataset.lockTag);
  });
  $("#wallGrid").addEventListener("click", (e) => {
    const item = e.target.closest(".wall-item");
    if (!item) return;
    const eid = Number(item.dataset.eid);
    if (state.wallSelMode) {
      wallSelToggle(eid);
      return;
    }
    openWallDrawer(eid);
  });
  $("#btnImportQQ").addEventListener("click", importQqCache);
  $("#btnImportEmoji").addEventListener("click", () => $("#emojiImportFile").click());
  $("#emojiImportFile").addEventListener("change", (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = ""; // 允许再次选择同一批文件
    importEmojiFiles(files);
  });
  $("#btnExportAll").addEventListener("click", () => wallBatchZip(true));
  $("#btnWallMulti").addEventListener("click", () => setWallSelMode(!state.wallSelMode));
  $("#btnSelAll").addEventListener("click", wallSelAll);
  $("#btnSelNone").addEventListener("click", () => {
    state.wallSel.clear();
    renderEmojiWall();
  });
  $("#btnSelExit").addEventListener("click", () => setWallSelMode(false));
  $("#btnBatchDelete").addEventListener("click", wallBatchDelete);
  $("#btnBatchLock").addEventListener("click", wallBatchLock);
  $("#btnBatchRetag").addEventListener("click", wallBatchRetag);
  $("#btnBatchZip").addEventListener("click", () => wallBatchZip(false));
  $("#btnRetagUntagged").addEventListener("click", wallRetagUntagged);
  $("#btnConsolidateTags").addEventListener("click", consolidateTags);
  $("#btnWallMore").addEventListener("click", loadMoreEmojis);
}

// ------------------------------------------------------------ 表情包预览墙
function wallList() {
  const q = (state.wallFilter.q || "").toLowerCase();
  return state.emojis.filter((e) => {
    if (state.wallFilter.tag === "__untagged" && e.tags) return false;
    if (state.wallFilter.tag && state.wallFilter.tag !== "__untagged"
        && !(e.tags || "").split(",").includes(state.wallFilter.tag)) return false;
    if (q && !(((e.tags || "") + " " + (e.description || "")).toLowerCase().includes(q))) return false;
    return true;
  });
}

function renderEmojiWall() {
  const grid = $("#wallGrid");
  if (!grid) return;
  const list = wallList();
  const selMode = state.wallSelMode;
  grid.innerHTML = list.length
    ? list.map((e) => {
        const first = (e.tags || "").split(",")[0] || "";
        const picked = selMode && state.wallSel.has(e.id);
        const locked = !!e.locked;
        const isSite = e.source === "searchanythingfun";
        return `<div class="wall-item ${state.wallEmojiId === e.id ? "on" : ""} ${picked ? "picked" : ""} ${locked ? "locked" : ""}" data-eid="${e.id}">
          ${selMode ? `<i class="wall-check">${picked ? "✓" : ""}</i>` : ""}
          ${locked ? `<i class="wall-lock" title="已锁定">🔒</i>` : ""}
          ${isSite ? `<i class="wall-source" title="来自 searchanythingfun">串子站</i>` : ""}
          <img src="${emojiProxyUrl(e)}" alt="表情" loading="lazy" onerror="this.classList.add('broken')">
          <em class="wall-tag ${first ? "" : "none"}">${esc(first || "未打标")}</em>
          ${selMode ? "" : `<div class="wall-quick">
            <button type="button" title="发送（选会话）" onclick="event.stopPropagation();openWallDrawer(${e.id})">✉</button>
            <button type="button" title="${locked ? "已锁定，先解锁再删除" : "删除"}"
              ${locked ? "disabled" : `onclick="event.stopPropagation();wallDelete(${e.id})"`}>🗑</button>
          </div>`}
        </div>`;
      }).join("")
    : `<p class="wall-empty">没有符合条件的表情</p>`;
  renderWallChips();
  const tagged = state.emojis.filter((e) => e.tags).length;
  $("#wallStats").textContent = `已加载 ${state.emojis.length} · 已打标 ${tagged}`;
  const more = $("#wallMore");
  if (more) {
    more.hidden = !state.emojiHasMore;
    const btn = $("#btnWallMore");
    if (btn) btn.textContent = `加载更多（已显示 ${state.emojis.length}）`;
  }
  updateWallSelBar();
}

// ------------------------------------------------------------ 表情墙多选批处理
function setWallSelMode(on) {
  state.wallSelMode = !!on;
  if (!on) state.wallSel.clear();
  $("#btnWallMulti").textContent = on ? "退出多选" : "多选";
  $("#wallSelBar").hidden = !on;
  renderEmojiWall();
}

function wallSelToggle(eid) {
  if (state.wallSel.has(eid)) state.wallSel.delete(eid);
  else state.wallSel.add(eid);
  renderEmojiWall();
}

function wallSelAll() {
  const list = wallList();
  if (!list.length) return;
  const allPicked = list.every((e) => state.wallSel.has(e.id));
  for (const e of list) {
    if (allPicked) state.wallSel.delete(e.id);
    else state.wallSel.add(e.id);
  }
  renderEmojiWall();
}

function updateWallSelBar() {
  const bar = $("#wallSelBar");
  if (!bar || bar.hidden) return;
  $("#wallSelCount").textContent = `已选 ${state.wallSel.size}`;
  // 口径与 wallBatchDelete 一致：本地查不到的行（页外或已被拒收摘除）交由服务端裁决，
  // 否则打标运行中把选中行 splice 掉后，按钮会在仍有可删项时误置灰。
  const hasUnlocked = [...state.wallSel].some((id) => {
    const e = state.emojis.find((x) => x.id === id);
    return !e || !e.locked;
  });
  $("#btnBatchRetag").disabled = !state.wallSel.size || !hasUnlocked;
  $("#btnBatchDelete").disabled = !state.wallSel.size || !hasUnlocked;
  $("#btnBatchZip").disabled = !state.wallSel.size;
  // 选中项全部已锁时按钮切成「解锁」，否则统一走「锁定」；页外行按未锁定算
  const selRows = [...state.wallSel]
    .map((id) => state.emojis.find((x) => x.id === id))
    .filter(Boolean);
  const allLocked =
    state.wallSel.size > 0 &&
    selRows.length === state.wallSel.size &&
    selRows.every((e) => e.locked);
  const lockBtn = $("#btnBatchLock");
  lockBtn.disabled = !state.wallSel.size;
  lockBtn.textContent = allLocked ? "🔓 解锁" : "🔒 锁定";
  lockBtn.title = allLocked
    ? "解锁选中的表情：解锁后可改标签、AI 重打标或删除"
    : "锁定选中的表情：锁定后禁改标签、禁 AI 重打标、禁删除";
}

async function wallBatchDelete() {
  const all = [...state.wallSel];
  if (!all.length) return;
  const ids = all.filter((id) => {
    const e = state.emojis.find((x) => x.id === id);
    return !e || !e.locked;
  });
  const lockedCount = all.length - ids.length;
  if (!ids.length) {
    toast("选中的表情都已锁定", "ok");
    return;
  }
  const extra = lockedCount ? `已跳过 ${lockedCount} 张锁定表情。` : "";
  if (!confirm(`确定删除选中的 ${ids.length} 个表情吗？${extra}删除后不可恢复。`)) return;
  try {
    try {
      const r = await api("/api/emojis/batch-delete", {
        method: "POST",
        body: JSON.stringify({ ids }),
      });
      state.wallSel.clear();
      await loadEmojis();
      if (state.wallEmojiId != null && !state.emojis.some((x) => x.id === state.wallEmojiId)) {
        state.wallEmojiId = null;
        $("#wallDrawer").hidden = true;
      }
      // 锁定项在本地就被过滤掉、根本没进 payload，所以服务端 skipped_locked 通常是 0；
      // 最终提示要把本地已知的 lockedCount 一起算上，否则跳过数会被少报。
      const skipped = lockedCount + Number(r.skipped_locked || 0);
      toast(
        `已删除 ${r.deleted} 个表情${skipped ? `，跳过 ${skipped} 张锁定` : ""}`,
        "ok"
      );
    } catch (err) {
      toast(err.message, "err");
    }
  } catch (err) {
    toast(err.message, "err");
  }
}

async function wallBatchLock() {
  const ids = [...state.wallSel];
  if (!ids.length) return;
  const rows = ids.map((id) => state.emojis.find((x) => x.id === id)).filter(Boolean);
  const allLocked = rows.length === ids.length && rows.every((e) => e.locked);
  const locked = !allLocked;
  const msg = locked
    ? `锁定后这 ${ids.length} 张表情的标签、描述、AI 重打标和删除都会被禁止；发送不受影响。确定锁定？`
    : `解锁后这 ${ids.length} 张表情可以修改标签/描述、AI 重打标或删除。确定解锁？`;
  if (!confirm(msg)) return;
  const btn = $("#btnBatchLock");
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/emojis/batch-lock", {
      method: "POST",
      body: JSON.stringify({ ids, locked }),
    });
    await loadEmojis();
    const skipped = ids.length - Number(r.updated || 0);
    toast(
      `${locked ? "已锁定" : "已解锁"} ${r.updated} 张表情` +
        (skipped > 0 ? `，跳过 ${skipped} 张（不存在或不属于当前账号）` : ""),
      "ok"
    );
  } catch (err) {
    toast(err.message, "err");
  } finally {
    updateWallSelBar();
  }
}

async function wallBatchRetag() {
  // 运行中再点（无论哪个入口）= 请求停止
  if (state.wallRetagRunning) {
    state.wallRetagStop = true;
    wallRetagProgress("⏹ 停止中…");
    return;
  }
  const ids = [...state.wallSel];
  if (!ids.length) return;
  runRetagJobs(ids);
}

async function consolidateTags() {
  const btn = $("#btnConsolidateTags");
  if (btn) { btn.disabled = true; btn.textContent = "整理中…"; }
  try {
    const r = await api("/api/emojis/consolidate-tags", { method: "POST" });
    await loadEmojis();
    const n = Object.keys(r.mapping || {}).length;
    const skipped = Number(r.locked_tags_skipped || 0);
    let msg = n
      ? `已归并 ${n} 个标签写法，改动 ${r.changed} 个表情`
      : "没有需要归并的标签";
    if (skipped) msg += `；跳过 ${skipped} 个锁定标签`;
    toast(msg, "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "整理标签"; }
  }
}

async function wallRetagUntagged() {
  if (state.wallRetagRunning) {
    state.wallRetagStop = true;
    wallRetagProgress("⏹ 停止中…");
    return;
  }
  const untagged = state.emojis.filter((e) => !e.tags);
  const ids = untagged.filter((e) => !e.locked).map((e) => e.id);
  // 这里先把锁定项滤掉，runRetagJobs 自己的跳过统计就永远是 0，
  // 所以本入口必须自己把跳过数报出来（不会与 runRetagJobs 重复提示）。
  const skipped = untagged.length - ids.length;
  if (skipped) toast(`已跳过 ${skipped} 张锁定表情`, "ok");
  if (!ids.length) { toast("没有未打标且未锁定的表情", "ok"); return; }
  runRetagJobs(ids);
}

function wallRetagProgress(text) {
  // 两个入口按钮都当停止键用，同步显示进度
  for (const id of ["btnBatchRetag", "btnRetagUntagged"]) {
    const b = $(`#${id}`);
    if (b) b.textContent = text;
  }
}

async function runRetagJobs(ids) {
  const CONCURRENCY = 6; // 并发打标路数：太低慢，太高容易撞模型限流
  const lockedIds = ids.filter((id) => {
    const e = state.emojis.find((x) => x.id === id);
    return e && e.locked;
  });
  ids = ids.filter((id) => !lockedIds.includes(id));
  if (!ids.length) {
    toast("选中的表情都已锁定", "ok");
    return;
  }
  if (lockedIds.length) toast(`已跳过 ${lockedIds.length} 张锁定表情`, "ok");
  if (
    !confirm(
      `对 ${ids.length} 个表情并发 AI 打标（${CONCURRENCY} 路）？`
      + "会覆盖已有标签，非表情包照片会被拒收，运行中可随时点停止。"
    )
  ) return;
  const origSel = "🔄 AI打标";
  const origUntagged = "AI打标未打标";
  state.wallRetagRunning = true;
  state.wallRetagStop = false;
  $("#btnBatchRetag").disabled = $("#btnRetagUntagged").disabled = false;
  $("#btnBatchZip").disabled = $("#btnBatchDelete").disabled = true;
  let done = 0, ok = 0, fail = 0, rejected = 0;
  const queue = ids.slice();
  async function worker() {
    while (queue.length && !state.wallRetagStop) {
      const id = queue.shift();
      const e = state.emojis.find((x) => x.id === id);
      if (e) await warmEmojiCache(e); // 没缓存的先经代理取一次，避免打标因缺缓存失败
      try {
        const r = await api(`/api/emojis/${id}/retag`, { method: "POST" });
        const idx = state.emojis.findIndex((x) => x.id === id);
        if (r.rejected) {
          if (idx >= 0) state.emojis.splice(idx, 1);
          rejected++;
        } else {
          if (idx >= 0 && r.emoji) state.emojis[idx] = r.emoji;
          ok++;
        }
      } catch {
        fail++;
      }
      done++;
      if (!state.wallRetagStop) wallRetagProgress(`⏹ 停止 (${done}/${ids.length})`);
      // 全量 innerHTML 重绘有 O(n²) 开销，大库节流到每 25 个刷一次
      if (done % 25 === 0 || !queue.length) renderEmojiWall();
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, ids.length) }, worker));
  state.wallRetagRunning = false;
  const stopped = state.wallRetagStop;
  $("#btnBatchZip").disabled = $("#btnBatchDelete").disabled = false;
  updateWallSelBar();
  $("#btnBatchRetag").textContent = origSel;
  $("#btnRetagUntagged").textContent = origUntagged;
  const summary = `成功 ${ok}${rejected ? `，拒收 ${rejected}` : ""}${fail ? `，失败 ${fail}` : ""}`;
  toast(
    stopped ? `已停止：已处理 ${done}/${ids.length}，${summary}` : `AI 打标完成：${summary}`,
    ok || rejected ? "ok" : "err"
  );
}

async function warmEmojiCache(e) {
  if (!String(e.url || "").startsWith("http")) return;
  try {
    await fetch(emojiProxyUrl(e));
  } catch {}
}

async function wallBatchZip(all = false) {
  const ids = [...state.wallSel];
  if (!all && !ids.length) return;
  const btn = all ? $("#btnExportAll") : $("#btnBatchZip");
  if (!btn) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = all ? "⬇ 导出中…" : "⬇ 打包中…";
  try {
    const headers = { "Content-Type": "application/json" };
    if (getToken()) headers["X-Access-Token"] = getToken();
    if (state.account) headers["X-Account-Id"] = state.account;
    const r = await fetch("/api/emojis/batch-zip", {
      method: "POST",
      headers,
      body: JSON.stringify(all ? { all: true } : { ids }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `导出失败 (${r.status})`);
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `emojis-${new Date().toISOString().slice(0, 10)}.zip`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    const skipped = Number(r.headers.get("X-Emoji-Skipped") || 0);
    const packed = Number(r.headers.get("X-Emoji-Packed") || 0) || (all ? 0 : ids.length - skipped);
    const head = packed ? `已导出 ${packed} 个表情` : "已导出全部表情";
    toast(`${head}${skipped ? `（${skipped} 个取不到已跳过）` : ""}`, "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function importEmojiFiles(files) {
  if (!files.length) return;
  // zip 清单里的标签优先；没有清单的旧 zip 和散图都靠这里补一组统一标签
  const answer = prompt(
    "给没有标签的图片统一加标签（逗号分隔，可留空；zip 清单里的标签优先）：",
    ""
  );
  if (answer === null) {
    toast("已取消导入");
    return;
  }
  const tags = answer;
  const btn = $("#btnImportEmoji");
  const orig = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "导入中…"; }
  try {
    const fd = new FormData();
    for (const file of files) fd.append("files", file);
    fd.append("tags", tags);
    const headers = {};
    if (getToken()) headers["X-Access-Token"] = getToken();
    if (state.account) headers["X-Account-Id"] = state.account;
    const r = await fetch("/api/emojis/import", { method: "POST", headers, body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `导入失败 (${r.status})`);
    await loadEmojis();
    const parts = [];
    if (data.added) parts.push(`新增 ${data.added}`);
    if (data.updated) parts.push(`更新 ${data.updated}`);
    if (data.unchanged) parts.push(`已有 ${data.unchanged}`);
    if (data.skipped) parts.push(`跳过 ${data.skipped}`);
    toast(`导入完成：${parts.join("，") || "没有变化"}`, data.added || data.updated ? "ok" : "");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

function renderWallChips() {
  const box = $("#wallChips");
  if (!box) return;
  const count = {};
  for (const e of state.emojis) {
    for (const t of (e.tags || "").split(",")) {
      const tag = t.trim();
      if (tag) count[tag] = (count[tag] || 0) + 1;
    }
  }
  // 锁定标签即使当前没有表情引用也要显示，保证随时能解锁
  for (const tag of state.tagLocks) {
    if (!(tag in count)) count[tag] = 0;
  }
  const untagged = state.emojis.filter((e) => !e.tags).length;
  const chip = (val, label, n, cls, lockable = true) => {
    const locked = state.tagLocks.has(val);
    const lockTip = locked ? "已锁定标签名；点此解锁" : "锁定标签名：整理标签不会改名或合并它";
    const lock = lockable
      ? `<i class="chip-lock ${locked ? "on" : ""}" data-lock-tag="${esc(val)}"
           role="button" tabindex="0"
           title="${lockTip}" aria-label="${lockTip}">${locked ? "🔒" : "🔓"}</i>`
      : "";
    return `<button type="button" class="chip ${cls || ""} ${state.wallFilter.tag === val ? "on" : ""}"
      data-tag="${esc(val)}">${esc(label)} ${n}${lock}</button>`;
  };
  const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]);
  const cap = state.wallShowAllTags ? sorted.length : 100;
  const shown = sorted.slice(0, cap);
  const shownTags = new Set(shown.map(([t]) => t));
  for (const tag of state.tagLocks) {
    if (!shownTags.has(tag)) shown.push([tag, count[tag] || 0]);
  }
  // 强制显示的锁定标签已从 shown 里扣除，tail 只统计真正被折叠的低频标签
  const tail = sorted.length - shown.length;
  const toggle =
    tail > 0
      ? `<button type="button" class="chip" id="btnChipsAll">展开全部 ${sorted.length} 个标签（还有 ${tail} 个低频）</button>`
      : state.wallShowAllTags && sorted.length > 100
        ? `<button type="button" class="chip" id="btnChipsAll">收起，只看高频标签</button>`
        : "";
  box.innerHTML =
    chip("", "全部", state.emojis.length, "", false) +
    shown.map(([t, n]) => chip(t, t, n)).join("") +
    toggle +
    (untagged ? chip("__untagged", "未打标", untagged, "warn", false) : "");
}

function wallFilterTag(val) {
  state.wallFilter.tag = val;
  renderEmojiWall();
}

async function wallToggleLock(eid, locked) {
  const e = state.emojis.find((x) => x.id === eid);
  if (!e) return;
  const msg = locked
    ? "锁定后这张表情的标签、描述、AI 重打标和删除都会被禁止；发送不受影响。确定锁定？"
    : "解锁后才能修改标签/描述、AI 重打标或删除。确定解锁这张表情？";
  if (!confirm(msg)) return;
  try {
    const r = await api(`/api/emojis/${encodeURIComponent(eid)}/lock`, {
      method: "POST",
      body: JSON.stringify({ locked }),
    });
    const idx = state.emojis.findIndex((x) => x.id === eid);
    if (idx >= 0) state.emojis[idx] = r.emoji;
    renderEmojiWall();
    renderWallDrawer();
    toast(locked ? "已锁定" : "已解锁", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function toggleTagLock(tag) {
  if (!tag) return;
  const locked = state.tagLocks.has(tag);
  try {
    const r = await api("/api/emoji/tag-locks", {
      method: "POST",
      body: JSON.stringify({ tag, locked: !locked }),
    });
    state.tagLocks = new Set(r.tags || []);
    renderWallChips();
    toast(locked ? "已解锁标签名" : "已锁定标签名", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

function openWallDrawer(eid) {
  state.wallEmojiId = eid;
  renderEmojiWall();
  renderWallDrawer();
  $("#wallDrawer").hidden = false;
  if (!state.chats.length) loadChats().then(renderWallDrawer).catch(() => {});
}

function closeWallDrawer() {
  $("#wallDrawer").hidden = true;
  state.wallEmojiId = null;
  renderEmojiWall();
}

function renderWallDrawer() {
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  const box = $("#wallDrawer");
  if (!e) { closeWallDrawer(); return; }
  const tags = (e.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
  const locked = !!e.locked;
  const tagHtml = locked
    ? tags.map((t) =>
        `<span class="chip on">${esc(t)}${state.tagLocks.has(t) ? " 🔒" : ""}</span>`
      ).join("")
    : tags.map((t) =>
        `<span class="chip on" data-tag="${esc(t)}">${esc(t)}<i onclick="wallRemoveTag(this)">×</i></span>`
      ).join("");
  const source =
    e.source === "searchanythingfun"
      ? `<div class="wall-meta">来源：<a href="https://www.searchanythingfun.xyz" target="_blank" rel="noopener">searchanythingfun</a></div>`
      : "";
  box.innerHTML = `
    <div class="wall-drawer-head"><b>表情 #${e.id}</b>
      <span class="wall-badge">${locked ? "🔒 已锁定" : "🔓 未锁定"}</span>
      <button type="button" onclick="closeWallDrawer()">×</button></div>
    <img class="wall-big" src="${emojiProxyUrl(e)}" alt="表情" onerror="this.classList.add('broken')">
    <div class="wall-meta">命中 ${e.hit_count || 0} 次 · 最近 ${fmtTime(e.last_seen_at)}</div>
    ${source}
    ${locked && e.source === "searchanythingfun"
      ? '<div class="wall-meta">这张表情由 searchanythingfun 自动入库，默认锁定。</div>'
      : ""}
    <div class="label">标签</div>
    <div class="wall-edit-tags" id="wallEditTags">
      ${tagHtml}
      ${locked ? "" : '<input id="wallNewTag" placeholder="回车添加">'}
    </div>
    <div class="label">描述</div>
    <textarea id="wallDesc" rows="2" ${locked ? "readonly" : ""}>${esc(e.description || "")}</textarea>
    <div class="wall-actions">
      <select id="wallChatPick">
        <option value="">发送到会话…</option>
        ${state.chats.slice(0, 20).map((c) => `<option value="${esc(c.uid)}">${esc(c.name || c.uid)}</option>`).join("")}
      </select>
      <button type="button" class="primary" onclick="wallSend()">✉ 发送</button>
      ${locked
        ? `<button type="button" class="unlock" onclick="wallToggleLock(${e.id}, false)">🔓 解锁</button>`
        : `<button type="button" class="lock" onclick="wallToggleLock(${e.id}, true)">🔒 锁定</button>`}
      <button type="button" class="retag" ${locked ? "disabled" : ""} onclick="wallRetag()">🔄 AI 重打标</button>
      <button type="button" class="danger" ${locked ? "disabled" : ""} onclick="wallDelete(${e.id})">🗑 删除</button>
    </div>`;
  if (!locked) {
    $("#wallNewTag").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        wallAddTag($("#wallNewTag").value);
      }
    });
    $("#wallDesc").addEventListener("change", () => wallSaveTags());
  }
}

async function wallSaveTags() {
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  if (!e) return;
  if (e.locked) { toast("已锁定，先解锁再修改", "err"); return; }
  const tags = [...$("#wallEditTags").querySelectorAll(".chip")]
    .map((c) => c.dataset.tag).filter(Boolean).join(",");
  try {
    const r = await api(`/api/emojis/${e.id}`, {
      method: "PUT",
      body: JSON.stringify({ tags, description: $("#wallDesc").value }),
    });
    const idx = state.emojis.findIndex((x) => x.id === e.id);
    if (idx >= 0) state.emojis[idx] = r.emoji;
    renderEmojiWall();
    renderWallDrawer();
    toast("已保存", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

function wallAddTag(raw) {
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  if (!e || e.locked) { toast("已锁定，先解锁再修改", "err"); return; }
  const tag = String(raw || "").trim().slice(0, 20);
  if (!tag) return;
  if ([...$("#wallEditTags").querySelectorAll(".chip")].some((c) => c.dataset.tag === tag)) {
    toast("标签已存在");
    return;
  }
  $("#wallNewTag").value = "";
  const span = document.createElement("span");
  span.className = "chip on";
  span.dataset.tag = tag;
  span.innerHTML = `${esc(tag)}<i>×</i>`;
  span.querySelector("i").onclick = () => { span.remove(); wallSaveTags(); };
  $("#wallEditTags").insertBefore(span, $("#wallNewTag"));
  wallSaveTags();
}

function wallRemoveTag(el) {
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  if (!e || e.locked) { toast("已锁定，先解锁再修改", "err"); return; }
  el.parentElement.remove();
  wallSaveTags();
}

async function wallSend() {
  const uid = $("#wallChatPick").value;
  if (!uid) { toast("先选择一个会话", "err"); return; }
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  if (!e) return;
  try {
    await api(`/api/chats/${encodeURIComponent(uid)}/send-emoji`, {
      method: "POST",
      body: JSON.stringify({ url: e.url, md5: e.md5 || "", width: e.width || 0, height: e.height || 0 }),
    });
    toast("已发送", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function wallRetag() {
  const e = state.emojis.find((x) => x.id === state.wallEmojiId);
  if (!e) return;
  if (e.locked) { toast("已锁定，先解锁再重新打标", "err"); return; }
  const btn = $("#wallDrawer .retag");
  if (btn) { btn.disabled = true; btn.textContent = "🔄 打标中…"; }
  try {
    const r = await api(`/api/emojis/${e.id}/retag`, { method: "POST" });
    if (r.rejected) {
      state.emojis = state.emojis.filter((x) => x.id !== e.id);
      renderEmojiWall();
      renderWallDrawer();
      toast("AI 判定为非表情包照片，已删除并拒收", "ok");
      return;
    }
    const idx = state.emojis.findIndex((x) => x.id === e.id);
    if (idx >= 0) state.emojis[idx] = r.emoji;
    renderEmojiWall();
    renderWallDrawer();
    toast("AI 已重新打标", "ok");
  } catch (err) {
    toast(err.message, "err");
    if (btn) { btn.disabled = false; btn.textContent = "🔄 AI 重打标"; }
  }
}

async function importQqCache() {
  const btn = $("#btnImportQQ");
  if (btn) { btn.disabled = true; btn.textContent = "扫描中…"; }
  try {
    const r = await api("/api/emojis/import-qq", { method: "POST" });
    await loadEmojis();
    if (r.imported) {
      toast(
        `从QQ缓存导入 ${r.imported} 个表情（跳过已有 ${r.skipped}`
        + `${r.rejected ? `，拒收名单 ${r.rejected}` : ""}）`,
        "ok"
      );
    } else if (r.skipped || r.rejected) {
      toast(`没有新表情，${r.skipped} 个已在库里${r.rejected ? `，${r.rejected} 个在拒收名单` : ""}`, "ok");
    } else {
      toast("Ori 目录里没有可用图片");
    }
  } catch (err) {
    toast(err.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "从QQ缓存导入"; }
  }
}

async function wallDelete(eid) {
  eid = Number(eid);
  const current = state.emojis.find((x) => x.id === eid);
  if (current && current.locked) {
    toast("已锁定，先解锁再删除", "err");
    return;
  }
  if (!confirm("确定删除这个表情吗？")) return;
  try {
    await api(`/api/emojis/${encodeURIComponent(eid)}`, { method: "DELETE" });
    state.emojis = state.emojis.filter((x) => x.id !== eid);
    if (state.wallEmojiId === eid) state.wallEmojiId = null;
    $("#wallDrawer").hidden = true;
    renderEmojiWall();
    renderEmojiPanel();
    toast("已删除", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ---------------------------------------------------------------- 使用说明 */
async function loadUsagePage() {
  const box = $("#usageDoc");
  if (!box) return;
  const note = $("#usageUpdated");
  if (note) note.textContent = "加载中…";
  box.innerHTML = '<div class="empty-mini">加载中…</div>';
  try {
    const data = await api("/api/usage", { cache: "no-store" });
    box.innerHTML = data.html || '<div class="empty-mini">说明内容为空</div>';
    if (note && data.updated_at) {
      const d = new Date(data.updated_at * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      note.textContent =
        `最后更新：${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
  } catch (err) {
    if (note) note.textContent = "读取失败";
    box.innerHTML = `<div class="empty-mini">说明读取失败：${esc(
      err.message || "未知错误"
    )}</div>`;
  }
}

/* ---------------------------------------------------------------- 协议 */
async function loadEulaPage() {
  const s = state.eula;
  $$(".eula-tab").forEach((b) =>
    b.classList.toggle("on", b.dataset.eulaLang === s.lang)
  );
  const note = $("#eulaNote");
  if (note) note.hidden = s.lang !== "en";
  const open = $("#eulaOpen");
  if (open) open.href = `/eula?lang=${s.lang}`;
  const box = $("#eulaDoc");
  if (!box) return;
  if (s.cache[s.lang]) {
    box.innerHTML = s.cache[s.lang];
    return;
  }
  const lang = s.lang;
  box.innerHTML = '<div class="empty-mini">加载中…</div>';
  try {
    const data = await api(`/api/eula?lang=${encodeURIComponent(lang)}`);
    const content = data.html || "";
    s.cache[data.lang || lang] = content;
    if (s.lang === lang) {
      box.innerHTML = content || '<div class="empty-mini">协议内容为空</div>';
    }
  } catch (err) {
    if (s.lang === lang) {
      box.innerHTML = `<div class="empty-mini">协议读取失败：${esc(
        err.message || "未知错误"
      )}</div>`;
    }
  }
}

function setEulaLang(lang) {
  state.eula.lang = lang === "en" ? "en" : "zh";
  storageSet("df_eula_lang", state.eula.lang);
  loadEulaPage();
}

(async function init() {
  bind();
  syncPlaceholders();
  await loadSettings();
  state.account = state.settings.active_account || "";
  await loadAccounts();
  if (state.accounts[0]) {
    state.editingAccount = state.account || state.accounts[0].id;
    fillAccountForm(state.editingAccount);
  }
  await loadStatus();
  await Promise.all([loadChats(), loadStats(), loadEvents(), loadEmojis()]);
  connectWs();
  checkUpdate(false); // 静默初始化侧边栏版本号，失败不打扰
  setInterval(() => {
    if (state.page === "overview") loadStats();
  }, 20000);
})();

/* ---------------------------------------------------------------- 自我进化 */
const GROWTH_KIND_LABELS = {
  voice: "口癖",
  opinion: "观点",
  value: "价值观",
  preference: "偏好",
  relationship: "关系",
  habit: "习惯",
  narrative: "自我叙事",
  joke: "内部梗",
  other: "其它",
};

function renderGrowthPending(n) {
  const count = Number(n) || 0;
  const badge = $("#growthBadge");
  if (badge) {
    badge.hidden = count <= 0;
    badge.textContent = count;
  }
  const inline = $("#growthPendingInline");
  if (inline) {
    inline.hidden = count <= 0;
    inline.textContent = count;
  }
}

function renderGrowthStatus(st) {
  state.growth.status = st;
  const enabled = !!st.enabled;
  $("#growthStateTag").textContent = st.running ? "反思中" : enabled ? "已启动" : "未启动";
  $("#btnGrowthEnable").textContent = enabled ? "关闭成长" : "启动成长";
  $("#btnGrowthEnable").classList.toggle("danger", enabled);
  $("#btnGrowthReflect").disabled = !enabled || !!st.running;
  $("#growthIntro").hidden = enabled;
  const last = st.last_run;
  $("#growthStatus").innerHTML = `
    <div class="growth-stat"><span>状态</span><strong>${enabled ? "运行中" : "已关闭"}</strong></div>
    <div class="growth-stat"><span>上次反思</span><strong>${last ? fmtTime(last.finished_at || last.started_at) : "还没跑过"}</strong></div>
    <div class="growth-stat"><span>下次预计</span><strong>${st.next_run ? fmtTime(st.next_run) : "—"}</strong></div>
    <div class="growth-stat"><span>待批</span><strong>${Number(st.pending || 0)}</strong></div>`;
  renderGrowthPending(st.pending || 0);
}

async function loadGrowthStatus() {
  try {
    const st = await api("/api/growth/status");
    renderGrowthStatus(st);
  } catch (e) {
    toast(e.message || "成长状态读取失败", "err");
  }
}

function renderGrowthSummary(text) {
  state.growth.summary = text || "";
  const el = $("#growthSummary");
  if (!el) return;
  el.innerHTML = text
    ? `<div class="growth-summary-text">${esc(text)}</div>
       <button class="link-btn" onclick="editGrowthSummary()">编辑总览</button>`
    : `<div class="empty-mini">还没有自我总览。点「现在想想」或等夜间反思。</div>
       <button class="link-btn" onclick="editGrowthSummary()">手写一段</button>`;
}

async function editGrowthSummary() {
  const text = prompt("编辑自我总览（建议 500-800 字）：", state.growth.summary || "");
  if (text === null) return;
  try {
    await api("/api/growth/summary", {
      method: "PATCH",
      body: JSON.stringify({ self_summary: text }),
    });
    toast("总览已保存");
    await loadGrowthPage();
  } catch (e) {
    toast(e.message || "保存失败", "err");
  }
}

async function loadGrowthTraits() {
  const params = new URLSearchParams();
  const q = ($("#growthTraitSearch")?.value || "").trim();
  const kind = $("#growthKindFilter")?.value || "";
  const status = $("#growthStatusFilter")?.value || "";
  if (q) params.set("q", q);
  if (kind) params.set("kind", kind);
  if (status) params.set("status", status);
  params.set("limit", "300");
  try {
    const res = await api("/api/growth/traits?" + params.toString());
    state.growth.traits = res.items || [];
    renderGrowthTraits();
  } catch (e) {
    toast(e.message || "特质读取失败", "err");
  }
}

function renderGrowthTraits() {
  const el = $("#growthTraitList");
  if (!el) return;
  const items = state.growth.traits || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-mini">还没有特质。聊几天，或手动加一条。</div>';
    return;
  }
  el.innerHTML = items
    .map((t) => {
      const evidence = (t.evidence || [])
        .map((e) => `<div class="growth-evidence">${esc(e.chat || "")} ${esc(e.quote || "")}</div>`)
        .join("");
      const scope = t.scope === "chat" ? " · 仅当前会话" : "";
      return `<div class="growth-trait">
        <div class="growth-trait-head">
          <span class="chip chip-mini">${esc(GROWTH_KIND_LABELS[t.kind] || t.kind)}</span>
          <span class="chip chip-mini">${esc(t.status)}</span>
          ${t.source === "owner" ? '<span class="chip chip-mini">号主</span>' : ""}
          <span class="growth-meta">强度 ${Number(t.strength)} · 置信 ${Number(t.confidence || 0).toFixed(2)}${scope}</span>
        </div>
        <div class="growth-trait-content">${esc(t.content)}</div>
        <div class="growth-trait-actions">
          ${
            t.status === "retired"
              ? `<button class="link-btn" onclick="activateGrowthTrait(${t.id})">复活</button>`
              : `<button class="link-btn" onclick="retireGrowthTrait(${t.id})">退役</button>`
          }
          ${evidence ? `<details><summary>证据</summary>${evidence}</details>` : ""}
        </div>
      </div>`;
    })
    .join("");
}

async function addGrowthTrait() {
  const content = ($("#growthTraitContent")?.value || "").trim();
  if (!content) return toast("先写一条特质", "err");
  const kind = $("#growthTraitKind")?.value || "other";
  try {
    await api("/api/growth/traits", {
      method: "POST",
      body: JSON.stringify({ content, kind }),
    });
    $("#growthTraitContent").value = "";
    toast("特质已添加");
    await loadGrowthTraits();
  } catch (e) {
    toast(e.message || "添加失败", "err");
  }
}

async function retireGrowthTrait(id) {
  try {
    await api(`/api/growth/traits/${id}/retire`, { method: "POST" });
    await loadGrowthTraits();
  } catch (e) {
    toast(e.message || "退役失败", "err");
  }
}

async function activateGrowthTrait(id) {
  try {
    await api(`/api/growth/traits/${id}/activate`, { method: "POST" });
    await loadGrowthTraits();
  } catch (e) {
    toast(e.message || "复活失败", "err");
  }
}

async function loadGrowthJournal() {
  try {
    const res = await api("/api/growth/journal?limit=50");
    state.growth.journal = res.items || [];
    renderGrowthJournal();
  } catch (e) {
    toast(e.message || "日记读取失败", "err");
  }
}

function renderGrowthJournal() {
  const el = $("#growthJournalList");
  if (!el) return;
  const items = state.growth.journal || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-mini">还没有日记。夜里反思后会写第一篇。</div>';
    return;
  }
  el.innerHTML = items
    .map(
      (j) => `<div class="growth-trait">
        <div class="growth-trait-head">
          <strong>${esc(j.day)}</strong>
          <div class="spacer"></div>
          <button class="link-btn" onclick="deleteGrowthJournal(${j.id})">删除</button>
        </div>
        <div class="growth-trait-content">${esc(j.content)}</div>
      </div>`
    )
    .join("");
}

async function deleteGrowthJournal(id) {
  if (!confirm("删除这篇日记？删除后不可恢复。")) return;
  try {
    await api(`/api/growth/journal/${id}`, { method: "DELETE" });
    toast("日记已删除");
    await loadGrowthJournal();
  } catch (e) {
    toast(e.message || "删除失败", "err");
  }
}

async function loadGrowthGoals() {
  try {
    const res = await api("/api/growth/goals");
    state.growth.goals = res.items || [];
    renderGrowthGoals();
  } catch (e) {
    toast(e.message || "目标读取失败", "err");
  }
}

function renderGrowthGoals() {
  const el = $("#growthGoalList");
  if (!el) return;
  const items = state.growth.goals || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-mini">还没有目标。它会自己定，也可以手动加。</div>';
    return;
  }
  el.innerHTML = items
    .map(
      (g) => `<div class="growth-trait">
        <div class="growth-trait-head">
          <span class="chip chip-mini">${esc(g.status)}</span>
          <span class="growth-trait-content">${esc(g.content)}</span>
        </div>
        <div class="growth-meta">${esc(g.progress || "")}</div>
        <div class="growth-trait-actions">
          ${g.status === "open" ? `<button class="link-btn" onclick="setGrowthGoalStatus(${g.id}, 'done')">完成</button><button class="link-btn" onclick="setGrowthGoalStatus(${g.id}, 'dropped')">放弃</button>` : ""}
        </div>
      </div>`
    )
    .join("");
}

async function addGrowthGoal() {
  const content = ($("#growthGoalContent")?.value || "").trim();
  if (!content) return toast("先写一个目标", "err");
  try {
    await api("/api/growth/goals", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    $("#growthGoalContent").value = "";
    toast("目标已添加");
    await loadGrowthGoals();
  } catch (e) {
    toast(e.message || "添加失败", "err");
  }
}

async function setGrowthGoalStatus(id, status) {
  try {
    await api(`/api/growth/goals/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    await loadGrowthGoals();
  } catch (e) {
    toast(e.message || "更新失败", "err");
  }
}

async function loadGrowthProposals() {
  try {
    const res = await api("/api/growth/proposals?status=pending");
    state.growth.proposals = res.items || [];
    renderGrowthProposals();
    renderGrowthPending(state.growth.proposals.length);
  } catch (e) {
    toast(e.message || "待批读取失败", "err");
  }
}

function renderGrowthProposals() {
  const el = $("#growthProposalList");
  if (!el) return;
  const items = state.growth.proposals || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-mini">没有待批。跳变和身份变化会出现在这里。</div>';
    return;
  }
  el.innerHTML = items
    .map(
      (p) => `<div class="growth-proposal">
        <div class="growth-trait-head">
          <span class="chip chip-mini">${esc(p.kind)}</span>
          <span class="growth-meta">${esc(p.reason || "")}</span>
        </div>
        <div class="growth-diff">
          <div><div class="growth-meta">现在</div><pre>${esc(JSON.stringify(p.before, null, 2))}</pre></div>
          <div><div class="growth-meta">拟改为</div><pre>${esc(JSON.stringify(p.payload, null, 2))}</pre></div>
        </div>
        <div class="growth-trait-actions">
          <button class="btn btn-sm" onclick="approveGrowthProposal(${p.id})">批准</button>
          <button class="btn btn-sm danger" onclick="rejectGrowthProposal(${p.id})">驳回</button>
        </div>
      </div>`
    )
    .join("");
}

async function approveGrowthProposal(id) {
  const p = (state.growth.proposals || []).find((x) => x.id === id);
  if (!p) return;
  const raw = prompt(
    "可编辑提案 JSON 后批准（留空按原样）：",
    JSON.stringify(p.payload || {}, null, 2)
  );
  if (raw === null) return;
  let payload = p.payload;
  if (raw.trim()) {
    try {
      payload = JSON.parse(raw);
    } catch {
      return toast("JSON 不合法", "err");
    }
  }
  try {
    await api(`/api/growth/proposals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ payload }),
    });
    toast("已批准并生效");
    await loadGrowthPage();
  } catch (e) {
    toast(e.message || "批准失败", "err");
  }
}

async function rejectGrowthProposal(id) {
  const reason = prompt("驳回原因（可留空）：", "");
  if (reason === null) return;
  try {
    await api(`/api/growth/proposals/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    toast("已驳回");
    await loadGrowthPage();
  } catch (e) {
    toast(e.message || "驳回失败", "err");
  }
}

async function loadGrowthEvents() {
  try {
    const res = await api("/api/growth/events?limit=100");
    state.growth.events = res.items || [];
    renderGrowthEvents();
  } catch (e) {
    toast(e.message || "时间线读取失败", "err");
  }
}

function renderGrowthEvents() {
  const el = $("#growthEventList");
  if (!el) return;
  const items = state.growth.events || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-mini">还没有变化记录。</div>';
    return;
  }
  el.innerHTML = items
    .map(
      (e) => `<div class="growth-event">
        <span class="chip chip-mini">${esc(e.kind)}</span>
        <span class="growth-meta">${fmtTime(e.created_at)}</span>
        <span class="growth-meta">${esc((JSON.stringify(e.after) || "").slice(0, 160))}</span>
        ${e.reversible ? `<button class="link-btn" onclick="rollbackGrowthEvent(${e.id})">回滚</button>` : ""}
      </div>`
    )
    .join("");
}

async function rollbackGrowthEvent(id) {
  if (!confirm("回滚这条变化？")) return;
  try {
    await api(`/api/growth/events/${id}/rollback`, {
      method: "POST",
      body: JSON.stringify({ force: false }),
    });
  } catch (e) {
    if (String(e.message || "").includes("覆盖")) {
      if (!confirm("这条变化已被后续变化覆盖，仍要强制回滚？")) return;
      try {
        await api(`/api/growth/events/${id}/rollback`, {
          method: "POST",
          body: JSON.stringify({ force: true }),
        });
      } catch (err) {
        return toast(err.message || "回滚失败", "err");
      }
    } else {
      return toast(e.message || "回滚失败", "err");
    }
  }
  toast("已回滚");
  await loadGrowthPage();
}

async function toggleGrowth() {
  const enabled = !!state.growth.status?.enabled;
  try {
    await api("/api/growth/settings", {
      method: "PATCH",
      body: JSON.stringify(
        enabled
          ? { account_enabled: false }
          : { global_enabled: true, account_enabled: true }
      ),
    });
    toast(enabled ? "成长已关闭，提示词回到原样" : "成长已启动");
    await loadGrowthPage();
  } catch (e) {
    toast(e.message || "切换失败", "err");
  }
}

async function reflectNow() {
  try {
    await api("/api/growth/reflect", { method: "POST" });
    toast("开始反思，完成后会自动刷新");
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        const st = await api("/api/growth/status");
        renderGrowthStatus(st);
        if (!st.running || tries > 40) {
          clearInterval(timer);
          await loadGrowthPage();
        }
      } catch {
        clearInterval(timer);
      }
    }, 3000);
  } catch (e) {
    toast(e.message || "反思启动失败", "err");
  }
}

function switchGrowthTab(tab) {
  state.growth.tab = tab;
  $$(".growth-tabs .chip").forEach((b) =>
    b.classList.toggle("active", b.dataset.growthTab === tab)
  );
  $$(".growth-pane").forEach((p) => {
    p.classList.toggle("active", p.id === `growth${tab[0].toUpperCase()}${tab.slice(1)}Pane`);
    p.hidden = p.id !== `growth${tab[0].toUpperCase()}${tab.slice(1)}Pane`;
  });
}

function exportGrowth() {
  window.open(mediaUrl("/api/growth/export?format=md"), "_blank");
}

async function loadGrowthPage() {
  try {
    const [status, summary] = await Promise.all([
      api("/api/growth/status"),
      api("/api/growth/summary"),
    ]);
    renderGrowthStatus(status);
    renderGrowthSummary(summary.self_summary || "");
    await Promise.all([
      loadGrowthTraits(),
      loadGrowthJournal(),
      loadGrowthGoals(),
      loadGrowthProposals(),
      loadGrowthEvents(),
    ]);
    switchGrowthTab(state.growth.tab || "self");
  } catch (e) {
    toast(e.message || "成长页加载失败", "err");
  }
}

/* ---------------------------------------------------------------- 心情 */
const moodState = { data: null, tab: "state" };

async function loadMoodPage() {
  try {
    const d = await api("/api/mood/state");
    moodState.data = d;
    renderMoodPage(d);
  } catch (e) {
    $("#moodReason").textContent = e.message || "加载失败";
  }
}

function renderMoodPage(d) {
  $("#moodEnabled").checked = !!d.enabled;
  $("#moodEnabledTag").textContent = d.enabled ? "已启用" : "已关闭";
  $("#moodValue").textContent = d.mood;
  $("#moodLabel").textContent = d.moodLabel || "";
  $("#moodReason").textContent = d.lastReason || "这会儿平静地待着，等你来。";
  $("#moodFace").src = "/mood-assets/" + (d.face || "face-ok.png");
  $("#moodDims").innerHTML = (d.dims || []).map((x) => `
    <div class="mood-dim">
      <span>${esc(x.label)}</span>
      <div class="mood-bar"><i style="width:${Math.max(0, Math.min(100, x.value))}%"></i></div>
      <em>${x.value}</em>
    </div>`).join("");
  $("#moodTimeline").innerHTML = (d.timeline && d.timeline.length)
    ? d.timeline.map((t) => {
        const dt = new Date(t.at);
        const hh = String(dt.getHours()).padStart(2, "0");
        const mm = String(dt.getMinutes()).padStart(2, "0");
        const sign = t.delta > 0 ? "+" + t.delta : String(t.delta || "·");
        return `<div class="mood-tlrow"><span>${hh}:${mm}</span><b>${t.mood}</b>
          <em>${sign}</em><span>${esc(t.reason || "—")}</span></div>`;
      }).join("")
    : '<div class="empty-mini">还没有记录 —— 聊起来就有了。</div>';
  fillMoodSettings(d);
  loadMoodSlots();
}

function fillMoodSettings(d) {
  const s = d.settings || {};
  $("#moodMode").value = s.mode || "account";
  $("#moodAutoJudge").checked = s.auto_judge !== false;
  $("#moodJudgeModel").value = s.judge_model || "";
  $("#moodInjectConf").value = s.inject_confidence ?? 60;
  $("#moodSigMood").value = s.sig_mood ?? 3;
  $("#moodSigDim").value = s.sig_dim ?? 5;
  $("#moodInjectMode").value = s.inject_mode || "event";
  $("#moodPerDay").value = s.inject_per_day ?? 6;
  $("#moodBase").value = s.base ?? 70;
  $("#moodFloor").value = s.floor ?? 0;
  $("#moodHalf").value = s.decay_half_hours ?? 12;
  $("#moodSelfName").value = s.self_name || "";
  $("#moodOwnerName").value = s.owner_name || "";
  $("#moodAliases").value = s.self_aliases || "";
}

async function saveMoodEnabled(on) {
  try {
    await api("/api/mood/config", { method: "POST", body: JSON.stringify({ enabled: on }) });
    toast(on ? "心情已开启" : "心情已关闭");
    loadMoodPage();
  } catch (e) {
    toast(e.message || "保存失败", "err");
    $("#moodEnabled").checked = !on;
  }
}

async function saveMoodAccount(patch) {
  try {
    await api("/api/mood/config", { method: "POST", body: JSON.stringify({ account: patch }) });
    toast("已保存");
    loadMoodPage();
  } catch (e) {
    toast(e.message || "保存失败", "err");
  }
}

async function saveMoodJudgeForm() {
  const num = (id) => Number($(id).value);
  await saveMoodAccount({
    auto_judge: $("#moodAutoJudge").checked,
    judge_model: $("#moodJudgeModel").value.trim(),
    inject_confidence: num("#moodInjectConf"),
    sig_mood: num("#moodSigMood"),
    sig_dim: num("#moodSigDim"),
    inject_mode: $("#moodInjectMode").value,
    inject_per_day: num("#moodPerDay"),
    base: num("#moodBase"),
    floor: num("#moodFloor"),
    decay_half_hours: num("#moodHalf"),
    self_name: $("#moodSelfName").value.trim(),
    owner_name: $("#moodOwnerName").value.trim(),
    self_aliases: $("#moodAliases").value.trim(),
  });
}

async function loadMoodSlots() {
  const d = await api("/api/mood/state");
  const list = d.slots || [];
  $("#moodSlots").innerHTML = list.map((s) => `
    <div class="mood-slotrow">
      <b>${esc(s.label || s.slot_id)}</b>
      <span class="muted">${esc(s.slot_id)}</span>
      <button class="link-btn" data-mood-act="rename" data-slot="${esc(s.slot_id)}">改名</button>
      ${s.slot_id === "default" ? "" :
        `<button class="link-btn" data-mood-act="delete" data-slot="${esc(s.slot_id)}">删除</button>`}
    </div>`).join("") || '<div class="empty-mini">只有默认槽</div>';
  const opts = ['<option value="default">默认</option>']
    .concat(list.filter((s) => s.slot_id !== "default")
      .map((s) => `<option value="${esc(s.slot_id)}">${esc(s.label || s.slot_id)}</option>`));
  $("#moodBindSlot").innerHTML = opts.join("");
  $("#moodBindings").innerHTML = (d.bindings || []).map((b) => `
    <div class="mood-slotrow"><span>${esc(b.chat_uid)}</span>
      <span class="muted">→ ${esc(b.slot_id)}</span>
      <button class="link-btn" data-mood-act="unbind" data-chat="${esc(b.chat_uid)}">解绑</button>
    </div>`).join("") || '<div class="empty-mini">还没有绑定</div>';
}

async function moodSlotAction(payload) {
  try {
    await api("/api/mood/slots", { method: "POST", body: JSON.stringify(payload) });
    toast("已保存");
    loadMoodPage();
  } catch (e) {
    toast(e.message || "操作失败", "err");
  }
}

async function createMoodSlot() {
  const label = $("#moodSlotLabel").value.trim();
  $("#moodSlotLabel").value = "";
  await moodSlotAction({ action: "create", label });
}

async function renameMoodSlot(slotId) {
  const label = prompt("槽位名字", slotId) || "";
  if (label) await moodSlotAction({ action: "rename", slot_id: slotId, label });
}

async function deleteMoodSlot(slotId) {
  if (!confirm(`删除槽位 ${slotId}？时间和绑定会一起消失。`)) return;
  await moodSlotAction({ action: "delete", slot_id: slotId });
}

async function bindMoodChat() {
  const slot = $("#moodBindSlot").value;
  const chat = $("#moodBindChat").value.trim();
  if (!chat) return toast("先填会话 uid");
  await moodSlotAction({ action: "bind", slot_id: slot, chat_uid: chat });
}

async function unbindMoodChat(chatUid) {
  await moodSlotAction({ action: "unbind", chat_uid: chatUid });
}

async function resetMood() {
  if (!confirm("把心情重置到基线？会记一条「手动重置」。")) return;
  try {
    await api("/api/mood/reset", { method: "POST", body: JSON.stringify({}) });
    toast("已重置");
    loadMoodPage();
  } catch (e) {
    toast(e.message || "重置失败", "err");
  }
}

async function testMoodJudge() {
  const text = $("#moodTestText").value.trim();
  if (!text) return toast("先写一句对方说的话");
  const out = $("#moodTestOut");
  out.hidden = false;
  out.textContent = "判断中…";
  try {
    const d = await api("/api/mood/judge-test", {
      method: "POST",
      body: JSON.stringify({ text, reply: $("#moodTestReply").value.trim() }),
    });
    out.textContent = d.delta
      ? `涨跌 ${d.delta.moodDelta >= 0 ? "+" : ""}${d.delta.moodDelta}，可信度 ${d.delta.confidence}\n情绪：${
          Object.entries(d.delta.dims).filter(([, v]) => v).map(([k, v]) => `${k}${v > 0 ? "+" : ""}${v}`).join("、") || "无"
        }\n理由：${d.delta.reason || "—"}\n\n注入预览：\n${d.preview || "（这次不会注入）"}`
      : "这次没判出结果（模型没给合法 JSON）。";
  } catch (e) {
    out.textContent = e.message || "试判断失败";
  }
}

function resetMoodWidget() {
  try {
    localStorage.removeItem("fm-widget-pos");
    toast("已重置，刷新页面后生效");
  } catch (e) {
    toast("这个浏览器不允许本地存储", "err");
  }
}

/* ---------------------------------------------------------------- 主动搭话 */
const proactiveData = { status: null, chats: [], busy: false };

const PROACTIVE_STATUS_TEXT = {
  sent: "已发",
  skipped: "跳过",
  error: "失败",
  miss: "没掷中",
  preview: "试运行",
  busy: "正在跑",
  warn: "记账异常",
};
const PROACTIVE_TRIGGER_TEXT = { auto: "自动", test: "测试", manual: "手动" };

function proactiveTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toDateString() === new Date().toDateString()
    ? fmtClock(ts)
    : `${fmtTime(ts)} ${fmtClock(ts)}`;
}

function proactiveStatusLabel(s) {
  return PROACTIVE_STATUS_TEXT[String(s || "")] || String(s || "—");
}

async function loadProactivePage() {
  const box = $("#proactiveStates");
  try {
    const [status, chatRes] = await Promise.all([
      api("/api/proactive/status"),
      api("/api/proactive/chats"),
    ]);
    proactiveData.status = status;
    proactiveData.chats = chatRes.chats || [];
    const whitelist = status.whitelist || [];
    $("#proactiveEnabled").checked = !!status.enabled;
    $("#proactiveEnabledTag").textContent = status.enabled ? "已开启" : "已关闭";
    const sch = status.scheduler || {};
    $("#proactiveSchedulerTag").textContent = sch.running
      ? `调度中 · 每 ${sch.interval || 60} 秒一看`
      : "调度未运行";
    renderProactiveStates(status.states || []);
    renderProactiveWhitelist(whitelist);
    renderProactivePick(proactiveData.chats, whitelist);
    fillProactiveForm(status.settings || {});
    renderProactiveTestPick(whitelist);
    await loadProactiveEvents();
  } catch (e) {
    if (box) {
      box.innerHTML = `<div class="empty-mini">${esc(e.message || "主动搭话状态加载失败")}</div>`;
    }
  }
}

function renderProactiveStates(states) {
  const box = $("#proactiveStates");
  if (!states.length) {
    box.innerHTML = '<div class="empty-mini">白名单还是空的 —— 先在下面加会话。</div>';
    return;
  }
  const rows = states.map((s) => `
    <tr>
      <td>${esc(s.chat_name || s.chat_uid)}<span class="muted proactive-uid">${esc(s.chat_uid)}</span></td>
      <td>${Number(s.sent_today) || 0}</td>
      <td>${Number(s.misses) || 0}</td>
      <td>${proactiveTime(s.last_self_at)}</td>
      <td>${proactiveTime(s.last_roll_at)}</td>
    </tr>`).join("");
  box.innerHTML = `
    <div class="proactive-table-wrap">
      <table class="proactive-table">
        <thead><tr><th>会话</th><th>今天已发</th><th>未累计</th><th>最近开口</th><th>最近掷骰</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderProactiveWhitelist(whitelist) {
  const box = $("#proactiveWhitelist");
  if (!whitelist.length) {
    box.innerHTML = '<div class="empty-mini">还没有白名单会话 —— 没有白名单不能开启。</div>';
    return;
  }
  box.innerHTML = whitelist.map((w) => `
    <div class="proactive-wl-row">
      <b>${esc(w.name || "（无名会话）")}</b>
      ${w.is_group ? '<span class="tag">群</span>' : ""}
      <span class="muted proactive-uid">${esc(w.chat_uid)}</span>
      <div class="spacer"></div>
      <button class="link-btn" data-proactive-remove="${esc(w.chat_uid)}">移除</button>
    </div>`).join("");
}

function renderProactivePick(chats, whitelist) {
  const sel = $("#proactiveChatPick");
  const keep = sel.value;
  const picked = new Set(whitelist.map((w) => w.chat_uid));
  const rows = chats.filter((c) => !picked.has(c.uid) && c.uid);
  sel.innerHTML = rows.length
    ? rows.map((c) => {
        const flags = [];
        if (c.is_group) flags.push("群");
        if (!c.ai_enabled) flags.push("AI 已关");
        if (c.paused) flags.push("接管中");
        if (c.blacklisted) flags.push("黑名单");
        const mark = flags.length ? `（${flags.join(" · ")}）` : "";
        return `<option value="${esc(c.uid)}">${esc(c.name || c.uid)}${esc(mark)}</option>`;
      }).join("")
    : '<option value="">没有可加入的会话</option>';
  if (keep && rows.some((c) => c.uid === keep)) sel.value = keep;
}

function renderProactiveTestPick(whitelist) {
  const sel = $("#proactiveTestPick");
  const keep = sel.value;
  sel.innerHTML = whitelist.length
    ? whitelist.map((w) => `<option value="${esc(w.chat_uid)}">${esc(w.name || w.chat_uid)}${w.is_group ? "（群）" : ""}</option>`).join("")
    : '<option value="">白名单为空</option>';
  if (keep && whitelist.some((w) => w.chat_uid === keep)) sel.value = keep;
}

async function saveProactiveEnabled(on) {
  if (on && !confirm("开启后 AI 会按概率主动给白名单会话发微信消息，确定开启？")) {
    $("#proactiveEnabled").checked = false;
    return;
  }
  try {
    await api("/api/proactive/config", { method: "POST", body: JSON.stringify({ enabled: on }) });
    toast(on ? "主动搭话已开启" : "主动搭话已关闭");
    loadProactivePage();
  } catch (e) {
    $("#proactiveEnabled").checked = !on;
    toast(e.message || "保存失败", "err");
  }
}

function fillProactiveForm(s) {
  const set = (id, v, d) => { $(id).value = v == null ? d : v; };
  set("#proactiveIntervalMin", s.interval_min, 30);
  set("#proactiveCooldownMin", s.cooldown_min, 20);
  set("#proactiveQuietStart", s.quiet_start, 1);
  set("#proactiveQuietEnd", s.quiet_end, 8);
  set("#proactiveDailyLimit", s.daily_limit, 6);
  set("#proactivePerChatDaily", s.per_chat_daily, 2);
  set("#proactiveHitPeak", s.hit_peak, 20);
  set("#proactiveHitOffpeak", s.hit_offpeak, 60);
  set("#proactiveRecentMin", s.recent_min, 20);
  set("#proactiveRecentFactor", s.recent_factor, 0.15);
  set("#proactiveColdMin", s.cold_min, 360);
  set("#proactiveColdFactor", s.cold_factor, 1.5);
  set("#proactiveMissBonus", s.miss_bonus, 10);
  set("#proactiveBonusCap", s.bonus_cap, 40);
  set("#proactiveMaxChars", s.max_chars, 200);
  $("#proactiveTriggerText").value = s.trigger_text || "";
}

async function saveProactiveForm() {
  const num = (id) => Number($(id).value);
  const patch = {
    interval_min: num("#proactiveIntervalMin"),
    cooldown_min: num("#proactiveCooldownMin"),
    quiet_start: num("#proactiveQuietStart"),
    quiet_end: num("#proactiveQuietEnd"),
    daily_limit: num("#proactiveDailyLimit"),
    per_chat_daily: num("#proactivePerChatDaily"),
    hit_peak: num("#proactiveHitPeak"),
    hit_offpeak: num("#proactiveHitOffpeak"),
    recent_min: num("#proactiveRecentMin"),
    recent_factor: num("#proactiveRecentFactor"),
    cold_min: num("#proactiveColdMin"),
    cold_factor: num("#proactiveColdFactor"),
    miss_bonus: num("#proactiveMissBonus"),
    bonus_cap: num("#proactiveBonusCap"),
    max_chars: num("#proactiveMaxChars"),
    trigger_text: $("#proactiveTriggerText").value.trim(),
  };
  try {
    await api("/api/proactive/config", { method: "POST", body: JSON.stringify({ account: patch }) });
    toast("已保存");
    loadProactivePage();
  } catch (e) {
    toast(e.message || "保存失败", "err");
  }
}

async function addProactiveChat() {
  const uid = $("#proactiveChatPick").value;
  if (!uid) return toast("先选一个会话");
  const chat = proactiveData.chats.find((c) => c.uid === uid) || {};
  const isGroup = !!chat.is_group;
  if (isGroup && !$("#proactiveGroupAck").checked) {
    return toast("这是群聊：先勾选「我确认允许在群聊里主动开口」", "err");
  }
  if (isGroup && !confirm(`确定要让 AI 主动在「${chat.name || uid}」群里开口吗？群里所有人都能看到。`)) return;
  try {
    await api("/api/proactive/whitelist", {
      method: "POST",
      body: JSON.stringify({ action: "add", chat_uid: uid, is_group: isGroup }),
    });
    toast(`已加入白名单：${chat.name || uid}`);
    await loadProactivePage();
  } catch (e) {
    toast(e.message || "加入失败", "err");
  }
}

async function removeProactiveChat(uid) {
  if (!uid) return;
  if (!confirm("把它移出白名单？之后不会再主动搭这个会话。")) return;
  try {
    const d = await api("/api/proactive/whitelist", {
      method: "POST",
      body: JSON.stringify({ action: "remove", chat_uid: uid }),
    });
    if (d.enabled === false) toast("白名单已空，主动搭话已自动关闭");
    else toast("已移除");
    await loadProactivePage();
  } catch (e) {
    toast(e.message || "移除失败", "err");
  }
}

function setProactiveBusy(on) {
  proactiveData.busy = on;
  ["#proactiveDryBtn", "#proactiveSendBtn", "#proactiveRunBtn"].forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.disabled = on;
    if (on) {
      if (!btn.dataset.label) btn.dataset.label = btn.textContent;
      btn.textContent = "生成中…";
    } else if (btn.dataset.label) {
      btn.textContent = btn.dataset.label;
      delete btn.dataset.label;
    }
  });
}

function proactiveResultText(d) {
  const label = `状态：${proactiveStatusLabel(d.status)}`;
  const head = `${label}${d.chat_name ? ` · ${d.chat_name}` : ""}`;
  if (d.status === "preview") return `试运行结果（未发送）\n${head}\n\n${d.text || "（模型没给出内容）"}`;
  if (d.status === "sent") return `已发送\n${head}\n\n${d.text || ""}`;
  if (d.status === "skipped") return `这次没发\n${head}\n\n${d.error || d.text || "AI 判断这会儿不该开口，或没过闸门。"}`;
  if (d.status === "busy") return `上一个还在跑，这次没排队\n${head}`;
  return `失败\n${head}\n\n${d.error || "发送失败"}`;
}

async function testProactive(dryRun) {
  if (proactiveData.busy) return;
  const uid = $("#proactiveTestPick").value;
  if (!uid) return toast("先选一个白名单会话", "err");
  if (!dryRun && !confirm("这条会真的发到微信，确定？")) return;
  const out = $("#proactiveTestOut");
  out.hidden = false;
  out.textContent = dryRun ? "生成中（不会发送）…" : "生成并发送中…";
  setProactiveBusy(true);
  try {
    const d = await api("/api/proactive/test", {
      method: "POST",
      body: JSON.stringify({ chat_uid: uid, dry_run: !!dryRun }),
    });
    out.textContent = proactiveResultText(d);
    await loadProactivePage();
  } catch (e) {
    out.textContent = e.message || "试运行失败";
  } finally {
    setProactiveBusy(false);
  }
}

async function runProactive() {
  if (proactiveData.busy) return;
  const uid = $("#proactiveTestPick").value;
  if (!uid) return toast("先选一个白名单会话", "err");
  if (!confirm("这会占用今日额度并真的发送，确定？")) return;
  const out = $("#proactiveTestOut");
  out.hidden = false;
  out.textContent = "生成并发送中…";
  setProactiveBusy(true);
  try {
    const d = await api("/api/proactive/run", {
      method: "POST",
      body: JSON.stringify({ chat_uid: uid }),
    });
    out.textContent = proactiveResultText(d);
    await loadProactivePage();
  } catch (e) {
    out.textContent = e.message || "手动发送失败";
  } finally {
    setProactiveBusy(false);
  }
}

async function loadProactiveEvents() {
  const box = $("#proactiveEvents");
  try {
    const d = await api("/api/proactive/events?limit=50");
    const events = d.events || [];
    if (!events.length) {
      box.innerHTML = '<div class="empty-mini">还没有记录 —— 开启并等到掷骰才会有。</div>';
      return;
    }
    box.innerHTML = `
      <div class="proactive-table-wrap">
        <table class="proactive-table">
          <thead><tr><th>时间</th><th>会话</th><th>触发</th><th>命中线 / 掷骰</th><th>状态</th><th>内容 / 错误</th></tr></thead>
          <tbody>${events.map((e) => `
            <tr>
              <td>${proactiveTime(e.at)}</td>
              <td>${esc(e.chat_name || e.chat_uid || "—")}</td>
              <td>${esc(PROACTIVE_TRIGGER_TEXT[e.trigger] || e.trigger || "—")}</td>
              <td>${Number(e.hit_line) || 0} / ${Math.round((Number(e.roll) || 0) * 10) / 10}</td>
              <td><span class="tag proactive-st proactive-st-${esc(e.status || "")}">${esc(proactiveStatusLabel(e.status))}</span></td>
              <td>${esc(e.message || e.error || "—")}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty-mini">${esc(e.message || "记录加载失败")}</div>`;
  }
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-mood-tab]");
  if (!tab) return;
  moodState.tab = tab.dataset.moodTab;
  $$("#moodTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.moodTab === moodState.tab));
  $$(".mood-pane").forEach((p) => p.classList.toggle("active", p.dataset.moodPane === moodState.tab));
});

// 槽位/绑定按钮走事件委托：行内 HTML 用 data-mood-act + data-slot/data-chat，避免拼接 onclick。
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-mood-act]");
  if (!btn) return;
  const act = btn.dataset.moodAct;
  if (act === "rename") renameMoodSlot(btn.dataset.slot);
  else if (act === "delete") deleteMoodSlot(btn.dataset.slot);
  else if (act === "unbind") unbindMoodChat(btn.dataset.chat);
});

// 白名单移除同样走委托：uid 只放在 data-proactive-remove 里，不拼进 onclick 字符串。
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-proactive-remove]");
  if (!btn) return;
  removeProactiveChat(btn.dataset.proactiveRemove);
});

/* ---------- 桌宠 ---------- */
let petEnabled = false;

async function loadPetPage() {
  try {
    const d = await api("/api/pet/state");
    renderPetPage(d);
  } catch (e) {
    toast(e.message || "桌宠状态读取失败", "err");
  }
}

function renderPetPage(d) {
  const s = d.state || {};
  const c = d.config || {};
  petEnabled = !!s.enabled;
  const tag = $("#petStateTag");
  if (s.running) {
    tag.textContent = "运行中 · " + (s.state || "IDLE");
    tag.classList.add("ok");
  } else {
    tag.classList.remove("ok");
    tag.textContent = s.enabled ? "已开启但未运行" : "未开启";
  }
  $("#btnPetEnable").textContent = s.enabled ? "停用桌宠" : "启用桌宠";
  const blocked = $("#petBlocked");
  if (s.disabledReason) {
    blocked.hidden = false;
    blocked.textContent = "没能启动：" + s.disabledReason
      + "（点上方按钮一键装好；也可以手动跑 python tools/pet_assets.py 与 pip install PySide6-Essentials）";
  } else {
    blocked.hidden = true;
  }
  $("#petScale").value = c.scale ?? 1;
  $("#petBubbleScale").value = c.bubbleScale ?? 1;
  $("#petActivity").value = c.activityLevel || "auto";
  $("#petBubbleMode").value = c.bubbleMode || "always";
  $("#petReducedMotion").checked = !!c.reducedMotion;
  $("#petSound").checked = !!c.soundEnabled;
  $("#petShowChat").checked = c.showChatName !== false;
  const inst = d.install || {};
  const fix = $("#petFixBtn");
  if (fix) {
    fix.hidden = !!s.running;
    fix.textContent = !inst.depsReady ? "一键安装依赖"
      : (!inst.assetsReady ? "一键装配素材" : "一键准备");
  }
}

async function savePetConfig(patch) {
  try {
    const d = await api("/api/pet/config", { method: "POST", body: JSON.stringify(patch) });
    renderPetPage({ state: d.state, config: d.config });
    toast("已保存");
  } catch (e) {
    toast(e.message || "保存失败", "err");
    loadPetPage();
  }
}

async function togglePet() {
  await savePetConfig({ enabled: !petEnabled });
  toast(!petEnabled ? "桌宠已开启" : "桌宠已关闭");
}

async function petPreview(state) {
  try {
    await api("/api/pet/test", { method: "POST", body: JSON.stringify({ state, text: "控制台预览" }) });
    toast("已发到桌宠");
  } catch (e) {
    toast(e.message || "预览失败", "err");
  }
}
/* ---------- 桌宠一键准备（依赖 + 素材），形态同 B 站安装日志 ---------- */
let petInstallTimer = null;
let petInstallOffset = 0;
let petInstallText = "";

const PET_PHASE_LABEL = {
  idle: "空闲", deps: "安装 PySide6-Essentials", assets: "装配角色素材",
  restart: "启用并拉起桌宠", ok: "完成", error: "失败",
};

function appendPetInstallLog(text) {
  if (!text) return;
  const term = $("#petInstallTerm");
  if (!term) return;
  const nearBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 40;
  petInstallText += text;
  if (petInstallText.length > 200000) petInstallText = petInstallText.slice(-200000);
  term.textContent = petInstallText;
  if (nearBottom) term.scrollTop = term.scrollHeight;
}

function setPetInstallStatus(text, type = "") {
  const status = $("#petInstallStatus");
  if (!status) return;
  status.textContent = text;
  status.style.color = type === "err" ? "var(--red)" : type === "ok" ? "var(--green-d)" : "";
}

function finishPetInstall() {
  if (petInstallTimer) {
    clearInterval(petInstallTimer);
    petInstallTimer = null;
  }
  const fix = $("#petFixBtn");
  if (fix) fix.disabled = false;
}

async function pollPetInstallLog() {
  try {
    const r = await api(`/api/pet/install/log?offset=${petInstallOffset}`);
    petInstallOffset = Number(r.offset || petInstallOffset);
    if (r.reset) petInstallText = "";
    appendPetInstallLog(r.text || "");
    setPetInstallStatus("当前阶段：" + (PET_PHASE_LABEL[r.phase] || r.phase || "进行中"));
    if (r.done) {
      if (r.phase === "ok") {
        appendPetInstallLog(String.fromCharCode(10) + "✓ 桌宠已就绪，正在刷新状态" + String.fromCharCode(10));
        setPetInstallStatus("完成", "ok");
        toast("桌宠已就绪", "ok");
      } else if (r.phase === "error") {
        appendPetInstallLog(String.fromCharCode(10) + "✗ " + (r.error || "准备失败") + String.fromCharCode(10));
        setPetInstallStatus("失败", "err");
        toast(r.error || "桌宠准备失败", "err");
      } else {
        setPetInstallStatus("已结束");
      }
      finishPetInstall();
      await loadPetPage();
    }
  } catch (e) {
    appendPetInstallLog(String.fromCharCode(10) + "✗ 读取准备日志失败：" + e.message + String.fromCharCode(10));
    setPetInstallStatus("日志读取失败", "err");
    finishPetInstall();
  }
}

async function petInstall() {
  const mask = $("#petInstallMask");
  const term = $("#petInstallTerm");
  if (!mask || !term) return;
  mask.hidden = false;
  term.textContent = "";
  petInstallText = "";
  petInstallOffset = 0;
  finishPetInstall();
  setPetInstallStatus("正在启动准备任务…");
  const fix = $("#petFixBtn");
  if (fix) fix.disabled = true;
  appendPetInstallLog("$ 桌宠一键准备 · " + new Date().toLocaleString() + String.fromCharCode(10, 10));
  try {
    const started = await api("/api/pet/install", { method: "POST" });
    petInstallOffset = Number(started.log_offset || 0);
    if (!started.ok && started.error) appendPetInstallLog("⚠ " + started.error + String.fromCharCode(10));
    petInstallTimer = setInterval(pollPetInstallLog, 800);
  } catch (e) {
    const hint = e.status === 404
      ? String.fromCharCode(10) + "（这条接口在当前进程里不存在：控制台多半还是旧版本，重启后按钮才可用）"
      : "";
    appendPetInstallLog("✗ 准备任务启动失败：" + e.message + hint + String.fromCharCode(10));
    setPetInstallStatus("启动失败", "err");
    finishPetInstall();
  }
}
window.petInstall = petInstall;

function closePetInstallTerminal() {
  finishPetInstall();
  const mask = $("#petInstallMask");
  if (mask) mask.hidden = true;
}
window.closePetInstallTerminal = closePetInstallTerminal;

async function copyPetInstallLog() {
  try {
    await navigator.clipboard.writeText(petInstallText);
    toast("准备日志已复制", "ok");
  } catch (e) {
    toast("复制失败：" + e.message, "err");
  }
}
window.copyPetInstallLog = copyPetInstallLog;
