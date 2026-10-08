"use strict";

const els = {
  loginView: document.getElementById("loginView"),
  dashView: document.getElementById("dashView"),
  connectBtn: document.getElementById("connectBtn"),
  loginBanner: document.getElementById("loginBanner"),
  account: document.getElementById("account"),
  logoutBtn: document.getElementById("logoutBtn"),
  wsStatus: document.getElementById("wsStatus"),
  banner: document.getElementById("banner"),
  tolerance: document.getElementById("tolerance"),
  body: document.getElementById("chainBody"),
  statIndex: document.getElementById("statIndex"),
  statSpot: document.getElementById("statSpot"),
  statAtm: document.getElementById("statAtm"),
  statExpiry: document.getElementById("statExpiry"),
  statClock: document.getElementById("statClock"),
  buttons: [
    document.getElementById("btnNifty"),
    document.getElementById("btnSensex"),
    document.getElementById("btnStocks"),
  ],
};

let lastSnapshot = null;
let toleranceInitialised = false;

// ---------------------------------------------------------------- selection
let defaultIndex = null;   // index expiring soonest, from /api/me
let autoSelectDone = false;
let selecting = false;     // suppress row re-renders while a selection loads
let stockFilter = "all";   // all | call | put (stocks view quick filter)

const skewFilterEl = document.getElementById("skewFilter");
skewFilterEl.querySelectorAll("button").forEach((btn) => {
  btn.addEventListener("click", () => {
    stockFilter = btn.dataset.filter;
    skewFilterEl.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b === btn));
    if (lastSnapshot) render(lastSnapshot);
  });
});

async function selectIndex(index) {
  els.buttons.forEach((b) => (b.disabled = true));
  selecting = true;
  els.body.innerHTML = `<tr><td colspan="11" class="empty">${
    index === "STOCKS" ? "Building the F&amp;O stock list (15&ndash;20 s)&hellip;" : "Loading option chain&hellip;"
  }</td></tr>`;
  try {
    const res = await fetch("/api/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    if (res.status === 401) {
      init(); // session expired -> back to the login view
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showBanner(err.detail || `Selection failed (${res.status})`);
      return;
    }
    hideBanner();
    selecting = false;
    render(await res.json());
  } catch (e) {
    showBanner(`Selection failed: ${e}`);
  } finally {
    selecting = false;
    els.buttons.forEach((b) => (b.disabled = false));
  }
}

els.buttons.forEach((btn) => {
  btn.addEventListener("click", () => {
    autoSelectDone = true; // manual choice wins
    selectIndex(btn.dataset.index);
  });
});

els.tolerance.addEventListener("input", () => {
  if (lastSnapshot) render(lastSnapshot);
});

// --------------------------------------------------------------------- auth
// Angel One may return tokens in the URL fragment (#auth_token=...), which
// never reaches the server — catch them here and post them to the backend.
function extractTokens() {
  const raw = (location.search.slice(1) + "&" + location.hash.slice(1))
    .replace(/[?#]/g, "&");
  const p = new URLSearchParams(raw);
  const pick = (...names) => names.map((n) => p.get(n)).find((v) => v);
  const auth = pick("auth_token", "authToken", "jwtToken", "jwttoken", "token");
  const feed = pick("feed_token", "feedToken", "feedtoken");
  if (auth && feed) return { auth_token: auth, feed_token: feed, names: null };
  const names = [...p.keys()].filter((k) => k && k !== "login_error");
  return names.length ? { auth_token: null, feed_token: null, names } : null;
}

async function init() {
  const found = extractTokens();
  if (found) {
    history.replaceState(null, "", "/"); // scrub tokens from the URL/history
    if (found.auth_token) {
      try {
        const res = await fetch("/api/callback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auth_token: found.auth_token, feed_token: found.feed_token }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showLogin(null, err.detail || `Login failed (${res.status})`);
          fillLoginUrl();
          return;
        }
      } catch (e) {
        showLogin(null, `Login failed: ${e}`);
        fillLoginUrl();
        return;
      }
    } else if (found.names) {
      showLogin(null, `Angel One returned no tokens (params received: ${found.names.join(", ")})`);
      fillLoginUrl();
      return;
    }
  }

  let me;
  try {
    me = await (await fetch("/api/me")).json();
  } catch (e) {
    showLogin(null, `Server unreachable: ${e}`);
    return;
  }

  if (!me.authenticated) {
    showLogin(me.login_url, me.auth_error);
    return;
  }

  els.loginView.hidden = true;
  els.dashView.hidden = false;
  if (me.mode === "publisher") {
    els.account.textContent = me.client_code || "";
    els.account.hidden = false;
    els.logoutBtn.hidden = false;
  }
  defaultIndex = me.default_index || null;
  connectWS();
}

// Fetch the connect link for a login view shown before /api/me was consulted.
async function fillLoginUrl() {
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.login_url) els.connectBtn.href = me.login_url;
  } catch (e) { /* leave the button pointing nowhere */ }
}

function showLogin(loginUrl, error) {
  els.dashView.hidden = true;
  els.loginView.hidden = false;
  if (loginUrl) els.connectBtn.href = loginUrl;

  const urlErr = new URLSearchParams(location.search).get("login_error");
  const msg = error || urlErr;
  if (msg) {
    els.loginBanner.textContent = msg;
    els.loginBanner.hidden = false;
  }
}

document.getElementById("loginForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = document.getElementById("loginSubmit");
  btn.disabled = true;
  btn.textContent = "Connecting…";
  els.loginBanner.hidden = true;
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_code: document.getElementById("fClient").value,
        pin: document.getElementById("fPin").value,
        totp: document.getElementById("fTotp").value,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      els.loginBanner.textContent = err.detail || `Login failed (${res.status})`;
      els.loginBanner.hidden = false;
      return;
    }
    document.getElementById("fPin").value = "";
    document.getElementById("fTotp").value = "";
    init();
  } catch (e) {
    els.loginBanner.textContent = `Login failed: ${e}`;
    els.loginBanner.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect Angel One";
  }
});

els.logoutBtn.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/";
});

// ---------------------------------------------------------------- websocket
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const ping = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) ws.send("ping");
  }, 15000);

  let authRejected = false;
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "auth_required") {
      authRejected = true;
      init(); // session expired -> back to the login view
      return;
    }
    render(msg);
  };
  ws.onclose = () => {
    clearInterval(ping);
    if (authRejected) return;
    setUiWsStatus("disconnected", "reconnecting…");
    setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}
init();

// ------------------------------------------------------------------- render
function render(snap) {
  lastSnapshot = snap;
  const h = snap.header;

  // First load: auto-select the index expiring soonest (today on expiry days).
  if (!autoSelectDone) {
    if (h.index) {
      autoSelectDone = true; // server session already has a chain
    } else if (defaultIndex) {
      autoSelectDone = true;
      selectIndex(defaultIndex);
    }
  }

  if (!toleranceInitialised && h.tolerance_pct != null) {
    els.tolerance.value = h.tolerance_pct;
    toleranceInitialised = true;
  }
  const tolerance = parseFloat(els.tolerance.value) || 0;

  // Backend feed status = the Angel One websocket connection.
  setUiWsStatus(h.ws_status, h.ws_status + (h.ws_error ? ` (${h.ws_error})` : ""));
  if (h.auth_error) showBanner(h.auth_error);

  els.statIndex.textContent =
    h.kind === "stocks" && h.count ? `STOCKS (${h.count})` : (h.index || "—");
  els.statSpot.textContent = h.spot != null ? fmt(h.spot) : "—";
  els.statAtm.textContent = h.atm_strike != null ? h.atm_strike : "—";
  els.statExpiry.textContent = h.expiry || "—";

  els.buttons.forEach((b) => b.classList.toggle("active", b.dataset.index === h.index));

  if (selecting || !snap.rows || !snap.rows.length) return;

  setTableHead(h.kind);
  if (h.kind === "stocks") renderStockRows(snap.rows, tolerance);
  else renderIndexRows(snap.rows, h, tolerance);
}

const INDEX_HEAD = `
  <th>Strike</th><th>Call Price</th><th>Put Price</th>
  <th>Call Time Value</th><th>Put Time Value</th>
  <th>Pricier Side</th><th>Gap (₹)</th><th>Gap (%)</th>`;
const STOCK_HEAD = `
  <th>Stock</th><th>NSE LTP</th><th>BSE LTP</th>
  <th>Strike</th><th>Call Price</th><th>Put Price</th>
  <th>Call Time Value</th><th>Put Time Value</th>
  <th>Pricier Side</th><th>Gap (₹)</th><th>Gap (%)</th>`;
let currentHead = null;

function setTableHead(kind) {
  skewFilterEl.hidden = kind !== "stocks";
  if (kind === currentHead) return;
  currentHead = kind;
  document.getElementById("chainHead").innerHTML = kind === "stocks" ? STOCK_HEAD : INDEX_HEAD;
  document.getElementById("chainTable").classList.toggle("stocks", kind === "stocks");
}

// Time & risk (extrinsic) value skew for one strike vs the (live) spot.
function skewFor(spot, strike, call, put, tolerance) {
  if (spot == null || call == null || put == null) {
    return { callTV: null, putTV: null, delta: null, pct: null, side: "—", sideClass: "side-neutral", bg: "" };
  }
  const callTV = call - Math.max(spot - strike, 0);
  const putTV = put - Math.max(strike - spot, 0);
  const delta = Math.abs(callTV - putTV);
  const higher = Math.max(callTV, putTV);
  const lower = Math.min(callTV, putTV);
  // % needs the cheaper leg above tick-size noise (one tick = 0.05), or a
  // near-zero denominator makes tiny gaps explode. Never scale this floor by
  // the strike — index strikes are huge relative to premiums.
  const TICK = 0.05;
  const pct = lower > TICK ? (higher / lower - 1) * 100 : null;

  let side = "≈ Even", sideClass = "side-neutral", bg = "";
  // One leg at/below parity: flag the row only if the other leg carries
  // real TV; two tick-level TVs (deep ITM near expiry) are noise.
  const neutral = pct != null ? pct <= tolerance : higher <= TICK;
  if (!neutral) {
    // Colour by % when it exists, else by the gap relative to the richer TV.
    const intensity = pct != null ? pct
      : Math.min(150, (delta / Math.max(higher, TICK)) * 100);
    if (callTV > putTV) {
      side = "Call";
      sideClass = "side-call";
      bg = `rgba(var(--call), ${alphaFor(intensity)})`;
    } else {
      side = "Put";
      sideClass = "side-put";
      bg = `rgba(var(--put), ${alphaFor(intensity)})`;
    }
  }
  return { callTV, putTV, delta, pct, side, sideClass, bg };
}

function skewCells(row, s) {
  return `
      <td>${row.call != null ? fmt(row.call) : "—"}</td>
      <td>${row.put != null ? fmt(row.put) : "—"}</td>
      <td>${s.callTV != null ? fmt(s.callTV) : "—"}</td>
      <td>${s.putTV != null ? fmt(s.putTV) : "—"}</td>
      <td class="${s.sideClass}">${s.side}</td>
      <td>${s.delta != null ? fmt(s.delta) : "—"}</td>
      <td>${s.pct != null ? (s.pct > 999 ? ">999%" : s.pct.toFixed(1) + "%") : "—"}</td>`;
}

function renderIndexRows(rows, h, tolerance) {
  els.body.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (h.atm_strike != null && row.strike === h.atm_strike) tr.classList.add("atm");
    const s = skewFor(h.spot, row.strike, row.call, row.put, tolerance);
    tr.style.background = s.bg;
    tr.innerHTML = `<td>${row.strike}</td>` + skewCells(row, s);
    els.body.appendChild(tr);
  }
}

function renderStockRows(rows, tolerance) {
  // One ATM row per stock, alphabetical; ATM legs with zero volume today
  // get a not-traded note instead of a (meaningless) gap.
  let computed = rows.map((row) => ({
    row,
    s: skewFor(row.spot, row.strike, row.call, row.put, tolerance),
    untraded: !row.ce_traded && !row.pe_traded ? "CE & PE not traded"
      : !row.ce_traded ? "CE not traded"
      : !row.pe_traded ? "PE not traded" : null,
  }));
  if (stockFilter !== "all") {
    const want = stockFilter === "call" ? "Call" : "Put";
    computed = computed.filter((c) => !c.untraded && c.s.side === want);
  }
  computed.sort((a, b) => a.row.name.localeCompare(b.row.name));

  els.body.innerHTML = "";
  if (!computed.length) {
    els.body.innerHTML = `<tr><td colspan="11" class="empty">No stocks match this filter right now</td></tr>`;
    return;
  }
  for (const { row, s, untraded } of computed) {
    const tr = document.createElement("tr");
    tr.style.background = untraded ? "" : s.bg;
    const tail = untraded
      ? `<td>${row.call != null ? fmt(row.call) : "—"}</td>
         <td>${row.put != null ? fmt(row.put) : "—"}</td>
         <td>—</td><td>—</td>
         <td colspan="3" class="side-neutral">${untraded}</td>`
      : skewCells(row, s);
    tr.innerHTML =
      `<td class="stock-name">${row.name}</td>` +
      `<td>${row.spot != null ? fmt(row.spot) : "—"}</td>` +
      `<td>${row.bse != null ? fmt(row.bse) : "—"}</td>` +
      `<td>${fmtStrike(row.strike)}</td>` + tail;
    els.body.appendChild(tr);
  }
}

function fmtStrike(k) {
  return Number.isInteger(k) ? k : k.toFixed(2);
}

// Colour intensity: 0% premium -> faint, >=150% -> max.
function alphaFor(pct) {
  const a = 0.07 + (Math.min(pct, 150) / 150) * 0.45;
  return a.toFixed(3);
}

function fmt(n) {
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function setUiWsStatus(status, label) {
  els.wsStatus.className = `ws-status ${status}`;
  els.wsStatus.querySelector(".label").textContent = label;
}

function showBanner(msg) {
  els.banner.textContent = msg;
  els.banner.hidden = false;
}
function hideBanner() {
  els.banner.hidden = true;
}

// -------------------------------------------------- countdown to 15:30 IST
function updateClock() {
  const nowUtcMs = Date.now();
  const istMs = nowUtcMs + (5.5 * 60 + new Date().getTimezoneOffset()) * 60000;
  const ist = new Date(istMs);
  const close = new Date(ist);
  close.setHours(15, 30, 0, 0);

  const diff = close - ist;
  if (diff <= 0) {
    els.statClock.textContent = "Market closed";
    els.statClock.classList.add("closed");
  } else {
    const hrs = Math.floor(diff / 3600000);
    const min = Math.floor((diff % 3600000) / 60000);
    const sec = Math.floor((diff % 60000) / 1000);
    els.statClock.textContent =
      `${String(hrs).padStart(2, "0")}:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    els.statClock.classList.remove("closed");
  }
}
updateClock();
setInterval(updateClock, 1000);
