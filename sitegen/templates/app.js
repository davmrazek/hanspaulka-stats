// Client-side team search over the prebuilt teams.json index.
(function () {
  const input = document.getElementById("team-search");
  const list = document.getElementById("search-results");
  if (!input || !list) return;
  let index = null;

  async function load() {
    if (index) return index;
    const res = await fetch(input.dataset.index);
    index = await res.json();
    return index;
  }

  const norm = (s) =>
    s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

  input.addEventListener("input", async () => {
    const q = norm(input.value.trim());
    if (q.length < 2) { list.hidden = true; list.innerHTML = ""; return; }
    const teams = await load();
    const hits = teams.filter((t) => norm(t.n).includes(q)).slice(0, 12);
    list.innerHTML = hits
      .map((t) => `<li><a href="${t.u}">${t.n}</a></li>`)
      .join("");
    list.hidden = hits.length === 0;
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
