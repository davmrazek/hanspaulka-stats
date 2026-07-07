// Customizable homepage: three draft concepts (tabs A/B/C).
// All state lives in localStorage; data comes from prebuilt JSON files.
(function () {
  const BASE = window.HS_BASE || "";
  const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const store = {
    get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
  };

  let teamsIndex = null;
  let groupsIndex = null;
  const loadTeams = async () =>
    teamsIndex ?? (teamsIndex = await (await fetch(`${BASE}/teams.json`)).json());
  const loadGroups = async () =>
    groupsIndex ?? (groupsIndex = await (await fetch(`${BASE}/groups.json`)).json());
  const teamData = (url) => fetch(`${url}data.json`).then((r) => r.json());

  // --- tabs -----------------------------------------------------------------
  const tabs = document.getElementById("home-tabs");
  if (!tabs) return;
  const panels = document.querySelectorAll("[data-panel]");
  function showTab(name) {
    tabs.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === name));
    panels.forEach((p) => (p.hidden = p.dataset.panel !== name));
    store.set("hs_tab", name);
  }
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (btn) showTab(btn.dataset.tab);
  });
  showTab(store.get("hs_tab", "prehled"));

  // --- shared: suggestion dropdown ------------------------------------------
  function attachSuggest(input, list, source, onPick) {
    input.addEventListener("input", async () => {
      const q = norm(input.value.trim());
      if (q.length < 2) { list.hidden = true; return; }
      const items = await source();
      const hits = items.filter((t) => norm(t.n ?? t.l).includes(q)).slice(0, 10);
      list.innerHTML = hits
        .map((t, i) => `<li data-i="${i}">${t.n ?? t.l}</li>`)
        .join("");
      list.hidden = hits.length === 0;
      list.onclick = (e) => {
        const li = e.target.closest("li");
        if (!li) return;
        list.hidden = true;
        input.value = "";
        onPick(hits[+li.dataset.i]);
      };
    });
  }

  const formHtml = (form) =>
    [...form].map((c) => `<b class="f-${c}">${c}</b>`).join("");

  // --- draft A: favourite teams ----------------------------------------------
  (function draftA() {
    const cards = document.getElementById("fav-cards");
    if (!cards) return;
    const empty = document.getElementById("fav-empty");
    let favs = store.get("hs_favs", []);

    async function render() {
      empty.hidden = favs.length > 0;
      cards.innerHTML = "";
      for (const fav of favs) {
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `<h3><a href="${fav.u}">${fav.n}</a></h3><p class="note">načítám…</p>`;
        cards.appendChild(card);
        teamData(fav.u).then((d) => {
          const last = d.history[d.history.length - 1];
          const s = d.split;
          const top = (d.roster || []).filter((p) => p.goals > 0)
            .sort((a, b) => b.goals - a.goals).slice(0, 3);
          card.innerHTML = `
            <button class="card-x" title="Odebrat" data-u="${fav.u}">×</button>
            <h3><a href="${d.url}">${d.name}</a></h3>
            <p>${last ? `${last.season} · skupina ${last.group} · ${last.position}. místo` : ""}</p>
            <p class="form">${formHtml(d.form)}
               <span class="note">bez porážky: ${d.unbeaten.current} (rekord ${d.unbeaten.longest})</span></p>
            <p class="note">Doma ${s.home.W}-${s.home.D}-${s.home.L} (${s.home.GF}:${s.home.GA}) ·
               Venku ${s.away.W}-${s.away.D}-${s.away.L} (${s.away.GF}:${s.away.GA}) ·
               Karty <b class="card-y">${d.discipline.yellow}</b> <b class="card-r">${d.discipline.red}</b></p>
            ${top.length ? `<p class="note">Střelci: ${top.map((p) => `${p.name} (${p.goals})`).join(", ")}</p>` : ""}
            ${d.biggest_win ? `<p class="note">Nejvyšší výhra: ${d.biggest_win.gf}:${d.biggest_win.ga} vs ${d.biggest_win.opponent}</p>` : ""}
            <table class="results">${d.recent.map((m) => `
              <tr><td><b class="f-${m.outcome}">${m.outcome}</b></td>
              <td>${m.opponent} <span class="note">(${m.venue === "home" ? "doma" : "venku"})</span></td>
              <td class="score">${m.gf}:${m.ga}</td></tr>`).join("")}
            </table>`;
        }).catch(() => { card.querySelector(".note").textContent = "data se nepodařilo načíst"; });
      }
    }
    cards.addEventListener("click", (e) => {
      const x = e.target.closest(".card-x");
      if (!x) return;
      favs = favs.filter((f) => f.u !== x.dataset.u);
      store.set("hs_favs", favs);
      render();
    });
    attachSuggest(
      document.getElementById("fav-search"),
      document.getElementById("fav-suggest"),
      loadTeams,
      (t) => {
        if (!favs.some((f) => f.u === t.u)) favs.push(t);
        store.set("hs_favs", favs);
        render();
      });
    render();
  })();

  // --- draft B: chart builder ---------------------------------------------------
  (function draftB() {
    const canvas = document.getElementById("custom-chart");
    if (!canvas) return;
    const chips = document.getElementById("chart-chips");
    const empty = document.getElementById("chart-empty");
    const metricSel = document.getElementById("chart-metric");
    let cfg = store.get("hs_chart", { metric: "position", teams: [] });
    metricSel.value = cfg.metric;
    let chart = null;

    const METRICS = {
      position: { label: "Umístění ve skupině", reverse: true,
                  pick: (h) => h.position },
      avg_goals: { label: "Průměr gólů na zápas", reverse: false, trend: "avg_goals" },
      tier: { label: "Liga", reverse: true, pick: (h) => h.tier },
      yellow: { label: "Žluté karty za sezónu", reverse: false, trend: "yellow" },
    };

    async function render() {
      chips.innerHTML = cfg.teams
        .map((t) => `<span class="chip">${t.n}<button data-u="${t.u}">×</button></span>`)
        .join("");
      empty.hidden = cfg.teams.length > 0;
      canvas.hidden = cfg.teams.length === 0;
      if (!cfg.teams.length || !window.Chart) return;

      const all = await Promise.all(cfg.teams.map((t) => teamData(t.u)));
      const seasons = [...new Set(all.flatMap((d) => d.history.map((h) => h.season)))].sort();
      const m = METRICS[cfg.metric];
      const datasets = all.map((d, i) => ({
        label: d.name,
        data: seasons.map((s) => {
          if (m.trend) {
            const idx = d.trend.seasons.indexOf(s);
            return idx === -1 ? null : d.trend[m.trend][idx];
          }
          const h = d.history.find((x) => x.season === s);
          return h ? m.pick(h) : null;
        }),
        borderColor: `hsl(${(i * 137) % 360} 70% 42%)`,
        backgroundColor: "transparent",
        spanGaps: true,
        tension: 0.2,
      }));
      if (chart) chart.destroy();
      chart = new Chart(canvas, {
        type: "line",
        data: { labels: seasons, datasets },
        options: {
          responsive: true,
          scales: { y: { reverse: m.reverse, title: { display: true, text: m.label },
                         ticks: { precision: 0 } } },
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
        },
      });
    }
    chips.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-u]");
      if (!b) return;
      cfg.teams = cfg.teams.filter((t) => t.u !== b.dataset.u);
      store.set("hs_chart", cfg);
      render();
    });
    metricSel.addEventListener("change", () => {
      cfg.metric = metricSel.value;
      store.set("hs_chart", cfg);
      render();
    });
    attachSuggest(
      document.getElementById("chart-search"),
      document.getElementById("chart-suggest"),
      loadTeams,
      (t) => {
        if (!cfg.teams.some((x) => x.u === t.u)) cfg.teams.push(t);
        store.set("hs_chart", cfg);
        render();
      });
    // Chart.js loads deferred; render once it's available
    if (window.Chart) render();
    else window.addEventListener("load", render);
  })();

  // --- draft C: widget grid --------------------------------------------------------
  (function draftC() {
    const grid = document.getElementById("widget-grid");
    if (!grid) return;
    const empty = document.getElementById("widget-empty");
    const typeSel = document.getElementById("widget-type");
    let widgets = store.get("hs_widgets", []);

    async function renderWidget(w, card) {
      if (w.type === "team-form") {
        const d = await teamData(w.u);
        const last = d.history[d.history.length - 1];
        const top = (d.roster || []).filter((p) => p.goals > 0)
          .sort((a, b) => b.goals - a.goals).slice(0, 3);
        card.innerHTML = `
          <button class="card-x" data-id="${w.id}" title="Odebrat">×</button>
          <h3><a href="${d.url}">${d.name}</a></h3>
          <p>${last ? `${last.season} · ${last.group} · ${last.position}. místo` : ""}</p>
          <p class="form">${formHtml(d.form)}
             <span class="note">karty <b class="card-y">${d.discipline.yellow}</b> <b class="card-r">${d.discipline.red}</b></span></p>
          ${top.length ? `<p class="note">Střelci: ${top.map((p) => `${p.name} (${p.goals})`).join(", ")}</p>` : ""}
          <table class="results">${d.recent.slice(0, 3).map((m) => `
            <tr><td><b class="f-${m.outcome}">${m.outcome}</b></td>
            <td>${m.opponent}</td><td class="score">${m.gf}:${m.ga}</td></tr>`).join("")}
          </table>`;
      } else if (w.type === "group-fairplay") {
        const d = await fetch(`${w.u}data.json`).then((r) => r.json());
        card.innerHTML = `
          <button class="card-x" data-id="${w.id}" title="Odebrat">×</button>
          <h3><a href="${d.url}">Karty · ${d.label}</a></h3>
          <table class="results">${d.fairplay.map((r) => `
            <tr><td>${r.team}</td>
            <td class="score"><b class="card-y">${r.yellow}</b> <b class="card-r">${r.red}</b></td></tr>`).join("")}
          </table>`;
      } else {
        const d = await fetch(`${w.u}data.json`).then((r) => r.json());
        if (w.type === "group-table") {
          card.innerHTML = `
            <button class="card-x" data-id="${w.id}" title="Odebrat">×</button>
            <h3><a href="${d.url}">${d.label}</a></h3>
            <table class="results">${d.table.map((r) => `
              <tr><td>${r.position}.</td><td>${r.team}</td>
              <td class="score">${r.gf}:${r.ga}</td><td class="score"><strong>${r.points}</strong></td></tr>`).join("")}
            </table>`;
        } else {
          card.innerHTML = `
            <button class="card-x" data-id="${w.id}" title="Odebrat">×</button>
            <h3><a href="${d.url}">Střelci · ${d.label}</a></h3>
            <ol class="scorers">${d.scorers.slice(0, 5).map((s) =>
              `<li>${s.name} <span class="note">(${s.team})</span> — <strong>${s.goals}</strong></li>`).join("")}
            </ol>`;
        }
      }
    }

    function render() {
      empty.hidden = widgets.length > 0;
      grid.innerHTML = "";
      for (const w of widgets) {
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `<p class="note">načítám…</p>`;
        grid.appendChild(card);
        renderWidget(w, card).catch(() =>
          (card.innerHTML = `<button class="card-x" data-id="${w.id}">×</button><p class="note">data se nepodařilo načíst</p>`));
      }
    }
    grid.addEventListener("click", (e) => {
      const x = e.target.closest(".card-x");
      if (!x) return;
      widgets = widgets.filter((w) => String(w.id) !== x.dataset.id);
      store.set("hs_widgets", widgets);
      render();
    });
    attachSuggest(
      document.getElementById("widget-search"),
      document.getElementById("widget-suggest"),
      () => (typeSel.value === "team-form" ? loadTeams() : loadGroups()),
      (item) => {
        widgets.push({ id: Date.now(), type: typeSel.value, u: item.u });
        store.set("hs_widgets", widgets);
        render();
      });
    render();
  })();
})();
