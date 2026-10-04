(() => {
  const STORAGE_PREFIX = "cardOrder:";

  function loadOrder(key) {
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + key);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  }

  function saveOrder(key, order) {
    try {
      localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(order));
    } catch {
      // localStorage unavailable (private browsing, quota) - the custom
      // order just won't survive a reload.
    }
  }

  function cardIds(grid) {
    return Array.from(grid.children).map((card) => card.dataset.canonicId);
  }

  function applyOrder(grid, order) {
    if (!order.length) return;
    const byId = {};
    Array.from(grid.children).forEach((card) => {
      byId[card.dataset.canonicId] = card;
    });
    const known = order.filter((id) => byId[id]);
    const rest = cardIds(grid).filter((id) => !known.includes(id));
    known.concat(rest).forEach((id) => grid.appendChild(byId[id]));
  }

  function wireHandle(grid, key, handle) {
    const card = handle.closest(".device-card");

    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      card.classList.add("card-dragging");
      document.body.classList.add("card-reordering");
      handle.setPointerCapture(e.pointerId);

      const onMove = (ev) => {
        const target = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".device-card");
        if (!target || target === card || target.parentElement !== grid) return;
        const rect = target.getBoundingClientRect();
        const before = ev.clientX < rect.left + rect.width / 2;
        grid.insertBefore(card, before ? target : target.nextSibling);
      };
      const stop = () => {
        handle.releasePointerCapture(e.pointerId);
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", stop);
        card.classList.remove("card-dragging");
        document.body.classList.remove("card-reordering");
        saveOrder(key, cardIds(grid));
      };
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", stop);
    });
  }

  function initGrid(grid) {
    grid.dataset.reorderInit = "1";
    const key = grid.dataset.reorderKey;
    if (!key) return;
    applyOrder(grid, loadOrder(key));
    grid.querySelectorAll(".card-drag-handle").forEach((handle) => wireHandle(grid, key, handle));
  }

  function initAll(scope) {
    (scope || document).querySelectorAll(".device-grid:not([data-reorder-init])").forEach(initGrid);
  }

  document.addEventListener("DOMContentLoaded", () => initAll(document));
  document.addEventListener("htmx:afterSwap", (e) => initAll(e.detail.target));
})();
