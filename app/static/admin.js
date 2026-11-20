"use strict";
(() => {
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
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
  const res = await fetch("/api/admin" + path, {
    method: opts.method || "GET", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "flatmatch" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) { const e = new Error((data && data.detail) || "Error"); e.status = res.status; throw e; }
  return data;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); setTimeout(() => t.classList.add("hidden"), 2200); }
const dt = (ts) => ts ? new Date(ts * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
const d = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "";
const rs = (n) => Number(n || 0).toLocaleString("en-IN");
function fail(e) { if (e.status === 401) { showLogin(); return; } toast(e.message); }

function showLogin() { $("#panel").classList.add("hidden"); $("#login").classList.remove("hidden"); }
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#loginErr").textContent = "";
  try { await api("/login", { method: "POST", body: { password: e.target.password.value } }); e.target.reset(); start(); }
  catch (err) { $("#loginErr").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); showLogin(); });

$$(".tabs button").forEach(b => b.addEventListener("click", () => tab(b.dataset.tab)));
function tab(name) {
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-view").forEach(v => v.classList.toggle("hidden", v.id !== "t-" + name));
  if (name === "dash") loadStats(); if (name === "users") loadUsers(); if (name === "reports") loadReports();
}

async function loadStats() {
  try {
    const s = await api("/stats");
    $("#dbTag").textContent = s.database === "postgres" ? "Cloud Postgres" : "Local SQLite";
    const items = [["users","Total users"],["new_users_7d","New (7 days)"],["profiles_complete","Complete profiles"],["swipes","Swipes"],
      ["likes","Likes"],["matches","Matches"],["messages","Messages"],["reports","Reports"]];
    $("#stats").replaceChildren(...items.map(([k, l]) => el("div", { class: "stat" }, el("div", { class: "n" }, rs(s[k])), el("div", { class: "l" }, l))));
    const max = Math.max(1, ...s.top_cities.map(c => c.n));
    $("#cities").replaceChildren(...(s.top_cities.length ? s.top_cities.map(c => el("div", { class: "city" },
      el("span", {}, c.city), el("div", { class: "bar", style: { width: (c.n / max * 70) + "%" } }), el("b", {}, c.n)))
      : [el("p", { class: "empty" }, "No data yet")]));
  } catch (e) { fail(e); throw e; }
}

let page = 1, pages = 1;
async function loadUsers() {
  try {
    const r = await api(`/users?page=${page}&q=${encodeURIComponent($("#q").value.trim())}`); pages = r.pages;
    $("#usersTable tbody").replaceChildren(...r.users.map(u => el("tr", { onclick: () => openUser(u.id) },
      el("td", {}, u.id), el("td", {}, el("b", {}, u.name)), el("td", {}, u.email), el("td", {}, u.age),
      el("td", {}, [u.area, u.city].filter(Boolean).join(", ") || "-"),
      el("td", {}, u.budget_max ? rs(u.budget_min) + " - " + rs(u.budget_max) : "-"),
      el("td", {}, el("span", { class: "pill" }, u.looking === "roommate" ? "Has room" : "Needs room")),
      el("td", {}, u.matches), el("td", {}, u.reported ? el("span", { class: "pill red" }, u.reported) : "0"),
      el("td", {}, d(u.created)))));
    if (!r.users.length) $("#usersTable tbody").append(el("tr", {}, el("td", { colspan: "10", class: "empty" }, "No users found")));
    $("#pageInfo").textContent = `Page ${r.page} of ${r.pages} · ${r.total} users`;
    $("#prev").disabled = page <= 1; $("#next").disabled = page >= pages;
  } catch (e) { fail(e); }
}
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); page = 1; loadUsers(); });
$("#prev").addEventListener("click", () => { if (page > 1) { page--; loadUsers(); } });
$("#next").addEventListener("click", () => { if (page < pages) { page++; loadUsers(); } });

async function openUser(id) {
  try {
    const u = await api("/users/" + id);
    const yes = (b) => b ? "Yes" : "No";
    const kv = [["User ID", u.id], ["Age / Gender", u.age + " / " + u.gender], ["City / Area", [u.area, u.city].filter(Boolean).join(", ") || "-"],
      ["Budget", u.budget_max ? "Rs " + rs(u.budget_min) + " - " + rs(u.budget_max) : "-"],
      ["Type", u.looking === "roommate" ? "Has a room" : "Looking for a room"], ["Move-in", u.move_in || "-"],
      ["Occupation", u.occupation || "-"], ["Bio", u.bio || "-"], ["Smoker", yes(u.smoking)], ["Drinks", yes(u.drinks)],
      ["Pets", yes(u.pets)], ["Vegetarian", yes(u.veg)], ["Cleanliness", u.cleanliness + "/5"], ["Sleep", u.sleep],
      ["Guests", u.guests], ["Shows", u.pref_gender], ["Joined", dt(u.created)]];
    const a = u.activity;
    $("#drawerBody").replaceChildren(
      el("button", { class: "btn small ghost", onclick: closeDrawer }, "Close"),
      el("h2", { style: { marginTop: "14px" } }, u.name), el("p", { class: "sub" }, u.email),
      el("div", { class: "mini" }, ...[["swipes_made","Swipes"],["likes_given","Likes given"],["likes_received","Likes received"],["messages_sent","Messages sent"]]
        .map(([k, l]) => el("div", {}, el("b", {}, a[k]), el("span", {}, l)))),
      el("dl", { class: "kv" }, ...kv.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("h3", {}, `Matches (${u.matches.length})`),
      el("ul", { class: "list" }, ...(u.matches.length ? u.matches.map(m => el("li", {}, m.name + " ", el("small", {}, "#" + m.id + " · " + d(m.ts)))) : [el("li", {}, el("small", {}, "None"))])),
      el("h3", { style: { marginTop: "16px" } }, `Reports against (${u.reports_against.length})`),
      el("ul", { class: "list" }, ...(u.reports_against.length ? u.reports_against.map(r => el("li", {}, "By " + r.name + ": " + (r.reason || "(blocked, no reason)"), el("br"), el("small", {}, dt(r.ts)))) : [el("li", {}, el("small", {}, "None"))])),
      el("button", { class: "btn danger", style: { marginTop: "20px" }, onclick: async () => {
        if (!confirm(`Permanently delete ${u.name} and all their data?`)) return;
        try { await api("/users/" + u.id, { method: "DELETE" }); closeDrawer(); toast("User deleted"); loadUsers(); } catch (e) { fail(e); }
      } }, "Delete user"));
    $("#drawer").classList.remove("hidden");
  } catch (e) { fail(e); }
}
function closeDrawer() { $("#drawer").classList.add("hidden"); }
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });

async function loadReports() {
  try {
    const rs2 = await api("/reports");
    $("#reportsTable tbody").replaceChildren(...rs2.map(r => el("tr", { onclick: () => openUser(r.reported_id) },
      el("td", {}, dt(r.ts)), el("td", {}, el("b", {}, r.reported), el("br"), el("small", {}, r.reported_email)),
      el("td", {}, r.reporter), el("td", { class: "wrap" }, r.reason || "(blocked, no reason)"))));
    $("#noReports").classList.toggle("hidden", rs2.length > 0);
  } catch (e) { fail(e); }
}

async function start() {
  try { await loadStats(); } catch (_) { return; }
  $("#login").classList.add("hidden"); $("#panel").classList.remove("hidden"); tab("dash");
}
start();
})();
