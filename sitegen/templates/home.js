// Homepage "Moje týmy" dashboard. Favorites live in localStorage (hs_favs,
// shared with the ★ toggle on team pages); card data comes from each team's
// prebuilt data.json. No favorites → invite state, no JS-only content lost.
(function () {
  const BASE = window.HS_BASE || "";
  const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const store = {
    get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
  };

  let teamsIndex = null;
  const loadTeams = async () =>
    teamsIndex ?? (teamsIndex = await (await fetch(`${BASE}/teams.json`)).json());
  const teamData = (url) => fetch(`${url}data.json`).then((r) => r.json());

  function attachSuggest(input, list, source, onPick) {
    input.addEventListener("input", async () => {
      const q = norm(input.value.trim());
      if (q.length < 2) { list.hidden = true; return; }
      const items = await source();
      const hits = items.filter((t) => norm(t.n).includes(q)).slice(0, 10);
      list.innerHTML = hits.map((t, i) => `<li data-i="${i}">${t.n}</li>`).join("");
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
          <p class="form">${formHtml(d.form)} ${d.spark ?? ""}
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
