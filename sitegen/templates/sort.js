// Client-side table sorting: opt in with <table data-sort>.
// First row is the header; click a th to sort, click again to reverse.
(function () {
  const collator = new Intl.Collator("cs", { numeric: true, sensitivity: "base" });
  const cellNum = (v) => {
    const m = v.match(/^-?\d+(?:[.,]\d+)?/);
    return m ? parseFloat(m[0].replace(",", ".")) : null;
  };
  document.querySelectorAll("table[data-sort]").forEach((table) => {
    const header = table.rows[0];
    if (!header) return;
    [...header.cells].forEach((th, col) => {
      th.addEventListener("click", () => {
        const dir = th.classList.contains("sort-asc") ? -1 : 1;
        [...header.cells].forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
        th.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
        const rows = [...table.rows].slice(1);
        const key = (tr) => (tr.cells[col]?.textContent ?? "").trim();
        const numeric =
          rows.some((tr) => key(tr) !== "") &&
          rows.every((tr) => key(tr) === "" || cellNum(key(tr)) !== null);
        rows
          .sort((a, b) => {
            const ka = key(a), kb = key(b);
            if (numeric)
              return dir * ((cellNum(ka) ?? -Infinity) - (cellNum(kb) ?? -Infinity));
            return dir * collator.compare(ka, kb);
          })
          .forEach((tr) => tr.parentNode.appendChild(tr));
      });
    });
  });
})();
