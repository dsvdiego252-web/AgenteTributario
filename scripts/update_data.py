#!/usr/bin/env python3
"""Coleta diária de novidades para o Agente Tributário.

Atualiza dois arquivos:
  - data/reforma_tributaria.json  -> novidades gerais sobre a Reforma Tributária (IBS/CBS/IS)
  - data/icms_sre.json             -> Portarias SRE (ICMS) da SEFAZ-SP

Fontes:
  - Reforma Tributária: Google News RSS, com consultas separadas para fontes
    oficiais (gov.br, camara.leg.br, senado.leg.br, Comitê Gestor do IBS) e
    para a imprensa especializada em tributos.
  - ICMS/SRE: tenta primeiro raspar o portal oficial de legislação da
    SEFAZ-SP (legislacao.fazenda.sp.gov.br). Como esse site pode bloquear
    tráfego automatizado, há um fallback via Google News RSS que localiza
    matérias de portais especializados citando o número da Portaria SRE.

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


def scrape_legislacao_sefaz_sp():
    """Best-effort scrape of the official SEFAZ-SP legislation index for
    'Portaria SRE'. This site may block automated traffic; failures here are
    non-fatal because collect_icms_sre() falls back to news coverage."""
    base = "https://legislacao.fazenda.sp.gov.br"
    candidate_urls = [
        f"{base}/Paginas/pesquisa.aspx?texto=Portaria+SRE",
        f"{base}/Paginas/Portaria-SRE.aspx",
    ]
    results = []
    for url in candidate_urls:
        try:
            raw = http_get(url).decode("utf-8", errors="ignore")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"[warn] falha ao acessar {url}: {exc}", file=sys.stderr)
            continue

        for m in re.finditer(
            r'href="([^"]*Portaria-SRE-(\d+)-de-(\d{4})[^"]*\.aspx)"[^>]*>([^<]+)<', raw
        ):
            href, number, year, _label = m.groups()
            link = href if href.startswith("http") else f"{base}{href}"
            results.append({
                "number": f"{number}/{year}",
                "title": f"Portaria SRE {number}, de {year}",
                "link": link,
                "date": f"{year}-01-01",  # data exata não disponível neste índice
                "source": "SEFAZ-SP (legislação oficial)",
                "summary": "",
            })
    return results


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


def collect_icms_sre():
    official = scrape_legislacao_sefaz_sp()
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


if __name__ == "__main__":
    main()
