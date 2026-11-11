#!/usr/bin/env python3
"""Erzeugt aus den RSS-Feeds in feeds.yaml eine statische HTML-Seite.

Aufruf:  python build.py [--config feeds.yaml] [--out public] [--no-translate]
"""
from __future__ import annotations

import argparse
import calendar
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml

USER_AGENT = "Mozilla/5.0 (compatible; Nachrichtenportal/1.0; +https://github.com/)"
TIMEOUT = 20
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- Text

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text or "")
    parser.close()
    out = html.unescape("".join(parser.parts))
    return re.sub(r"\s+", " ", out).strip()


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-–") + " …"


def norm_title(title: str) -> str:
    return re.sub(r"[^\w]+", " ", title.lower()).strip()


def norm_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."),
                       parts.path.rstrip("/"), "", ""))


# --------------------------------------------------------------------- Übersetzung

class Translator:
    """Übersetzt ins Deutsche. Nutzt DeepL, wenn DEEPL_API_KEY gesetzt ist,
    sonst den frei zugänglichen Google-Translate-Endpunkt. Schlägt beides fehl,
    bleibt der Originaltext stehen."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.deepl_key = os.environ.get("DEEPL_API_KEY", "").strip()
        self.cache: dict[tuple[str, str], str | None] = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def translate(self, text: str, source: str) -> str | None:
        if not self.enabled or not text:
            return None
        key = (source, text)
        if key not in self.cache:
            result = None
            try:
                result = self._deepl(text, source) if self.deepl_key else self._google(text, source)
            except Exception as exc:  # noqa: BLE001 – Übersetzung ist optional
                log(f"  ! Übersetzung fehlgeschlagen ({type(exc).__name__}): {exc}")
            self.cache[key] = result
        return self.cache[key]

    def _google(self, text: str, source: str) -> str | None:
        resp = self.session.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source, "tl": "de", "dt": "t", "q": text},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        time.sleep(0.3)  # höflich bleiben
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip() or None

    def _deepl(self, text: str, source: str) -> str | None:
        host = "api-free.deepl.com" if self.deepl_key.endswith(":fx") else "api.deepl.com"
        resp = self.session.post(
            f"https://{host}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={"text": text, "source_lang": source.upper(), "target_lang": "DE"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["translations"][0]["text"].strip() or None


# -------------------------------------------------------------------------- Feeds

@dataclass
class Item:
    title: str
    link: str
    source: str
    published: datetime | None
    excerpt: str
    lang: str
    translated: bool = False


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def fetch_feed(feed: dict, excerpt_len: int) -> list[Item]:
    name, url = feed["name"], feed["url"]
    lang = str(feed.get("lang", "de")).lower()
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        raise ValueError("Feed enthält keine Einträge")

    items = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        raw = entry.get("summary") or entry.get("description") or ""
        if not raw and entry.get("content"):
            raw = entry["content"][0].get("value", "")
        excerpt = strip_html(raw)
        if norm_title(excerpt) == norm_title(title):
            excerpt = ""
        # Etwas mehr Text behalten – wird nach einer evtl. Übersetzung endgültig gekürzt.
        excerpt = shorten(excerpt, excerpt_len + 100)
        items.append(Item(title, link, name, entry_time(entry), excerpt, lang))
    return items


def collect_rubric(rubric: dict, settings: dict, translator: Translator) -> tuple[list[Item], list[str]]:
    limit = int(settings.get("max_meldungen_pro_rubrik", 15))
    excerpt_len = int(settings.get("exzerpt_laenge", 300))
    items: list[Item] = []
    failed: list[str] = []

    for feed in rubric.get("feeds") or []:
        try:
            got = fetch_feed(feed, excerpt_len)
            log(f"  ✓ {feed.get('name')}: {len(got)} Meldungen")
            items.extend(got)
        except Exception as exc:  # noqa: BLE001 – ein defekter Feed darf nichts stoppen
            log(f"  ✗ {feed.get('name', '?')} übersprungen ({type(exc).__name__}: {exc})")
            failed.append(str(feed.get("name", feed.get("url", "?"))))

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: i.published or epoch, reverse=True)

    seen_urls, seen_titles, unique = set(), set(), []
    for item in items:
        u, t = norm_url(item.link), norm_title(item.title)
        if u in seen_urls or t in seen_titles:
            continue
        seen_urls.add(u)
        seen_titles.add(t)
        unique.append(item)
        if len(unique) >= limit:
            break

    for item in unique:
        if item.lang != "de":
            title_de = translator.translate(item.title, item.lang)
            excerpt_de = translator.translate(item.excerpt, item.lang) if item.excerpt else ""
            if title_de:
                item.title = title_de
                item.translated = True
            if excerpt_de:
                item.excerpt = excerpt_de
                item.translated = True
        item.excerpt = shorten(item.excerpt, excerpt_len)
    return unique, failed


# --------------------------------------------------------------------------- HTML

def slugify(text: str) -> str:
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "rubrik"


def fmt_item_time(dt: datetime | None, tz: ZoneInfo, now: datetime) -> tuple[str, str]:
    if not dt:
        return "", ""
    local = dt.astimezone(tz)
    if local.date() == now.date():
        label = f"heute, {local:%H:%M} Uhr"
    elif (now.date() - local.date()).days == 1:
        label = f"gestern, {local:%H:%M} Uhr"
    else:
        label = f"{local:%d.%m.}, {local:%H:%M} Uhr"
    return label, local.isoformat()


def render(title: str, sections: list[tuple[str, list[Item], list[str]]], tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    stamp = f"{WOCHENTAGE[now.weekday()]}, {now.day}. {MONATE[now.month - 1]} {now.year}, {now:%H:%M} Uhr"
    e = html.escape

    nav, body = [], []
    used_ids: set[str] = set()
    for name, items, failed in sections:
        sid = slugify(name)
        while sid in used_ids:
            sid += "-x"
        used_ids.add(sid)
        nav.append(f'<a href="#{sid}">{e(name)}</a>')

        cards = []
        for it in items:
            label, iso = fmt_item_time(it.published, tz, now)
            time_html = f' · <time datetime="{e(iso)}">{e(label)}</time>' if label else ""
            note = ' · <span class="tag">aus dem Englischen übersetzt</span>' if it.translated else ""
            excerpt = f"\n        <p class=\"excerpt\">{e(it.excerpt)}</p>" if it.excerpt else ""
            cards.append(f"""      <article>
        <h3><a href="{e(it.link)}" target="_blank" rel="noopener">{e(it.title)}</a></h3>
        <p class="meta">{e(it.source)}{time_html}{note}</p>{excerpt}
        <a class="more" href="{e(it.link)}" target="_blank" rel="noopener">Zum Beitrag →</a>
      </article>""")
        if not cards:
            cards.append('      <p class="empty">Derzeit keine Meldungen verfügbar.</p>')
        warn = (f'\n      <p class="warn">Nicht erreichbar: {e(", ".join(failed))}</p>' if failed else "")
        body.append(f"""    <section id="{sid}">
      <h2>{e(name)}</h2>{warn}
{chr(10).join(cards)}
    </section>""")

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)}</title>
<style>
  :root {{
    --bg: #f7f7f5; --card: #ffffff; --text: #1d1d1f; --muted: #5f6368;
    --accent: #0b57d0; --border: #e3e3e0; --warn: #a15c00;
    color-scheme: light;
  }}
  :root[data-theme="dark"] {{
    --bg: #121316; --card: #1c1d21; --text: #e8e8ea; --muted: #a0a3a8;
    --accent: #8ab4f8; --border: #2c2e33; --warn: #f0b45a;
    color-scheme: dark;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #121316; --card: #1c1d21; --text: #e8e8ea; --muted: #a0a3a8;
      --accent: #8ab4f8; --border: #2c2e33; --warn: #f0b45a;
      color-scheme: dark;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
    font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    -webkit-text-size-adjust: 100%; }}
  header {{ position: sticky; top: 0; z-index: 10; background: var(--bg);
    border-bottom: 1px solid var(--border); }}
  .bar {{ max-width: 860px; margin: 0 auto; padding: 14px 16px 8px;
    display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
  h1 {{ font-size: 1.35rem; margin: 0; }}
  .updated {{ margin: 2px 0 0; color: var(--muted); font-size: .85rem; }}
  #theme {{ flex: none; border: 1px solid var(--border); background: var(--card); color: var(--text);
    border-radius: 999px; padding: 6px 12px; font-size: .9rem; cursor: pointer; }}
  nav {{ max-width: 860px; margin: 0 auto; padding: 0 16px 10px; display: flex; gap: 8px;
    overflow-x: auto; scrollbar-width: none; }}
  nav a {{ flex: none; text-decoration: none; color: var(--text); background: var(--card);
    border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; font-size: .9rem; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 8px 16px 48px; }}
  section {{ scroll-margin-top: 130px; }}
  h2 {{ font-size: 1.2rem; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 2px solid var(--accent); }}
  article {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; margin-bottom: 12px; }}
  h3 {{ font-size: 1.05rem; line-height: 1.35; margin: 0 0 4px; }}
  h3 a {{ color: var(--text); text-decoration: none; }}
  h3 a:hover, h3 a:focus-visible {{ color: var(--accent); text-decoration: underline; }}
  .meta {{ margin: 0 0 6px; color: var(--muted); font-size: .82rem; }}
  .tag {{ font-style: italic; }}
  .excerpt {{ margin: 0 0 8px; overflow-wrap: anywhere; }}
  .more {{ color: var(--accent); font-size: .9rem; text-decoration: none; font-weight: 600; }}
  .more:hover {{ text-decoration: underline; }}
  .warn {{ color: var(--warn); font-size: .85rem; margin: -4px 0 10px; }}
  .empty {{ color: var(--muted); }}
  @media (max-width: 600px) {{
    header {{ position: static; }}
    section {{ scroll-margin-top: 8px; }}
    .updated {{ font-size: .8rem; }}
  }}
  footer {{ text-align: center; color: var(--muted); font-size: .8rem; padding: 0 16px 32px; }}
</style>
<script>
  (function () {{
    try {{ var t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}
  }})();
</script>
</head>
<body>
<header>
  <div class="bar">
    <div>
      <h1>{e(title)}</h1>
      <p class="updated">Zuletzt aktualisiert: <time datetime="{now.isoformat()}">{e(stamp)}</time></p>
    </div>
    <button id="theme" type="button" aria-label="Hell- oder Dunkelmodus umschalten">◐ Modus</button>
  </div>
  <nav aria-label="Rubriken">
    {"".join(nav)}
  </nav>
</header>
<main>
{chr(10).join(body)}
</main>
<footer>Automatisch erzeugt aus RSS-Feeds. Alle Rechte an den Inhalten liegen bei den jeweiligen Quellen.</footer>
<script>
  document.getElementById("theme").addEventListener("click", function () {{
    var root = document.documentElement;
    var dark = root.dataset.theme ? root.dataset.theme === "dark"
                                  : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try {{ localStorage.setItem("theme", root.dataset.theme); }} catch (e) {{}}
  }});
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- Main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="feeds.yaml")
    ap.add_argument("--out", default="public")
    ap.add_argument("--no-translate", action="store_true", help="englische Meldungen nicht übersetzen")
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    settings = config.get("einstellungen") or {}
    rubrics = config.get("rubriken") or []
    if not rubrics:
        log("Keine Rubriken in der Konfiguration gefunden.")
        return 1

    tz = ZoneInfo(settings.get("zeitzone", "Europe/Berlin"))
    translator = Translator(enabled=not args.no_translate)

    sections = []
    for rubric in rubrics:
        name = str(rubric.get("name", "Ohne Namen"))
        log(f"Rubrik: {name}")
        items, failed = collect_rubric(rubric, settings, translator)
        sections.append((name, items, failed))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = render(settings.get("titel", "Nachrichten"), sections, tz)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    total = sum(len(s[1]) for s in sections)
    log(f"Fertig: {total} Meldungen in {len(sections)} Rubriken → {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
