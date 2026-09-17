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
  buttons: [document.getElementById("btnNifty"), document.getElementById("btnSensex")],
};

let lastSnapshot = null;
let toleranceInitialised = false;

// ---------------------------------------------------------------- selection
els.buttons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    els.buttons.forEach((b) => (b.disabled = true));
    try {
      const res = await fetch("/api/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: btn.dataset.index }),
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
      render(await res.json());
    } catch (e) {
      showBanner(`Selection failed: ${e}`);
    } finally {
      els.buttons.forEach((b) => (b.disabled = false));
    }
  });
});

els.tolerance.addEventListener("input", () => {
  if (lastSnapshot) render(lastSnapshot);
});

// --------------------------------------------------------------------- auth
async function init() {
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
  connectWS();
}

function showLogin(loginUrl, error) {
  els.dashView.hidden = true;
  els.loginView.hidden = false;
  if (loginUrl) els.connectBtn.href = loginUrl;
  else els.connectBtn.style.display = "none";

  const urlErr = new URLSearchParams(location.search).get("login_error");
  const msg = error || urlErr;
  if (msg) {
    els.loginBanner.textContent = msg;
    els.loginBanner.hidden = false;
  }
}

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

  if (!toleranceInitialised && h.tolerance_pct != null) {
    els.tolerance.value = h.tolerance_pct;
    toleranceInitialised = true;
  }
  const tolerance = parseFloat(els.tolerance.value) || 0;

  // Backend feed status = the Angel One websocket connection.
  setUiWsStatus(h.ws_status, h.ws_status + (h.ws_error ? ` (${h.ws_error})` : ""));
  if (h.auth_error) showBanner(h.auth_error);

  els.statIndex.textContent = h.index || "—";
  els.statSpot.textContent = h.spot != null ? fmt(h.spot) : "—";
  els.statAtm.textContent = h.atm_strike != null ? h.atm_strike : "—";
  els.statExpiry.textContent = h.expiry || "—";

  els.buttons.forEach((b) => b.classList.toggle("active", b.dataset.index === h.index));

  if (!snap.rows || !snap.rows.length) return;

  const spot = h.spot;
  els.body.innerHTML = "";
  for (const row of snap.rows) {
    const tr = document.createElement("tr");
    if (h.atm_strike != null && row.strike === h.atm_strike) tr.classList.add("atm");

    const call = row.call, put = row.put;
    let callTV = null, putTV = null;
    let side = "—", sideClass = "side-neutral", delta = null, pct = null, bg = "";

    if (spot != null && call != null && put != null) {
      // Time & risk (extrinsic) value = LTP minus intrinsic vs live spot.
      callTV = call - Math.max(spot - row.strike, 0);
      putTV = put - Math.max(row.strike - spot, 0);
      delta = Math.abs(callTV - putTV);

      const higher = Math.max(callTV, putTV);
      const lower = Math.min(callTV, putTV);
      // % only meaningful when the cheaper leg has positive extrinsic value.
      pct = lower > 0 ? (higher / lower - 1) * 100 : null;

      const neutral = pct != null ? pct <= tolerance : delta === 0;
      if (neutral) {
        side = "≈ Even";
      } else if (callTV > putTV) {
        side = "Call";
        sideClass = "side-call";
        bg = `rgba(var(--call), ${alphaFor(pct != null ? pct : 150)})`;
      } else {
        side = "Put";
        sideClass = "side-put";
        bg = `rgba(var(--put), ${alphaFor(pct != null ? pct : 150)})`;
      }
    }

    tr.style.background = bg;
    tr.innerHTML = `
      <td>${row.strike}</td>
      <td>${call != null ? fmt(call) : "—"}</td>
      <td>${put != null ? fmt(put) : "—"}</td>
      <td>${callTV != null ? fmt(callTV) : "—"}</td>
      <td>${putTV != null ? fmt(putTV) : "—"}</td>
      <td class="${sideClass}">${side}</td>
      <td>${delta != null ? fmt(delta) : "—"}</td>
      <td>${pct != null ? pct.toFixed(1) + "%" : "—"}</td>`;
    els.body.appendChild(tr);
  }
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
