// Unified header search over teams + groups + players. Indexes are prebuilt
// JSON, lazy-loaded on the first keystroke and cached. Results are grouped.
(function () {
  const input = document.getElementById("team-search");
  const list = document.getElementById("search-results");
  if (!input || !list) return;
  const base = input.dataset.base || "";

  let indexes = null;
  async function load() {
    if (indexes) return indexes;
    const [teams, groups, players] = await Promise.all([
      fetch(`${base}/teams.json`).then((r) => r.json()),
      fetch(`${base}/groups.json`).then((r) => r.json()),
      fetch(`${base}/players.json`).then((r) => r.json()).catch(() => []),
    ]);
    indexes = { teams, groups, players };
    return indexes;
  }

  const norm = (s) =>
    s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

  function section(title, items) {
    if (!items.length) return "";
    return `<li class="search-head">${title}</li>` + items.join("");
  }

  input.addEventListener("input", async () => {
    const q = norm(input.value.trim());
    if (q.length < 2) { list.hidden = true; list.innerHTML = ""; return; }
    const { teams, groups, players } = await load();
    const teamHits = teams.filter((t) => norm(t.n).includes(q)).slice(0, 6)
      .map((t) => `<li><a href="${t.u}">${t.n}</a></li>`);
    const groupHits = groups.filter((g) => norm(g.l).includes(q)).slice(0, 4)
      .map((g) => `<li><a href="${g.u}">${g.l}</a></li>`);
    // players.json is { teams: [[name, slug]], players: [[name, teamIdx]] }
    const playerHits = (players.players || []).filter(([n]) => norm(n).includes(q))
      .slice(0, 6).map(([n, ti]) => {
        const [tn, slug] = players.teams[ti];
        return `<li><a href="${base}/tym/${slug}/#hraci">${n} <span class="note">(${tn})</span></a></li>`;
      });
    list.innerHTML =
      section("Týmy", teamHits) +
      section("Skupiny", groupHits) +
      section("Hráči", playerHits);
    list.hidden = list.innerHTML === "";
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search")) list.hidden = true;
  });
})();

// ★ favorites toggle on team pages; same hs_favs entries as the home dashboard.
(function () {
  const btn = document.getElementById("fav-toggle");
  if (!btn) return;
  const KEY = "hs_favs";
  const get = () => {
    try { return JSON.parse(localStorage.getItem(KEY)) ?? []; } catch { return []; }
  };
  const has = () => get().some((f) => f.u === btn.dataset.u);
  const paint = () => {
    const on = has();
    btn.textContent = on ? "★" : "☆";
    btn.classList.toggle("on", on);
    btn.title = on ? "Odebrat z Moje týmy" : "Přidat do Moje týmy";
  };
  btn.addEventListener("click", () => {
    const favs = has()
      ? get().filter((f) => f.u !== btn.dataset.u)
      : [...get(), { n: btn.dataset.n, u: btn.dataset.u }];
    localStorage.setItem(KEY, JSON.stringify(favs));
    paint();
  });
  paint();
})();
