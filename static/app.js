const API = "/api";
const TOKEN_KEY = "officeboard_token";
const ARCHIVE_DAYS = 7;
const PALETTE = ["#0047BB", "#F26522", "#0D9488", "#7B5FE0", "#C7371F", "#4C9F70"];
const DEPARTMENTS = ["Legal", "Branding", "Operations", "Sales", "Marketing", "Technical Team"];

const app = document.getElementById("app");

const state = {
  screen: "loading",
  publicRoster: [],
  roster: [],
  currentUser: null,
  token: localStorage.getItem(TOKEN_KEY),
  selectedForLogin: null,
  posts: [],
  notifs: [],
  filter: "all",
  showArchive: false,
  tier: "update",
  message: "",
  notifOpen: false,
  errorMsg: "",
  currentDept: "",
  lastNotifCount: 0,
  attachment: null,
  replyModalOpen: false,
  replyPostId: null,
  // ---- MFA flow (login only; no token has been issued yet at this point) ----
  mfaPreAuthToken: null,
  mfaSecret: null,
  mfaOtpUri: null,
  mfaQr: null,
  mfaChallengeMode: "code", // "code" | "recovery"
  mfaPendingAccessToken: null,
  mfaPendingUser: null,
  mfaRecoveryCodes: [],
  deptCounts: {},
  // ---- Direct messages ----
  dmConversations: [], // grouped: [{otherId, otherUser, messages, lastMessage}]
  dmView: "list", // "list" | "thread"
  dmActiveOtherId: null,
  // ---- @mention privatize prompt ----
  pendingPost: null, // {tier, message, department, attachment}
  pendingMentionUser: null, // roster entry
};

let dom = {};

// ---------- background bubbles (decorative only, no data) ----------
function bubbleFieldHtml() {
  return `
    <div class="bubble-field" aria-hidden="true">
      <div class="bubble blue b1"></div>
      <div class="bubble orange b2"></div>
      <div class="bubble blue b3"></div>
      <div class="bubble orange b4"></div>
      <div class="bubble blue b5"></div>
    </div>`;
}

function wordRowHtml() {
  const words = [
    { w: "CONNECT", d: "Reach the whole team in one place." },
    { w: "COLLABORATE", d: "Share ideas. Build together." },
    { w: "COMMUNICATE", d: "Updates that actually get seen." },
    { w: "CREATE", d: "Post, reply, attach — all in one thread." },
  ];
  return `<div class="word-row" aria-hidden="true">${words
    .map((x) => `<span class="word-chip">${x.w}<span class="peek">${x.d}</span></span>`)
    .join("")}</div>`;
}

// ---------- API helper ----------
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(API + path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && state.token && !path.startsWith("/auth/")) {
    handleSessionExpired();
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

function handleSessionExpired() {
  if (pollTimer) clearInterval(pollTimer);
  state.token = null;
  state.currentUser = null;
  localStorage.removeItem(TOKEN_KEY);
  state.screen = "login";
  state.errorMsg = "Your session has expired. Please log in again.";
  api("/users/public").then((roster) => { state.publicRoster = roster; render(); }).catch(() => render());
}

// ---------- password policy ----------
const PASSWORD_RULES = [
  { test: (p) => p.length >= 10, label: "10+ characters" },
  { test: (p) => /[A-Z]/.test(p), label: "Uppercase letter" },
  { test: (p) => /[a-z]/.test(p), label: "Lowercase letter" },
  { test: (p) => /[0-9]/.test(p), label: "A number" },
  { test: (p) => /[^A-Za-z0-9]/.test(p), label: "A special character" },
];
function isPasswordValid(password) {
  return PASSWORD_RULES.every((r) => r.test(password || ""));
}
function passwordChecklistHtml(password) {
  return `<div class="password-checklist">${PASSWORD_RULES.map((r) => {
    const ok = r.test(password || "");
    return `<span class="pw-rule${ok ? " pw-rule-ok" : ""}">${ok ? "✓" : "○"} ${r.label}</span>`;
  }).join("")}</div>`;
}
function wirePasswordHints(inputId, hintsId, submitId) {
  const input = document.getElementById(inputId);
  const hints = document.getElementById(hintsId);
  const submit = submitId ? document.getElementById(submitId) : null;
  const update = () => {
    hints.innerHTML = passwordChecklistHtml(input.value);
    if (submit) submit.disabled = !isPasswordValid(input.value);
  };
  input.addEventListener("input", update);
  update();
}
function deptOptionsHtml(selected) {
  return `<option value="">Select Department (optional)</option>` +
    DEPARTMENTS.map((d) => `<option value="${d}" ${selected === d ? "selected" : ""}>${d}</option>`).join("");
}

// ---------- helpers ----------
function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
function colorFor(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
  return PALETTE[Math.abs(hash) % PALETTE.length];
}
function timeAgo(isoString) {
  const ts = new Date(isoString + "Z").getTime();
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  return `${Math.floor(hr / 24)}d`;
}
function toTs(isoString) {
  return new Date(isoString + "Z").getTime();
}
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : str;
  return div.innerHTML;
}
function renderMessage(text, roster) {
  const handles = new Set(roster.map((r) => r.handle.toLowerCase()));
  handles.add("all");
  return escapeHtml(text).replace(/@(\w+)/g, (full, h) => {
    return handles.has(h.toLowerCase()) ? `<span class="mention">@${h}</span>` : full;
  });
}
function avatarHtml(name, imgUrl, size = 40, extraClass = "") {
  if (imgUrl) {
    return `<div class="avatar ${extraClass}" style="width:${size}px;height:${size}px;"><img src="${imgUrl}" alt=""></div>`;
  }
  return `<div class="avatar ${extraClass}" style="width:${size}px;height:${size}px;background:${colorFor(name)};font-size:${size * 0.35}px">${initials(name)}</div>`;
}

// ---------- init ----------
async function init() {
  if (state.token) {
    try {
      const user = await api("/auth/me");
      state.currentUser = { id: user.id, name: user.name, handle: user.handle, isAdmin: user.is_admin, department: user.department, profile_image: user.profile_image };
      await Promise.all([loadRoster(), loadPosts(), loadNotifs()]);
      state.screen = "board";
      render();
      startPolling();
      return;
    } catch (e) {
      state.token = null;
      localStorage.removeItem(TOKEN_KEY);
    }
  }
  try {
    const status = await api("/auth/status");
    if (!status.hasUsers) {
      state.screen = "setup";
    } else {
      state.publicRoster = await api("/users/public");
      state.screen = "login";
    }
  } catch (e) {
    state.errorMsg = "Couldn't reach the server. Is it running?";
    state.screen = "login";
  }
  render();
}

let pollTimer = null;
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (state.screen !== "board") return;
    if (state.replyModalOpen) return;
    try {
      await Promise.all([loadPosts(), loadNotifs(), loadDMs()]);
      updateFeed();
      updateNotifBadge();
      if (state.notifOpen) updateNotifPanel();
      if (state.dmView === "thread" && !document.getElementById("dm-modal").classList.contains("hidden")) renderDMThread();
    } catch (e) {}
  }, 5000);
}

async function loadRoster() {
  const users = await api("/users/");
  state.roster = users.map((u) => ({ id: u.id, name: u.name, handle: u.handle, isAdmin: u.is_admin, department: u.department, profile_image: u.profile_image }));
}
async function loadPosts() {
  let url = "/posts/";
  if (state.currentDept) url += `?department=${encodeURIComponent(state.currentDept)}`;
  const posts = await api(url);
  state.posts = posts.map((p) => ({
    id: p.id,
    tier: p.tier,
    message: p.message,
    department: p.department,
    ts: toTs(p.created_at),
    createdAtIso: p.created_at,
    author: p.author.name,
    authorHandle: p.author.handle,
    authorImage: p.author.profile_image,
    authorIsAdmin: p.author.is_admin,
    attachment_url: p.attachment_url,
    attachment_filename: p.attachment_filename,
    attachment_type: p.attachment_type,
    comments: p.comments || [],
  }));
  await loadDeptCounts();
  // NOTE: notifications are intentionally NOT auto-marked read just because
  // their post happens to be in the currently-fetched feed (that used to
  // happen here on every 5s poll, which silently marked new updates "read"
  // before you ever saw the bell -- see openReplyModal() and
  // toggleNotifications() for where reads are now actually earned).
}
// Department-pill counts come from a dedicated backend endpoint that always
// reports every department the caller can see, regardless of which
// department (if any) is currently selected in the UI. Previously this was
// guessed from whatever GET /posts/ happened to return, which for an admin
// viewing "All Updates" is ONLY general (non-department) posts -- so every
// department pill silently showed 0 until you clicked into it.
async function loadDeptCounts() {
  try {
    state.deptCounts = await api("/posts/dept-counts");
  } catch (e) {
    // Non-fatal: pill counts simply won't refresh this cycle.
  }
}

// ---------- Direct Messages ----------
function dmReadKey(otherId) {
  return `officeboard_dm_read_${state.currentUser.id}_${otherId}`;
}
function markDMThreadRead(otherId, latestIso) {
  if (!latestIso) return;
  localStorage.setItem(dmReadKey(otherId), String(toTs(latestIso)));
}
function isDMConvoUnread(conv) {
  if (!conv.lastMessage || conv.lastMessage.authorId === state.currentUser.id) return false;
  const readTs = parseInt(localStorage.getItem(dmReadKey(conv.otherId)) || "0", 10);
  return toTs(conv.lastMessage.created_at) > readTs;
}
async function loadDMs() {
  const raw = await api("/posts/dms");
  const byOther = new Map();
  raw.forEach((p) => {
    const otherId = p.author.id === state.currentUser.id ? p.dm_recipient_id : p.author.id;
    const otherUser = p.author.id === state.currentUser.id
      ? state.roster.find((r) => r.id === p.dm_recipient_id)
      : { id: p.author.id, name: p.author.name, handle: p.author.handle, profile_image: p.author.profile_image };
    if (!otherId) return;
    const msg = {
      id: p.id,
      text: p.message,
      createdAtIso: p.created_at,
      authorId: p.author.id,
      attachment_url: p.attachment_url,
      attachment_filename: p.attachment_filename,
      attachment_type: p.attachment_type,
    };
    if (!byOther.has(otherId)) byOther.set(otherId, { otherId, otherUser, messages: [] });
    byOther.get(otherId).messages.push(msg);
  });
  const convos = Array.from(byOther.values()).map((c) => {
    c.messages.sort((a, b) => toTs(a.createdAtIso) - toTs(b.createdAtIso));
    c.lastMessage = c.messages[c.messages.length - 1];
    return c;
  });
  convos.sort((a, b) => toTs(b.lastMessage.createdAtIso) - toTs(a.lastMessage.createdAtIso));
  state.dmConversations = convos;
  updateDMBadge();
}
function updateDMBadge() {
  const btn = document.getElementById("dm-btn");
  if (!btn) return;
  const anyUnread = state.dmConversations.some(isDMConvoUnread);
  let dot = btn.querySelector(".dm-nav-dot");
  if (anyUnread) {
    if (!dot) { dot = document.createElement("span"); dot.className = "dm-nav-dot"; btn.appendChild(dot); }
  } else if (dot) {
    dot.remove();
  }
}
function openDMs() {
  document.getElementById("dm-modal").classList.remove("hidden");
  state.dmView = "list";
  document.getElementById("dm-new-picker").classList.add("hidden");
  loadDMs().then(renderDMList).catch(() => showErrorToast("Couldn't load messages"));
  renderDMList();
}
function closeDMs() { document.getElementById("dm-modal").classList.add("hidden"); }

function renderDMList() {
  document.getElementById("dm-list-view").classList.remove("hidden");
  document.getElementById("dm-thread-view").classList.add("hidden");
  const container = document.getElementById("dm-conversations");
  if (state.dmConversations.length === 0) {
    container.innerHTML = `<div class="dm-empty">No private messages yet. Start one with "+ New message".</div>`;
  } else {
    container.innerHTML = `<div class="dm-conversations">${state.dmConversations.map((c) => `
      <button class="dm-convo-item ${isDMConvoUnread(c) ? "unread" : ""}" data-other="${c.otherId}">
        ${avatarHtml(c.otherUser ? c.otherUser.name : "?", c.otherUser ? c.otherUser.profile_image : null, 38)}
        <div class="dm-convo-info">
          <div class="dm-convo-name">${escapeHtml(c.otherUser ? c.otherUser.name : "Unknown")} ${isDMConvoUnread(c) ? '<span class="dm-unread-dot"></span>' : ""}</div>
          <div class="dm-convo-preview">${c.lastMessage.authorId === state.currentUser.id ? "You: " : ""}${escapeHtml(c.lastMessage.text || c.lastMessage.attachment_filename || "Attachment")}</div>
        </div>
        <div class="dm-convo-time">${timeAgo(c.lastMessage.createdAtIso)}</div>
      </button>`).join("")}</div>`;
    container.querySelectorAll("[data-other]").forEach((btn) => {
      btn.onclick = () => openDMThread(parseInt(btn.dataset.other));
    });
  }
  document.getElementById("dm-new-btn").onclick = toggleDMPicker;
}

function toggleDMPicker() {
  const picker = document.getElementById("dm-new-picker");
  const isHidden = picker.classList.contains("hidden");
  if (!isHidden) { picker.classList.add("hidden"); return; }
  const options = state.roster.filter((r) => r.id !== state.currentUser.id);
  picker.innerHTML = options.map((r) => `
    <button class="dm-picker-item" data-id="${r.id}">
      ${avatarHtml(r.name, r.profile_image, 30)}
      <span>${escapeHtml(r.name)} <span style="color:var(--faint)">@${escapeHtml(r.handle)}</span></span>
    </button>`).join("");
  picker.classList.remove("hidden");
  picker.querySelectorAll("[data-id]").forEach((btn) => {
    btn.onclick = () => { picker.classList.add("hidden"); openDMThread(parseInt(btn.dataset.id)); };
  });
}

function openDMThread(otherId) {
  state.dmView = "thread";
  state.dmActiveOtherId = otherId;
  document.getElementById("dm-list-view").classList.add("hidden");
  document.getElementById("dm-thread-view").classList.remove("hidden");
  renderDMThread();
  document.getElementById("dm-back-btn").onclick = () => { state.dmView = "list"; renderDMList(); };
  document.getElementById("dm-send-btn").onclick = sendDM;
  document.getElementById("dm-textarea").focus();
}

function renderDMThread() {
  const otherId = state.dmActiveOtherId;
  let conv = state.dmConversations.find((c) => c.otherId === otherId);
  const otherUser = (conv && conv.otherUser) || state.roster.find((r) => r.id === otherId);
  document.getElementById("dm-thread-header").innerHTML = `
    ${avatarHtml(otherUser ? otherUser.name : "?", otherUser ? otherUser.profile_image : null, 30)}
    <span>${escapeHtml(otherUser ? otherUser.name : "Unknown")}</span>`;
  const messages = conv ? conv.messages : [];
  const messagesDiv = document.getElementById("dm-thread-messages");
  messagesDiv.innerHTML = messages.length === 0
    ? `<div class="dm-empty">No messages yet. Say hello!</div>`
    : messages.map((m) => `
      <div class="dm-msg ${m.authorId === state.currentUser.id ? "dm-msg-mine" : "dm-msg-theirs"}">
        ${escapeHtml(m.text)}
        <span class="dm-msg-time">${timeAgo(m.createdAtIso)}</span>
      </div>`).join("");
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  if (conv && conv.lastMessage) {
    markDMThreadRead(otherId, conv.lastMessage.createdAtIso);
    updateDMBadge();
    // Also clear the underlying notification bell entries for this thread.
    const postIds = messages.map((m) => m.id);
    if (postIds.length) {
      api("/notifications/read-visible", { method: "POST", body: JSON.stringify({ post_ids: postIds }) })
        .then(() => loadNotifs()).then(() => updateNotifBadge()).catch(() => {});
    }
  }
}

async function sendDM() {
  const textarea = document.getElementById("dm-textarea");
  const message = textarea.value.trim();
  if (!message) return;
  textarea.value = "";
  try {
    await api("/posts/dm", { method: "POST", body: JSON.stringify({ recipient_id: state.dmActiveOtherId, message }) });
    await loadDMs();
    renderDMThread();
  } catch (e) {
    showErrorToast(e.message);
  }
}

// ---------- @mention privatize prompt ----------
function extractMentionInfo(message) {
  const handles = new Set();
  let all = false;
  const re = /@(\w+)/g;
  let m;
  while ((m = re.exec(message)) !== null) {
    const h = m[1].toLowerCase();
    if (h === "all") all = true;
    else handles.add(h);
  }
  return { handles, all };
}

function closePrivatizeModal() {
  document.getElementById("privatize-modal").classList.add("hidden");
  state.pendingPost = null;
  state.pendingMentionUser = null;
}

async function loadNotifs() {
  const notifs = await api("/notifications/");
  state.notifs = notifs.map((n) => ({
    id: n.id,
    preview: n.preview,
    read: n.is_read,
    isMention: n.is_mention,
    createdAtIso: n.created_at,
    from: n.from_user.name,
    fromImage: n.from_user.profile_image,
    tier: n.post_tier,
    postId: n.post_id,
  }));
}

// ---------- actions ----------
async function submitBootstrap(name, password, department) {
  state.errorMsg = "";
  try {
    const data = await api("/auth/bootstrap", { method: "POST", body: JSON.stringify({ name, password, department: department || null }) });
    state.token = data.access_token;
    localStorage.setItem(TOKEN_KEY, data.access_token);
    state.currentUser = { id: data.user.id, name: data.user.name, handle: data.user.handle, isAdmin: data.user.is_admin, department: data.user.department, profile_image: data.user.profile_image };
    await Promise.all([loadRoster(), loadPosts(), loadNotifs()]);
    state.screen = "board";
    startPolling();
  } catch (e) {
    state.errorMsg = e.message;
  }
  render();
}

function pickLoginUser(member) {
  state.selectedForLogin = member;
  state.errorMsg = "";
  state.screen = "password";
  render();
}

async function submitLogin(password) {
  state.errorMsg = "";
  const btn = document.getElementById("pw-submit");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Signing in…'; }
  try {
    const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ handle: state.selectedForLogin.handle, password }) });

    if (data.mfa_required) {
      state.mfaPreAuthToken = data.pre_auth_token;
      if (data.enrollment_required) {
        await startMfaEnroll();
      } else {
        state.mfaChallengeMode = "code";
        state.errorMsg = "";
        state.screen = "mfaChallenge";
        render();
      }
      return;
    }

    finalizeLogin(data.access_token, data.user);
    await Promise.all([loadRoster(), loadPosts(), loadNotifs()]);
    state.screen = "board";
    startPolling();
  } catch (e) {
    state.errorMsg = e.message;
  }
  render();
}

// Shared by: normal no-MFA login, the MFA challenge (/mfa/verify), and the
// first-time enrollment confirm (/mfa/confirm) -- all three eventually hand
// back the same {access_token, user} shape once the full login is complete.
function finalizeLogin(accessToken, user) {
  state.token = accessToken;
  localStorage.setItem(TOKEN_KEY, accessToken);
  state.currentUser = { id: user.id, name: user.name, handle: user.handle, isAdmin: user.is_admin, department: user.department, profile_image: user.profile_image };
  state.mfaPreAuthToken = null;
  state.mfaSecret = null;
  state.mfaOtpUri = null;
  state.mfaQr = null;
}

function backToLoginFromMfa() {
  state.mfaPreAuthToken = null;
  state.mfaSecret = null;
  state.mfaOtpUri = null;
  state.mfaQr = null;
  state.mfaRecoveryCodes = [];
  state.errorMsg = "";
  state.screen = "login";
  render();
}

// ---------- MFA: enrollment (forced, first time only) ----------
async function startMfaEnroll() {
  try {
    const data = await api("/auth/mfa/enroll", { method: "POST", body: JSON.stringify({ pre_auth_token: state.mfaPreAuthToken }) });
    state.mfaSecret = data.secret;
    state.mfaOtpUri = data.otpauth_uri;
    state.mfaQr = data.qr_code;
    state.errorMsg = "";
    state.screen = "mfaEnroll";
  } catch (e) {
    state.errorMsg = e.message;
    state.screen = "login";
  }
  render();
}

async function submitMfaConfirm(code) {
  state.errorMsg = "";
  const btn = document.getElementById("mfa-confirm-submit");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Verifying…'; }
  try {
    const data = await api("/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ pre_auth_token: state.mfaPreAuthToken, code }) });
    state.mfaRecoveryCodes = data.recovery_codes;
    state.mfaPendingAccessToken = data.access_token;
    state.mfaPendingUser = data.user;
    state.screen = "mfaRecoveryCodes";
  } catch (e) {
    state.errorMsg = e.message;
  }
  render();
}

async function continueAfterRecoveryCodes() {
  finalizeLogin(state.mfaPendingAccessToken, state.mfaPendingUser);
  state.mfaPendingAccessToken = null;
  state.mfaPendingUser = null;
  state.mfaRecoveryCodes = [];
  await Promise.all([loadRoster(), loadPosts(), loadNotifs()]);
  state.screen = "board";
  startPolling();
  render();
}

// ---------- MFA: login challenge (account already enrolled) ----------
async function submitMfaVerify(codeOrRecovery) {
  state.errorMsg = "";
  const btn = document.getElementById("mfa-verify-submit");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Verifying…'; }
  try {
    const body = { pre_auth_token: state.mfaPreAuthToken };
    if (state.mfaChallengeMode === "recovery") body.recovery_code = codeOrRecovery;
    else body.code = codeOrRecovery;
    const data = await api("/auth/mfa/verify", { method: "POST", body: JSON.stringify(body) });
    finalizeLogin(data.access_token, data.user);
    await Promise.all([loadRoster(), loadPosts(), loadNotifs()]);
    state.screen = "board";
    startPolling();
  } catch (e) {
    state.errorMsg = e.message;
  }
  render();
}

async function submitAddTeammate(name, password, department) {
  state.errorMsg = "";
  try {
    await api("/users/", { method: "POST", body: JSON.stringify({ name, password, department: department || null }) });
    await loadRoster();
    state.screen = "board";
    render();
  } catch (e) {
    state.errorMsg = e.message;
    render();
  }
}

async function logout() {
  // Best-effort server-side logout FIRST: revokes the session so the token
  // dies immediately instead of lingering valid until its 2-hour expiry.
  try { await api("/auth/logout", { method: "POST" }); } catch (e) {}

  state.token = null;
  state.currentUser = null;
  state.message = "";
  state.screen = "login";
  state.notifOpen = false;
  state.currentDept = "";
  state.lastNotifCount = 0;
  state.attachment = null;
  state.replyModalOpen = false;
  state.replyPostId = null;

  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem("officeboard_draft");

  if (pollTimer) clearInterval(pollTimer);
  dom = {};
  init();
}

async function handlePost() {
  const textarea = dom.msgInput;
  if (!textarea) return;
  const message = textarea.value.trim();
  const tier = state.tier;
  const dept = state.currentDept || null;

  if (!message && !state.attachment) return;

  const body = { tier, message: message || "", department: dept };
  if (state.attachment) {
    body.attachment_url = state.attachment.url;
    body.attachment_filename = state.attachment.filename;
    body.attachment_type = state.attachment.type;
  }

  // If the message @mentions exactly one teammate (and not @all), ask
  // whether this should actually be a private message instead of a public
  // department/feed post -- rather than silently posting it where anyone
  // in the department (or, for @all, the whole company) can read it.
  const { handles, all } = extractMentionInfo(message);
  if (!all && handles.size === 1) {
    const handle = Array.from(handles)[0];
    const mentioned = state.roster.find((r) => r.handle.toLowerCase() === handle);
    if (mentioned) {
      state.pendingPost = body;
      state.pendingMentionUser = mentioned;
      document.getElementById("privatize-handle").textContent = "@" + mentioned.handle;
      document.getElementById("privatize-modal").classList.remove("hidden");
      const dmBtn = document.getElementById("privatize-send-dm");
      const publicBtn = document.getElementById("privatize-post-public");
      dmBtn.onclick = () => confirmSendAsDM();
      publicBtn.onclick = () => confirmPostPublic();
      return; // wait for the person's choice before doing anything else
    }
  }

  await submitPost(body);
}

async function submitPost(body) {
  const textarea = dom.msgInput;
  if (textarea) textarea.value = "";
  state.message = "";
  state.attachment = null;
  updatePostButton();
  updateAttachmentPreview();
  localStorage.removeItem("officeboard_draft");

  try {
    await api("/posts/", { method: "POST", body: JSON.stringify(body) });
    await Promise.all([loadPosts(), loadNotifs()]);
    updateFeed();
    updateNotifBadge();
  } catch (e) {
    state.errorMsg = e.message;
    showErrorToast(state.errorMsg);
  }
}

async function confirmPostPublic() {
  const body = state.pendingPost;
  closePrivatizeModal();
  if (body) await submitPost(body);
}

async function confirmSendAsDM() {
  const body = state.pendingPost;
  const recipient = state.pendingMentionUser;
  closePrivatizeModal();
  if (!body || !recipient) return;

  const textarea = dom.msgInput;
  if (textarea) textarea.value = "";
  state.message = "";
  state.attachment = null;
  updatePostButton();
  updateAttachmentPreview();
  localStorage.removeItem("officeboard_draft");

  try {
    const dmBody = { recipient_id: recipient.id, message: body.message };
    if (body.attachment_url) {
      dmBody.attachment_url = body.attachment_url;
      dmBody.attachment_filename = body.attachment_filename;
      dmBody.attachment_type = body.attachment_type;
    }
    await api("/posts/dm", { method: "POST", body: JSON.stringify(dmBody) });
    showErrorToast(`Sent privately to @${recipient.handle}`);
  } catch (e) {
    showErrorToast(e.message);
  }
}

async function handleDelete(id) {
  try {
    await api(`/posts/${id}`, { method: "DELETE" });
    await loadPosts();
    updateFeed();
  } catch (e) {}
}

async function markAllRead() {
  try {
    await api("/notifications/read-all", { method: "POST" });
    await loadNotifs();
    updateNotifBadge();
    updateNotifPanel();
  } catch (e) {}
}

// ---------- Attachment upload ----------
async function handleAttachmentUpload(input) {
  if (!input.files.length) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch(`${API}/posts/upload`, { method: "POST", headers: { Authorization: `Bearer ${state.token}` }, body: formData });
    if (!res.ok) throw new Error("Upload failed");
    const data = await res.json();
    state.attachment = { url: data.url, filename: data.filename, type: data.type };
    updateAttachmentPreview();
  } catch (e) {
    showErrorToast("Failed to upload file");
  }
  input.value = "";
}

function updateAttachmentPreview() {
  const wrap = document.getElementById("attachment-preview");
  if (!wrap) return;
  if (!state.attachment) { wrap.innerHTML = ""; return; }
  let icon = "📎";
  if (state.attachment.type === "image") icon = "🖼️";
  if (state.attachment.type === "video") icon = "🎬";
  wrap.innerHTML = `
    <div class="ap-row">
      <span>${icon} ${escapeHtml(state.attachment.filename)}</span>
      <button onclick="removeAttachment()">Remove</button>
    </div>`;
}
function removeAttachment() { state.attachment = null; updateAttachmentPreview(); }

// ---------- Profile
function showProfile() {
  if (!state.currentUser) return;
  document.getElementById("profile-name").textContent = state.currentUser.name + (state.currentUser.isAdmin ? " (Admin)" : "");
  document.getElementById("profile-handle").textContent = "@" + state.currentUser.handle;
  document.getElementById("profile-dept").textContent = state.currentUser.department ? `Department: ${state.currentUser.department}` : "No department assigned";
  const wrap = document.getElementById("profile-avatar-wrap");
  if (state.currentUser.profile_image) {
    wrap.innerHTML = `<img src="${state.currentUser.profile_image}" alt="" class="profile-avatar-lg">`;
  } else {
    wrap.innerHTML = `<div class="profile-avatar-lg" style="background:${colorFor(state.currentUser.name)};display:flex;align-items:center;justify-content:center;font-size:28px;color:#fff;font-weight:700;">${initials(state.currentUser.name)}</div>`;
  }
  document.getElementById("profile-modal").classList.remove("hidden");
  loadSessions();
}
function closeProfile() { document.getElementById("profile-modal").classList.add("hidden"); }

async function uploadProfileImage() {
  const input = document.getElementById("profile-upload");
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);
  const res = await fetch(`${API}/users/${state.currentUser.id}/profile-image`, { method: "POST", headers: { Authorization: `Bearer ${state.token}` }, body: formData });
  if (res.ok) {
    const user = await res.json();
    state.currentUser.profile_image = user.profile_image;
    showProfile();
    await loadRoster();
    await loadPosts();
    updateFeed();
  } else {
    const data = await res.json().catch(() => ({}));
    showErrorToast(data.detail || "Upload failed");
  }
}

// ---------- Reply Modal ----------
function openReplyModal(postId) {
  const post = state.posts.find((p) => p.id === postId);
  if (!post) return;
  state.replyModalOpen = true;
  state.replyPostId = postId;

  const originalDiv = document.getElementById("reply-original-post");

  let attachmentHtml = "";
  if (post.attachment_url) {
    if (post.attachment_type === "image") {
      attachmentHtml = `<div class="post-attachment"><img src="${post.attachment_url}" alt="${escapeHtml(post.attachment_filename)}"></div>`;
    } else if (post.attachment_type === "video") {
      attachmentHtml = `<div class="post-attachment"><video controls src="${post.attachment_url}"></video></div>`;
    } else {
      attachmentHtml = `<div class="post-attachment"><a class="file-card" href="${post.attachment_url}" target="_blank" download>📎 ${escapeHtml(post.attachment_filename)}</a></div>`;
    }
  }

  originalDiv.innerHTML = `
    <div class="reply-original">
      <div class="ro-meta">${avatarHtml(post.author, post.authorImage, 24)} <strong>${escapeHtml(post.author)}</strong> · ${timeAgo(post.createdAtIso)}</div>
      <div class="ro-text">${renderMessage(post.message, state.roster)}</div>
      ${attachmentHtml}
    </div>`;

  renderReplyComments(post);
  document.getElementById("reply-modal").classList.remove("hidden");
  document.getElementById("reply-send-btn").onclick = () => submitReply(postId);

  // You've now actually looked at this post, so any notification pointing
  // at it can be marked read. This only fires on a deliberate open, not on
  // the background poll -- see loadPosts(), which no longer does this.
  api("/notifications/read-visible", { method: "POST", body: JSON.stringify({ post_ids: [postId] }) })
    .then(() => { loadNotifs().then(() => { updateNotifBadge(); if (state.notifOpen) updateNotifPanel(); }); })
    .catch(() => {});
}

function renderReplyComments(post) {
  const commentsDiv = document.getElementById("reply-comments-list");
  const comments = post.comments || [];
  if (comments.length === 0) {
    commentsDiv.innerHTML = `<div style="color:var(--faint);font-size:13px;padding:10px 0;">No replies yet. Be the first!</div>`;
    return;
  }
  commentsDiv.innerHTML = `
    <div class="comments-list">
      ${comments.map((c) => `
        <div class="comment-item">
          ${avatarHtml(c.author.name, c.author.profile_image, 32)}
          <div class="comment-body">
            <div class="comment-header">
              <span class="comment-author">${escapeHtml(c.author.name)}</span>
              <span class="comment-time">${timeAgo(c.created_at)}</span>
            </div>
            <div class="comment-text">${escapeHtml(c.text)}</div>
          </div>
        </div>
      `).join("")}
    </div>`;
}

function closeReplyModal() {
  state.replyModalOpen = false;
  state.replyPostId = null;
  document.getElementById("reply-modal").classList.add("hidden");
  document.getElementById("reply-textarea").value = "";
}

async function submitReply(postId) {
  const textarea = document.getElementById("reply-textarea");
  const text = textarea.value.trim();
  if (!text) return;
  textarea.value = "";
  try {
    await api(`/posts/${postId}/comments`, { method: "POST", body: JSON.stringify({ text }) });
    await loadPosts();
    const post = state.posts.find((p) => p.id === postId);
    if (post) renderReplyComments(post);
  } catch (e) {
    showErrorToast(e.message);
  }
}

// ---------- Admin ----------
let adminTab = "add";

function showAdmin() {
  document.getElementById("admin-modal").classList.remove("hidden");
  wirePasswordHints("new-user-password", "new-user-pw-hints", null);
  switchAdminTab(adminTab);
}
function closeAdmin() { document.getElementById("admin-modal").classList.add("hidden"); }

function switchAdminTab(tab) {
  adminTab = tab;
  document.querySelectorAll(".admin-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
  document.querySelectorAll(".admin-tab-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== `tab-${tab}`));
  if (tab === "logs") loadAccessLog();
  if (tab === "manage") loadManageMembers();
}

async function addTeammate() {
  const name = document.getElementById("new-user-name").value.trim();
  const password = document.getElementById("new-user-password").value;
  const department = document.getElementById("new-user-dept").value;

  if (!name || !isPasswordValid(password)) return showErrorToast("Name is required, and the password must meet all requirements above.");

  state.errorMsg = "";
  try {
    await api("/users/", { method: "POST", body: JSON.stringify({ name, password, department: department || null }) });
    document.getElementById("new-user-name").value = "";
    document.getElementById("new-user-password").value = "";
    document.getElementById("new-user-dept").value = "";
    document.getElementById("new-user-pw-hints").innerHTML = passwordChecklistHtml("");
    await loadRoster();
    await loadPosts();
    updateFeed();
    showErrorToast("Team member added successfully");
  } catch (e) {
    showErrorToast(e.message);
  }
}

async function loadAccessLog() {
  const res = await fetch(`${API}/admin/access-log`, { headers: { Authorization: `Bearer ${state.token}` } });
  const logs = await res.json();
  const container = document.getElementById("access-log");
  container.innerHTML = logs.map((log) => `
    <div class="log-entry">
      <span>${new Date(log.created_at).toLocaleString()}</span>
      <strong>${escapeHtml(log.actor_name)}</strong> — ${escapeHtml(log.event)}${!log.ok ? ' <span style="color:var(--urgent)">(failed)</span>' : ""}
    </div>`).join("");
}

async function loadManageMembers() {
  try {
    const users = await api("/admin/users");
    const container = document.getElementById("manage-members-list");
    const depts = ["", ...DEPARTMENTS];
    container.innerHTML = users.map((u) => `
      <div class="manage-item">
        ${avatarHtml(u.name, u.profile_image, 36)}
        <div class="minfo">
          <div class="mname">${escapeHtml(u.name)} ${u.is_admin ? '<span class="roster-admin-badge">Admin</span>' : ""}</div>
          <div class="mhandle">@${escapeHtml(u.handle)} • ${u.department || "No dept"}</div>
        </div>
        <div class="manage-actions">
          <select onchange="changeUserDept(${u.id}, this.value)">
            ${depts.map((d) => `<option value="${d}" ${u.department === d ? "selected" : ""}>${d || "No Dept"}</option>`).join("")}
          </select>
          <button class="btn-small" onclick="resetUserPassword(${u.id})">Reset PW</button>
          ${u.id !== state.currentUser.id ? `<button class="btn-danger" onclick="removeUser(${u.id})">Remove</button>` : ""}
        </div>
      </div>`).join("");
  } catch (e) {
    showErrorToast("Failed to load members");
  }
}

async function changeUserDept(userId, dept) {
  try {
    await api(`/admin/users/${userId}/department`, { method: "PUT", body: JSON.stringify({ department: dept || null }) });
    await loadRoster();
    await loadPosts();
    updateFeed();
    updateDepartments();
    loadManageMembers();
  } catch (e) {
    showErrorToast(e.message);
  }
}

async function resetUserPassword(userId) {
  const pw = prompt("Enter new password (10+ characters, upper + lower + number + symbol):");
  if (!pw) return;
  if (!isPasswordValid(pw)) return showErrorToast("Password doesn't meet the requirements.");
  try {
    await api(`/admin/users/${userId}/reset-password`, { method: "PUT", body: JSON.stringify({ new_password: pw }) });
    showErrorToast("Password reset successfully");
  } catch (e) {
    showErrorToast(e.message);
  }
}

async function removeUser(userId) {
  if (!confirm("Are you sure you want to remove this user?")) return;
  try {
    await api(`/admin/users/${userId}`, { method: "DELETE" });
    await loadRoster();
    await loadPosts();
    updateFeed();
    loadManageMembers();
  } catch (e) {
    showErrorToast(e.message);
  }
}

// ---------- render ----------
function render() {
  if (state.screen === "loading") return renderLoading();
  if (state.screen === "setup") return renderSetup();
  if (state.screen === "login") return renderLogin();
  if (state.screen === "password") return renderPassword();
  if (state.screen === "addTeammate") return renderAddTeammate();
  if (state.screen === "mfaEnroll") return renderMfaEnroll();
  if (state.screen === "mfaChallenge") return renderMfaChallenge();
  if (state.screen === "mfaRecoveryCodes") return renderMfaRecoveryCodes();
  if (state.screen === "board") return renderBoard();
}

function renderLoading() {
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel narrow" style="text-align:center;">
        <div class="spinner" style="width:26px;height:26px;margin-bottom:14px;"></div>
        <div style="color:var(--muted);font-size:.85rem;">Loading Jerry Agu's Dashboard…</div>
      </div>
    </div>`;
}

function renderSetup() {
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="ghost-word">JERRY</div>
    <div class="auth-stage">
      <div class="auth-panel">
        <div class="eyebrow">SET UP YOUR BOARD</div>
        <div class="headline">Welcome to <span class="accent">Jerry Agu's</span> Dashboard.</div>
        <div class="lede">Create the first account. You'll be the administrator for your workspace.</div>
        ${wordRowHtml()}
        <div class="section-mark"><div class="rule"></div></div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}
        <input class="field" id="su-name" placeholder="Your full name" />
        <input class="field" id="su-pass" type="password" placeholder="Choose a password" />
        <div id="su-pw-hints"></div>
        <select class="field" id="su-dept">${deptOptionsHtml("")}</select>
        <button class="btn-primary" id="su-submit">Create account &amp; continue</button>
      </div>
    </div>`;
  wirePasswordHints("su-pass", "su-pw-hints", "su-submit");
  document.getElementById("su-submit").onclick = () => {
    const name = document.getElementById("su-name").value;
    const password = document.getElementById("su-pass").value;
    const dept = document.getElementById("su-dept").value;
    if (name.trim() && isPasswordValid(password)) submitBootstrap(name, password, dept);
  };
}

function renderLogin() {
  const rosterHtml = state.publicRoster.map((m) => `
    <button class="user-card" data-handle="${escapeHtml(m.handle)}">
      ${avatarHtml(m.name, m.profile_image, 40)}
      <div style="min-width:0">
        <div class="uc-name">${escapeHtml(m.name)}</div>
        <div class="uc-handle">@${escapeHtml(m.handle)}</div>
        ${m.is_admin ? '<span class="role-badge admin">Admin</span>' : ""}
      </div>
      <svg class="uc-arrow" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
    </button>`).join("");

  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="ghost-word">JERRY</div>
    <div class="auth-stage">
      <div class="auth-panel wide">
        <div class="eyebrow">WHO'S THIS?</div>
        <div class="headline">A space for people, <span class="accent">ideas</span> &amp; communication.</div>
        ${wordRowHtml()}
        <div class="section-mark"><div class="rule"></div></div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}
        <div class="user-grid">${rosterHtml}</div>
      </div>
    </div>`;
  app.querySelectorAll(".user-card").forEach((btn) => {
    btn.onclick = () => {
      const handle = btn.dataset.handle;
      const member = state.publicRoster.find((m) => m.handle === handle);
      pickLoginUser(member);
    };
  });
}

function renderPassword() {
  const m = state.selectedForLogin;
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel narrow" style="text-align:center;">
        <button class="back-link" id="back-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          Back
        </button>
        ${avatarHtml(m.name, m.profile_image, 76, "pin-avatar-lg")}
        <div class="eyebrow" style="justify-content:center;display:flex;">AUTHENTICATION</div>
        <div class="headline" style="font-size:1.4rem;">Welcome back, ${escapeHtml(m.name.split(" ")[0])}.</div>
        <div class="lede" style="margin:0 auto 20px;">Enter your credentials to access the dashboard.</div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}
        <div class="field-wrap">
          <input class="field" id="pw-input" type="password" placeholder="Enter your password" autofocus />
          <button class="field-icon-btn" id="pw-toggle" type="button" title="Show/Hide">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          </button>
        </div>
        <button class="btn-primary" id="pw-submit">Continue →</button>
      </div>
    </div>`;
  document.getElementById("back-btn").onclick = () => { state.screen = "login"; state.errorMsg = ""; render(); };
  const input = document.getElementById("pw-input");
  input.focus();
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submitLogin(input.value); });
  document.getElementById("pw-submit").onclick = () => submitLogin(input.value);
  document.getElementById("pw-toggle").onclick = () => { input.type = input.type === "password" ? "text" : "password"; };
}

function renderAddTeammate() {
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel">
        <button class="back-link" id="back-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          Back to board
        </button>
        <div class="eyebrow">ADD A TEAMMATE</div>
        <div class="headline" style="font-size:1.6rem;">Invite someone to the workspace.</div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}
        <input class="field" id="nt-name" placeholder="Full name" style="margin-top:14px;" />
        <input class="field" id="nt-pass" type="password" placeholder="Set a password" />
        <div id="nt-pw-hints"></div>
        <select class="field" id="nt-dept">${deptOptionsHtml("")}</select>
        <button class="btn-primary" id="nt-submit">Add teammate</button>
      </div>
    </div>`;
  wirePasswordHints("nt-pass", "nt-pw-hints", "nt-submit");
  document.getElementById("back-btn").onclick = () => { state.screen = "board"; state.errorMsg = ""; render(); };
  document.getElementById("nt-submit").onclick = () => {
    const name = document.getElementById("nt-name").value;
    const password = document.getElementById("nt-pass").value;
    const dept = document.getElementById("nt-dept").value;
    if (name.trim() && isPasswordValid(password)) submitAddTeammate(name, password, dept);
  };
}

// ---------- MFA: enrollment screen ----------
function renderMfaEnroll() {
  const m = state.selectedForLogin;
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel">
        <button class="back-link" id="back-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          Back
        </button>
        <div class="eyebrow">SECURE YOUR ACCOUNT</div>
        <div class="headline" style="font-size:1.6rem;">Set up your authenticator${m ? `, ${escapeHtml(m.name.split(" ")[0])}` : ""}.</div>
        <div class="lede">Every account on this board now requires an authenticator app. Scan the code below, then enter the 6-digit number it shows you.</div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}

        ${state.mfaQr ? `
          <div class="mfa-qr-wrap">
            <img src="${state.mfaQr}" alt="MFA QR code" class="mfa-qr-img" />
          </div>
          <div class="mfa-secret-fallback">
            <div class="mfa-secret-label">Can't scan it? Enter this key manually:</div>
            <div class="mfa-secret-value" id="mfa-secret-text">${escapeHtml(state.mfaSecret)}</div>
          </div>
        ` : `<div class="lede">Generating your QR code…</div>`}

        <div class="section-mark"><div class="rule"></div></div>
        <input class="field mfa-code-input" id="mfa-enroll-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" />
        <button class="btn-primary" id="mfa-confirm-submit">Verify &amp; enable MFA</button>
      </div>
    </div>`;

  document.getElementById("back-btn").onclick = backToLoginFromMfa;
  const codeInput = document.getElementById("mfa-enroll-code");
  codeInput.focus();
  const submit = () => { if (codeInput.value.trim().length >= 6) submitMfaConfirm(codeInput.value.trim()); };
  codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  document.getElementById("mfa-confirm-submit").onclick = submit;
}

// ---------- MFA: recovery codes (shown once, right after enrollment) ----------
function renderMfaRecoveryCodes() {
  const codes = state.mfaRecoveryCodes || [];
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel">
        <div class="eyebrow">SAVE YOUR RECOVERY CODES</div>
        <div class="headline" style="font-size:1.6rem;">MFA is enabled. One more thing.</div>
        <div class="lede">Each code below works once, and gets you in if you ever lose access to your authenticator app. Save them somewhere safe — you won't see them again.</div>
        <div class="warning-banner">⚠ These will not be shown again after you continue.</div>
        <div class="recovery-code-grid">
          ${codes.map((c) => `<div class="recovery-code-item">${escapeHtml(c)}</div>`).join("")}
        </div>
        <button class="btn-primary" id="recovery-continue-btn" style="margin-top:18px;">I've saved these — continue</button>
      </div>
    </div>`;
  document.getElementById("recovery-continue-btn").onclick = continueAfterRecoveryCodes;
}

// ---------- MFA: login challenge (already-enrolled account) ----------
function renderMfaChallenge() {
  const m = state.selectedForLogin;
  const isRecovery = state.mfaChallengeMode === "recovery";
  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="auth-stage">
      <div class="auth-panel narrow" style="text-align:center;">
        <button class="back-link" id="back-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          Back
        </button>
        ${m ? avatarHtml(m.name, m.profile_image, 64, "pin-avatar-lg") : ""}
        <div class="eyebrow" style="justify-content:center;display:flex;">TWO-FACTOR CHECK</div>
        <div class="headline" style="font-size:1.4rem;">${isRecovery ? "Enter a recovery code." : "Enter your 6-digit code."}</div>
        <div class="lede" style="margin:0 auto 20px;">${isRecovery ? "Use one of the one-time recovery codes you saved during setup." : "Open your authenticator app and type the current code."}</div>
        ${state.errorMsg ? `<div class="error-banner">${escapeHtml(state.errorMsg)}</div>` : ""}

        ${isRecovery
          ? `<input class="field" id="mfa-verify-input" placeholder="XXXXX-XXXXX" autofocus />`
          : `<input class="field mfa-code-input" id="mfa-verify-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" autofocus />`}
        <button class="btn-primary" id="mfa-verify-submit">Continue →</button>
        <button class="back-link" id="mfa-mode-toggle" style="margin:16px auto 0;display:block;">
          ${isRecovery ? "Use my authenticator app instead" : "Use a recovery code instead"}
        </button>
      </div>
    </div>`;

  document.getElementById("back-btn").onclick = backToLoginFromMfa;
  const input = document.getElementById("mfa-verify-input");
  input.focus();
  const submit = () => { if (input.value.trim()) submitMfaVerify(input.value.trim()); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  document.getElementById("mfa-verify-submit").onclick = submit;
  document.getElementById("mfa-mode-toggle").onclick = () => {
    state.mfaChallengeMode = isRecovery ? "code" : "recovery";
    state.errorMsg = "";
    render();
  };
}

// ---------- BOARD ----------
function renderBoard() {
  const unread = state.notifs.filter((n) => !n.read).length;
  const hasUrgentUnread = state.notifs.some((n) => !n.read && n.tier === "urgent");
  const first = (state.currentUser.name || "").split(" ")[0];

  app.innerHTML = `
    ${bubbleFieldHtml()}
    <div class="dashboard">
      <div class="top-nav">
        <div class="brand"><div class="brand-mark">JA</div> Jerry Agu's Dashboard</div>
        <div class="nav-actions">
          ${state.currentUser.isAdmin ? `<button class="nav-btn" id="add-teammate-header" title="Add teammate">${userPlusSvg()}</button>` : ""}
          <button class="nav-btn" id="dm-btn" title="Messages" onclick="openDMs()">${messageSvg()}</button>
          <div class="notif-panel-wrap">
            <button class="nav-btn" id="notif-btn" title="Notifications">
              ${bellSvg()}<span id="notif-badge" class="badge ${unread > 0 ? "" : "hidden"}">${unread > 99 ? "99+" : unread}</span>
            </button>
            <div id="notif-panel" class="hidden"></div>
          </div>
          <button class="nav-btn" id="profile-btn" title="Profile" onclick="showProfile()">${avatarHtml(state.currentUser.name, state.currentUser.profile_image, 30)}</button>
          ${state.currentUser.isAdmin ? `<button class="nav-btn" id="admin-btn" title="Admin" onclick="showAdmin()">${shieldSvg()}</button>` : ""}
          <button class="nav-btn" id="logout-btn" title="Log out">${logoutSvg()}</button>
        </div>
      </div>

      <div class="greeting-block">
        <div class="eyebrow">TODAY</div>
        <div class="headline">Good to see you, <span class="accent">${escapeHtml(first)}</span>.</div>
        <div class="lede">Posting to <strong>${escapeHtml(state.currentDept || "General")}</strong>.</div>
      </div>

      <div class="composer">
        <div class="composer-row">
          ${avatarHtml(state.currentUser.name, state.currentUser.profile_image, 40)}
          <div class="composer-input-wrap">
            <textarea id="msg-input" rows="1" placeholder="What's happening? Use @handle to mention…"></textarea>
            <div id="mention-dropdown"></div>
          </div>
        </div>
        <div class="composer-extras">
          <label class="attach-btn ${state.attachment ? "has-file" : ""}">
            <input type="file" id="attach-input" style="display:none" accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip" onchange="handleAttachmentUpload(this)">
            ${paperclipSvg()} ${state.attachment ? "1 file" : "Attach"}
          </label>
        </div>
        <div id="attachment-preview"></div>
        <div class="composer-footer">
          <div class="tier-toggle">
            <button class="tier-btn urgent ${state.tier === "urgent" ? "selected" : ""}" data-tier="urgent">${alertSvg()} Urgent</button>
            <button class="tier-btn update ${state.tier === "update" ? "selected" : ""}" data-tier="update">${megaphoneSvg()} Update</button>
          </div>
          <button class="post-btn" id="post-btn" disabled>Post</button>
        </div>
      </div>

      <div class="eyebrow" style="margin-bottom:10px;">DEPARTMENTS</div>
      <div class="dept-row" id="dept-list"></div>

      <div class="eyebrow" style="margin-bottom:14px;">ANNOUNCEMENTS</div>
      <div class="feed-filters">
        <button class="filter-btn ${state.filter === "all" ? "active" : ""}" data-filter="all">All</button>
        <button class="filter-btn ${state.filter === "urgent" ? "active" : ""}" data-filter="urgent">Urgent</button>
        <button class="filter-btn ${state.filter === "update" ? "active" : ""}" data-filter="update">Updates</button>
        <button class="archive-toggle" id="archive-btn">${state.showArchive ? "← Back to board" : "🗄 Archive"}</button>
      </div>

      <div id="feed"></div>

      <div class="footer-note">Type @ to tag a teammate, or @all for everyone. Posts older than ${ARCHIVE_DAYS} days move to the archive.</div>
    </div>`;

  dom.msgInput = document.getElementById("msg-input");
  dom.postBtn = document.getElementById("post-btn");
  dom.feed = document.getElementById("feed");
  dom.notifBadge = document.getElementById("notif-badge");
  dom.notifBtn = document.getElementById("notif-btn");
  dom.notifPanel = document.getElementById("notif-panel");
  dom.mentionDropdown = document.getElementById("mention-dropdown");
  dom.deptList = document.getElementById("dept-list");

  dom.msgInput.addEventListener("input", onComposerInput);
  dom.msgInput.addEventListener("keydown", onComposerKeydown);
  dom.postBtn.onclick = handlePost;

  document.getElementById("notif-btn").onclick = toggleNotifications;
  document.getElementById("logout-btn").onclick = logout;
  document.getElementById("archive-btn").onclick = () => {
    state.showArchive = !state.showArchive;
    updateFeed();
    document.getElementById("archive-btn").innerHTML = state.showArchive ? "← Back to board" : "🗄 Archive";
  };

  const addTeammateBtn = document.getElementById("add-teammate-header");
  if (addTeammateBtn) addTeammateBtn.onclick = () => { state.screen = "addTeammate"; state.errorMsg = ""; render(); };

  app.querySelectorAll("[data-filter]").forEach((btn) => { btn.onclick = () => { state.filter = btn.dataset.filter; updateFeed(); updateFilterButtons(); }; });
  app.querySelectorAll("[data-tier]").forEach((btn) => { btn.onclick = () => { state.tier = btn.dataset.tier; updateTierButtons(); }; });

  const draft = localStorage.getItem("officeboard_draft");
  if (draft) {
    dom.msgInput.value = draft;
    state.message = draft;
    updatePostButton();
    autoResizeTextarea();
  }

  updateFeed();
  updateDepartments();
  updateNotifBadge();
  updateAttachmentPreview();
  loadDMs().catch(() => {});
}

// ---------- Composer handlers ----------
function onComposerInput(e) {
  const val = e.target.value;
  state.message = val;
  localStorage.setItem("officeboard_draft", val);
  updatePostButton();
  autoResizeTextarea();
  updateMentionDropdown();
}
function onComposerKeydown(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    const dropdown = document.getElementById("mention-dropdown");
    if (dropdown && dropdown.children.length === 0) { e.preventDefault(); handlePost(); }
  }
}
function autoResizeTextarea() {
  const el = dom.msgInput;
  if (!el) return;
  el.rows = el.value ? 3 : 1;
}
function updatePostButton() {
  if (dom.postBtn) dom.postBtn.disabled = !dom.msgInput.value.trim() && !state.attachment;
}
function updateTierButtons() {
  document.querySelectorAll("[data-tier]").forEach((btn) => btn.classList.toggle("selected", btn.dataset.tier === state.tier));
}
function updateFilterButtons() {
  document.querySelectorAll("[data-filter]").forEach((btn) => btn.classList.toggle("active", btn.dataset.filter === state.filter));
}

function updateMentionDropdown() {
  const val = dom.msgInput.value;
  const lastAt = val.lastIndexOf("@");
  if (lastAt === -1) { dom.mentionDropdown.innerHTML = ""; return; }
  const after = val.slice(lastAt + 1);
  if (/\s/.test(after)) { dom.mentionDropdown.innerHTML = ""; return; }

  const query = after.toLowerCase();
  const suggestions = [
    ...("all".startsWith(query) ? [{ name: "Everyone", handle: "all", isAll: true }] : []),
    ...state.roster.filter((r) => r.handle.toLowerCase().startsWith(query) && r.handle !== state.currentUser.handle),
  ];
  if (suggestions.length === 0) { dom.mentionDropdown.innerHTML = ""; return; }

  dom.mentionDropdown.innerHTML = `
    <div class="mention-dropdown">
      ${suggestions.map((r) => `
        <button class="mention-option" data-handle="${escapeHtml(r.handle)}">
          <div class="avatar" style="width:22px;height:22px;font-size:10px;background:${r.isAll ? "var(--blue)" : colorFor(r.name)}">${r.isAll ? "@" : initials(r.name)}</div>
          <span class="m-name">${escapeHtml(r.name)}</span>
          ${r.isAdmin ? `<span class="admin-badge" title="Admin">Admin</span>` : ""}
          <span class="m-handle">@${escapeHtml(r.handle)}</span>
        </button>`).join("")}
    </div>`;

  dom.mentionDropdown.querySelectorAll(".mention-option").forEach((btn) => {
    btn.onclick = () => {
      const handle = btn.dataset.handle;
      const before = val.slice(0, lastAt);
      const afterQuery = val.slice(lastAt + 1 + query.length);
      const newVal = `${before}@${handle} ${afterQuery}`;
      dom.msgInput.value = newVal;
      dom.msgInput.focus();
      state.message = newVal;
      localStorage.setItem("officeboard_draft", newVal);
      dom.mentionDropdown.innerHTML = "";
      updatePostButton();
      autoResizeTextarea();
    };
  });
}

// ---------- Feed ----------
function updateFeed() {
  const cutoff = Date.now() - ARCHIVE_DAYS * 24 * 60 * 60 * 1000;
  const active = state.posts.filter((p) => p.ts >= cutoff);
  const archived = state.posts.filter((p) => p.ts < cutoff);
  const pool = state.showArchive ? archived : active;
  const visible = pool.filter((p) => state.filter === "all" || p.tier === state.filter).sort((a, b) => b.ts - a.ts);

  if (!dom.feed) return;
  if (visible.length === 0) {
    dom.feed.innerHTML = `<div class="feed-empty">${state.showArchive ? "Nothing archived yet." : "No posts yet."}</div>`;
    return;
  }
  dom.feed.innerHTML = visible.map((p) => postCardHtml(p)).join("");
  dom.feed.querySelectorAll("[data-delete]").forEach((btn) => { btn.onclick = () => handleDelete(parseInt(btn.dataset.delete)); });
  dom.feed.querySelectorAll("[data-reply]").forEach((btn) => { btn.onclick = () => openReplyModal(parseInt(btn.dataset.reply)); });
}

function postCardHtml(p) {
  const canDelete = p.authorHandle === state.currentUser.handle || state.currentUser.isAdmin;
  const urgentClass = p.tier === "urgent" ? "urgent-post" : "";

  let attachmentHtml = "";
  if (p.attachment_url) {
    if (p.attachment_type === "image") {
      attachmentHtml = `<div class="post-attachment"><img src="${p.attachment_url}" alt="${escapeHtml(p.attachment_filename)}"></div>`;
    } else if (p.attachment_type === "video") {
      attachmentHtml = `<div class="post-attachment"><video controls src="${p.attachment_url}"></video></div>`;
    } else {
      attachmentHtml = `<div class="post-attachment"><a class="file-card" href="${p.attachment_url}" target="_blank" download>📎 ${escapeHtml(p.attachment_filename)}</a></div>`;
    }
  }

  const replyCount = (p.comments || []).length;

  return `
    <div class="post-card ${urgentClass}">
      <div class="post-row">
        ${avatarHtml(p.author, p.authorImage, 40)}
        <div class="post-body">
          <div class="post-meta">
            <span class="post-author">${escapeHtml(p.author)}</span>
            ${p.authorIsAdmin ? `<span class="admin-badge" title="Admin">Admin</span>` : ""}
            <span class="post-tag ${p.tier}">${p.tier}</span>
            ${p.department ? `<span class="post-dept-tag">${escapeHtml(p.department)}</span>` : ""}
            <span class="post-time">· ${timeAgo(p.createdAtIso)}</span>
          </div>
          <div class="post-text">${renderMessage(p.message, state.roster)}</div>
          ${attachmentHtml}
          <div class="post-actions">
            <button class="reply-link" data-reply="${p.id}">Reply ${replyCount > 0 ? `(${replyCount})` : ""}</button>
          </div>
        </div>
        ${canDelete ? `<button class="delete-btn" data-delete="${p.id}" title="Remove">${trashSvg()}</button>` : ""}
      </div>
    </div>`;
}

// ---------- Departments ----------
function updateDepartments() {
  const depts = new Set();
  state.roster.forEach((u) => { if (u.department) depts.add(u.department); });
  if (state.currentUser.department) depts.add(state.currentUser.department);
  const sorted = Array.from(depts).sort();

  let visibleDepts = sorted;
  if (!state.currentUser.isAdmin && state.currentUser.department) {
    visibleDepts = sorted.filter((d) => d === state.currentUser.department);
  } else if (!state.currentUser.isAdmin) {
    visibleDepts = [];
  }

  let html = `<button class="dept-pill ${state.currentDept === "" ? "active" : ""}" data-dept="">All Updates</button>`;
  visibleDepts.forEach((dept) => {
    // Uses state.deptCounts (always computed from an UNFILTERED post list,
    // see loadDeptCounts) rather than state.posts, which is scoped to
    // whichever department is currently selected -- reading counts from
    // state.posts was why every pill except the one you were viewing
    // showed 0 until something else happened to reload the full list.
    const count = state.deptCounts[dept] || 0;
    html += `<button class="dept-pill ${state.currentDept === dept ? "active" : ""}" data-dept="${escapeHtml(dept)}">${escapeHtml(dept)}<span class="cnt">${count}</span></button>`;
  });
  if (dom.deptList) dom.deptList.innerHTML = html;
  dom.deptList.querySelectorAll(".dept-pill").forEach((btn) => { btn.onclick = () => filterDept(btn.dataset.dept); });
}

async function filterDept(dept) {
  state.currentDept = dept;
  state.showArchive = false;
  await loadPosts();
  updateFeed();
  updateDepartments();
  const lede = document.querySelector(".greeting-block .lede");
  if (lede) lede.innerHTML = `Posting to <strong>${escapeHtml(dept || "General")}</strong>.`;
}

// ---------- Notifications ----------
function updateNotifBadge() {
  if (!dom.notifBadge) return;
  const unreadNotifs = state.notifs.filter((n) => !n.read);
  const unread = unreadNotifs.length;

  if (unread > 0) {
    dom.notifBadge.textContent = unread > 99 ? "99+" : unread;
    dom.notifBadge.classList.remove("hidden");
    if (unread > state.lastNotifCount) playDing();
  } else {
    dom.notifBadge.classList.add("hidden");
  }
  state.lastNotifCount = unread;
}

function toggleNotifications() {
  state.notifOpen = !state.notifOpen;
  if (dom.notifPanel) dom.notifPanel.classList.toggle("hidden", !state.notifOpen);
  if (state.notifOpen) { updateNotifPanel(); markAllRead(); }
}

function updateNotifPanel() {
  if (!dom.notifPanel || !state.notifOpen) return;
  const unread = state.notifs.filter((n) => !n.read).length;

  dom.notifPanel.innerHTML = `
    <div class="notif-panel">
      <div class="notif-panel-header">
        <div class="notif-panel-title">Notifications</div>
        ${unread > 0 ? `<button class="notif-mark-read" id="mark-read-btn">Mark all read</button>` : ""}
      </div>
      ${state.notifs.length === 0
        ? `<div class="notif-empty">No notifications yet.</div>`
        : `<div class="notif-list">${state.notifs.map((n) => `
            <div class="notif-item ${n.read ? "" : "unread"} ${n.tier === "urgent" ? "urgent" : ""}">
              <div>
                <span class="n-from">${escapeHtml(n.from)}</span>
                ${n.tier === "urgent" ? `<span class="urgent-badge">${alertSvg()} Urgent</span>` : ""}
                ${n.isMention ? '<span class="mention-badge">@mention</span>' : "<span></span>"}
              </div>
              <div class="n-preview">${escapeHtml(n.preview || "")}</div>
              <div class="n-time">${timeAgo(n.createdAtIso)} ago</div>
            </div>`).join("")}</div>`}
    </div>`;

  const mrBtn = document.getElementById("mark-read-btn");
  if (mrBtn) mrBtn.onclick = markAllRead;
}

function playDing() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.4);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.4);
  } catch (e) {}
}

function showErrorToast(msg) {
  const existing = document.querySelector(".error-toast");
  if (existing) existing.remove();
  const div = document.createElement("div");
  div.className = "error-toast";
  div.textContent = msg;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}
// ---------- Active sessions (profile modal) ----------
function deviceLabel(ua) {
  ua = (ua || "").toLowerCase();
  if (ua.includes("windows")) return "Windows";
  if (ua.includes("mac os") || ua.includes("macintosh")) return "Mac";
  if (ua.includes("android")) return "Android";
  if (ua.includes("iphone") || ua.includes("ipad")) return "iOS";
  if (ua.includes("linux")) return "Linux";
  return "Unknown device";
}

async function loadSessions() {
  // Container is injected after the dept line so index.html doesn't need
  // any change — the whole feature ships from this file alone.
  let box = document.getElementById("profile-sessions");
  if (!box) {
    const anchor = document.getElementById("profile-dept");
    if (!anchor) return;
    box = document.createElement("div");
    box.id = "profile-sessions";
    anchor.insertAdjacentElement("afterend", box);
  }
  box.innerHTML = `<div style="color:var(--muted);font-size:.8rem;padding:6px 0;">Loading sessions…</div>`;
  let sessions;
  try {
    sessions = await api("/auth/sessions");
  } catch (e) {
    box.innerHTML = "";
    return;
  }
  const rows = sessions.map((s) => `
    <div class="manage-item">
      ${avatarHtml(deviceLabel(s.user_agent), null, 36)}
      <div class="minfo">
        <div class="mname">${escapeHtml(deviceLabel(s.user_agent))} ${s.current ? '<span class="roster-admin-badge">This device</span>' : ""}</div>
        <div class="mhandle">${escapeHtml(s.ip_address || "unknown IP")} · active ${timeAgo(s.last_seen_at || s.created_at)}</div>
      </div>
      <div class="manage-actions">
        ${s.current ? "" : `<button class="btn-small" onclick="revokeSession(${s.id})">Sign out</button>`}
      </div>
    </div>`).join("");
  const others = sessions.filter((s) => !s.current).length;
  box.innerHTML = `
    <div class="eyebrow" style="margin:16px 0 8px;">ACTIVE SESSIONS</div>
    ${rows || `<div style="color:var(--muted);font-size:.85rem;">No active sessions.</div>`}
    ${others > 0 ? `<button class="btn-small" style="margin-top:10px;" onclick="revokeOtherSessions()">Sign out of all other devices</button>` : ""}`;
}

async function revokeSession(id) {
  try {
    await api(`/auth/sessions/${id}/revoke`, { method: "POST" });
    showErrorToast("Session signed out");
    loadSessions();
  } catch (e) {
    showErrorToast(e.message);
  }
}

async function revokeOtherSessions() {
  try {
    await api("/auth/sessions/revoke-all", { method: "POST" });
    showErrorToast("All other sessions signed out");
    loadSessions();
  } catch (e) {
    showErrorToast(e.message);
  }
}

// ---------- icons ----------
function messageSvg() { return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`; }
function bellSvg() { return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`; }
function logoutSvg() { return `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`; }
function alertSvg() { return `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`; }
function megaphoneSvg() { return `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5v12L3 13v-2z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>`; }
function trashSvg() { return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`; }
function userPlusSvg() { return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>`; }
function shieldSvg() { return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`; }
function paperclipSvg() { return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>`; }

init();