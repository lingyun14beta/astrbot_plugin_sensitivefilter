const bridge = window.AstrBotPluginPage;

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const SWITCH_LABELS = {
  enabled: "插件总开关",
  local_enabled: "本地词库检测",
  api_enabled: "外部接口检测",
  llm_enabled: "AI 语义检测",
  image_enabled: "图片检测",
  recall_enabled: "命中后撤回",
  warn_enabled: "命中后警告",
  notify_enabled: "命中后通知",
  mute_enabled: "命中后自动禁言",
  case_insensitive: "忽略大小写",
  fuzzy_match: "模糊匹配（防拆字）",
};

const STAT_LABELS = [
  ["words", "全局敏感词"],
  ["groups", "分群配置"],
  ["whitelist", "群白名单"],
  ["blacklist", "群黑名单"],
  ["user_whitelist", "用户白名单"],
  ["violation_records", "违规计数（内存）"],
];

const LIST_NAMES = {
  whitelist: "群白名单",
  blacklist: "群黑名单",
  user: "用户白名单",
};

const ICONS = {
  close:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  check:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  alert:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
  warn:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  chevron:
    '<svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
};

let currentTab = "overview";
let allWords = [];
let wordFilter = "";

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------

function el(id) {
  return document.getElementById(id);
}

function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

function toast(message, type = "success") {
  const stack = el("toast-stack");
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.innerHTML = `${type === "success" ? ICONS.check : ICONS.alert}<span></span>`;
  node.querySelector("span").textContent = message;
  stack.appendChild(node);
  setTimeout(() => {
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  }, 2800);
}

async function callApi(fn) {
  try {
    return await fn();
  } catch (error) {
    toast(error.message || String(error), "error");
    throw error;
  }
}

function confirmDialog({ title = "确认操作", message, confirmText = "确认删除" }) {
  return new Promise((resolve) => {
    const modal = el("modal");
    el("modal-title").textContent = title;
    el("modal-message").textContent = message;
    el("modal-confirm").textContent = confirmText;
    modal.classList.remove("hidden");

    const done = (result) => {
      modal.classList.add("hidden");
      el("modal-confirm").removeEventListener("click", onConfirm);
      el("modal-cancel").removeEventListener("click", onCancel);
      modal.removeEventListener("click", onOverlay);
      document.removeEventListener("keydown", onKeydown);
      resolve(result);
    };
    const onConfirm = () => done(true);
    const onCancel = () => done(false);
    const onOverlay = (e) => {
      if (e.target === modal) done(false);
    };
    const onKeydown = (e) => {
      if (e.key === "Escape") done(false);
    };
    el("modal-confirm").addEventListener("click", onConfirm);
    el("modal-cancel").addEventListener("click", onCancel);
    modal.addEventListener("click", onOverlay);
    document.addEventListener("keydown", onKeydown);
  });
}

function makeChip(text, { highlight = "", onRemove } = {}) {
  const chip = document.createElement("span");
  chip.className = "chip";
  const label = document.createElement("span");
  if (highlight) {
    const lower = text.toLowerCase();
    const idx = lower.indexOf(highlight.toLowerCase());
    if (idx >= 0) {
      label.innerHTML =
        escapeText(text.slice(0, idx)) +
        "<mark>" +
        escapeText(text.slice(idx, idx + highlight.length)) +
        "</mark>" +
        escapeText(text.slice(idx + highlight.length));
    } else {
      label.textContent = text;
    }
  } else {
    label.textContent = text;
  }
  chip.appendChild(label);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.title = "删除";
  btn.innerHTML = ICONS.close;
  btn.addEventListener("click", onRemove);
  chip.appendChild(btn);
  return chip;
}

// ---------------------------------------------------------------------------
// Tab 切换
// ---------------------------------------------------------------------------

const LOADERS = {
  overview: loadOverview,
  words: loadWords,
  groups: loadGroups,
  lists: loadLists,
  test: () => {},
};

function switchTab(tab) {
  if (tab === currentTab) return;
  currentTab = tab;
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  document
    .querySelectorAll(".panel")
    .forEach((p) => p.classList.toggle("active", p.id === `panel-${tab}`));
  LOADERS[tab]();
}

function refreshCurrent() {
  LOADERS[currentTab]();
}

// ---------------------------------------------------------------------------
// 总览
// ---------------------------------------------------------------------------

async function loadOverview() {
  const data = await callApi(() => bridge.apiGet("page/overview"));

  const stats = el("overview-stats");
  stats.innerHTML = "";
  for (const [key, label] of STAT_LABELS) {
    const div = document.createElement("div");
    div.className = "stat";
    div.innerHTML =
      `<div class="num">${escapeText(data.counts[key] ?? 0)}</div>` +
      `<div class="label">${escapeText(label)}</div>`;
    stats.appendChild(div);
  }

  const switches = el("overview-switches");
  switches.innerHTML = "";
  for (const [key, value] of Object.entries(data.global)) {
    const row = document.createElement("div");
    row.className = "kv";
    row.innerHTML =
      `<span class="k">${escapeText(SWITCH_LABELS[key] || key)}</span>` +
      `<span class="pill ${value ? "on" : "off"}">${value ? "开启" : "关闭"}</span>`;
    switches.appendChild(row);
  }
  const access = document.createElement("div");
  access.className = "kv";
  access.innerHTML =
    `<span class="k">访问控制</span><span class="v">` +
    `白名单 ${data.access.whitelist_enabled ? "开" : "关"} · ` +
    `黑名单 ${data.access.blacklist_enabled ? "开" : "关"} · ` +
    `用户白名单 ${data.access.user_whitelist_enabled ? "开" : "关"}</span>`;
  switches.appendChild(access);

  const batch = el("overview-batch");
  batch.innerHTML = "";
  const queues = Object.entries(data.batch.queues || {});
  const rows = [
    ["批量审核", data.batch.enabled, "pill"],
    ["批量大小", `${data.batch.size} 条`],
    ["兜底等待", `${data.batch.max_wait_minutes} 分钟`],
    [
      "等待中的队列",
      queues.length
        ? queues.map(([umo, n]) => `${umo}（${n} 条）`).join("；")
        : "无",
    ],
  ];
  for (const [label, value, kind] of rows) {
    const row = document.createElement("div");
    row.className = "kv";
    if (kind === "pill") {
      row.innerHTML =
        `<span class="k">${escapeText(label)}</span>` +
        `<span class="pill ${value ? "on" : "off"}">${value ? "已开启" : "已关闭"}</span>`;
    } else {
      row.innerHTML =
        `<span class="k">${escapeText(label)}</span>` +
        `<span class="v">${escapeText(value)}</span>`;
    }
    batch.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// 全局词库
// ---------------------------------------------------------------------------

function renderWords() {
  const list = el("words-list");
  const empty = el("words-empty");
  list.innerHTML = "";
  const filter = wordFilter.trim();
  const shown = filter
    ? allWords.filter((w) => w.toLowerCase().includes(filter.toLowerCase()))
    : allWords;

  el("words-count").textContent = filter
    ? `${shown.length} / ${allWords.length}`
    : `${allWords.length} 个词`;
  el("words-count").className = "pill accent";

  empty.classList.toggle("hidden", shown.length > 0);
  empty.textContent = allWords.length
    ? "没有匹配的词"
    : "词库为空，先在上方添加敏感词";

  for (const word of shown) {
    list.appendChild(
      makeChip(word, {
        highlight: filter,
        onRemove: async () => {
          const res = await callApi(() =>
            bridge.apiPost("page/words/delete", { word }),
          );
          toast(`已删除「${word}」，剩余 ${res.total} 个`);
          loadWords();
        },
      }),
    );
  }
}

async function loadWords() {
  const data = await callApi(() => bridge.apiGet("page/words"));
  allWords = data.words || [];
  renderWords();
}

async function addWords(words) {
  if (!words.length) return;
  const res = await callApi(() => bridge.apiPost("page/words/add", { words }));
  let msg = `已添加 ${res.added.length} 个词，当前共 ${res.total} 个`;
  if (res.skipped.length) msg += `（${res.skipped.length} 个已存在被跳过）`;
  toast(msg);
  loadWords();
}

// ---------------------------------------------------------------------------
// 分群配置
// ---------------------------------------------------------------------------

async function loadGroups() {
  const data = await callApi(() => bridge.apiGet("page/groups"));
  const container = el("groups-list");
  container.innerHTML = "";
  el("groups-empty").classList.toggle("hidden", data.groups.length > 0);
  for (const group of data.groups) {
    container.appendChild(buildGroupCard(group, data));
  }
}

function buildGroupCard(group, meta) {
  const card = document.createElement("div");
  card.className = "card group-card";

  const overridden = Object.values(group.settings || {}).filter(
    (v) => v && v !== "跟随全局",
  ).length;
  const extraCount = (group.extra_words || []).length;

  // 头部（点击展开/收起）
  const head = document.createElement("div");
  head.className = "group-head";
  head.innerHTML =
    ICONS.chevron +
    `<span class="umo">${escapeText(group.umo)}</span>` +
    (overridden ? `<span class="pill accent">${overridden} 项覆盖</span>` : "") +
    (extraCount ? `<span class="pill">${extraCount} 专属词</span>` : "");
  card.appendChild(head);

  // 主体
  const body = document.createElement("div");
  body.className = "group-body";
  const inner = document.createElement("div");
  inner.className = "group-body-inner";
  body.appendChild(inner);
  card.appendChild(body);
  head.addEventListener("click", () => card.classList.toggle("open"));

  // 三态开关
  const grid = document.createElement("div");
  grid.className = "switch-grid";
  const selects = {};
  for (const key of meta.switch_keys) {
    const label = document.createElement("label");
    const span = document.createElement("span");
    span.textContent = SWITCH_LABELS[key] || key;
    const select = document.createElement("select");
    for (const opt of meta.tristate_options) {
      const option = document.createElement("option");
      option.value = opt;
      option.textContent = opt;
      select.appendChild(option);
    }
    select.value = group.settings[key] || "跟随全局";
    selects[key] = select;
    label.appendChild(span);
    label.appendChild(select);
    grid.appendChild(label);
  }
  inner.appendChild(grid);

  // 群专属词
  const sectionLabel = document.createElement("p");
  sectionLabel.className = "section-label";
  sectionLabel.textContent = "本群专属敏感词（与全局词库叠加生效）";
  inner.appendChild(sectionLabel);

  const extraWords = [...(group.extra_words || [])];
  const chips = document.createElement("div");
  chips.className = "chip-list";
  const renderChips = () => {
    chips.innerHTML = "";
    if (!extraWords.length) {
      const none = document.createElement("span");
      none.className = "hint";
      none.textContent = "暂无专属词";
      chips.appendChild(none);
      return;
    }
    for (const word of extraWords) {
      chips.appendChild(
        makeChip(word, {
          onRemove: () => {
            extraWords.splice(extraWords.indexOf(word), 1);
            renderChips();
          },
        }),
      );
    }
  };
  renderChips();
  inner.appendChild(chips);

  const addRow = document.createElement("div");
  addRow.className = "row";
  addRow.style.marginTop = "10px";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "输入本群专属词，回车加入";
  const addBtn = document.createElement("button");
  addBtn.className = "btn ghost sm";
  addBtn.type = "button";
  addBtn.textContent = "添加";
  const doAdd = () => {
    const word = input.value.trim();
    if (word && !extraWords.includes(word)) {
      extraWords.push(word);
      renderChips();
    }
    input.value = "";
  };
  addBtn.addEventListener("click", doAdd);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doAdd();
  });
  addRow.appendChild(input);
  addRow.appendChild(addBtn);
  inner.appendChild(addRow);

  // 操作按钮
  const actions = document.createElement("div");
  actions.className = "group-actions";
  const delBtn = document.createElement("button");
  delBtn.className = "btn ghost sm";
  delBtn.type = "button";
  delBtn.textContent = "删除该配置";
  delBtn.style.color = "var(--danger)";
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn primary sm";
  saveBtn.type = "button";
  saveBtn.textContent = "保存";
  actions.appendChild(delBtn);
  actions.appendChild(saveBtn);
  inner.appendChild(actions);

  saveBtn.addEventListener("click", async () => {
    const settings = {};
    for (const [key, select] of Object.entries(selects)) {
      settings[key] = select.value;
    }
    saveBtn.disabled = true;
    try {
      await callApi(() =>
        bridge.apiPost("page/groups/save", {
          umo: group.umo,
          settings,
          extra_words: extraWords,
        }),
      );
      toast(`已保存 ${group.umo} 的分群配置`);
      loadGroups();
    } finally {
      saveBtn.disabled = false;
    }
  });

  delBtn.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "删除分群配置",
      message: `将删除 ${group.umo} 的全部分群覆盖配置和专属词，该群恢复使用全局默认值。`,
    });
    if (!ok) return;
    await callApi(() => bridge.apiPost("page/groups/delete", { umo: group.umo }));
    toast("已删除该分群配置");
    loadGroups();
  });

  return card;
}

async function createGroup() {
  const umo = el("group-new-umo").value.trim();
  if (!umo) {
    toast("请先填写 umo", "error");
    return;
  }
  await callApi(() =>
    bridge.apiPost("page/groups/save", { umo, settings: {}, extra_words: [] }),
  );
  el("group-new-umo").value = "";
  toast("已新增分群配置");
  loadGroups();
}

// ---------------------------------------------------------------------------
// 名单管理
// ---------------------------------------------------------------------------

async function loadLists() {
  const data = await callApi(() => bridge.apiGet("page/lists"));
  for (const name of Object.keys(LIST_NAMES)) {
    const info = data[name] || { enabled: false, items: [] };
    el(`${name}-enabled`).checked = info.enabled;
    renderListItems(name, info.items);
  }
}

function renderListItems(name, items) {
  const container = el(`${name}-items`);
  container.innerHTML = "";
  if (!items.length) {
    const none = document.createElement("span");
    none.className = "hint";
    none.textContent = "列表为空";
    container.appendChild(none);
    return;
  }
  for (const item of items) {
    container.appendChild(
      makeChip(item, {
        onRemove: async () => {
          const res = await callApi(() =>
            bridge.apiPost("page/lists/save", { list: name, remove: item }),
          );
          if (res.removed === false) toast(`${item} 不在列表中`, "error");
          else toast(`已从${LIST_NAMES[name]}移除`);
          renderListItems(name, res.items || []);
        },
      }),
    );
  }
}

async function addListItem(name) {
  const input = el(`${name}-input`);
  const value = input.value.trim();
  if (!value) return;
  const res = await callApi(() =>
    bridge.apiPost("page/lists/save", { list: name, add: value }),
  );
  input.value = "";
  if (res.added === false) toast(`${value} 已在列表中`, "error");
  else toast(`已添加到${LIST_NAMES[name]}`);
  renderListItems(name, res.items || []);
}

async function toggleList(name, enabled) {
  const res = await callApi(() =>
    bridge.apiPost("page/lists/save", { list: name, enabled }),
  );
  toast(`${LIST_NAMES[name]}已${res.enabled ? "开启" : "关闭"}`);
}

// ---------------------------------------------------------------------------
// 命中测试
// ---------------------------------------------------------------------------

async function runTest() {
  const text = el("test-text").value.trim();
  if (!text) {
    toast("请输入要测试的文本", "error");
    return;
  }
  const umo = el("test-umo").value.trim();
  const btn = el("test-run-btn");
  btn.disabled = true;
  try {
    const res = await callApi(() =>
      bridge.apiPost("page/test", { text, umo: umo || undefined }),
    );
    const box = el("test-result");
    box.classList.remove("hidden", "hit", "safe");
    if (res.hit) {
      box.classList.add("hit");
      box.innerHTML =
        `${ICONS.warn}<span>命中敏感词「${escapeText(res.word)}」</span>` +
        `<span class="detail">来源：${escapeText(res.source)}</span>`;
    } else {
      box.classList.add("safe");
      box.innerHTML = `${ICONS.check}<span>未命中本地词库</span>`;
    }
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

function bindEvents() {
  el("tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) switchTab(tab.dataset.tab);
  });
  el("refresh-btn").addEventListener("click", refreshCurrent);

  // 词库
  el("word-add-btn").addEventListener("click", () => {
    const input = el("word-input");
    const word = input.value.trim();
    if (word) {
      input.value = "";
      addWords([word]);
    }
  });
  el("word-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") el("word-add-btn").click();
  });
  el("bulk-toggle").addEventListener("click", () => {
    const area = el("bulk-area");
    const collapsed = area.classList.toggle("collapsed");
    el("bulk-toggle").textContent = collapsed ? "批量添加" : "收起";
  });
  el("word-bulk-add-btn").addEventListener("click", () => {
    const words = el("word-bulk-input")
      .value.split("\n")
      .map((w) => w.trim())
      .filter(Boolean);
    el("word-bulk-input").value = "";
    addWords(words);
  });
  el("word-filter").addEventListener("input", (e) => {
    wordFilter = e.target.value;
    renderWords();
  });

  // 分群配置
  el("group-new-btn").addEventListener("click", createGroup);
  el("group-new-umo").addEventListener("keydown", (e) => {
    if (e.key === "Enter") createGroup();
  });

  // 名单
  for (const name of Object.keys(LIST_NAMES)) {
    document
      .querySelector(`[data-list-add="${name}"]`)
      .addEventListener("click", () => addListItem(name));
    el(`${name}-input`).addEventListener("keydown", (e) => {
      if (e.key === "Enter") addListItem(name);
    });
    el(`${name}-enabled`).addEventListener("change", (e) =>
      toggleList(name, e.target.checked),
    );
  }

  // 测试
  el("test-run-btn").addEventListener("click", runTest);
}

function applyContext() {
  const title = bridge.t("pages.manage.title", "敏感词管理");
  document.title = title;
  el("page-title").textContent = title;
}

await bridge.ready();
applyContext();
bridge.onContext(applyContext);
bindEvents();
loadOverview();
