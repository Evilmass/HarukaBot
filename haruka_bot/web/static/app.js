const state = {
  items: [],
  bots: [],
  editing: null,
  loading: false,
  subscriptionsInitialized: false,
  optionsLoading: false,
  mutationLoading: false,
  exporting: false,
  subscriptionRequestId: 0,
  subscriptionController: null,
  optionsRequestId: 0,
  optionsController: null,
  autoRefreshTimer: null,
  sourceType: "uid",
  page: 1,
  pageSize: 10,
  total: 0,
  summaryTotal: 0,
  liveTotal: 0,
  enabledTotal: 0,
  summaryFilter: "all",
  sortBy: "live_status",
  sortOrder: "asc",
  selectedIds: new Set(),
};

const AUTO_REFRESH_INTERVAL_MS = 15_000;
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
  stopAutoRefresh();
  state.subscriptionRequestId += 1;
  state.optionsRequestId += 1;
  state.subscriptionController?.abort();
  state.optionsController?.abort();
  state.subscriptionController = null;
  state.optionsController = null;
  state.loading = false;
  state.subscriptionsInitialized = false;
  state.optionsLoading = false;
  state.items = [];
  state.total = 0;
  state.summaryTotal = 0;
  state.liveTotal = 0;
  state.enabledTotal = 0;
  state.page = 1;
  state.selectedIds.clear();
  state.summaryFilter = "all";
  appView.classList.add("hidden");
  loginView.classList.remove("hidden");
  $("#login-error").textContent = message;
  updateBusyState();
  setTimeout(() => $("#login-password").focus(), 0);
}

function showApp() {
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
  startAutoRefresh();
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
  const initialLoading = loading && !state.subscriptionsInitialized;
  $("#loading-state").classList.toggle("hidden", !initialLoading);
  updateBusyState();
  renderPagination();
}

function updateBusyState() {
  $("#refresh-button").disabled =
    state.loading || state.optionsLoading || state.mutationLoading;
  $("#add-button").disabled = state.mutationLoading;
  $("#export-toggle").disabled = state.exporting;
  $("#bulk-apply").disabled = state.mutationLoading;
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

function formatDateTime(timestamp) {
  if (!timestamp) return "尚未检测";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours) return `${hours} 小时 ${minutes} 分钟`;
  return `${minutes} 分钟`;
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

function avatarContents(item) {
  const fallback = escapeHtml((item.name || "H").slice(0, 1));
  const image = item.avatar_url
    ? `<img class="avatar-image" src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" decoding="async">`
    : "";
  return `<span class="avatar-fallback">${fallback}</span>${image}`;
}

function pushResult(item) {
  if (!item.last_push_at) return `<span class="push-result muted">尚无推送记录</span>`;
  const stateClass = item.last_push_success ? "success" : "failed";
  const label = item.last_push_success ? "最近推送成功" : `推送失败：${item.last_push_error || "未知原因"}`;
  return `<span class="push-result ${stateClass}" title="${escapeHtml(formatDateTime(item.last_push_at))}">${escapeHtml(label)}</span>`;
}

function renderTable(items) {
  $("#subscription-table").innerHTML = items
    .map(
      (item) => `
      <tr>
        <td class="select-column">
          <input class="row-select" type="checkbox" data-id="${item.id}" aria-label="选择订阅" ${state.selectedIds.has(item.id) ? "checked" : ""}>
        </td>
        <td>
          <div class="streamer-cell">
            <span class="avatar">${avatarContents(item)}</span>
            <div>
              <strong>${escapeHtml(item.name || `UID ${item.uid}`)}</strong>
              <span>
                UID ${escapeHtml(item.uid)}
                ·
                ${item.room_id ? `房间 ${escapeHtml(item.room_id)}` : "房间未知"}
              </span>
            </div>
          </div>
        </td>
        <td>
          ${statusBadge(item.live_status)}
          <span class="status-meta">${escapeHtml(formatDateTime(item.checked_at))}</span>
          ${
            item.live_status === "live"
              ? `<span class="status-meta">${escapeHtml(item.live_title || "直播中")}</span>
                 <span class="status-meta">${escapeHtml([item.live_area, formatDuration(item.current_live_duration)].filter(Boolean).join(" · "))}</span>`
              : ""
          }
        </td>
        <td>
          <div class="target-cell">
            <strong>
              ${escapeHtml(targetTypeName(item.target_type))}
              · ${escapeHtml(targetLabel(item))}
            </strong>
            <span>${escapeHtml(item.target_name && item.target_type === "group" ? item.target_name : `目标 ID ${item.target_id}`)}</span>
          </div>
        </td>
        <td>
          <div class="bot-cell">
            <span class="online-dot ${item.bot_online ? "online" : ""}"></span>
            <div>
              <strong>${escapeHtml(item.bot_name || item.bot_id)}</strong>
              <span>${escapeHtml(item.bot_name ? `QQ ${item.bot_id}` : item.bot_online ? "在线" : "离线或未连接")}</span>
              ${pushResult(item)}
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
            <button class="row-button" data-action="test" data-id="${item.id}" type="button">测试推送</button>
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
          <input class="row-select" type="checkbox" data-id="${item.id}" aria-label="选择订阅" ${state.selectedIds.has(item.id) ? "checked" : ""}>
          <div class="streamer-cell">
            <span class="avatar">${avatarContents(item)}</span>
            <div>
              <strong>${escapeHtml(item.name || `UID ${item.uid}`)}</strong>
              <span>UID ${escapeHtml(item.uid)} · ${item.room_id ? `房间 ${escapeHtml(item.room_id)}` : "房间未知"}</span>
            </div>
          </div>
          ${statusBadge(item.live_status)}
        </div>
        <div class="mobile-detail">
          <span>${escapeHtml(targetTypeName(item.target_type))} · ${escapeHtml(targetLabel(item))}</span>
          <span>机器人 ${escapeHtml(item.bot_name || item.bot_id)}</span>
        </div>
        <div class="mobile-detail"><span>检测：${escapeHtml(formatDateTime(item.checked_at))}</span>${pushResult(item)}</div>
        <div class="mobile-detail">${featureBadges(item)}</div>
        <div class="mobile-card-actions">
          <button class="row-button" data-action="test" data-id="${item.id}" type="button">测试推送</button>
          <button class="row-button" data-action="edit" data-id="${item.id}" type="button">编辑</button>
          <button class="row-button danger" data-action="delete" data-id="${item.id}" type="button">删除</button>
        </div>
      </article>`,
    )
    .join("");
}

function render() {
  const items = state.items;
  $("#stat-total").textContent = state.summaryTotal;
  $("#stat-live").textContent = state.liveTotal;
  $("#stat-enabled").textContent = state.enabledTotal;
  $("#stat-bots").textContent = state.bots.filter((bot) => bot.online).length;

  const initialLoading = state.loading && !state.subscriptionsInitialized;
  const empty = state.subscriptionsInitialized && items.length === 0;
  $("#empty-state").classList.toggle("hidden", !empty);
  $("#desktop-table").classList.toggle(
    "hidden",
    initialLoading || empty,
  );
  $("#mobile-list").classList.toggle(
    "hidden",
    initialLoading || empty,
  );
  renderTable(items);
  renderMobile(items);
  renderSummaryState();
  renderBulkToolbar();
  renderSortState();
  renderPagination();
}

function renderSummaryState() {
  document.querySelectorAll("[data-summary-filter]").forEach((card) => {
    const active = card.dataset.summaryFilter === state.summaryFilter;
    card.classList.toggle("active", active);
    card.setAttribute("aria-pressed", String(active));
  });
}

function renderSortState() {
  document.querySelectorAll("[data-sort]").forEach((button) => {
    const active = button.dataset.sort === state.sortBy;
    button.classList.toggle("active", active);
    button.dataset.direction = active ? state.sortOrder : "";
    const base = button.textContent.replace(/\s[↑↓]$/, "");
    button.textContent = `${base}${active ? (state.sortOrder === "asc" ? " ↑" : " ↓") : ""}`;
  });
}

function renderBulkToolbar() {
  const count = state.selectedIds.size;
  $("#bulk-toolbar").classList.toggle(
    "hidden",
    count === 0,
  );
  $("#selected-count").textContent = `已选择 ${count} 条`;
  const pageIds = state.items.map((item) => item.id);
  const checked = pageIds.length > 0 && pageIds.every((id) => state.selectedIds.has(id));
  $("#select-page").checked = checked;
  $("#select-page").indeterminate =
    !checked && pageIds.some((id) => state.selectedIds.has(id));
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const hidden = !state.subscriptionsInitialized || state.total === 0;
  $("#pagination").classList.toggle("hidden", hidden);
  $("#page-size-input").value = String(state.pageSize);
  $("#page-info").textContent =
    `第 ${state.page} / ${totalPages} 页，共 ${state.total} 条`;
  $("#page-first").disabled = state.page <= 1;
  $("#page-prev").disabled = state.page <= 1;
  $("#page-next").disabled = state.page >= totalPages;
  $("#page-last").disabled = state.page >= totalPages;
}

function filtersQuery() {
  const params = new URLSearchParams();
  const q = $("#search-input").value.trim();
  if (q) params.set("q", q);
  if (state.summaryFilter === "live") params.set("live_status", "live");
  if (state.summaryFilter === "enabled") params.set("live_enabled", "true");
  if (state.summaryFilter === "online-bots") params.set("bot_online", "true");
  params.set("sort_by", state.sortBy);
  params.set("sort_order", state.sortOrder);
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return params.toString();
}

function resetAndLoadSubscriptions() {
  state.page = 1;
  state.selectedIds.clear();
  return loadSubscriptions();
}

async function loadSubscriptions() {
  const requestId = ++state.subscriptionRequestId;
  state.subscriptionController?.abort();
  const controller = new AbortController();
  state.subscriptionController = controller;
  setLoading(true);
  try {
    const payload = await api(`/admin/api/subscriptions?${filtersQuery()}`, {
      signal: controller.signal,
    });
    if (requestId !== state.subscriptionRequestId) return false;
    state.items = payload.items;
    state.total = payload.total;
    state.summaryTotal = payload.summary_total;
    state.liveTotal = payload.summary_live_total;
    state.enabledTotal = payload.summary_enabled_total;
    state.page = payload.page;
    state.pageSize = payload.page_size;
    state.subscriptionsInitialized = true;
    render();
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    if (error.status !== 401) toast(error.message, "error");
    return false;
  } finally {
    if (requestId === state.subscriptionRequestId) {
      state.subscriptionController = null;
      state.subscriptionsInitialized = true;
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
    const payload = await api("/admin/api/options", {
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

function startAutoRefresh() {
  stopAutoRefresh();
  state.autoRefreshTimer = window.setInterval(() => {
    if (
      document.visibilityState === "visible" &&
      modal.classList.contains("hidden") &&
      !state.loading &&
      !state.mutationLoading
    ) {
      loadSubscriptions();
    }
  }, AUTO_REFRESH_INTERVAL_MS);
}

function stopAutoRefresh() {
  if (state.autoRefreshTimer !== null) {
    window.clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
}

function renderGroupOptions() {
  const botId = Number($("#bot-input").value);
  const bot = state.bots.find((item) => item.id === botId);
  const groups = $("#target-type-input").value === "group" ? bot?.groups || [] : [];
  $("#group-options").innerHTML = groups
    .map((group) => `<option value="${group.id}">${escapeHtml(group.name || `群 ${group.id}`)}</option>`)
    .join("");
}

function updateTargetFields() {
  const targetTypeInput = $("#target-type-input");
  const targetIdInput = $("#target-id-input");
  const guildIdInput = $("#guild-id-input");
  const channelIdInput = $("#channel-id-input");
  const targetType = state.editing?.target_type || targetTypeInput.value;
  const editing = Boolean(state.editing);
  const guild = targetType === "guild";
  const privateTarget = targetType === "private";

  targetTypeInput.value = targetType;
  targetTypeInput.disabled = editing;
  $("#target-id-field").classList.toggle("hidden", guild);
  $("#guild-target-fields").classList.toggle("hidden", !guild);
  targetIdInput.disabled = guild || (editing && targetType !== "group");
  targetIdInput.required = !guild && (!editing || targetType === "group");
  guildIdInput.disabled = !guild || editing;
  channelIdInput.disabled = !guild || editing;
  guildIdInput.required = guild && !editing;
  channelIdInput.required = guild && !editing;
  $("#target-id-label").textContent = privateTarget ? "接收私聊的 QQ" : "通知群号";
  targetIdInput.placeholder = privateTarget ? "请输入接收者 QQ" : "选择或手动输入";
  if (targetType === "group") {
    targetIdInput.setAttribute("list", "group-options");
  } else {
    targetIdInput.removeAttribute("list");
  }

  $("#immutable-target-note").classList.toggle(
    "hidden",
    !editing || targetType === "group",
  );
  $("#at-switch").disabled = privateTarget;
  if (privateTarget) $("#at-switch").checked = false;
  renderGroupOptions();
}

function setSourceType(sourceType, focus = true) {
  state.sourceType = sourceType === "room" ? "room" : "uid";
  const uidSelected = state.sourceType === "uid";
  $("#source-type-input").value = state.sourceType;
  $("#source-value-label").textContent = uidSelected ? "用户 UID" : "直播间号";
  $("#source-value-input").placeholder = uidSelected
    ? "请输入 B站用户 UID"
    : "请输入直播间号";
  $("#source-value-input").required = true;
  if (focus) $("#source-value-input").focus();
}

function openCreateModal() {
  state.editing = null;
  $("#modal-title").textContent = "新增直播监控";
  $("#subscription-id").value = "";
  $("#source-field").classList.remove("hidden");
  $("#source-value-input").value = "";
  $("#editing-streamer").classList.add("hidden");
  $("#immutable-target-note").classList.add("hidden");
  $("#target-type-input").disabled = false;
  $("#target-type-input").value = "group";
  $("#bot-input").disabled = false;
  $("#bot-input").value = state.bots.find((bot) => bot.online)?.id || "";
  $("#target-id-input").value = "";
  $("#guild-id-input").value = "";
  $("#channel-id-input").value = "";
  $("#live-switch").checked = true;
  $("#dynamic-switch").checked = false;
  $("#at-switch").checked = false;
  $("#subscription-error").textContent = "";
  setSourceType("uid", false);
  updateTargetFields();
  modal.classList.remove("hidden");
  setTimeout(() => $("#source-value-input").focus(), 0);
}

function openEditModal(item) {
  state.editing = item;
  $("#modal-title").textContent = "编辑直播订阅";
  $("#subscription-id").value = item.id;
  $("#source-field").classList.add("hidden");
  $("#source-value-input").required = false;
  $("#editing-streamer").classList.remove("hidden");
  $("#editing-avatar").innerHTML = avatarContents(item);
  $("#editing-name").textContent = item.name || `UID ${item.uid}`;
  $("#editing-meta").textContent = `UID ${item.uid} · 直播间 ${item.room_id || "未知"}`;
  $("#target-type-input").value = item.target_type;
  $("#bot-input").value = item.bot_id;
  $("#target-id-input").value = item.target_id;
  $("#guild-id-input").value = item.guild_id || "";
  $("#channel-id-input").value = item.channel_id || "";
  $("#live-switch").checked = item.live;
  $("#dynamic-switch").checked = item.dynamic;
  $("#at-switch").checked = item.at;
  $("#subscription-error").textContent = "";
  updateTargetFields();
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
  const targetType = state.editing?.target_type || $("#target-type-input").value;
  const targetId = Number($("#target-id-input").value);
  if (!botId) {
    errorNode.textContent = "请输入有效的机器人 QQ";
    return;
  }
  const payload = {
    bot_id: botId,
    live: $("#live-switch").checked,
    dynamic: $("#dynamic-switch").checked,
    at: $("#at-switch").checked,
  };
  let path = "/admin/api/subscriptions";
  let method = "POST";
  if (state.editing) {
    path += `/${state.editing.id}`;
    method = "PATCH";
    if (state.editing.target_type === "group") {
      if (!targetId) {
        errorNode.textContent = "请输入有效的通知群号";
        return;
      }
      payload.target_id = targetId;
    }
  } else {
    payload.target_type = targetType;
    const sourceValue = Number($("#source-value-input").value);
    if (state.sourceType === "uid") {
      payload.uid = sourceValue;
      if (!payload.uid) {
        errorNode.textContent = "请输入有效的用户 UID";
        return;
      }
    } else {
      payload.room_id = sourceValue;
      if (!payload.room_id) {
        errorNode.textContent = "请输入有效的直播间号";
        return;
      }
    }
    if (targetType === "guild") {
      payload.guild_id = $("#guild-id-input").value.trim();
      payload.channel_id = $("#channel-id-input").value.trim();
      if (!payload.guild_id || !payload.channel_id) {
        errorNode.textContent = "请输入频道 ID 和子频道 ID";
        return;
      }
    } else {
      if (!targetId) {
        errorNode.textContent =
          targetType === "private" ? "请输入有效的接收者 QQ" : "请输入有效的通知群号";
        return;
      }
      payload.target_id = targetId;
    }
  }

  saveButton.disabled = true;
  saveButton.textContent = state.editing ? "正在保存…" : "正在添加…";
  setMutationLoading(true);
  try {
    await api(path, { method, body: JSON.stringify(payload) });
    closeModal();
    await refreshData(wasEditing ? "订阅已更新" : "监控已添加");
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
    await api(`/admin/api/subscriptions/${item.id}`, { method: "DELETE" });
    state.selectedIds.delete(item.id);
    await refreshData("订阅已删除");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setMutationLoading(false);
  }
}

function findSubscription(id) {
  return state.items.find((row) => row.id === id);
}

async function testPush(item) {
  if (
    !window.confirm(
      `将使用配置机器人 ${item.bot_id} 向 ${targetTypeName(item.target_type)} ${targetLabel(item)} 发送真实测试消息，是否继续？`,
    )
  ) {
    return;
  }
  setMutationLoading(true);
  try {
    const result = await api(
      `/admin/api/subscriptions/${item.id}/test-push`,
      { method: "POST" },
    );
    toast(
      result.success ? "测试推送成功" : `测试推送失败：${result.message}`,
      result.success ? "success" : "error",
    );
    await loadSubscriptions();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setMutationLoading(false);
  }
}

async function handleRowAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const item = findSubscription(Number(button.dataset.id));
  if (!item) return;
  if (button.dataset.action === "test") await testPush(item);
  if (button.dataset.action === "edit") openEditModal(item);
  if (button.dataset.action === "delete") await deleteSubscription(item);
}

function handleSelection(event) {
  const checkbox = event.target.closest(".row-select");
  if (!checkbox) return;
  const id = Number(checkbox.dataset.id);
  if (checkbox.checked) state.selectedIds.add(id);
  else state.selectedIds.delete(id);
  renderBulkToolbar();
}

async function applyBulkAction() {
  const action = $("#bulk-action").value;
  const ids = Array.from(state.selectedIds);
  if (!action || ids.length === 0) {
    toast("请选择批量操作", "error");
    return;
  }
  const payload = { ids, operation: action === "delete" ? "delete" : "update" };
  const updateMap = {
    live_on: { live: true },
    live_off: { live: false },
    dynamic_on: { dynamic: true },
    dynamic_off: { dynamic: false },
    at_on: { at: true },
    at_off: { at: false },
  };
  Object.assign(payload, updateMap[action] || {});
  if (action === "bot") {
    payload.bot_id = Number($("#bulk-bot-input").value);
    if (!payload.bot_id) {
      toast("请输入有效的机器人 QQ", "error");
      return;
    }
  }
  if (
    action === "delete" &&
    !window.confirm(`确定删除已选择的 ${ids.length} 条订阅吗？此操作不可撤销。`)
  ) {
    return;
  }

  setMutationLoading(true);
  try {
    const result = await api("/admin/api/subscriptions/bulk", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.selectedIds.clear();
    toast(
      `批量操作完成：成功 ${result.processed_ids.length}，缺失 ${result.missing_ids.length}，失败 ${result.failed_ids.length}`,
      result.failed_ids.length ? "error" : "success",
    );
    await loadSubscriptions();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setMutationLoading(false);
  }
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
    const session = await api("/admin/api/auth/session");
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
    await api("/admin/api/auth/login", {
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
    await api("/admin/api/auth/logout", { method: "POST" });
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
$("#target-type-input").addEventListener("change", () => {
  $("#target-id-input").value = "";
  $("#guild-id-input").value = "";
  $("#channel-id-input").value = "";
  updateTargetFields();
});
$("#source-type-input").addEventListener("change", (event) => {
  $("#source-value-input").value = "";
  setSourceType(event.target.value);
});
$("#subscription-table").addEventListener("click", handleRowAction);
$("#mobile-list").addEventListener("click", handleRowAction);
$("#subscription-table").addEventListener("change", handleSelection);
$("#mobile-list").addEventListener("change", handleSelection);
$("#select-page").addEventListener("change", (event) => {
  state.items.forEach((item) => {
    if (event.target.checked) state.selectedIds.add(item.id);
    else state.selectedIds.delete(item.id);
  });
  render();
});
document.querySelectorAll("[data-summary-filter]").forEach((card) => {
  card.addEventListener("click", () => {
    state.summaryFilter = card.dataset.summaryFilter;
    renderSummaryState();
    resetAndLoadSubscriptions();
  });
});
document.querySelectorAll("[data-sort]").forEach((button) => {
  button.addEventListener("click", () => {
    const sortBy = button.dataset.sort;
    if (state.sortBy === sortBy) {
      state.sortOrder = state.sortOrder === "asc" ? "desc" : "asc";
    } else {
      state.sortBy = sortBy;
      state.sortOrder = "asc";
    }
    state.page = 1;
    loadSubscriptions();
  });
});
$("#bulk-action").addEventListener("change", () => {
  $("#bulk-bot-input").classList.toggle(
    "hidden",
    $("#bulk-action").value !== "bot",
  );
});
$("#bulk-apply").addEventListener("click", applyBulkAction);
$("#bulk-clear").addEventListener("click", () => {
  state.selectedIds.clear();
  render();
});
document.addEventListener(
  "load",
  (event) => {
    if (event.target.matches?.(".avatar-image")) {
      event.target.classList.add("loaded");
    }
  },
  true,
);
document.addEventListener(
  "error",
  (event) => {
    if (event.target.matches?.(".avatar-image")) {
      event.target.remove();
    }
  },
  true,
);
$("#refresh-button").addEventListener("click", async () => {
  await refreshData("数据已刷新");
});
$("#search-input").addEventListener(
  "input",
  debounce(resetAndLoadSubscriptions, 280),
);
$("#page-size-input").addEventListener("change", () => {
  state.pageSize = Number($("#page-size-input").value);
  state.page = 1;
  loadSubscriptions();
});
$("#page-first").addEventListener("click", () => {
  state.page = 1;
  loadSubscriptions();
});
$("#page-prev").addEventListener("click", () => {
  if (state.page <= 1) return;
  state.page -= 1;
  loadSubscriptions();
});
$("#page-next").addEventListener("click", () => {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (state.page >= totalPages) return;
  state.page += 1;
  loadSubscriptions();
});
$("#page-last").addEventListener("click", () => {
  state.page = Math.max(1, Math.ceil(state.total / state.pageSize));
  loadSubscriptions();
});
document.addEventListener("visibilitychange", () => {
  if (
    document.visibilityState === "visible" &&
    !appView.classList.contains("hidden") &&
    modal.classList.contains("hidden")
  ) {
    loadSubscriptions();
  }
});
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
