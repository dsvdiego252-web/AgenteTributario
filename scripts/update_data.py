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
BASE_LEGAL_ENRICH_BUDGET = 25

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
PORTARIA_SRE_LISTING_RE = re.compile(r"Portaria\s+SRE\s+(\d+)\s+de\s+(\d{4})", re.IGNORECASE)
DETAIL_DATE_RE = re.compile(
    r"Portaria\s+SRE\s*n?[ºo°]?\s*\d+\s*,?\s*de\s+(\d{1,2})º?\s+de\s+(\w+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)


def parse_atos_table(html_text):
    """Parse the 'Ato Legal | Ementa' table from an Atos.aspx listing page."""
    items = []
    for row_match in ROW_RE.finditer(html_text):
        cells = CELL_RE.findall(row_match.group(1))
        if len(cells) < 2:
            continue
        link_match = LINK_RE.search(cells[0])
        if not link_match:
            continue
        href, link_text = link_match.groups()
        link_text = strip_html(link_text)
        m = PORTARIA_SRE_LISTING_RE.search(link_text)
        if not m:
            continue
        number, year = m.groups()
        ementa = strip_html(cells[1])
        link = href if href.startswith("http") else f"{LEGISLACAO_BASE}/{href.lstrip('/')}"
        items.append({
            "number": f"{number}/{year}",
            "title": ementa or link_text,
            "link": link,
            "_year": year,
        })
    return items


def fetch_year_listing(year):
    """Fetch and parse the official Portarias SRE listing for a given year."""
    url = f"{ATOS_LIST_URL}?Tipo=Portarias%20CAT/SRE&StartDate={year}-01-01&EndDate={year}-12-31"
    try:
        raw = http_get(url).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao listar Portarias SRE de {year}: {exc}", file=sys.stderr)
        return []
    items = parse_atos_table(raw)
    mentions = raw.count("Portaria SRE")
    print(
        f"[debug] listagem {year}: {len(raw)} bytes, {mentions} ocorrências de 'Portaria SRE', "
        f"{raw.count('<tr')} <tr>, {len(items)} itens extraídos",
        file=sys.stderr,
    )
    if mentions and not items:
        idx = raw.find("Portaria SRE")
        print(
            f"[debug] {year} trecho ao redor da 1ª ocorrência de 'Portaria SRE':\n"
            f"{raw[max(0, idx - 400):idx + 400]}",
            file=sys.stderr,
        )
    return items


def fetch_detail_date(url):
    """Visit a Portaria SRE detail page and extract its exact publication date."""
    try:
        raw = http_get(url).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[warn] falha ao abrir {url}: {exc}", file=sys.stderr)
        return None
    text = strip_html(raw)
    m = DETAIL_DATE_RE.search(text)
    if not m:
        idx = text.lower().find("portaria sre")
        snippet = text[max(0, idx - 60):idx + 120] if idx >= 0 else text[:180]
        print(f"[debug] data não encontrada em {url}: {snippet!r}", file=sys.stderr)
        return None
    dia, mes, ano = m.groups()
    mes_num = MESES_PT.get(mes.lower())
    if not mes_num:
        print(f"[debug] mês não reconhecido em {url}: {mes!r}", file=sys.stderr)
        return None
    try:
        return f"{int(ano):04d}-{mes_num:02d}-{int(dia):02d}"
    except ValueError:
        return None


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
    for it in fetch_year_listing(current_year):
        results.append({
            "number": it["number"],
            "title": it["title"],
            "link": it["link"],
            "date": f"{it['_year']}-01-01",
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

def collect_icms_base_legal(existing_items):
    by_number = {it["number"]: it for it in existing_items if it.get("number")}
    current_year = datetime.now(timezone.utc).year

    if not existing_items:
        # Primeira execução: percorre todo o histórico disponível.
        years = list(range(FIRST_YEAR_AVAILABLE, current_year + 1))
    else:
        # Execuções seguintes: só o ano corrente e o anterior já bastam para
        # pegar novas portarias assim que forem publicadas.
        years = [current_year, current_year - 1]

    discovered = []
    for year in years:
        discovered.extend(fetch_year_listing(year))
        time.sleep(1)

    added = 0
    for it in discovered:
        if it["number"] in by_number:
            continue
        by_number[it["number"]] = {
            "number": it["number"],
            "title": it["title"],
            "link": it["link"],
            "date": f"{it['_year']}-01-01",
            "date_precise": False,
            "source": LEGISLACAO_SOURCE_LABEL,
            "summary": "",
        }
        added += 1

    merged = list(by_number.values())

    # Enriquecimento gradual: busca a data exata (dia/mês) de itens que ainda
    # só têm o ano, sem sobrecarregar o site oficial em uma única execução.
    pending = [it for it in merged if not it.get("date_precise")]
    pending.sort(key=lambda it: it.get("date") or "", reverse=True)
    enriched = 0
    for it in pending[:BASE_LEGAL_ENRICH_BUDGET]:
        exact_date = fetch_detail_date(it["link"])
        time.sleep(1)
        if exact_date:
            it["date"] = exact_date
            it["date_precise"] = True
            enriched += 1

    return merged, added, enriched


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
    merged_base_legal, added_bl, enriched_bl = collect_icms_base_legal(base_legal_store.get("items", []))
    merged_base_legal.sort(key=lambda it: it.get("date") or "", reverse=True)
    save_json(ICMS_BASE_LEGAL_FILE, {"items": merged_base_legal})
    print(
        f"[icms_base_legal] +{added_bl} novos itens, {enriched_bl} datas exatas "
        f"enriquecidas, total {len(merged_base_legal)}"
    )


if __name__ == "__main__":
    main()
