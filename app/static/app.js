"use strict";
(() => {
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
// Safe DOM builder: user data only ever goes through text nodes -> XSS-safe
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k === "style") Object.assign(n.style, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}
async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "flatmatch" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => (d.loc ? d.loc[d.loc.length-1] + ": " : "") + d.msg).join(", ");
    const e = new Error(msg || "Something went wrong"); e.status = res.status; throw e;
  }
  return data;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2400); }
const COLORS = ["#ff4f6d,#ff8a3d","#7b61ff,#2f9bff","#00b894,#55efc4","#e84393,#fd79a8","#0984e3,#74b9ff","#e17055,#fdcb6e","#6c5ce7,#a29bfe"];
const grad = (id) => `linear-gradient(160deg,${COLORS[id % COLORS.length]})`;
const initials = (n) => (n || "?").trim().charAt(0).toUpperCase();
const rs = (n) => "Rs " + Number(n).toLocaleString("en-IN");
function show(id) { for (const s of ["#auth","#main","#chat"]) $(s).classList.toggle("hidden", s !== id); }

// ---------- AUTH ----------
let authMode = "login";
$$(".tab").forEach(b => b.addEventListener("click", () => {
  authMode = b.dataset.auth; $$(".tab").forEach(x => x.classList.toggle("active", x === b));
  $("#authForm").classList.toggle("signup", authMode === "signup");
  $("#authBtn").textContent = authMode === "signup" ? "Create account" : "Log in"; $("#authErr").textContent = "";
}));
$("#authForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = new FormData(e.target); $("#authErr").textContent = "";
  try {
    if (authMode === "signup") await api("/signup", { method: "POST", body: { name: f.get("name"), age: Number(f.get("age")), email: f.get("email"), password: f.get("password") } });
    else await api("/login", { method: "POST", body: { email: f.get("email"), password: f.get("password") } });
    e.target.reset(); boot();
  } catch (err) { $("#authErr").textContent = err.message; }
});

// ---------- NAV ----------
let me = null;
function go(view) {
  $$(".bottomnav button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "v-" + view));
  $("#topTitle").textContent = { discover: "Discover", matches: "Matches", profile: "My Profile" }[view];
  $("#filterBtn").style.visibility = view === "discover" ? "visible" : "hidden";
  if (view === "matches") loadMatches(); if (view === "profile") fillProfile();
}
$$(".bottomnav button").forEach(b => b.addEventListener("click", () => go(b.dataset.view)));
$("#filterBtn").addEventListener("click", () => $("#filters").classList.toggle("hidden"));
$("#fApply").addEventListener("click", () => { $("#filters").classList.add("hidden"); loadDeck(); });

// ---------- DECK ----------
let cards = [];
function tagList(u) {
  const t = [];
  if (u.area || u.city) t.push([u.area, u.city].filter(Boolean).join(", "));
  if (u.budget_max) t.push(rs(u.budget_min) + " - " + rs(u.budget_max));
  if (u.move_in) t.push("Move-in " + new Date(u.move_in).toLocaleDateString("en-IN", { day: "numeric", month: "short" }));
  t.push(u.smoking ? "Smoker" : "Non-smoker"); if (u.drinks) t.push("Drinks"); if (u.pets) t.push("Pet friendly");
  if (u.veg) t.push("Vegetarian"); t.push("Cleanliness " + u.cleanliness + "/5");
  t.push({ early: "Early bird", late: "Night owl", flexible: "Flexible sleep" }[u.sleep]);
  t.push("Guests: " + u.guests);
  return t;
}
function cardEl(u) {
  const hero = el("div", { class: "hero", style: { background: grad(u.id) } },
    el("div", { class: "avatar" }, initials(u.name)),
    el("span", { class: "score" }, u.score + "% match"),
    u.superliked_you ? el("span", { class: "superflag" }, "Super liked you") : null,
    el("h2", {}, u.name + " ", el("small", {}, u.age)),
    el("div", { class: "sub" }, (u.looking === "roommate" ? "Has a room" : "Looking for a room") + (u.occupation ? " · " + u.occupation : "")));
  const info = el("div", { class: "info" }, u.bio ? el("p", { class: "bio" }, u.bio) : null,
    el("div", { class: "tags" }, ...tagList(u).map(x => el("span", { class: "tag" }, x))));
  const c = el("div", { class: "card" }, el("div", { class: "stamp like" }, "LIKE"), el("div", { class: "stamp nope" }, "NOPE"),
    el("div", { class: "stamp sup" }, "SUPER"), hero, info);
  c._u = u; return c;
}
function renderDeck() {
  const d = $("#deck"); d.replaceChildren();
  cards.slice(0, 3).reverse().forEach((u, i, arr) => {
    const c = cardEl(u); const depth = arr.length - 1 - i;
    c.style.transform = `scale(${1 - depth * 0.04}) translateY(${depth * 10}px)`;
    d.append(c); if (depth === 0) attachDrag(c);
  });
  $("#deckEmpty").classList.toggle("hidden", cards.length > 0);
  $(".actions").style.visibility = cards.length ? "visible" : "hidden";
}
async function loadDeck() {
  const q = new URLSearchParams(); const city = $("#fCity").value.trim(), b = $("#fBudget").value;
  if (city) q.set("city", city); if (b) q.set("max_budget", b);
  try { cards = await api("/deck?" + q); renderDeck(); } catch (e) { handleErr(e); }
}
function attachDrag(c) {
  let sx = 0, sy = 0, dx = 0, dy = 0, drag = false;
  const stamps = { like: c.querySelector(".stamp.like"), nope: c.querySelector(".stamp.nope"), sup: c.querySelector(".stamp.sup") };
  c.addEventListener("pointerdown", (e) => { const inf = e.target.closest(".info"); if (inf && inf.scrollHeight > inf.clientHeight) return; drag = true; sx = e.clientX; sy = e.clientY; c.setPointerCapture(e.pointerId); c.style.transition = "none"; });
  c.addEventListener("pointermove", (e) => {
    if (!drag) return; dx = e.clientX - sx; dy = e.clientY - sy;
    c.style.transform = `translate(${dx}px,${dy}px) rotate(${dx / 18}deg)`;
    stamps.like.style.opacity = Math.max(0, Math.min(1, dx / 100));
    stamps.nope.style.opacity = Math.max(0, Math.min(1, -dx / 100));
    stamps.sup.style.opacity = Math.max(0, Math.min(1, -dy / 120)) * (Math.abs(dx) < 60 ? 1 : 0);
  });
  const end = () => {
    if (!drag) return; drag = false; c.style.transition = "transform .3s ease";
    if (dx > 110) fling(c, "like"); else if (dx < -110) fling(c, "pass"); else if (dy < -130 && Math.abs(dx) < 60) fling(c, "super");
    else { c.style.transform = ""; Object.values(stamps).forEach(s => s.style.opacity = 0); }
    dx = dy = 0;
  };
  c.addEventListener("pointerup", end); c.addEventListener("pointercancel", end);
}
let busy = false;
async function fling(c, action) {
  if (busy) return; busy = true; const u = c._u;
  c.style.transition = "transform .35s ease";
  const x = action === "like" ? 600 : action === "pass" ? -600 : 0, y = action === "super" ? -900 : 40;
  c.style.transform = `translate(${x}px,${y}px) rotate(${x / 20}deg)`;
  try {
    const r = await api("/swipe", { method: "POST", body: { target: u.id, action } });
    cards = cards.filter(x => x.id !== u.id);
    setTimeout(() => { renderDeck(); if (cards.length < 3) refill(); }, 250);
    if (r.matched) showMatch(u, r.match_id);
  } catch (e) { c.style.transform = ""; handleErr(e); }
  finally { setTimeout(() => busy = false, 300); }
}
async function refill() { try { const more = await api("/deck"); const ids = new Set(cards.map(c => c.id)); cards = cards.concat(more.filter(m => !ids.has(m.id))); renderDeck(); } catch (_) {} }
function topCard() { const cs = $$("#deck .card"); return cs[cs.length - 1]; }
$("#bLike").addEventListener("click", () => topCard() && fling(topCard(), "like"));
$("#bNope").addEventListener("click", () => topCard() && fling(topCard(), "pass"));
$("#bSuper").addEventListener("click", () => topCard() && fling(topCard(), "super"));
$("#bUndo").addEventListener("click", async () => { try { await api("/undo", { method: "POST" }); toast("Rewound last pass"); loadDeck(); } catch (e) { handleErr(e); } });

let pendingMatch = null;
function showMatch(u, mid) {
  pendingMatch = { match_id: mid, user: u };
  $("#matchPopText").textContent = "You and " + u.name + " liked each other.";
  $("#matchPop").classList.remove("hidden"); $("#mBadge").classList.remove("hidden");
}
$("#matchKeep").addEventListener("click", () => $("#matchPop").classList.add("hidden"));
$("#matchChat").addEventListener("click", () => { $("#matchPop").classList.add("hidden"); openChat(pendingMatch); });

// ---------- MATCHES ----------
async function loadMatches() {
  $("#mBadge").classList.add("hidden");
  try {
    const ms = await api("/matches"); const list = $("#matchList"); list.replaceChildren();
    $("#matchEmpty").classList.toggle("hidden", ms.length > 0);
    for (const m of ms) {
      const preview = m.last ? (m.last.sender === m.user.id ? "" : "You: ") + m.last.body : "New match - say hi!";
      list.append(el("div", { class: "mitem", onclick: () => openChat(m) },
        el("div", { class: "mav", style: { background: grad(m.user.id) } }, initials(m.user.name)),
        el("div", { class: "t" }, el("b", {}, m.user.name + ", " + m.user.age), el("div", { class: "p" }, preview)),
        el("span", { class: "pct" }, m.score + "%")));
    }
  } catch (e) { handleErr(e); }
}

// ---------- CHAT ----------
let chat = null, lastId = 0, poll = null;
async function openChat(m) {
  chat = m; lastId = 0; show("#chat"); $("#chatTitle").textContent = m.user.name;
  $("#msgs").replaceChildren(el("div", { class: "chathead" }, "You matched with " + m.user.name + ". Discuss rent, area and move-in date. Meet in public places and never pay deposits before seeing the flat."));
  await pullMsgs(); clearInterval(poll); poll = setInterval(pullMsgs, 3000);
}
async function pullMsgs() {
  if (!chat) return;
  try {
    const ms = await api(`/matches/${chat.match_id}/messages?after=${lastId}`); const box = $("#msgs");
    for (const m of ms) { box.append(el("div", { class: "bubble " + (m.mine ? "me" : "them") }, m.body)); lastId = m.id; }
    if (ms.length) box.scrollTop = box.scrollHeight;
  } catch (e) { if (e.status === 404) { closeChat(); toast("This match is no longer available"); } }
}
function closeChat() { clearInterval(poll); chat = null; show("#main"); go("matches"); }
$("#chatBack").addEventListener("click", closeChat);
$("#chatForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const inp = $("#chatInput"); const body = inp.value.trim(); if (!body) return;
  inp.value = ""; try { await api(`/matches/${chat.match_id}/messages`, { method: "POST", body: { body } }); pullMsgs(); } catch (err) { inp.value = body; handleErr(err); }
});
$("#chatMenu").addEventListener("click", () => {
  const u = chat.user;
  sheet(el("div", {},
    el("h3", {}, u.name + ", " + u.age), el("p", { class: "bio", style: { margin: "8px 0", color: "#555" } }, u.bio || ""),
    el("div", { class: "tags" }, ...tagList(u).map(x => el("span", { class: "tag" }, x))),
    el("button", { class: "btn ghost", onclick: async () => { if (!confirm("Unmatch " + u.name + "?")) return; await api(`/matches/${chat.match_id}`, { method: "DELETE" }); closeSheet(); closeChat(); toast("Unmatched"); } }, "Unmatch"),
    el("button", { class: "btn danger", onclick: async () => { const reason = prompt("Why are you reporting this user? (optional)"); if (reason === null) return; await api("/report", { method: "POST", body: { target: u.id, reason: reason.slice(0, 300) } }); closeSheet(); closeChat(); toast("Reported and blocked"); } }, "Report & block"),
    el("button", { class: "btn ghost", onclick: closeSheet }, "Close")));
});
function sheet(content) { $("#sheetBody").replaceChildren(content); $("#sheet").classList.remove("hidden"); }
function closeSheet() { $("#sheet").classList.add("hidden"); }
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

// ---------- PROFILE ----------
const pf = $("#profileForm");
function fillProfile() {
  if (!me) return;
  for (const x of pf.elements) { if (!x.name || !(x.name in me)) continue;
    if (x.type === "checkbox") x.checked = !!me[x.name]; else x.value = me[x.name] ?? ""; }
  $("#cleanOut").textContent = me.cleanliness; $("#completeHint").classList.toggle("hidden", me.complete);
}
pf.cleanliness.addEventListener("input", (e) => $("#cleanOut").textContent = e.target.value);
pf.addEventListener("submit", async (e) => {
  e.preventDefault(); $("#profErr").textContent = ""; const b = {};
  for (const x of pf.elements) { if (!x.name) continue;
    b[x.name] = x.type === "checkbox" ? x.checked : (x.type === "number" || x.type === "range") ? Number(x.value || 0) : x.value; }
  try { await api("/me", { method: "PUT", body: b }); me = await api("/me"); fillProfile(); toast("Profile saved"); loadDeck(); }
  catch (err) { $("#profErr").textContent = err.message; }
});
$("#logoutBtn").addEventListener("click", async () => { await api("/logout", { method: "POST" }); me = null; show("#auth"); });
$("#deleteBtn").addEventListener("click", async () => {
  if (!confirm("Permanently delete your account, matches and messages?")) return;
  await api("/me", { method: "DELETE" }); me = null; show("#auth"); toast("Account deleted");
});

function handleErr(e) { if (e.status === 401) { show("#auth"); return; } toast(e.message); }

async function boot() {
  try { me = await api("/me"); } catch (_) { show("#auth"); return; }
  show("#main");
  if (!me.complete) go("profile"); else go("discover");
  loadDeck();
}
boot();
})();
