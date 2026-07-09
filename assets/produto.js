(function () {
  "use strict";

  const MAX_CANDIDATOS = 20;
  const MIN_QUERY_LENGTH = 3;

  const PIS_COFINS_REGRA_GERAL = {
    lucro_real: { pis: "1,65%", cofins: "7,60%", regime: "não cumulativo" },
    lucro_presumido: { pis: "0,65%", cofins: "3,00%", regime: "cumulativo" },
  };

  const REGIME_LABELS = {
    monofasico: "Monofásico",
    aliquota_zero: "Alíquota zero",
  };

  const state = {
    ncmList: [],       // [{ncm, descricao, ipi_aliquota, _search}]
    cestByNcm: new Map(),
    pisCofinsByNcm: new Map(),
    beneficios: [],
    aliquotasRicms: null,
    alimenticios: null,
    reducoesAlimenticios: null,
    loaded: false,
  };

  function qs(sel, ctx) { return (ctx || document).querySelector(sel); }

  function escapeHtml(str) {
    return String(str || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function stripAccents(str) {
    return String(str || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function normalize(str) {
    return stripAccents(String(str || "").toLowerCase()).trim();
  }

  function formatIpi(value) {
    if (value == null) return null;
    if (typeof value === "number") {
      return `${Math.round(value * 100) / 100}%`;
    }
    const str = String(value);
    if (/^\d+(\.\d+)?$/.test(str)) {
      return `${Math.round(parseFloat(str) * 100) / 100}%`;
    }
    return escapeHtml(str);
  }

  function formatNcm(ncm) {
    if (!ncm || ncm.length !== 8) return ncm || "";
    return `${ncm.slice(0, 4)}.${ncm.slice(4, 6)}.${ncm.slice(6, 8)}`;
  }

  async function loadJson(path, fallback) {
    try {
      const res = await fetch(path, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      return await res.json();
    } catch (err) {
      return fallback;
    }
  }

  async function loadAll() {
    const [ncmData, cestData, pisCofinsData, beneficiosData, aliquotasData, alimenticiosData, reducoesData] = await Promise.all([
      loadJson("data/ncm_tipi.json", { items: [] }),
      loadJson("data/cest_st_sp.json", { items: [] }),
      loadJson("data/pis_cofins_especial.json", { items: [] }),
      loadJson("data/icms_beneficios_sample.json", { items: [] }),
      loadJson("data/icms_aliquotas_sp.json", null),
      loadJson("data/icms_alimenticios_sp.json", null),
      loadJson("data/icms_reducoes_alimenticios_sp.json", null),
    ]);
    state.aliquotasRicms = aliquotasData;
    state.alimenticios = alimenticiosData;
    state.reducoesAlimenticios = reducoesData;

    state.ncmList = (ncmData.items || []).map((it) => ({
      ...it,
      _search: normalize(it.descricao) + " " + (it.ncm || ""),
    }));

    state.cestByNcm = new Map();
    (cestData.items || []).forEach((it) => {
      if (!it.ncm) return;
      if (!state.cestByNcm.has(it.ncm)) state.cestByNcm.set(it.ncm, []);
      state.cestByNcm.get(it.ncm).push(it);
    });

    state.pisCofinsByNcm = new Map();
    (pisCofinsData.items || []).forEach((it) => {
      if (!it.ncm) return;
      if (!state.pisCofinsByNcm.has(it.ncm)) state.pisCofinsByNcm.set(it.ncm, []);
      state.pisCofinsByNcm.get(it.ncm).push(it);
    });

    state.beneficios = beneficiosData.items || [];
    state.loaded = true;
  }

  function scoreMatch(query, tokens, item) {
    const haystack = item._search;
    if (haystack.includes(query)) return 1000 - Math.abs(haystack.length - query.length) * 0.01;
    let score = 0;
    for (const token of tokens) {
      if (haystack.includes(token)) score += token.length;
    }
    return score;
  }

  function searchCandidatos(rawQuery) {
    const query = normalize(rawQuery);
    if (query.length < MIN_QUERY_LENGTH) return [];
    const tokens = query.split(/\s+/).filter(Boolean);

    return state.ncmList
      .map((item) => ({ item, score: scoreMatch(query, tokens, item) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_CANDIDATOS)
      .map((r) => r.item);
  }

  function renderCandidatos(candidatos, query) {
    const el = qs("#produto-candidatos");
    if (!state.ncmList.length) {
      el.innerHTML = '<p class="empty">Base de NCM ainda não carregada. A primeira atualização automática (Siscomex/TIPI) ainda não rodou ou está em andamento — tente novamente mais tarde.</p>';
      return;
    }
    if (!query || normalize(query).length < MIN_QUERY_LENGTH) {
      el.innerHTML = "";
      return;
    }
    if (!candidatos.length) {
      el.innerHTML = '<p class="empty">Nenhum NCM encontrado para essa descrição. Tente outras palavras.</p>';
      return;
    }
    el.innerHTML = candidatos.map((item) => `
      <button type="button" class="produto-candidato" data-ncm="${escapeHtml(item.ncm)}">
        <span class="produto-candidato-ncm">${escapeHtml(formatNcm(item.ncm))}</span>
        <span class="produto-candidato-desc">${escapeHtml(item.descricao)}</span>
      </button>
    `).join("");

    el.querySelectorAll(".produto-candidato").forEach((btn) => {
      btn.addEventListener("click", () => {
        el.querySelectorAll(".produto-candidato").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        renderResultado(btn.dataset.ncm);
        const resultado = qs("#produto-resultado");
        if (resultado) resultado.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }

  function summarizeBeneficio(ncm) {
    const matches = state.beneficios.filter((b) => ncm.startsWith(b.ncm_prefixo));
    if (!matches.length) return "Nenhum benefício mapeado nesta amostra (ver detalhes abaixo)";
    return matches
      .map((b) => `${b.tipo === "isencao" ? "Isenção" : "Redução de BC"} (Anexo ${escapeHtml(b.anexo)}, art. ${escapeHtml(b.artigo)}) — ver detalhes abaixo`)
      .join("; ");
  }

  function summarizeSubstituicaoTributaria(ncm) {
    const rows = state.cestByNcm.get(ncm);
    if (!rows || !rows.length) return "Não localizado nesta base — confirme na Portaria CAT 68/2019 (ver observação abaixo)";
    return rows
      .map((r) => `CEST ${escapeHtml(r.cest || "—")}${r.mva_original ? ` · MVA ${escapeHtml(r.mva_original)}%` : ""} — confirmar vigência na CAT 68/2019`)
      .join("; ");
  }

  function summarizePisCofins(ncm) {
    const rows = state.pisCofinsByNcm.get(ncm);
    if (rows && rows.length) {
      return rows
        .map((r) => `${escapeHtml(REGIME_LABELS[r.regime] || r.regime)}${r.codigo_natureza_receita ? ` (nat. ${escapeHtml(r.codigo_natureza_receita)})` : ""}`)
        .join("; ");
    }
    return "Regime geral — ver alíquotas de Lucro Real/Presumido abaixo";
  }

  function matchesAnyPrefix(ncm, prefixos) {
    return (prefixos || []).some((p) => ncm.startsWith(p.replace(/\D/g, "")));
  }

  function matchAliquotaInterna(ncm) {
    const regras = (state.aliquotasRicms && state.aliquotasRicms.regras) || [];
    let best = null;
    regras.forEach((r) => {
      if (!matchesAnyPrefix(ncm, r.ncm_prefixos)) return;
      if (r.ncm_excecoes && matchesAnyPrefix(ncm, r.ncm_excecoes)) return;
      const specificity = Math.max(...r.ncm_prefixos.map((p) => p.replace(/\D/g, "").length));
      if (!best || specificity > best._specificity) best = { ...r, _specificity: specificity };
    });
    const fecoep = state.aliquotasRicms && state.aliquotasRicms.adicional_fecoep;
    const temFecoep = fecoep && matchesAnyPrefix(ncm, fecoep.ncm_prefixos);
    return { regra: best, fecoep: temFecoep ? fecoep : null };
  }

  function matchAnexoXVI(ncm) {
    const itens = (state.alimenticios && state.alimenticios.itens) || [];
    const candidatos = [];
    itens.forEach((it) => {
      const prefixos = it.ncm || [];
      let melhor = 0;
      prefixos.forEach((p) => {
        const digits = p.replace(/\D/g, "");
        if (ncm.startsWith(digits) && digits.length > melhor) melhor = digits.length;
      });
      if (melhor > 0) candidatos.push({ item: it, especificidade: melhor });
    });
    if (!candidatos.length) return [];
    const max = Math.max(...candidatos.map((c) => c.especificidade));
    return candidatos.filter((c) => c.especificidade === max).map((c) => c.item);
  }

  function matchReducaoCestaBasica(ncm) {
    const regra = state.reducoesAlimenticios && state.reducoesAlimenticios.art3_cesta_basica;
    if (!regra) return null;
    const item = (regra.itens || []).find((it) => matchesAnyPrefix(ncm, it.ncm_prefixos));
    return item ? { regra, item } : null;
  }

  function matchReducaoArt39(ncm) {
    const regra = state.reducoesAlimenticios && state.reducoesAlimenticios.art39_produtos_alimenticios;
    if (!regra) return null;
    const prefixosCapitulo = (regra.capitulos_ncm || []);
    const prefixosAdicionais = (regra.posicoes_adicionais || []);
    const bateCapitulo = prefixosCapitulo.some((c) => ncm.startsWith(c));
    const bateAdicional = prefixosAdicionais.some((p) => ncm.startsWith(p));
    return (bateCapitulo || bateAdicional) ? regra : null;
  }

  function renderProdutosAlimenticios(ncm) {
    const anexoXviMatches = matchAnexoXVI(ncm);
    const cestaBasica = matchReducaoCestaBasica(ncm);
    const art39 = matchReducaoArt39(ncm);

    if (!anexoXviMatches.length && !cestaBasica && !art39) return "";

    let html = `<div class="produto-subsection"><h4>Produtos Alimentícios — regras específicas do RICMS/SP</h4>`;

    if (anexoXviMatches.length) {
      html += anexoXviMatches.map((it) => {
        const ivaSt = state.alimenticios.iva_st_sre_12_2026 && state.alimenticios.iva_st_sre_12_2026[it.cest];
        if (it.revogado) {
          return `
            <p class="produto-resumo">
              <strong>CEST ${escapeHtml(it.cest)}</strong> (item ${escapeHtml(it.item)} do Anexo XVI) —
              ${escapeHtml(it.descricao)}
            </p>
            <p class="produto-beneficio-tag" style="color:#a3450b;background:#fde8d8;">
              ❌ Não é mais ST desde ${escapeHtml(it.revogado_desde)} — revogado pela ${escapeHtml(it.revogado_por)}
            </p>`;
        }
        return `
          <p class="produto-resumo">
            <strong>CEST ${escapeHtml(it.cest)}</strong> (item ${escapeHtml(it.item)} do Anexo XVI) —
            ${escapeHtml(it.descricao)}
          </p>
          <p class="produto-beneficio-tag">✅ Sujeito a ICMS-ST${ivaSt ? ` — IVA-ST ${escapeHtml(ivaSt)} (Portaria SRE 12/2026)` : " — IVA-ST não localizado na Portaria SRE 12/2026 para este CEST"}</p>`;
      }).join("");
    } else {
      html += `<p class="produto-obs">Não localizado no Anexo XVI da Portaria CAT 68/2019 (base curada) — não deveria estar sujeito à ST como produto alimentício, mas confirme no texto oficial.</p>`;
    }

    if (cestaBasica) {
      html += `
        <p class="produto-resumo"><strong>Redução de base de cálculo — Cesta Básica (Art. 3º, Anexo II)</strong></p>
        <p class="produto-obs">${escapeHtml(cestaBasica.item.descricao)} — carga tributária efetiva de <strong>${escapeHtml(cestaBasica.regra.carga_tributaria)}</strong> em operações internas (vigente até ${escapeHtml(cestaBasica.regra.vigencia_fim)}).</p>`;
    }

    if (art39) {
      html += `
        <p class="produto-resumo"><strong>Redução de base de cálculo — Produtos Alimentícios (Art. 39, Anexo II)</strong></p>
        <p class="produto-obs">Carga tributária efetiva de <strong>${escapeHtml(art39.carga_tributaria)}</strong> em saídas internas de estabelecimento fabricante/atacadista (vigente até ${escapeHtml(art39.vigencia_fim)}), <strong>desde que</strong>:</p>
        <ul class="produto-obs">${art39.condicoes.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`;
    }

    html += `</div>`;
    return html;
  }

  function renderIcmsBenefits(ncm) {
    const matches = state.beneficios.filter((b) => ncm.startsWith(b.ncm_prefixo));
    if (!matches.length) {
      return `
        <div class="produto-subsection">
          <h4>Benefícios de ICMS (Anexos I e II do RICMS/SP)</h4>
          <p class="empty">Nenhum benefício mapeado nesta amostra para este NCM. Isso não significa que não exista — a base de benefícios ainda é uma amostra reduzida. Consulte os Anexos I e II do RICMS/SP.</p>
        </div>`;
    }
    return matches.map((b) => `
      <div class="produto-subsection">
        <h4>Benefício de ICMS — ${escapeHtml(b.descricao_produto)}</h4>
        <p class="produto-beneficio-tag">${b.tipo === "isencao" ? "Isenção" : "Redução de base de cálculo"} — Anexo ${escapeHtml(b.anexo)}, art. ${escapeHtml(b.artigo)}${b.carga_tributaria_efetiva ? ` (carga efetiva ${escapeHtml(b.carga_tributaria_efetiva)})` : ""}</p>
        <p class="produto-resumo">${escapeHtml(b.resumo)}</p>
        <div class="produto-entrada-saida">
          <div class="produto-es-col">
            <strong>Entrada</strong>
            <p>${b.entrada.aplica_beneficio ? "✅ Benefício se aplica" : "❌ Benefício não se aplica (tributação normal)"}</p>
            <p class="produto-obs">${escapeHtml(b.entrada.observacao)}</p>
          </div>
          <div class="produto-es-col">
            <strong>Saída (geral)</strong>
            <p>${b.saida.aplica_beneficio ? "✅ Benefício se aplica" : "❌ Benefício não se aplica (tributação normal)"}</p>
            <p class="produto-obs">${escapeHtml(b.saida.observacao)}</p>
          </div>
          <div class="produto-es-col">
            <strong>Saída p/ consumidor final</strong>
            <p>${b.saida_consumidor_final.aplica_beneficio ? "✅ Benefício se aplica" : "❌ Benefício NÃO se aplica (alíquota integral)"}</p>
            <p class="produto-obs">${escapeHtml(b.saida_consumidor_final.observacao)}</p>
          </div>
        </div>
      </div>
    `).join("");
  }

  function renderSubstituicaoTributaria(ncm) {
    const rows = state.cestByNcm.get(ncm);
    const regraCat68 = `
      <p class="produto-obs"><strong>Regra:</strong> quem decide se um item tem ICMS-ST em São Paulo é a
      <strong>Portaria CAT 68/2019</strong> (e suas alterações posteriores) — não o Convênio ICMS 142/2018 por si só.
      O Convênio é a base nacional (define o que <em>pode</em> ter CEST); a CAT 68/2019 é quem lista o que
      <em>efetivamente</em> tem ST em SP e o MVA aplicável. Um item pode constar no Convênio e mesmo assim já ter
      sido <strong>revogado</strong> da ST em SP por uma alteração posterior da CAT 68/2019 — nesse caso ele deixa
      de ser ST a partir da data da revogação, mesmo continuando na tabela nacional.</p>`;
    if (!rows || !rows.length) {
      return `
        <div class="produto-subsection">
          <h4>Substituição Tributária (ICMS-ST)</h4>
          <p class="empty">NCM não localizado na base nacional (Convênio ICMS 142/2018) usada nesta consulta.</p>
          ${regraCat68}
        </div>`;
    }
    return `
      <div class="produto-subsection">
        <h4>Substituição Tributária (ICMS-ST)</h4>
        ${rows.map((r) => `
          <p class="produto-resumo">
            <strong>CEST ${escapeHtml(r.cest || "—")}</strong> · ${escapeHtml(r.segmento || "")}
            ${r.mva_original ? ` · MVA de referência: <strong>${escapeHtml(r.mva_original)}%</strong>` : ""}
          </p>
          ${r.descricao ? `<p class="produto-obs">${escapeHtml(r.descricao)}</p>` : ""}
        `).join("")}
        ${regraCat68}
      </div>`;
  }

  function renderPisCofins(ncm) {
    const rows = state.pisCofinsByNcm.get(ncm);
    if (rows && rows.length) {
      return `
        <div class="produto-subsection">
          <h4>PIS/COFINS</h4>
          ${rows.map((r) => `
            <p class="produto-resumo">
              <strong>${escapeHtml(REGIME_LABELS[r.regime] || r.regime)}</strong>
              ${r.codigo_natureza_receita ? ` — Código da natureza da receita: <strong>${escapeHtml(r.codigo_natureza_receita)}</strong>` : ""}
            </p>
            ${r.descricao ? `<p class="produto-obs">${escapeHtml(r.descricao)}</p>` : ""}
          `).join("")}
        </div>`;
    }
    return `
      <div class="produto-subsection">
        <h4>PIS/COFINS</h4>
        <p class="produto-obs">Não localizado nas tabelas de monofásico/alíquota zero do SPED Contribuições — segue o regime geral de tributação:</p>
        <table class="produto-tabela">
          <thead><tr><th>Regime</th><th>PIS</th><th>COFINS</th></tr></thead>
          <tbody>
            <tr><td>Lucro Real (${PIS_COFINS_REGRA_GERAL.lucro_real.regime})</td><td>${PIS_COFINS_REGRA_GERAL.lucro_real.pis}</td><td>${PIS_COFINS_REGRA_GERAL.lucro_real.cofins}</td></tr>
            <tr><td>Lucro Presumido (${PIS_COFINS_REGRA_GERAL.lucro_presumido.regime})</td><td>${PIS_COFINS_REGRA_GERAL.lucro_presumido.pis}</td><td>${PIS_COFINS_REGRA_GERAL.lucro_presumido.cofins}</td></tr>
          </tbody>
        </table>
      </div>`;
  }

  function renderResultado(ncm) {
    const item = state.ncmList.find((it) => it.ncm === ncm);
    if (!item) return;

    const ipiTexto = formatIpi(item.ipi_aliquota) || "Não localizada na TIPI";
    const aliquotaMatch = matchAliquotaInterna(item.ncm);
    const aliquotaResumo = aliquotaMatch.regra
      ? `${escapeHtml(aliquotaMatch.regra.aliquota)} (Art. ${escapeHtml(aliquotaMatch.regra.artigo)})${aliquotaMatch.fecoep ? ` + ${escapeHtml(aliquotaMatch.fecoep.percentual)} FECOEP (Art. ${escapeHtml(aliquotaMatch.fecoep.artigo)})` : ""}`
      : `18% (regra geral)${aliquotaMatch.fecoep ? ` + ${escapeHtml(aliquotaMatch.fecoep.percentual)} FECOEP (Art. ${escapeHtml(aliquotaMatch.fecoep.artigo)})` : ""}`;

    const html = `
      <article class="produto-card">
        <div class="produto-header">
          <h3>${escapeHtml(formatNcm(item.ncm))}</h3>
          <p>${escapeHtml(item.descricao)}</p>
        </div>

        <div class="produto-subsection">
          <h4>Resumo</h4>
          <table class="produto-tabela produto-tabela-resumo">
            <tbody>
              <tr><th>Produto</th><td>${escapeHtml(item.descricao)}</td></tr>
              <tr><th>NCM</th><td>${escapeHtml(formatNcm(item.ncm))}</td></tr>
              <tr><th>IPI</th><td>${ipiTexto}</td></tr>
              <tr><th>ICMS interna (SP)</th><td>${aliquotaResumo}</td></tr>
              <tr><th>Benefício ICMS (Anexo I/II)</th><td>${summarizeBeneficio(item.ncm)}</td></tr>
              <tr><th>Substituição Tributária</th><td>${summarizeSubstituicaoTributaria(item.ncm)}</td></tr>
              <tr><th>PIS/COFINS</th><td>${summarizePisCofins(item.ncm)}</td></tr>
            </tbody>
          </table>
          <p class="produto-obs">Resumo rápido — os detalhes, ressalvas e diferenças entre entrada e saída estão nas seções abaixo.</p>
        </div>

        <div class="produto-subsection">
          <h4>IPI</h4>
          <p class="produto-resumo">${formatIpi(item.ipi_aliquota) ? `Alíquota: <strong>${formatIpi(item.ipi_aliquota)}</strong>` : "Alíquota de IPI não localizada na TIPI para este NCM."}</p>
        </div>

        <div class="produto-subsection">
          <h4>ICMS — alíquota interna (SP)</h4>
          <p class="produto-resumo">
            ${aliquotaMatch.regra
              ? `<strong>${escapeHtml(aliquotaMatch.regra.aliquota)}</strong> — ${escapeHtml(aliquotaMatch.regra.descricao)} (RICMS/SP, art. ${escapeHtml(aliquotaMatch.regra.artigo)})`
              : `<strong>18%</strong> (alíquota interna geral do Estado de São Paulo, RICMS/SP art. 52, I) — não encontrada nenhuma das alíquotas diferenciadas mapeadas (arts. 53-A a 55-A) para este NCM.`}
            ${aliquotaMatch.fecoep ? `<br>+ <strong>${escapeHtml(aliquotaMatch.fecoep.percentual)}</strong> de adicional FECOEP (RICMS/SP, art. ${escapeHtml(aliquotaMatch.fecoep.artigo)}) sobre saídas a consumidor final em SP.` : ""}
          </p>
          <p class="produto-obs">Cobertura curada a partir do texto oficial dos artigos 52 a 56-C do RICMS/SP — cobre as alíquotas diferenciadas mais comuns, mas não todas as exceções pontuais (ex.: ferros/aços e cerâmicos específicos do art. 54, §§1º e 2º). Confirme casos de fronteira no texto oficial.</p>
        </div>

        ${renderProdutosAlimenticios(item.ncm)}
        ${renderIcmsBenefits(item.ncm)}
        ${renderSubstituicaoTributaria(item.ncm)}
        ${renderPisCofins(item.ncm)}
      </article>`;

    qs("#produto-resultado").innerHTML = html;
  }

  function setupSearch() {
    const input = qs("#search-produto");
    const form = qs("#form-produto");
    if (!input) return;
    let timer = null;
    const runSearch = () => {
      clearTimeout(timer);
      qs("#produto-resultado").innerHTML = "";
      renderCandidatos(searchCandidatos(input.value), input.value);
    };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(runSearch, 200);
    });
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        runSearch();
      });
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    setupSearch();
    await loadAll();
    const input = qs("#search-produto");
    if (input && input.value) {
      renderCandidatos(searchCandidatos(input.value), input.value);
    }
  });
})();
