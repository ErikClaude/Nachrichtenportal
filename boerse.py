#!/usr/bin/env python3
"""Börsen-Radar: liest boerse/aktuell.json und erzeugt daraus den Inhalt der Seite boerse.html.

Die Datei wird nicht von diesem Skript, sondern täglich von einer Claude-Routine
recherchiert und geschrieben (siehe boerse/ROUTINE.md). Dieses Modul prüft und zeigt sie nur.

Prüfen:  python boerse.py --check boerse/aktuell.json
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REGIONEN = [("usa", "USA"), ("europa", "Europa"), ("asien", "Asien")]
IDEEN_PRO_REGION = 10
VERALTET_NACH_TAGEN = 2
WOCHENTAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

HINWEIS = ("Automatisch von einer KI recherchierte Einschätzungen – keine Anlageberatung. "
           "Szenarien sind Möglichkeiten, keine Vorhersagen; Kennzahlen können veraltet oder "
           "fehlerhaft sein. Vor jeder Entscheidung selbst prüfen.")


# ---------------------------------------------------------------------- Laden/Prüfen

def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_stand(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def validate(data) -> list[str]:
    """Gibt eine Liste von Fehlern zurück; leer heißt: Datei entspricht dem Format."""
    if not isinstance(data, dict):
        return ["Wurzel muss ein Objekt sein"]
    errors: list[str] = []

    if not _parse_stand(data.get("stand")):
        errors.append("stand: fehlt oder kein ISO-Zeitstempel (z. B. 2026-11-13T02:00:00+01:00)")
    if not isinstance(data.get("marktlage"), str) or not data.get("marktlage", "").strip():
        errors.append("marktlage: fehlt oder leer")

    def check_quellen(where: str, quellen) -> None:
        if not isinstance(quellen, list) or not quellen:
            errors.append(f"{where}.quellen: mindestens eine Quelle nötig")
            return
        for i, q in enumerate(quellen):
            if not (isinstance(q, dict) and str(q.get("name", "")).strip()
                    and str(q.get("url", "")).startswith(("http://", "https://"))):
                errors.append(f"{where}.quellen[{i}]: braucht name und http(s)-url")

    def check_text(where: str, obj: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            if not isinstance(obj.get(key), str) or not obj[key].strip():
                errors.append(f"{where}.{key}: fehlt oder leer")

    ereignisse = data.get("ereignisse")
    if not isinstance(ereignisse, list) or not ereignisse:
        errors.append("ereignisse: fehlt oder leer")
    else:
        for i, ev in enumerate(ereignisse):
            where = f"ereignisse[{i}]"
            if not isinstance(ev, dict):
                errors.append(f"{where}: muss ein Objekt sein")
                continue
            check_text(where, ev, ("titel", "region", "kategorie", "expose"))
            if ev.get("status") not in ("aktuell", "bevorstehend"):
                errors.append(f'{where}.status: muss "aktuell" oder "bevorstehend" sein')
            if not _parse_date(ev.get("datum")):
                errors.append(f"{where}.datum: fehlt oder nicht JJJJ-MM-TT")
            if ev.get("datum_bis") and not _parse_date(ev["datum_bis"]):
                errors.append(f"{where}.datum_bis: nicht JJJJ-MM-TT")
            ausw = ev.get("auswirkungen")
            if not isinstance(ausw, list) or not ausw:
                errors.append(f"{where}.auswirkungen: mindestens ein Szenario nötig")
            else:
                for j, a in enumerate(ausw):
                    if not isinstance(a, dict):
                        errors.append(f"{where}.auswirkungen[{j}]: muss ein Objekt sein")
                    else:
                        check_text(f"{where}.auswirkungen[{j}]", a, ("szenario", "wirkung"))
            ideen = ev.get("ideen")
            if not isinstance(ideen, list) or not ideen or not all(isinstance(x, str) and x.strip() for x in ideen):
                errors.append(f"{where}.ideen: mindestens eine Handlungsidee (Text) nötig")
            check_quellen(where, ev.get("quellen"))

    kaufideen = data.get("kaufideen")
    if not isinstance(kaufideen, dict):
        errors.append("kaufideen: fehlt")
    else:
        for key, label in REGIONEN:
            liste = kaufideen.get(key)
            if not isinstance(liste, list):
                errors.append(f"kaufideen.{key}: fehlt")
                continue
            if len(liste) != IDEEN_PRO_REGION:
                errors.append(f"kaufideen.{key}: {len(liste)} statt {IDEEN_PRO_REGION} Einträge ({label})")
            for i, k in enumerate(liste):
                where = f"kaufideen.{key}[{i}]"
                if not isinstance(k, dict):
                    errors.append(f"{where}: muss ein Objekt sein")
                    continue
                check_text(where, k, ("name", "ticker", "boerse", "branche", "begruendung", "risiken"))
                kz = k.get("kennzahlen")
                if not isinstance(kz, dict) or not kz:
                    errors.append(f"{where}.kennzahlen: mindestens eine Kennzahl nötig")
                check_quellen(where, k.get("quellen"))
    return errors


def load(path: Path) -> tuple[dict | None, list[str]]:
    """Liest die Datei. (None, []) wenn sie fehlt; sonst Daten und Formatfehler."""
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"Datei nicht lesbar: {exc}"]
    errors = validate(data)
    return (data if isinstance(data, dict) else None), errors


# ------------------------------------------------------------------------------ HTML

e = html.escape


def _s(value) -> str:
    return str(value).strip() if value is not None else ""


def _fmt_date(d: date, today: date) -> str:
    diff = (d - today).days
    label = f"{WOCHENTAGE_KURZ[d.weekday()]}, {d.day}.{d.month}."
    if diff == 0:
        return f"heute · {label}"
    if diff == 1:
        return f"morgen · {label}"
    if 1 < diff <= 14:
        return f"in {diff} Tagen · {label}"
    return label


def _quellen(quellen) -> str:
    links = []
    for q in quellen if isinstance(quellen, list) else []:
        if not isinstance(q, dict):
            continue
        name, url = _s(q.get("name")), _s(q.get("url"))
        if name and url.startswith(("http://", "https://")):
            links.append(f'<a href="{e(url)}" target="_blank" rel="noopener">{e(name)}</a>')
    return f'\n        <p class="src">Quellen: {" · ".join(links)}</p>' if links else ""


def _event_card(ev: dict, today: date) -> str:
    d, bis = _parse_date(ev.get("datum")), _parse_date(ev.get("datum_bis"))
    when = _fmt_date(d, today) if d else ""
    if d and bis and bis != d:
        when += f" bis {bis.day}.{bis.month}."
    chips = "".join(f'<span class="chip">{e(_s(ev.get(k)))}</span>' for k in ("kategorie", "region") if _s(ev.get(k)))
    rows = []
    for a in ev.get("auswirkungen") if isinstance(ev.get("auswirkungen"), list) else []:
        if isinstance(a, dict) and _s(a.get("wirkung")):
            rows.append(f"<li><strong>{e(_s(a.get('szenario')))}:</strong> {e(_s(a.get('wirkung')))}</li>")
    ideen = [f"<li>{e(_s(x))}</li>" for x in (ev.get("ideen") if isinstance(ev.get("ideen"), list) else []) if _s(x)]
    ausw_html = f'\n        <h4>Mögliche Auswirkungen</h4>\n        <ul>{"".join(rows)}</ul>' if rows else ""
    ideen_html = f'\n        <h4>Was man tun könnte</h4>\n        <ul>{"".join(ideen)}</ul>' if ideen else ""
    return f"""      <article>
        <p class="meta"><strong>{e(when)}</strong> {chips}</p>
        <h3>{e(_s(ev.get("titel")))}</h3>
        <p class="excerpt">{e(_s(ev.get("expose")))}</p>{ausw_html}{ideen_html}{_quellen(ev.get("quellen"))}
      </article>"""


def _idea_card(nr: int, k: dict) -> str:
    kz = k.get("kennzahlen") if isinstance(k.get("kennzahlen"), dict) else {}
    facts = "".join(f"<div><dt>{e(_s(label))}</dt><dd>{e(_s(value))}</dd></div>" for label, value in kz.items())
    facts_html = f'\n        <dl class="facts">{facts}</dl>' if facts else ""
    ticker = " · ".join(x for x in (_s(k.get("ticker")), _s(k.get("boerse")), _s(k.get("branche"))) if x)
    risiken = _s(k.get("risiken"))
    risk_html = f'\n        <p class="risk"><strong>Risiken:</strong> {e(risiken)}</p>' if risiken else ""
    return f"""      <article>
        <h3>{nr}. {e(_s(k.get("name")))}</h3>
        <p class="meta">{e(ticker)}</p>{facts_html}
        <p class="excerpt">{e(_s(k.get("begruendung")))}</p>{risk_html}{_quellen(k.get("quellen"))}
      </article>"""


def render(data: dict | None, errors: list[str], tz: ZoneInfo) -> tuple[str, str]:
    """Gibt (Navigation, Hauptinhalt) der Börsen-Seite als HTML zurück."""
    nav = ['<a class="primary" href="index.html">← Nachrichten</a>']
    body = [f'    <p class="notice">{e(HINWEIS)}</p>']
    now = datetime.now(tz)
    today = now.date()

    if data is None:
        grund = errors[0] if errors else "Die tägliche Analyse wurde noch nicht erstellt."
        body.append(f'    <p class="empty">Noch keine Börsen-Analyse verfügbar. {e(grund)}</p>')
        return "".join(nav), "\n".join(body)

    stand = _parse_stand(data.get("stand"))
    if stand:
        if stand.tzinfo is None:
            stand = stand.replace(tzinfo=tz)
        local = stand.astimezone(tz)
        alt = (today - local.date()).days
        cls = "warn" if alt >= VERALTET_NACH_TAGEN else "updated"
        zusatz = f" – seit {alt} Tagen nicht aktualisiert, Angaben können überholt sein" if alt >= VERALTET_NACH_TAGEN else ""
        body.append(f'    <p class="{cls}">Stand der Analyse: {local.day}.{local.month}.{local.year}, '
                    f'{local:%H:%M} Uhr{e(zusatz)}</p>')
    if errors:
        body.append(f'    <p class="warn">Die Analyse ist unvollständig ({len(errors)} Formatfehler), '
                    f'z. B.: {e(errors[0])}</p>')

    if _s(data.get("marktlage")):
        nav.append('<a href="#marktlage">Marktlage</a>')
        body.append(f"""    <section id="marktlage">
      <h2>Marktlage</h2>
      <article><p class="excerpt">{e(_s(data.get("marktlage")))}</p></article>
    </section>""")

    events = [ev for ev in data.get("ereignisse") or [] if isinstance(ev, dict)]
    far = date.max
    aktuell = sorted((ev for ev in events if ev.get("status") == "aktuell"),
                     key=lambda ev: _parse_date(ev.get("datum")) or date.min, reverse=True)
    # Vergangene Termine fallen heraus, falls die Analyse einmal ein paar Tage alt ist.
    kommend = sorted((ev for ev in events if ev.get("status") != "aktuell"
                      and (_parse_date(ev.get("datum_bis")) or _parse_date(ev.get("datum")) or far) >= today),
                     key=lambda ev: _parse_date(ev.get("datum")) or far)
    for sid, titel, liste in (("aktuell", "Aktuelle Ereignisse", aktuell),
                              ("termine", "Kommende Termine", kommend)):
        nav.append(f'<a href="#{sid}">{titel}</a>')
        cards = [_event_card(ev, today) for ev in liste] or ['      <p class="empty">Derzeit keine Einträge.</p>']
        body.append(f"""    <section id="{sid}">
      <h2>{titel}</h2>
{chr(10).join(cards)}
    </section>""")

    kaufideen = data.get("kaufideen") if isinstance(data.get("kaufideen"), dict) else {}
    for key, label in REGIONEN:
        liste = [k for k in kaufideen.get(key) or [] if isinstance(k, dict)]
        nav.append(f'<a href="#ideen-{key}">Kaufideen {label}</a>')
        cards = [_idea_card(i, k) for i, k in enumerate(liste, 1)] or ['      <p class="empty">Derzeit keine Einträge.</p>']
        body.append(f"""    <section id="ideen-{key}">
      <h2>Kaufideen {label}</h2>
{chr(10).join(cards)}
    </section>""")
    return "".join(nav), "\n".join(body)


# ------------------------------------------------------------------------------ Main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", metavar="DATEI", required=True, help="Datei gegen das Format prüfen")
    args = ap.parse_args()
    path = Path(args.check)
    if not path.is_file():
        print(f"{path}: Datei nicht gefunden", file=sys.stderr)
        return 1
    data, errors = load(path)
    for err in errors:
        print(f"✗ {err}", file=sys.stderr)
    if errors:
        return 1
    n_ideen = sum(len(data["kaufideen"][k]) for k, _ in REGIONEN)
    print(f"✓ {path}: {len(data['ereignisse'])} Ereignisse, {n_ideen} Kaufideen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
