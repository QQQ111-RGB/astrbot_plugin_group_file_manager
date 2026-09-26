/**
 * 蓝奏云文件管理器 - WebUI 管理页面前端逻辑。
 *
 * 本插件**不索引、不识别、不保存、不发送任何群文件**：列表里的每一行都直接
 * 来自你**自己蓝奏云网盘**的只读文件清单，唯一动作是「获取链接」——取该文件
 * 的永久分享链接（含提取码）。
 *
 * 与后端通信全部通过 AstrBot 的 bridge（window.AstrBotPluginPage）完成：
 *   - bridge.apiGet(endpoint, params)   -> GET  {PLUGIN_NAME}/xxx
 *   - bridge.apiPost(endpoint, body)    -> POST {PLUGIN_NAME}/xxx
 *
 * 返回值规则（见官方「插件 Pages」文档）：
 *   - `{status:"ok", data:...}` -> resolve 为 data
 *   - 普通 JSON                 -> resolve 为完整对象
 *   - `{status:"error", ...}` 或 HTTP 失败 -> reject 为 Error
 *
 * 安全：所有来自后端/用户的数据都通过 textContent / createElement 写入 DOM，
 * 不使用 innerHTML 拼接，避免文件名中的特殊字符造成 XSS。
 */

const bridge = window.AstrBotPluginPage;

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const state = {
  files: [],
  total: 0,
  keyword: "",
  loading: false,
};

// ---------------------------------------------------------------------------
// DOM 快捷方式
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

const el = {
  title: $("page-title"),
  heroSub: $("hero-sub"),
  statFiles: $("stat-files"),
  statFolder: $("stat-folder"),
  statCache: $("stat-cache"),
  statStatus: $("stat-status"),
  inputKeyword: $("input-keyword"),
  btnSearch: $("btn-search"),
  btnRefresh: $("btn-refresh"),
  btnLzRefresh: $("btn-lzrefresh"),
  tbody: $("file-tbody"),
  footNote: $("foot-note"),
  dialogLink: $("dialog-link"),
  linkTitle: $("link-title"),
  linkDesc: $("link-desc"),
  linkList: $("link-list"),
  toastWrap: $("toast-wrap"),
};

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast toast-${kind}`;
  node.textContent = message;
  el.toastWrap.appendChild(node);
  setTimeout(() => {
    node.classList.add("toast-out");
    setTimeout(() => node.remove(), 300);
  }, kind === "error" ? 5200 : 3000);
}

function errorMessage(error) {
  if (!error) return "未知错误";
  return error.message || String(error);
}

/** 把 Unix 秒时间戳格式化为「MM-DD HH:mm」，无效值返回「—」。 */
function formatTs(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const date = new Date(value * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

/** 生成一个文本单元格。 */
function td(text, className, title) {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = text;
  if (title) cell.title = title;
  return cell;
}

function button(label, className, onClick) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = `btn btn-sm ${className || ""}`.trim();
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
async function loadOverview() {
  try {
    const data = await bridge.apiGet("overview");
    el.statFiles.textContent = String(data.files ?? "–");
    el.statFolder.textContent = String(data.folder ?? "–");
    el.statCache.textContent =
      data.cached_at > 0 ? formatTs(data.cached_at) : "未加载";

    const configured = Boolean(data.configured);
    el.statStatus.textContent = configured ? "已就绪" : "未配置";
    el.statStatus.title = configured
      ? `每 ${data.cache_minutes ?? 30} 分钟自动刷新一次清单`
      : "请在插件配置中打开 lanzou_enabled 并填写蓝奏云登录信息";

    let sub;
    if (!data.enabled) {
      sub = "蓝奏云链接提取未开启（可在插件配置中打开 lanzou_enabled）";
    } else if (!configured) {
      sub = "蓝奏云已开启但未填写登录信息（推荐用 Cookie：ylogin + phpdisk_info）";
    } else {
      const scope = data.recursive ? "含子目录" : "仅第一层";
      sub = `只读模式 · 匹配「${data.folder}」（${scope}）· 每 ${
        data.cache_minutes ?? 30
      } 分钟刷新清单 · 不下载 / 不转存`;
    }
    el.heroSub.textContent = sub;
    return data;
  } catch (error) {
    el.heroSub.textContent = `加载统计失败：${errorMessage(error)}`;
    throw error;
  }
}

async function loadFiles() {
  if (state.loading) return;
  state.loading = true;
  renderTableLoading();
  try {
    const params = {};
    if (state.keyword) params.keyword = state.keyword;

    const data = await bridge.apiGet("files", params);
    state.files = Array.isArray(data.items) ? data.items : [];
    state.total = Number(data.total || 0);
    renderTable();
  } catch (error) {
    state.files = [];
    state.total = 0;
    renderTableError(errorMessage(error));
    toast(`加载文件列表失败：${errorMessage(error)}`, "error");
  } finally {
    state.loading = false;
  }
}

async function refreshAll(options = {}) {
  try {
    await loadOverview();
    await loadFiles();
    if (!options.silent) toast("已刷新", "ok");
  } catch (error) {
    toast(`刷新失败：${errorMessage(error)}`, "error");
  }
}

/** 强制后端重新读取蓝奏云文件清单，再刷新页面数据。 */
async function refreshCloudList() {
  el.btnLzRefresh.disabled = true;
  try {
    const data = await bridge.apiPost("files/refresh", {});
    toast(`网盘清单已刷新：共 ${data.files ?? 0} 个文件`, "ok");
    await refreshAll({ silent: true });
  } catch (error) {
    toast(`刷新网盘清单失败：${errorMessage(error)}`, "error");
  } finally {
    el.btnLzRefresh.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------------------
/** 表格列数（与 index.html 的 thead 保持一致）。 */
const COLUMN_COUNT = 5;

function renderTableLoading() {
  el.tbody.replaceChildren();
  const row = document.createElement("tr");
  const cell = td("正在加载…", "empty");
  cell.colSpan = COLUMN_COUNT;
  row.appendChild(cell);
  el.tbody.appendChild(row);
}

function renderTableError(message) {
  el.tbody.replaceChildren();
  const row = document.createElement("tr");
  const cell = td(message, "empty");
  cell.colSpan = COLUMN_COUNT;
  row.appendChild(cell);
  el.tbody.appendChild(row);
}

function renderTable() {
  el.tbody.replaceChildren();

  if (state.files.length === 0) {
    const row = document.createElement("tr");
    const cell = td(
      state.keyword
        ? `网盘里没有找到包含「${state.keyword}」的文件。`
        : "网盘目录里还没有文件。把文件上传到蓝奏云后，点「刷新网盘清单」即可看到。",
      "empty",
    );
    cell.colSpan = COLUMN_COUNT;
    row.appendChild(cell);
    el.tbody.appendChild(row);
  } else {
    for (const file of state.files) {
      el.tbody.appendChild(renderRow(file));
    }
  }

  const shown = state.files.length;
  el.footNote.textContent =
    state.total > shown
      ? `共 ${state.total} 个匹配文件，当前显示前 ${shown} 个。`
      : `共 ${shown} 个文件。`;
}

function renderRow(file) {
  const row = document.createElement("tr");

  // 文件名 + 文件ID
  const nameCell = document.createElement("td");
  nameCell.className = "col-name";
  const nameText = document.createElement("span");
  nameText.className = "file-name";
  nameText.textContent = file.name;
  nameText.title = file.name;
  nameCell.appendChild(nameText);
  const idLine = document.createElement("span");
  idLine.className = "file-id";
  idLine.textContent = `文件ID：${file.id}`;
  nameCell.appendChild(idLine);
  row.appendChild(nameCell);

  row.appendChild(td(file.size_text || "未知", "col-size"));
  row.appendChild(td(file.time_text || "—", "col-time"));

  // 状态（永远是永久分享链接）
  const statusCell = document.createElement("td");
  statusCell.className = "col-status";
  const badge = document.createElement("span");
  badge.className = "badge badge-netdisk";
  badge.textContent = "永久链接";
  badge.title = "蓝奏云网盘文件，可取永久分享链接（含提取码）";
  statusCell.appendChild(badge);
  row.appendChild(statusCell);

  // 操作：只有「获取链接」
  const opsCell = document.createElement("td");
  opsCell.className = "col-ops";
  opsCell.appendChild(button("获取链接", "btn-primary", () => openLinkDialog(file)));
  row.appendChild(opsCell);

  return row;
}

// ---------------------------------------------------------------------------
// 获取链接（永久分享链接）
// ---------------------------------------------------------------------------
/** 复制文本到剪贴板：优先 Clipboard API，失败回退 execCommand。 */
async function copyToClipboard(text) {
  const value = String(text || "");
  if (!value) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (error) {
    console.warn("clipboard API failed:", error);
  }
  try {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch (error) {
    console.warn("execCommand copy failed:", error);
    return false;
  }
}

/** 生成一行「标签 + 只读值 + 复制按钮」。 */
function linkField(label, value, options = {}) {
  const wrap = document.createElement("div");
  wrap.className = "link-row";

  const labelNode = document.createElement("span");
  labelNode.className = "link-label";
  labelNode.textContent = label;
  wrap.appendChild(labelNode);

  const line = document.createElement("div");
  line.className = "link-line";

  const input = document.createElement("input");
  input.className = "link-value";
  input.type = "text";
  input.readOnly = true;
  input.value = value;
  input.title = value;
  line.appendChild(input);

  if (options.copyable !== false && value) {
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn btn-sm";
    copyBtn.textContent = "复制";
    copyBtn.addEventListener("click", async () => {
      const ok = await copyToClipboard(value);
      toast(
        ok ? `已复制${options.copyLabel || label}` : "复制失败，请手动选中文本复制",
        ok ? "ok" : "error",
      );
    });
    line.appendChild(copyBtn);
  }

  wrap.appendChild(line);
  return wrap;
}

function openLinkDialog(file) {
  el.linkTitle.textContent = `文件链接 · ${file.name}`;
  el.linkDesc.textContent = "正在获取链接…";
  el.linkList.replaceChildren();
  el.dialogLink.showModal();
  loadFileLink(file);
}

async function loadFileLink(file) {
  try {
    const data = await bridge.apiGet("files/link", { file_id: file.id });
    el.linkDesc.textContent = `${data.size_text || "—"} · 存储位置：蓝奏网盘`;

    const fields = [
      linkField("链接", data.link, { copyLabel: "链接" }),
      linkField("提取码", data.pwd || "无", { copyLabel: "提取码" }),
    ];
    // 蓝奏云只有「分享链接 + 提取码」：短链接 / 口令是百度网盘那套的概念，
    // 后端只在确实拿到对应字段时才会返回，这里也只在那时才渲染，
    // 避免把同一个长链接重复显示成「短链接」。
    if (data.short_url) {
      fields.push(linkField("短链接", data.short_url, { copyLabel: "短链接" }));
    }
    if (data.command) {
      fields.push(linkField("口令", data.command, { copyLabel: "口令" }));
    }
    fields.push(linkField("有效期", "永久", { copyable: false }));
    el.linkList.replaceChildren(...fields);
  } catch (error) {
    el.linkDesc.textContent = `获取失败：${errorMessage(error)}`;
  }
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------
function bindEvents() {
  el.btnRefresh.addEventListener("click", () => refreshAll());
  el.btnLzRefresh.addEventListener("click", refreshCloudList);

  el.btnSearch.addEventListener("click", () => {
    state.keyword = el.inputKeyword.value.trim();
    loadFiles();
  });

  el.inputKeyword.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      el.btnSearch.click();
    }
  });

  // 搜索输入做防抖，体验更顺滑
  let debounce = null;
  el.inputKeyword.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.keyword = el.inputKeyword.value.trim();
      loadFiles();
    }, 320);
  });

  // 链接对话框里的只读输入框按回车会触发表单提交并关闭窗口，这里拦掉
  el.linkList.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------
async function boot() {
  bindEvents();

  // 页面若在 bridge 注入前执行，window.AstrBotPluginPage 可能尚未就绪
  if (!bridge) {
    el.heroSub.textContent =
      "未检测到 AstrBot Bridge。请通过 AstrBot WebUI 的插件详情页打开本页面。";
    renderTableError("Bridge 不可用");
    return;
  }

  try {
    const context = await bridge.ready();
    if (context && context.pageTitle) {
      el.title.textContent = context.pageTitle;
      document.title = context.pageTitle;
    }
  } catch (error) {
    // ready() 失败不阻塞后续 API 调用
    console.warn("bridge.ready() failed:", error);
  }

  await refreshAll({ silent: true });
}

boot();
