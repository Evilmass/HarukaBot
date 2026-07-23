const state = {
  items: [],
  bots: [],
  editing: null,
  loading: false,
  optionsLoading: false,
  mutationLoading: false,
  exporting: false,
  subscriptionRequestId: 0,
  subscriptionController: null,
  optionsRequestId: 0,
  optionsController: null,
};

const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const appView = $("#app-view");
const modal = $("#subscription-modal");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getCookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const part = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return part ? decodeURIComponent(part.slice(prefix.length)) : "";
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", getCookie("haruka_web_csrf"));
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    method,
    headers,
  });
  let payload = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("json")) {
    payload = await response.json();
  }
  if (!response.ok) {
    if (response.status === 401 && !path.endsWith("/auth/login")) {
      showLogin();
    }
    const error = new Error(payload?.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showLogin(message = "") {
  state.subscriptionRequestId += 1;
  state.optionsRequestId += 1;
  state.subscriptionController?.abort();
  state.optionsController?.abort();
  state.subscriptionController = null;
  state.optionsController = null;
  state.loading = false;
  state.optionsLoading = false;
  appView.classList.add("hidden");
  loginView.classList.remove("hidden");
  $("#login-error").textContent = message;
  updateBusyState();
  setTimeout(() => $("#login-password").focus(), 0);
}

function showApp() {
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.textContent = message;
  $("#toast-region").appendChild(node);
  setTimeout(() => node.remove(), 3300);
}

function setLoading(loading) {
  state.loading = loading;
  $("#loading-state").classList.toggle("hidden", !loading);
  updateBusyState();
}

function updateBusyState() {
  $("#refresh-button").disabled =
    state.loading || state.optionsLoading || state.mutationLoading;
  $("#add-button").disabled = state.mutationLoading;
  $("#export-toggle").disabled = state.exporting;
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.disabled = state.mutationLoading;
  });
}

function setMutationLoading(loading) {
  state.mutationLoading = loading;
  updateBusyState();
}

function targetTypeName(type) {
  return { group: "QQ群", private: "私聊", guild: "频道" }[type] || type;
}

function statusBadge(status) {
  const text = { live: "直播中", offline: "未开播", unknown: "状态未知" }[status] || status;
  return `<span class="status-badge ${escapeHtml(status)}">${escapeHtml(text)}</span>`;
}

function featureBadges(item) {
  return `
    <div class="feature-list">
      <span class="feature-badge ${item.live ? "on" : ""}">直播</span>
      <span class="feature-badge ${item.dynamic ? "on" : ""}">动态</span>
      <span class="feature-badge ${item.at ? "on" : ""}">@全体</span>
    </div>`;
}

function targetLabel(item) {
  if (item.target_type === "guild" && item.guild_id) {
    return `${item.guild_id} / ${item.channel_id}`;
  }
  return item.target_name || item.target_id;
}

function renderTable(items) {
  $("#subscription-table").innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>
          <div class="streamer-cell">
            <span class="avatar">${escapeHtml((item.name || "H").slice(0, 1))}</span>
            <div>
              <strong>${escapeHtml(item.name || `UID ${item.uid}`)}</strong>
              <span>UID ${escapeHtml(item.uid)} · 房间 ${escapeHtml(item.room_id || "未知")}</span>
            </div>
          </div>
        </td>
        <td>${statusBadge(item.live_status)}</td>
        <td>
          <div class="target-cell">
            <strong>${escapeHtml(targetTypeName(item.target_type))} · ${escapeHtml(targetLabel(item))}</strong>
            <span>${escapeHtml(item.target_name && item.target_type === "group" ? item.target_name : `目标 ID ${item.target_id}`)}</span>
          </div>
        </td>
        <td>
          <div class="bot-cell">
            <span class="online-dot ${item.bot_online ? "online" : ""}"></span>
            <div>
              <strong>${escapeHtml(item.bot_name || item.bot_id)}</strong>
              <span>${escapeHtml(item.bot_name ? `QQ ${item.bot_id}` : item.bot_online ? "在线" : "离线或未连接")}</span>
            </div>
          </div>
        </td>
        <td>${featureBadges(item)}</td>
        <td>
          <div class="row-actions">
            ${
              item.room_id
                ? `<a class="row-button" href="https://live.bilibili.com/${encodeURIComponent(item.room_id)}" target="_blank" rel="noopener">直播间</a>`
                : ""
            }
            <button class="row-button" data-action="edit" data-id="${item.id}" type="button">编辑</button>
            <button class="row-button danger" data-action="delete" data-id="${item.id}" type="button">删除</button>
          </div>
        </td>
      </tr>`,
    )
    .join("");
}

function renderMobile(items) {
  $("#mobile-list").innerHTML = items
    .map(
      (item) => `
      <article class="mobile-card">
        <div class="mobile-card-header">
          <div class="streamer-cell">
            <span class="avatar">${escapeHtml((item.name || "H").slice(0, 1))}</span>
            <div>
              <strong>${escapeHtml(item.name || `UID ${item.uid}`)}</strong>
              <span>UID ${escapeHtml(item.uid)} · 房间 ${escapeHtml(item.room_id || "未知")}</span>
            </div>
          </div>
          ${statusBadge(item.live_status)}
        </div>
        <div class="mobile-detail">
          <span>${escapeHtml(targetTypeName(item.target_type))} · ${escapeHtml(targetLabel(item))}</span>
          <span>机器人 ${escapeHtml(item.bot_name || item.bot_id)}</span>
        </div>
        <div class="mobile-detail">${featureBadges(item)}</div>
        <div class="mobile-card-actions">
          <button class="row-button" data-action="edit" data-id="${item.id}" type="button">编辑</button>
          <button class="row-button danger" data-action="delete" data-id="${item.id}" type="button">删除</button>
        </div>
      </article>`,
    )
    .join("");
}

function render() {
  const items = state.items;
  $("#stat-total").textContent = items.length;
  $("#stat-live").textContent = items.filter((item) => item.live_status === "live").length;
  $("#stat-enabled").textContent = items.filter((item) => item.live).length;
  $("#stat-bots").textContent = state.bots.filter((bot) => bot.online).length;

  const empty = !state.loading && items.length === 0;
  $("#empty-state").classList.toggle("hidden", !empty);
  $("#desktop-table").classList.toggle("hidden", state.loading || empty);
  $("#mobile-list").classList.toggle("hidden", state.loading || empty);
  renderTable(items);
  renderMobile(items);
}

function filtersQuery() {
  const params = new URLSearchParams();
  const q = $("#search-input").value.trim();
  const type = $("#type-filter").value;
  const live = $("#live-filter").value;
  if (q) params.set("q", q);
  if (type) params.set("target_type", type);
  if (live) params.set("live_enabled", live);
  return params.toString();
}

async function loadSubscriptions() {
  const requestId = ++state.subscriptionRequestId;
  state.subscriptionController?.abort();
  const controller = new AbortController();
  state.subscriptionController = controller;
  setLoading(true);
  try {
    const payload = await api(`/haruka/api/subscriptions?${filtersQuery()}`, {
      signal: controller.signal,
    });
    if (requestId !== state.subscriptionRequestId) return false;
    state.items = payload.items;
    render();
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    if (error.status !== 401) toast(error.message, "error");
    return false;
  } finally {
    if (requestId === state.subscriptionRequestId) {
      state.subscriptionController = null;
      setLoading(false);
      render();
    }
  }
}

async function loadOptions() {
  const requestId = ++state.optionsRequestId;
  state.optionsController?.abort();
  const controller = new AbortController();
  state.optionsController = controller;
  state.optionsLoading = true;
  updateBusyState();
  try {
    const payload = await api("/haruka/api/options", {
      signal: controller.signal,
    });
    if (requestId !== state.optionsRequestId) return false;
    state.bots = payload.bots;
    $("#bot-options").innerHTML = state.bots
      .map(
        (bot) =>
          `<option value="${bot.id}">${escapeHtml(bot.name || `机器人 ${bot.id}`)}${bot.online ? "" : "（离线）"}</option>`,
      )
      .join("");
    renderGroupOptions();
    render();
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    if (error.status !== 401) toast(`机器人列表加载失败：${error.message}`, "error");
    return false;
  } finally {
    if (requestId === state.optionsRequestId) {
      state.optionsController = null;
      state.optionsLoading = false;
      updateBusyState();
    }
  }
}

async function refreshData(successMessage = "") {
  const [subscriptionsLoaded, optionsLoaded] = await Promise.all([
    loadSubscriptions(),
    loadOptions(),
  ]);
  const succeeded = subscriptionsLoaded && optionsLoaded;
  if (successMessage) {
    toast(
      succeeded ? successMessage : "操作已完成，但部分数据刷新失败",
      succeeded ? "success" : "error",
    );
  }
  return succeeded;
}

function renderGroupOptions() {
  const botId = Number($("#bot-input").value);
  const bot = state.bots.find((item) => item.id === botId);
  const groups = bot?.groups || [];
  $("#group-options").innerHTML = groups
    .map((group) => `<option value="${group.id}">${escapeHtml(group.name || `群 ${group.id}`)}</option>`)
    .join("");
}

function openCreateModal() {
  state.editing = null;
  $("#modal-title").textContent = "新增直播监控";
  $("#subscription-id").value = "";
  $("#room-field").classList.remove("hidden");
  $("#room-input").required = true;
  $("#room-input").value = "";
  $("#editing-streamer").classList.add("hidden");
  $("#immutable-target-note").classList.add("hidden");
  $("#bot-input").disabled = false;
  $("#group-input").disabled = false;
  $("#bot-input").value = state.bots.find((bot) => bot.online)?.id || "";
  $("#group-input").value = "";
  $("#live-switch").checked = true;
  $("#dynamic-switch").checked = false;
  $("#at-switch").checked = false;
  $("#subscription-error").textContent = "";
  renderGroupOptions();
  modal.classList.remove("hidden");
  setTimeout(() => $("#room-input").focus(), 0);
}

function openEditModal(item) {
  state.editing = item;
  $("#modal-title").textContent = "编辑直播订阅";
  $("#subscription-id").value = item.id;
  $("#room-field").classList.add("hidden");
  $("#room-input").required = false;
  $("#editing-streamer").classList.remove("hidden");
  $("#editing-avatar").textContent = (item.name || "H").slice(0, 1);
  $("#editing-name").textContent = item.name || `UID ${item.uid}`;
  $("#editing-meta").textContent = `UID ${item.uid} · 直播间 ${item.room_id || "未知"}`;
  $("#bot-input").value = item.bot_id;
  $("#group-input").value = item.target_id;
  const targetMutable = item.target_type === "group";
  $("#group-input").disabled = !targetMutable;
  $("#immutable-target-note").classList.toggle("hidden", targetMutable);
  $("#live-switch").checked = item.live;
  $("#dynamic-switch").checked = item.dynamic;
  $("#at-switch").checked = item.at;
  $("#subscription-error").textContent = "";
  renderGroupOptions();
  modal.classList.remove("hidden");
}

function closeModal() {
  modal.classList.add("hidden");
  state.editing = null;
}

async function saveSubscription(event) {
  event.preventDefault();
  if (state.mutationLoading) return;
  const saveButton = $("#save-button");
  const errorNode = $("#subscription-error");
  const wasEditing = Boolean(state.editing);
  errorNode.textContent = "";
  const botId = Number($("#bot-input").value);
  const targetId = Number($("#group-input").value);
  if (!botId || !targetId) {
    errorNode.textContent = "请输入有效的机器人 QQ 和通知目标 ID";
    return;
  }
  const payload = {
    bot_id: botId,
    live: $("#live-switch").checked,
    dynamic: $("#dynamic-switch").checked,
    at: $("#at-switch").checked,
  };
  let path = "/haruka/api/subscriptions";
  let method = "POST";
  if (state.editing) {
    path += `/${state.editing.id}`;
    method = "PATCH";
    if (state.editing.target_type === "group") payload.target_id = targetId;
  } else {
    payload.room = $("#room-input").value.trim();
    payload.target_id = targetId;
    if (!payload.room) {
      errorNode.textContent = "请输入直播间号或链接";
      return;
    }
  }

  saveButton.disabled = true;
  saveButton.textContent = state.editing ? "正在保存…" : "正在解析并添加…";
  setMutationLoading(true);
  try {
    await api(path, { method, body: JSON.stringify(payload) });
    closeModal();
    await refreshData(wasEditing ? "订阅已更新" : "直播间已加入监控");
  } catch (error) {
    errorNode.textContent = error.message;
  } finally {
    setMutationLoading(false);
    saveButton.disabled = false;
    saveButton.textContent = "保存订阅";
  }
}

async function deleteSubscription(item) {
  if (state.mutationLoading) return;
  const label = item.name || `UID ${item.uid}`;
  if (!window.confirm(`确定删除 ${label} 到 ${targetTypeName(item.target_type)} ${targetLabel(item)} 的订阅吗？`)) {
    return;
  }
  setMutationLoading(true);
  try {
    await api(`/haruka/api/subscriptions/${item.id}`, { method: "DELETE" });
    await refreshData("订阅已删除");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setMutationLoading(false);
  }
}

async function handleRowAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const item = state.items.find((row) => row.id === Number(button.dataset.id));
  if (!item) return;
  if (button.dataset.action === "edit") openEditModal(item);
  if (button.dataset.action === "delete") await deleteSubscription(item);
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

async function initialize() {
  try {
    const session = await api("/haruka/api/auth/session");
    if (!session.authenticated) {
      showLogin();
      return;
    }
    showApp();
    await refreshData();
  } catch (error) {
    showLogin(error.status === 503 ? error.message : "");
  }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  $("#login-error").textContent = "";
  button.disabled = true;
  try {
    await api("/haruka/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: $("#login-password").value }),
    });
    $("#login-password").value = "";
    showApp();
    await refreshData();
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#logout-button").addEventListener("click", async () => {
  try {
    await api("/haruka/api/auth/logout", { method: "POST" });
  } finally {
    state.items = [];
    state.bots = [];
    showLogin();
  }
});

$("#add-button").addEventListener("click", openCreateModal);
$("#modal-close").addEventListener("click", closeModal);
$("#modal-cancel").addEventListener("click", closeModal);
$("#subscription-form").addEventListener("submit", saveSubscription);
$("#bot-input").addEventListener("input", renderGroupOptions);
$("#subscription-table").addEventListener("click", handleRowAction);
$("#mobile-list").addEventListener("click", handleRowAction);
$("#refresh-button").addEventListener("click", async () => {
  await refreshData("数据已刷新");
});
$("#search-input").addEventListener("input", debounce(loadSubscriptions, 280));
$("#type-filter").addEventListener("change", loadSubscriptions);
$("#live-filter").addEventListener("change", loadSubscriptions);
$("#export-toggle").addEventListener("click", (event) => {
  event.stopPropagation();
  $("#export-popover").classList.toggle("hidden");
});
$("#export-popover").addEventListener("click", async (event) => {
  const link = event.target.closest("[data-export]");
  if (!link || state.exporting) return;
  event.preventDefault();
  event.stopPropagation();
  $("#export-popover").classList.add("hidden");
  state.exporting = true;
  updateBusyState();
  try {
    const response = await fetch(link.href, { credentials: "same-origin" });
    if (!response.ok) {
      let detail = "";
      if ((response.headers.get("content-type") || "").includes("json")) {
        detail = (await response.json()).detail || "";
      }
      if (response.status === 401) showLogin();
      throw new Error(detail || `导出失败（${response.status}）`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const fallback = `haruka-subscriptions.${link.dataset.export}`;
    const download = document.createElement("a");
    download.href = URL.createObjectURL(blob);
    download.download = match?.[1] || fallback;
    document.body.appendChild(download);
    download.click();
    download.remove();
    const objectUrl = download.href;
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    toast(`${link.dataset.export.toUpperCase()} 导出已开始`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.exporting = false;
    updateBusyState();
  }
});
document.addEventListener("click", () => $("#export-popover").classList.add("hidden"));
modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("#export-popover").classList.add("hidden");
    if (!modal.classList.contains("hidden")) closeModal();
  }
});

initialize();
