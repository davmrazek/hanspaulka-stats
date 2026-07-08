// /srovnani/ — client-side two-team comparison. State is the ?a=&b= query
// (slugs), so any comparison is shareable. Reads the prebuilt teams.json index
// and each team's data.json; no server involvement.
(function () {
  const BASE = window.HS_BASE || "";
  const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const slugOf = (u) => u.split("/tym/")[1]?.replace(/\/$/, "") || "";
  const dataUrl = (slug) => `${BASE}/tym/${slug}/data.json`;

  const empty = document.getElementById("compare-empty");
  const out = document.getElementById("compare-out");
  const h2hOut = document.getElementById("h2h-out");
  const chartWrap = document.getElementById("compare-chart-wrap");
  const metricSel = document.getElementById("cmp-metric");

  let teamsIndex = null;
  const loadTeams = async () =>
    teamsIndex ?? (teamsIndex = await (await fetch(`${BASE}/teams.json`)).json());

  const state = { a: null, b: null };
  const cache = {};
  const teamData = async (slug) =>
    cache[slug] ?? (cache[slug] = await (await fetch(dataUrl(slug)).then((r) => r.json())));

  function attachSuggest(input, list, onPick) {
    input.addEventListener("input", async () => {
      const q = norm(input.value.trim());
      if (q.length < 2) { list.hidden = true; return; }
      const hits = (await loadTeams())
        .filter((t) => norm(t.n).includes(q)).slice(0, 10);
      list.innerHTML = hits
        .map((t, i) => `<li data-i="${i}">${t.n}</li>`).join("");
      list.hidden = hits.length === 0;
      list.onclick = (e) => {
        const li = e.target.closest("li");
        if (!li) return;
        list.hidden = true;
        const t = hits[+li.dataset.i];
        input.value = t.n;
        onPick({ n: t.n, slug: slugOf(t.u) });
      };
    });
  }

  function setQuery() {
    const p = new URLSearchParams();
    if (state.a) p.set("a", state.a.slug);
    if (state.b) p.set("b", state.b.slug);
    history.replaceState(null, "", `${location.pathname}?${p}`);
  }

  const formHtml = (form) =>
    [...(form || "")].map((c) => `<b class="f-${c}">${c}</b>`).join("");

  function card(d) {
    const last = d.history[d.history.length - 1];
    const s = d.split;
    return `<div>
      <h2><a href="${d.url}">${d.name}</a></h2>
      <div class="badges">
        <span class="badge">Forma: <span class="form">${formHtml(d.form)}</span></span>
        <span class="badge">${last ? `${last.season} · ${last.group} · ${last.position}. místo` : "—"}</span>
        <span class="badge">Bez porážky: ${d.unbeaten.current} (rekord ${d.unbeaten.longest})</span>
        <span class="badge">Karty: <b class="card-y">${d.discipline.yellow}</b> <b class="card-r">${d.discipline.red}</b></span>
      </div>
      <table class="splits">
        <tr><th></th><th>Z</th><th>V</th><th>R</th><th>P</th><th>Skóre</th></tr>
        <tr><td>Doma</td><td>${s.home.P}</td><td>${s.home.W}</td><td>${s.home.D}</td><td>${s.home.L}</td><td>${s.home.GF}:${s.home.GA}</td></tr>
        <tr><td>Venku</td><td>${s.away.P}</td><td>${s.away.W}</td><td>${s.away.D}</td><td>${s.away.L}</td><td>${s.away.GF}:${s.away.GA}</td></tr>
      </table>
    </div>`;
  }

  function h2hBlock(da, db) {
    const o = (da.opponents || []).find(
      (x) => x.slug === state.b.slug || x.opponent === db.name);
    if (!o) {
      return `<h2>Vzájemné zápasy</h2><p class="note">Tyto týmy spolu v databázi neodehrály žádný zápas.</p>`;
    }
    const rows = o.matches.slice().reverse().map((m) => `
      <tr><td class="date">${m.date}</td>
      <td><b class="f-${m.outcome}">${m.outcome}</b></td>
      <td class="note">${m.season} · ${m.venue === "home" ? "doma" : "venku"}</td>
      <td class="score">${m.gf}:${m.ga}</td></tr>`).join("");
    return `<h2>Vzájemné zápasy</h2>
      <div class="badges">
        <span class="badge">${da.name} – ${db.name}</span>
        <span class="badge">Bilance: <b class="f-W">${o.won}</b>–<b class="f-D">${o.drawn}</b>–<b class="f-L">${o.lost}</b></span>
        <span class="badge">Skóre: ${o.gf}:${o.ga}</span>
      </div>
      <table class="results">${rows}</table>`;
  }

  const METRICS = {
    position: { label: "Umístění", reverse: true, pick: (h) => h.position },
    tier: { label: "Liga", reverse: true, pick: (h) => h.tier },
    avg_goals: { label: "Průměr vstřelených", trend: "avg_goals" },
    avg_conceded: { label: "Průměr obdržených", trend: "avg_conceded" },
    yellow: { label: "Žluté karty", trend: "yellow" },
  };
  let chart = null;

  function drawChart(da, db) {
    if (!window.Chart) { window.addEventListener("load", () => drawChart(da, db), { once: true }); return; }
    const m = METRICS[metricSel.value];
    const seasons = [...new Set([...da.trend.seasons, ...db.trend.seasons])].sort();
    const series = (d) => seasons.map((s) => {
      if (m.trend) {
        const i = d.trend.seasons.indexOf(s);
        return i === -1 ? null : d.trend[m.trend][i];
      }
      const h = d.history.find((x) => x.season === s);
      return h ? m.pick(h) : null;
    });
    const ds = [[da, "#fbba00"], [db, "#2b6cb0"]].map(([d, c]) => ({
      label: d.name, data: series(d), borderColor: c,
      backgroundColor: "transparent", spanGaps: true, tension: 0.2,
    }));
    if (chart) chart.destroy();
    chart = new Chart(document.getElementById("compare-chart"), {
      type: "line",
      data: { labels: seasons, datasets: ds },
      options: {
        responsive: true,
        scales: { y: { reverse: !!m.reverse, ticks: { precision: 0 }, title: { display: true, text: m.label } } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  async function render() {
    if (!state.a || !state.b) {
      empty.hidden = false; out.innerHTML = ""; h2hOut.innerHTML = "";
      chartWrap.hidden = true;
      return;
    }
    empty.hidden = true;
    const [da, db] = await Promise.all([teamData(state.a.slug), teamData(state.b.slug)]);
    out.innerHTML = card(da) + card(db);
    h2hOut.innerHTML = h2hBlock(da, db);
    chartWrap.hidden = false;
    drawChart(da, db);
  }

  attachSuggest(document.getElementById("pick-a"), document.getElementById("sug-a"),
    (t) => { state.a = t; setQuery(); render(); });
  attachSuggest(document.getElementById("pick-b"), document.getElementById("sug-b"),
    (t) => { state.b = t; setQuery(); render(); });
  metricSel.addEventListener("change", () => { if (state.a && state.b) render(); });

  // hydrate from ?a=&b= so comparisons are shareable
  (async function initFromQuery() {
    const q = new URLSearchParams(location.search);
    const [sa, sb] = [q.get("a"), q.get("b")];
    if (!sa && !sb) return;
    const idx = await loadTeams();
    const bySlug = Object.fromEntries(idx.map((t) => [slugOf(t.u), t]));
    if (sa && bySlug[sa]) {
      state.a = { n: bySlug[sa].n, slug: sa };
      document.getElementById("pick-a").value = bySlug[sa].n;
    }
    if (sb && bySlug[sb]) {
      state.b = { n: bySlug[sb].n, slug: sb };
      document.getElementById("pick-b").value = bySlug[sb].n;
    }
    render();
  })();
})();
