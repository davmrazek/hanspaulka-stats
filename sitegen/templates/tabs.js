// Shared tab component.
// Markup: <nav class="tabs" data-tabs><button data-tab="x">…</nav>
//         <section data-panel="x">…</section>
// The first tab is the default; #hash deep-links to any tab.
// Without JS all panels stay visible (CSS hides them only under html.js).
(function () {
  document.documentElement.classList.add("js");
  const nav = document.querySelector("[data-tabs]");
  if (!nav) return;
  const buttons = [...nav.querySelectorAll("button[data-tab]")];
  const panels = [...document.querySelectorAll("[data-panel]")];
  const names = buttons.map((b) => b.dataset.tab);

  function show(name, updateHash) {
    if (!names.includes(name)) name = names[0];
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    panels.forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
    // charts init lazily: a hidden canvas has no size to render into
    const shown = panels.find((p) => p.dataset.panel === name);
    if (shown) shown.dispatchEvent(new CustomEvent("tabshow"));
    if (updateHash) {
      history.replaceState(
        null, "", name === names[0] ? location.pathname : `#${name}`);
    }
  }

  nav.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) show(b.dataset.tab, true);
  });
  window.addEventListener("hashchange", () =>
    show(decodeURIComponent(location.hash.slice(1)), false));
  show(decodeURIComponent(location.hash.slice(1)) || names[0], false);
})();
