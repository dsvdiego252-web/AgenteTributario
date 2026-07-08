(function () {
  "use strict";

  const NEW_ITEM_WINDOW_DAYS = 3;

  const state = {
    reforma: [],
    icms: [],
  };

  function qs(sel, ctx) { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + (iso.length <= 10 ? "T00:00:00" : ""));
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
  }

  function formatDateTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("pt-BR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function isRecent(iso) {
    if (!iso) return false;
    const d = new Date(iso + (iso.length <= 10 ? "T00:00:00" : ""));
    if (isNaN(d)) return false;
    const diffDays = (Date.now() - d.getTime()) / 86400000;
    return diffDays >= 0 && diffDays <= NEW_ITEM_WINDOW_DAYS;
  }

  function escapeHtml(str) {
    return String(str || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function renderCard(item, kind) {
    const title = escapeHtml(item.title);

    const badgeNew = isRecent(item.date) ? '<span class="item-badge-new">Novo</span>' : "";
    const summary = item.summary ? `<p class="item-summary">${escapeHtml(item.summary)}</p>` : "";
    const source = item.source ? `<span class="item-source">${escapeHtml(item.source)}</span>` : "";
    const number = kind === "icms" && item.number
      ? `<span class="item-number">Portaria SRE ${escapeHtml(item.number)}</span>`
      : "";

    return `
      <article class="item-card">
        <div class="item-card-head">
          <h3 class="item-title"><a href="${escapeHtml(item.link)}" target="_blank" rel="noopener noreferrer">${title}</a></h3>
          <span class="item-date">${formatDate(item.date)}</span>
        </div>
        <div class="item-meta">${number}${source}${badgeNew}</div>
        ${summary}
      </article>`;
  }

  function renderList(kind) {
    const listEl = qs(kind === "icms" ? "#list-icms" : "#list-reforma");
    const countEl = qs(kind === "icms" ? "#count-icms" : "#count-reforma");
    const searchEl = qs(kind === "icms" ? "#search-icms" : "#search-reforma");
    const query = (searchEl && searchEl.value || "").trim().toLowerCase();

    let items = state[kind] || [];
    if (query) {
      items = items.filter((it) => {
        const haystack = [it.title, it.summary, it.number, it.source].join(" ").toLowerCase();
        return haystack.includes(query);
      });
    }

    countEl.textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;

    if (!items.length) {
      listEl.innerHTML = '<p class="empty">Nenhum item encontrado.</p>';
      return;
    }

    listEl.innerHTML = items.map((it) => renderCard(it, kind)).join("");
  }

  async function loadData(path) {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new Error(`Falha ao carregar ${path}: ${res.status}`);
    return res.json();
  }

  function mostRecentUpdate(dates) {
    const valid = dates.filter(Boolean).map((d) => new Date(d)).filter((d) => !isNaN(d));
    if (!valid.length) return null;
    return new Date(Math.max(...valid.map((d) => d.getTime())));
  }

  async function init() {
    let reformaMeta = null;
    let icmsMeta = null;

    try {
      reformaMeta = await loadData("data/reforma_tributaria.json");
      state.reforma = (reformaMeta.items || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    } catch (err) {
      qs("#list-reforma").innerHTML = '<p class="error">Não foi possível carregar as novidades da Reforma Tributária.</p>';
    }

    try {
      icmsMeta = await loadData("data/icms_sre.json");
      state.icms = (icmsMeta.items || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    } catch (err) {
      qs("#list-icms").innerHTML = '<p class="error">Não foi possível carregar as Portarias SRE.</p>';
    }

    renderList("reforma");
    renderList("icms");

    const lastUpdate = mostRecentUpdate([
      reformaMeta && reformaMeta.last_updated,
      icmsMeta && icmsMeta.last_updated,
    ]);
    qs("#global-updated").textContent = lastUpdate
      ? `Última atualização: ${formatDateTime(lastUpdate.toISOString())}`
      : "Última atualização indisponível";

    qs("#search-reforma").addEventListener("input", () => renderList("reforma"));
    qs("#search-icms").addEventListener("input", () => renderList("icms"));
  }

  function setupTabs() {
    qsa(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        qsa(".tab-btn").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
        qsa(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        qs("#tab-" + btn.dataset.tab).classList.add("active");
      });
    });

    qsa(".subtab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        qsa(".subtab-btn").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
        qsa(".subtab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        qs("#subtab-" + btn.dataset.subtab).classList.add("active");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupTabs();
    init();
  });
})();
