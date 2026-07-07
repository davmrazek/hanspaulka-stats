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
