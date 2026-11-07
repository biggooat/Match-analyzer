"use strict";

const SEL = [["H", "П1"], ["D", "Х"], ["A", "П2"], ["O25", "ТБ 2.5"], ["U25", "ТМ 2.5"]];
const state = { data: null, tab: "bets", league: "all" };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "—" : (x * 100).toFixed(d) + "%");
const signed = (x, d = 1) => (x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("ma-prefs") || "{}");
    if (p.tab) state.tab = p.tab;
    if (p.league) state.league = p.league;
  } catch (_) { /* хранилище недоступно — работаем без него */ }
}
function savePrefs() {
  try { localStorage.setItem("ma-prefs", JSON.stringify({ tab: state.tab, league: state.league })); } catch (_) {}
}

function dayTitle(iso) {
  const d = new Date(iso + "T12:00:00");
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const diff = Math.round((d - today) / 86400000);
  const base = d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  return diff === 0 ? "Сегодня, " + base : diff === 1 ? "Завтра, " + base : cap(base);
}

function leagueName(code) { return (state.data.leagues || {})[code] || code; }

function visibleMatches() {
  const today = new Date().toISOString().slice(0, 10);
  return state.data.matches.filter((m) => m.date >= today && (state.league === "all" || m.league === state.league));
}

function grid(m) {
  return '<div class="grid">' + SEL.map(([k, label]) => {
    const hl = m.bet && m.bet.selection === k ? " hl" : "";
    return `<div class="cell${hl}"><div class="k">${label}</div><div class="p">${pct((m.p || m.model)[k])}</div>` +
      `<div class="o">${m.odds[k] ? m.odds[k].toFixed(2) : "—"}</div></div>`;
  }).join("") + "</div>";
}

function card(m, withPick) {
  const t = m.time ? esc(m.time) : "";
  let html = `<article class="card"><div class="meta"><span>${esc(leagueName(m.league))}</span><span>${t ? t + " МСК" : ""}</span></div>` +
    `<div class="teams">${esc(m.home)}<span class="vs">—</span>${esc(m.away)}</div>`;
  if (withPick && m.bet) {
    const b = m.bet;
    html += `<div class="pick"><span class="what">${esc(b.label)}</span><span class="odds">${b.price.toFixed(2)}</span></div>` +
      `<div class="facts"><span>Вероятность <b>${pct(b.p)}</b></span><span>Перевес <b>${signed(b.edge, 0)}</b></span>` +
      `<span>Ставка <b>${pct(b.stake, 1)}</b> банка</span></div>`;
  }
  html += grid(m);
  if (m.xg && m.xg[0] != null) html += `<div class="xg">Ожидаемые голы: ${m.xg[0].toFixed(2)} : ${m.xg[1].toFixed(2)}</div>`;
  return html + "</article>";
}

function byDay(list, withPick) {
  let out = "", day = "";
  for (const m of list) {
    if (m.date !== day) { day = m.date; out += `<div class="day">${esc(dayTitle(day))}</div>`; }
    out += card(m, withPick);
  }
  return out;
}

function robustBanner() {
  const s = state.data.strategy || {};
  if (s.robust === false) {
    return '<div class="banner warn">⚠️ Последняя проверка на истории не нашла надёжно прибыльной стратегии. Ставки ниже — только для наблюдения.</div>';
  }
  return "";
}

function renderBets() {
  const list = visibleMatches().filter((m) => m.bet);
  if (!list.length) {
    return robustBanner() + '<div class="empty"><div class="big">🧘</div>Сейчас ставок с перевесом нет.<br>Лучше пропустить, чем играть без преимущества.</div>';
  }
  return robustBanner() + byDay(list, true) +
    '<p class="help">Коэффициент — лучший по рынку. Если у вашего букмекера он заметно ниже, перевес может исчезнуть.</p>';
}

function renderAll() {
  const list = visibleMatches();
  if (!list.length) return '<div class="empty"><div class="big">📅</div>Нет ближайших матчей с коэффициентами.</div>';
  return '<p class="help">Проценты — наша оценка вероятности (модель + рынок), ниже — лучший коэффициент. Зелёным отмечена ставка с перевесом.</p>' + byDay(list, false);
}

function renderStrategy() {
  const s = state.data.strategy || {};
  const st = s.stats;
  let html = "";
  if (!st) {
    html += '<div class="banner warn">Проверка стратегии на истории ещё не запускалась — используются настройки по умолчанию.</div>';
  } else {
    html += s.robust
      ? '<div class="banner ok">✅ Стратегия была прибыльной и на подборе, и на новых данных.</div>'
      : '<div class="banner warn">⚠️ Надёжной прибыли на истории не найдено.</div>';
    const cls = (x) => (x >= 0 ? "pos" : "neg");
    html += '<div class="stats">' +
      `<div class="stat"><div class="k">ROI на проверке</div><div class="v ${cls(st.test_roi)}">${signed(st.test_roi)}</div></div>` +
      `<div class="stat"><div class="k">Ставок на проверке</div><div class="v">${st.test_bets}</div></div>` +
      `<div class="stat"><div class="k">ROI за весь период</div><div class="v ${cls(st.roi)}">${signed(st.roi)}</div></div>` +
      `<div class="stat"><div class="k">Угадано</div><div class="v">${pct(st.hit_rate)}</div></div>` +
      `<div class="stat"><div class="k">Банк 1000 →</div><div class="v ${cls(st.final_bank - 1000)}">${Math.round(st.final_bank)}</div></div>` +
      `<div class="stat"><div class="k">Макс. просадка банка</div><div class="v">${pct(st.max_dd_bank)}</div></div>` +
      "</div>";
  }
  const markets = (s.markets || ["1X2", "OU25"]).map((x) => (x === "1X2" ? "исход матча" : "тотал 2.5")).join(", ");
  html += '<div class="help">' +
    `<h3>Текущие настройки</h3><p>Рынки: ${esc(markets)}. Перевес от ${pct(s.min_edge ?? 0.05)}, ` +
    `коэффициенты ${s.min_odds ?? 1.5}–${s.max_odds ?? 3.5}.${s.updated ? " Обновлено " + esc(s.updated) + "." : ""}</p>` +
    "<h3>Как это работает</h3><p>Модель оценивает силу атаки и обороны каждой команды по прошлым матчам и считает вероятности исходов. " +
    "Ставка предлагается, только когда коэффициент выше, чем должен быть по этой вероятности.</p>" +
    "<h3>Как часто обновляется</h3><p>Матчи и коэффициенты — каждый день утром и вечером. Стратегия заново проверяется на истории (10 сезонов) каждый понедельник.</p>" +
    "<h3>Размер ставки</h3><p>Процент банка считается по осторожному критерию Келли и не превышает 3%, чтобы серия проигрышей не обнулила банк.</p>" +
    "</div>";
  return html;
}

function renderFilters() {
  const el = $("#filters");
  if (state.tab === "strategy") { el.innerHTML = ""; return; }
  const codes = [...new Set(state.data.matches.map((m) => m.league))].sort((a, b) => leagueName(a).localeCompare(leagueName(b), "ru"));
  if (state.league !== "all" && !codes.includes(state.league)) state.league = "all";
  const chip = (code, label) => `<button class="chip" data-league="${esc(code)}" aria-pressed="${state.league === code}">${esc(label)}</button>`;
  el.innerHTML = codes.length > 1 ? chip("all", "Все лиги") + codes.map((c) => chip(c, leagueName(c))).join("") : "";
}

function render() {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
  if (!state.data) return;
  renderFilters();
  $("#view").innerHTML = state.tab === "bets" ? renderBets() : state.tab === "all" ? renderAll() : renderStrategy();
}

async function load() {
  try {
    const r = await fetch("data.json?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    state.data = await r.json();
    const d = new Date(state.data.updated);
    $("#updated").textContent = "обновлено " + d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    $("#updated").textContent = "нет связи";
    if (!state.data) $("#view").innerHTML = '<div class="empty"><div class="big">📡</div>Не удалось загрузить данные. Проверьте интернет.</div>';
  }
  render();
}

document.querySelector(".tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  state.tab = b.dataset.tab; savePrefs(); render(); window.scrollTo(0, 0);
});
$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-league]");
  if (!b) return;
  state.league = b.dataset.league; savePrefs(); render();
});
// колесо мыши прокручивает ленту лиг по горизонтали
$("#filters").addEventListener("wheel", (e) => {
  const el = e.currentTarget;
  if (el.scrollWidth > el.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
    el.scrollLeft += e.deltaY;
    e.preventDefault();
  }
}, { passive: false });
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });

loadPrefs();
load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
