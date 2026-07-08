#!/usr/bin/env python3
"""Coleta diária de novidades para o Agente Tributário.

Atualiza três arquivos:
  - data/reforma_tributaria.json  -> novidades gerais sobre a Reforma Tributária (IBS/CBS/IS)
  - data/icms_sre.json             -> Portarias SRE (ICMS): novidades/repercussão na imprensa
  - data/icms_base_legal.json      -> Portarias SRE (ICMS): registro legal oficial completo

Fontes:
  - Reforma Tributária: Google News RSS, com consultas separadas para fontes
    oficiais (gov.br, camara.leg.br, senado.leg.br, Comitê Gestor do IBS) e
    para a imprensa especializada em tributos.
  - ICMS/SRE (Novidades): Google News RSS localizando matérias de portais
    especializados que citam o número da Portaria SRE, combinado com as
    portarias do ano corrente extraídas direto da listagem oficial.
  - ICMS/SRE (Base Legal): raspagem direta da listagem oficial da SEFAZ-SP em
    legislacao.fazenda.sp.gov.br/Paginas/Atos.aspx?Tipo=Portarias%20CAT/SRE,
    uma página por ano. Na primeira execução (arquivo vazio) percorre todo o
    histórico disponível (2011 em diante); nas execuções seguintes checa
    apenas o ano corrente e o anterior, o suficiente para pegar as novas
    portarias assim que forem publicadas. A data exata (dia/mês) de cada
    portaria é obtida visitando a página de detalhe, mas em lote pequeno por
    execução (para não sobrecarregar o site), com autopreenchimento gradual.

O script é incremental: itens já conhecidos (mesmo link, ou mesmo número de
portaria) são preservados e apenas novos itens são adicionados.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REFORMA_FILE = DATA_DIR / "reforma_tributaria.json"
ICMS_FILE = DATA_DIR / "icms_sre.json"
ICMS_BASE_LEGAL_FILE = DATA_DIR / "icms_base_legal.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAX_ITEMS = 150
REQUEST_TIMEOUT = 20


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


def google_news_rss(query, when="30d"):
    """Fetch Google News RSS results for a query. Returns list of dicts."""
    q = quote(f"{query} when:{when}")
    url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-BR"
    try:
        raw = http_get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao consultar Google News RSS ({query!r}): {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"[warn] resposta RSS inválida para {query!r}: {exc}", file=sys.stderr)
        return []

    items = []
    for item in root.findall(".//item"):
        title_raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = strip_html(item.findtext("description") or "")
        source_el = item.find("source")
        source_name = source_el.text.strip() if source_el is not None and source_el.text else None

        title = title_raw
        if not source_name and " - " in title_raw:
            title, _, source_name = title_raw.rpartition(" - ")

        try:
            dt = parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_iso = dt.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            date_iso = None

        if not link or not title or not date_iso:
            continue

        items.append({
            "title": title.strip(),
            "link": link,
            "date": date_iso,
            "source": source_name or "Google News",
            "summary": description[:280],
        })
    return items


def dedupe_by_link(existing_items, new_items, key="link"):
    seen = {it.get(key) for it in existing_items if it.get(key)}
    merged = list(existing_items)
    added = 0
    for it in new_items:
        if it.get(key) and it[key] not in seen:
            merged.append(it)
            seen.add(it[key])
            added += 1
    return merged, added


def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[warn] {path} corrompido, recriando.", file=sys.stderr)
    return {"last_updated": None, "items": []}


def save_json(path, payload):
    payload = dict(payload)
    payload["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Reforma Tributária
# ---------------------------------------------------------------------------

REFORMA_QUERIES_OFICIAIS = [
    "reforma tributária IBS CBS site:gov.br",
    "reforma tributária Comitê Gestor do IBS",
    "reforma tributária Receita Federal regulamentação",
    "reforma tributária Congresso Nacional projeto de lei",
]

REFORMA_QUERIES_IMPRENSA = [
    "reforma tributária IBS CBS",
    "reforma tributária Imposto Seletivo",
    "reforma tributária split payment",
]


def collect_reforma_tributaria():
    collected = []
    for q in REFORMA_QUERIES_OFICIAIS:
        collected.extend(google_news_rss(q))
        time.sleep(1)
    for q in REFORMA_QUERIES_IMPRENSA:
        collected.extend(google_news_rss(q))
        time.sleep(1)
    return collected


# ---------------------------------------------------------------------------
# ICMS - Portarias SRE (SEFAZ-SP)
# ---------------------------------------------------------------------------

PORTARIA_SRE_RE = re.compile(
    r"Portaria\s+SRE\s*n?[ºo°]?\s*(\d+)\s*,?\s*de\s+\d{1,2}\s+de\s+\w+\s+de\s+(\d{4})",
    re.IGNORECASE,
)
PORTARIA_SRE_SHORT_RE = re.compile(
    r"Portaria\s+SRE\s*n?[ºo°]?\s*(\d+)\s*/\s*(\d{4})", re.IGNORECASE,
)

LEGISLACAO_BASE = "https://legislacao.fazenda.sp.gov.br"
ATOS_LIST_URL = f"{LEGISLACAO_BASE}/Paginas/Atos.aspx"
LEGISLACAO_SOURCE_LABEL = "SEFAZ-SP (legislação oficial)"

FIRST_YEAR_AVAILABLE = 2011

# A listagem de Atos.aspx não é uma tabela HTML simples: cada ato aparece como
# um objeto JSON (serializado no estilo .NET, com \/ e \uXXXX escapados)
# embutido na página para renderização em JavaScript. Extraímos os campos
# "Title" (ex.: "Portaria SRE 1 de 2026"), "DataPublicacao." (data ISO exata)
# e "Ementa" (resumo) diretamente desses blocos.
ROW_TITLE_RE = re.compile(r'"Title"\s*:\s*"((?:\\.|[^"\\])*)"')
DATA_PUBLICACAO_RE = re.compile(r'"DataPublicacao\."\s*:\s*"((?:\\.|[^"\\])*)"')
EMENTA_RE = re.compile(r'"Ementa"\s*:\s*"((?:\\.|[^"\\])*)"')
PORTARIA_SRE_LISTING_RE = re.compile(r"Portaria\s+SRE\s+(\d+)\s+de\s+(\d{4})", re.IGNORECASE)


def _json_unescape(value):
    """Decode a .NET/JS-style escaped string (\\uXXXX, \\/) captured from the page."""
    try:
        return json.loads(f'"{value}"')
    except (json.JSONDecodeError, ValueError):
        return value


def parse_atos_table(html_text):
    """Parse the embedded per-item JSON blocks from an Atos.aspx listing page."""
    items = []
    title_matches = list(ROW_TITLE_RE.finditer(html_text))
    for idx, title_match in enumerate(title_matches):
        title_raw = _json_unescape(title_match.group(1))
        m = PORTARIA_SRE_LISTING_RE.search(title_raw)
        if not m:
            continue
        number, year = m.groups()

        window_end = title_matches[idx + 1].start() if idx + 1 < len(title_matches) else min(len(html_text), title_match.end() + 4000)
        window = html_text[title_match.end():window_end]

        date_iso = None
        date_match = DATA_PUBLICACAO_RE.search(window)
        if date_match:
            date_iso = _json_unescape(date_match.group(1))[:10]

        ementa = ""
        ementa_match = EMENTA_RE.search(window)
        if ementa_match:
            ementa = _json_unescape(ementa_match.group(1))

        link = f"{LEGISLACAO_BASE}/Paginas/Portaria-SRE-{number}-de-{year}.aspx"
        items.append({
            "number": f"{number}/{year}",
            "title": ementa or title_raw,
            "link": link,
            "date": date_iso or f"{year}-01-01",
        })
    return items


def fetch_year_listing(year):
    """Fetch and parse the official Portarias SRE listing for a given year.

    Returns None (not []) on a fetch failure, so callers can tell "the site
    didn't answer" apart from "this year genuinely has no Portaria SRE".
    """
    url = f"{ATOS_LIST_URL}?Tipo=Portarias%20CAT/SRE&StartDate={year}-01-01&EndDate={year}-12-31"
    try:
        raw = http_get(url).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao listar Portarias SRE de {year}: {exc}", file=sys.stderr)
        return None
    return parse_atos_table(raw)


def collect_icms_sre_news_fallback():
    items = google_news_rss('"Portaria SRE" ICMS São Paulo', when="45d")
    normalized = []
    for it in items:
        text = f"{it['title']} {it['summary']}"
        m = PORTARIA_SRE_RE.search(text) or PORTARIA_SRE_SHORT_RE.search(text)
        number = f"{m.group(1)}/{m.group(2)}" if m else None
        normalized.append({
            "number": number,
            "title": it["title"],
            "link": it["link"],
            "date": it["date"],
            "source": it["source"],
            "summary": it["summary"],
        })
    return normalized


def collect_icms_sre_official_recentes():
    """Portarias SRE do ano corrente, direto da listagem oficial (para a aba Novidades)."""
    current_year = datetime.now(timezone.utc).year
    results = []
    for it in fetch_year_listing(current_year) or []:
        results.append({
            "number": it["number"],
            "title": it["title"],
            "link": it["link"],
            "date": it["date"],
            "source": LEGISLACAO_SOURCE_LABEL,
            "summary": "",
        })
    return results


def collect_icms_sre():
    official = collect_icms_sre_official_recentes()
    news_fallback = collect_icms_sre_news_fallback()
    return official + news_fallback


def merge_icms_items(existing_items, new_items):
    """Dedupe primarily by Portaria number when available, else by link."""
    by_number = {it.get("number"): it for it in existing_items if it.get("number")}
    by_link = {it.get("link") for it in existing_items if it.get("link")}
    merged = list(existing_items)
    added = 0
    for it in new_items:
        num = it.get("number")
        if num and num in by_number:
            continue
        if not num and it.get("link") in by_link:
            continue
        merged.append(it)
        if num:
            by_number[num] = it
        if it.get("link"):
            by_link.add(it["link"])
        added += 1
    return merged, added


# ---------------------------------------------------------------------------
# ICMS - Base Legal (registro oficial completo das Portarias SRE)
# ---------------------------------------------------------------------------

def collect_icms_base_legal(store):
    existing_items = store.get("items", [])
    by_number = {it["number"]: it for it in existing_items if it.get("number")}
    current_year = datetime.now(timezone.utc).year
    backfill_complete = store.get("backfill_complete", False)
    doing_backfill = not backfill_complete

    if doing_backfill:
        # Primeira vez: percorre todo o histórico disponível.
        years = list(range(FIRST_YEAR_AVAILABLE, current_year + 1))
    else:
        # Depois do backfill: só o ano corrente e o anterior já bastam para
        # pegar novas portarias assim que forem publicadas.
        years = [current_year, current_year - 1]

    discovered = []
    all_years_ok = True
    for year in years:
        year_items = fetch_year_listing(year)
        if year_items is None:
            all_years_ok = False
            continue
        discovered.extend(year_items)
        time.sleep(1)

    added = 0
    updated = 0
    for it in discovered:
        canonical = {
            "number": it["number"],
            "title": it["title"],
            "link": it["link"],
            "date": it["date"],
            "source": LEGISLACAO_SOURCE_LABEL,
            "summary": "",
        }
        previous = by_number.get(it["number"])
        if previous is None:
            added += 1
        elif previous != canonical:
            updated += 1
        by_number[it["number"]] = canonical

    merged = list(by_number.values())
    # Só marca o backfill como concluído se todas as buscas do histórico
    # completo realmente funcionaram; caso contrário, tenta de novo no
    # próximo dia em vez de assumir sucesso silenciosamente.
    if doing_backfill and all_years_ok:
        backfill_complete = True
    return merged, added, updated, backfill_complete


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    reforma_store = load_json(REFORMA_FILE)
    new_reforma = collect_reforma_tributaria()
    merged_reforma, added_r = dedupe_by_link(reforma_store.get("items", []), new_reforma)
    merged_reforma.sort(key=lambda it: it.get("date") or "", reverse=True)
    merged_reforma = merged_reforma[:MAX_ITEMS]
    save_json(REFORMA_FILE, {"items": merged_reforma})
    print(f"[reforma_tributaria] +{added_r} novos itens, total {len(merged_reforma)}")

    icms_store = load_json(ICMS_FILE)
    new_icms = collect_icms_sre()
    merged_icms, added_i = merge_icms_items(icms_store.get("items", []), new_icms)
    merged_icms.sort(key=lambda it: it.get("date") or "", reverse=True)
    merged_icms = merged_icms[:MAX_ITEMS]
    save_json(ICMS_FILE, {"items": merged_icms})
    print(f"[icms_sre] +{added_i} novos itens, total {len(merged_icms)}")

    base_legal_store = load_json(ICMS_BASE_LEGAL_FILE)
    merged_base_legal, added_bl, updated_bl, backfill_complete = collect_icms_base_legal(base_legal_store)
    merged_base_legal.sort(key=lambda it: it.get("date") or "", reverse=True)
    save_json(ICMS_BASE_LEGAL_FILE, {"items": merged_base_legal, "backfill_complete": backfill_complete})
    print(
        f"[icms_base_legal] +{added_bl} novos itens, {updated_bl} atualizados, "
        f"total {len(merged_base_legal)} (backfill_complete={backfill_complete})"
    )


if __name__ == "__main__":
    main()
