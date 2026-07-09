#!/usr/bin/env python3
"""Coleta de dados para a aba "Consulta Produto" do Agente Tributário.

Gera três arquivos, todos indexados por NCM (8 dígitos, só números):
  - data/ncm_tipi.json          -> NCM + descrição (Siscomex) + alíquota de IPI (TIPI)
  - data/cest_st_sp.json        -> CEST/segmento/MVA original (Portaria CAT 68/2019 - ICMS-ST/SP)
  - data/pis_cofins_especial.json -> NCMs com PIS/COFINS monofásico ou alíquota zero
                                      (tabelas 4.3.10/4.3.13 do SPED Contribuições), com o
                                      código da natureza da receita

Diferente do update_data.py (notícias, roda todo dia), este script busca tabelas de
referência que mudam pouco — pensado para rodar semanalmente.

Fontes oficiais:
  - NCM/descrição: API pública do Portal Único Siscomex
    (portalunico.siscomex.gov.br/classif/api/publico/nomenclatura/download/json)
  - IPI: planilha TIPI da Receita Federal (gov.br)
  - CEST/MVA: anexos da Portaria CAT 68/2019 (legislacao.fazenda.sp.gov.br)
  - PIS/COFINS monofásico/alíquota zero: tabelas do SPED Contribuições (sped.rfb.gov.br)

Os benefícios de ICMS (isenção/redução de base de cálculo dos Anexos I e II do
RICMS/SP) e a alíquota interna do ICMS NÃO são gerados por este script — ficam em
data/icms_beneficios_sample.json, uma amostra pequena curada manualmente, porque
esses anexos são texto legal (não uma tabela "NCM -> benefício") e exigem
interpretação jurídica.

Este script escreve logs de diagnóstico verbosos (contagem de itens, trechos de
resposta quando o parser não encontra nada) porque as fontes de CEST/MVA e
PIS/COFINS não puderam ser validadas ao vivo durante o desenvolvimento — a ideia é
que, se o parser não bater com o formato real na primeira execução em produção, dá
para corrigir rapidamente lendo esses logs, em vez de descobrir o formato às cegas.
"""

import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
NCM_TIPI_FILE = DATA_DIR / "ncm_tipi.json"
CEST_ST_FILE = DATA_DIR / "cest_st_sp.json"
PIS_COFINS_FILE = DATA_DIR / "pis_cofins_especial.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 40


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read()


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def only_digits(text):
    return re.sub(r"\D", "", text or "")


def strip_accents(text):
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c))


def save_json(path, payload):
    payload = dict(payload)
    payload["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# NCM + descrição (Portal Único Siscomex)
# ---------------------------------------------------------------------------

NCM_API_URL = "https://portalunico.siscomex.gov.br/classif/api/publico/nomenclatura/download/json?perfil=PUBLICO"


def fetch_ncm_table():
    """Baixa a tabela pública de NCM (código + descrição) do Portal Único Siscomex."""
    try:
        raw = http_get(NCM_API_URL)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao baixar tabela NCM do Siscomex: {exc}", file=sys.stderr)
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[warn] resposta da tabela NCM não é JSON válido: {exc}", file=sys.stderr)
        print(f"[debug] primeiros 500 bytes: {raw[:500]!r}", file=sys.stderr)
        return {}

    rows = None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and "codigo" in value[0]:
                rows = value
                break
    if rows is None:
        print(f"[warn] não encontrei a lista de NCMs na resposta; chaves de topo: {list(data.keys()) if isinstance(data, dict) else type(data)}", file=sys.stderr)
        return {}

    print(f"[debug] NCM Siscomex: {len(rows)} linhas brutas recebidas", file=sys.stderr)

    ncm_map = {}
    for row in rows:
        codigo = only_digits(str(row.get("codigo", "")))
        if len(codigo) != 8:
            continue
        descricao = strip_html(row.get("descricao") or "")
        if not descricao:
            continue
        ncm_map[codigo] = descricao

    print(f"[debug] NCM Siscomex: {len(ncm_map)} códigos de 8 dígitos com descrição", file=sys.stderr)
    return ncm_map


# ---------------------------------------------------------------------------
# IPI (TIPI - Receita Federal)
# ---------------------------------------------------------------------------

TIPI_CANDIDATE_URLS = [
    "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/legislacao/documentos-e-arquivos/tipi.xlsx/view",
    "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/legislacao/documentos-e-arquivos/tipi.xlsx",
    "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/legislacao/documentos-e-arquivos/tipi.xlsx/@@download/file",
]


def fetch_tipi_ipi_rates():
    """Baixa e faz o parse da planilha TIPI, retornando {ncm: aliquota_ipi_str}."""
    try:
        import openpyxl
    except ImportError:
        print("[warn] openpyxl não instalado; pulando alíquotas de IPI (TIPI).", file=sys.stderr)
        return {}

    raw = None
    used_url = None
    for url in TIPI_CANDIDATE_URLS:
        try:
            candidate = http_get(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"[warn] falha ao acessar {url}: {exc}", file=sys.stderr)
            continue
        if candidate[:2] == b"PK":
            raw = candidate
            used_url = url
            break
        print(f"[debug] {url} não retornou um .xlsx (assinatura: {candidate[:20]!r})", file=sys.stderr)

    if raw is None:
        print("[warn] não consegui baixar a TIPI em nenhuma das URLs candidatas.", file=sys.stderr)
        return {}

    print(f"[debug] TIPI baixada de {used_url} ({len(raw)} bytes)", file=sys.stderr)

    import io
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # arquivo corrompido/formato inesperado
        print(f"[warn] falha ao abrir a planilha TIPI: {exc}", file=sys.stderr)
        return {}

    sheet = wb[wb.sheetnames[0]]

    header_row_idx = None
    col_ncm = col_aliquota = None
    for i, row in enumerate(sheet.iter_rows(min_row=1, max_row=30, values_only=True)):
        normalized = [strip_accents(strip_html(str(c)).lower()) if c else "" for c in row]
        for j, cell in enumerate(normalized):
            if "ncm" in cell and col_ncm is None:
                col_ncm = j
            if "aliquota" in cell and col_aliquota is None:
                col_aliquota = j
        if col_ncm is not None and col_aliquota is not None:
            header_row_idx = i + 1
            break

    if header_row_idx is None or col_ncm is None or col_aliquota is None:
        print(
            f"[warn] não encontrei as colunas NCM/Alíquota no cabeçalho da TIPI "
            f"(col_ncm={col_ncm}, col_aliquota={col_aliquota}). Primeiras linhas: "
            f"{[list(r) for r in sheet.iter_rows(min_row=1, max_row=5, values_only=True)]}",
            file=sys.stderr,
        )
        return {}

    print(f"[debug] TIPI: cabeçalho na linha {header_row_idx}, col_ncm={col_ncm}, col_aliquota={col_aliquota}", file=sys.stderr)

    ipi_map = {}
    for row in sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
        if col_ncm >= len(row):
            continue
        codigo = only_digits(str(row[col_ncm] or ""))
        if len(codigo) != 8:
            continue
        aliquota = str(row[col_aliquota] or "").strip() if col_aliquota < len(row) else ""
        if not aliquota:
            continue
        ipi_map[codigo] = aliquota

    print(f"[debug] TIPI: {len(ipi_map)} alíquotas de IPI extraídas", file=sys.stderr)
    return ipi_map


def build_ncm_tipi():
    ncm_map = fetch_ncm_table()
    ipi_map = fetch_tipi_ipi_rates()
    items = []
    for codigo, descricao in ncm_map.items():
        items.append({
            "ncm": codigo,
            "descricao": descricao,
            "ipi_aliquota": ipi_map.get(codigo),
        })
    return items


# ---------------------------------------------------------------------------
# CEST / MVA (Portaria CAT 68/2019 - ICMS-ST em São Paulo)
# ---------------------------------------------------------------------------

CAT68_URL = "https://legislacao.fazenda.sp.gov.br/Paginas/Portaria-CAT-68-de-2019.aspx"
LEGISLACAO_BASE = "https://legislacao.fazenda.sp.gov.br"

ANEXO_LINK_RE = re.compile(r'href="([^"]*[Aa]nexo[^"]*)"[^>]*>([^<]*)</a>', re.IGNORECASE)
CEST_RE = re.compile(r"(\d{2}\.\d{3}\.\d{2})")
NCM_TEXT_RE = re.compile(r"(\d{4}\.?\d{2}\.?\d{2})")
MVA_RE = re.compile(r"(\d{1,3}(?:,\d{1,2})?)\s*%")


def fetch_cat68_annex_links():
    try:
        raw = http_get(CAT68_URL).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao acessar a Portaria CAT 68/2019: {exc}", file=sys.stderr)
        return []

    print(f"[debug] Portaria CAT 68/2019: página principal com {len(raw)} bytes", file=sys.stderr)

    links = []
    seen = set()
    for href, label in ANEXO_LINK_RE.findall(raw):
        url = href if href.startswith("http") else f"{LEGISLACAO_BASE}/{href.lstrip('/')}"
        if url in seen:
            continue
        seen.add(url)
        links.append((url, strip_html(label)))

    print(f"[debug] Portaria CAT 68/2019: {len(links)} links de anexo encontrados", file=sys.stderr)
    if not links:
        print(f"[debug] trecho da página principal (1500 chars): {raw[:1500]!r}", file=sys.stderr)
    return links


def parse_cest_rows_from_html(raw, anexo_label):
    """Best-effort: procura linhas de tabela contendo CEST + NCM + MVA."""
    rows = []
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", raw, re.IGNORECASE | re.DOTALL):
        row_html = row_match.group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cells) < 3:
            continue
        cells_text = [strip_html(c) for c in cells]
        joined = " | ".join(cells_text)

        cest_match = CEST_RE.search(joined)
        ncm_match = NCM_TEXT_RE.search(joined)
        if not cest_match or not ncm_match:
            continue

        mva_match = MVA_RE.search(joined)
        # A célula de descrição costuma ser a que tem mais letras (as outras são
        # majoritariamente números: item, CEST, NCM, MVA) — mais robusto do que
        # supor uma posição fixa de coluna, que pode variar entre anexos.
        descricao_cell = max(cells_text, key=lambda c: sum(ch.isalpha() for ch in c), default="")
        rows.append({
            "cest": cest_match.group(1),
            "ncm": only_digits(ncm_match.group(1)),
            "segmento": anexo_label,
            "descricao": descricao_cell,
            "mva_original": mva_match.group(1).replace(",", ".") if mva_match else None,
        })
    return rows


def fetch_cest_st_sp():
    annex_links = fetch_cat68_annex_links()
    all_rows = []
    for url, label in annex_links:
        try:
            raw = http_get(url).decode("utf-8", errors="ignore")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"[warn] falha ao acessar anexo {url}: {exc}", file=sys.stderr)
            continue
        rows = parse_cest_rows_from_html(raw, label)
        print(f"[debug] anexo {label!r} ({url}): {len(raw)} bytes, {len(rows)} linhas CEST/NCM extraídas", file=sys.stderr)
        if not rows:
            print(f"[debug] trecho do anexo {label!r} (1000 chars): {raw[:1000]!r}", file=sys.stderr)
        all_rows.extend(rows)
        time.sleep(1)
    return all_rows


# ---------------------------------------------------------------------------
# PIS/COFINS monofásico / alíquota zero (tabelas do SPED Contribuições)
# ---------------------------------------------------------------------------

SPED_TABELAS = [
    ("monofasico", "http://sped.rfb.gov.br/arquivo/show/1638"),   # Tabela 4.3.10
    ("aliquota_zero", "http://sped.rfb.gov.br/arquivo/show/1643"),  # Tabela 4.3.13
]

# Âncora no rótulo "natureza da receita" para não confundir com pedaços do
# próprio código NCM (que também tem o formato NN.NN em algumas posições).
NATUREZA_RECEITA_RE = re.compile(
    r"natureza\s*(?:da)?\s*receita[^\d]{0,20}(\d{1,2}\.\d{2})", re.IGNORECASE
)


def parse_sped_table(raw_text, regime):
    """Best-effort: extrai NCM + código da natureza da receita de um texto tabular."""
    rows = []
    for line in raw_text.splitlines():
        ncm_match = NCM_TEXT_RE.search(line)
        codigo_match = NATUREZA_RECEITA_RE.search(line)
        if not ncm_match or not codigo_match:
            continue
        rows.append({
            "ncm": only_digits(ncm_match.group(1)),
            "regime": regime,
            "codigo_natureza_receita": codigo_match.group(1),
            "descricao": strip_html(line)[:200],
        })
    return rows


def fetch_pis_cofins_especial():
    all_rows = []
    for regime, url in SPED_TABELAS:
        try:
            raw = http_get(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"[warn] falha ao acessar tabela SPED ({regime}) {url}: {exc}", file=sys.stderr)
            continue

        print(f"[debug] tabela SPED {regime} ({url}): {len(raw)} bytes, assinatura {raw[:10]!r}", file=sys.stderr)

        if raw[:2] == b"PK":
            try:
                import io
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
                sheet = wb[wb.sheetnames[0]]
                text_lines = []
                for row in sheet.iter_rows(values_only=True):
                    text_lines.append(" | ".join(str(c) for c in row if c is not None))
                raw_text = "\n".join(text_lines)
            except Exception as exc:
                print(f"[warn] falha ao abrir tabela SPED ({regime}) como xlsx: {exc}", file=sys.stderr)
                continue
        else:
            raw_text = raw.decode("utf-8", errors="ignore")

        rows = parse_sped_table(raw_text, regime)
        print(f"[debug] tabela SPED {regime}: {len(rows)} linhas NCM/natureza extraídas", file=sys.stderr)
        if not rows:
            print(f"[debug] trecho da tabela SPED {regime} (1000 chars): {raw_text[:1000]!r}", file=sys.stderr)
        all_rows.extend(rows)
        time.sleep(1)
    return all_rows


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ncm_items = build_ncm_tipi()
    save_json(NCM_TIPI_FILE, {"items": ncm_items})
    print(f"[ncm_tipi] {len(ncm_items)} NCMs salvos")

    cest_rows = fetch_cest_st_sp()
    save_json(CEST_ST_FILE, {"items": cest_rows})
    print(f"[cest_st_sp] {len(cest_rows)} linhas CEST/MVA salvas")

    pis_cofins_rows = fetch_pis_cofins_especial()
    save_json(PIS_COFINS_FILE, {"items": pis_cofins_rows})
    print(f"[pis_cofins_especial] {len(pis_cofins_rows)} linhas salvas")


if __name__ == "__main__":
    main()
