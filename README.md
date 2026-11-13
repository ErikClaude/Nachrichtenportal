# Nachrichtenportal

Persönliches Nachrichtenportal als statische Webseite. `build.py` liest die RSS-Feeds aus
`feeds.yaml`, gruppiert die Meldungen nach Rubriken (neueste zuerst, ohne Duplikate,
max. 15 pro Rubrik) und schreibt `public/index.html`. Ein GitHub-Actions-Workflow baut die
Seite täglich gegen 4 Uhr und veröffentlicht sie über GitHub Pages.

## Neue Rubrik hinzufügen

Nur `feeds.yaml` bearbeiten – am Code ändert sich nichts. Einen Block unter `rubriken:` ergänzen:

```yaml
  - name: "Technik"
    feeds:
      - name: "heise online"
        url: "https://www.heise.de/rss/heise-top-atom.xml"
      - name: "Ars Technica"
        url: "https://feeds.arstechnica.com/arstechnica/index"
        lang: "en"        # nicht-deutsche Feeds werden automatisch übersetzt
```

- `name` der Rubrik erscheint als Überschrift und im Menü oben. Die Reihenfolge in der Datei
  ist die Reihenfolge auf der Seite.
- `lang` ist optional (Standard `de`). Bei anderen Sprachen werden Schlagzeile und Exzerpt
  ins Deutsche übersetzt und als „aus dem Englischen übersetzt“ markiert.
- Feeds kann man genauso zu bestehenden Rubriken hinzufügen oder daraus löschen.
- Vorher testen, ob der Feed funktioniert: lokal `python build.py` ausführen – in der Ausgabe
  steht pro Feed `✓` oder `✗`. Ein defekter Feed wird übersprungen, die Seite trotzdem gebaut.

Änderung committen und pushen, dann im Reiter **Actions** den Workflow
„Nachrichtenportal bauen & veröffentlichen“ per **Run workflow** starten (oder bis zum
nächsten nächtlichen Lauf warten).

## Börsen-Radar

Die zweite Seite `boerse.html` (oben über „Börsen-Radar →“ erreichbar) zeigt Marktlage,
aktuelle Ereignisse, Termine der nächsten Tage (inklusive Börsengänge) mit möglichen
Auswirkungen und Handlungsideen sowie je 10 begründete Kaufideen für USA, Europa und Asien.

Die Inhalte kommen nicht aus RSS-Feeds: Eine geplante Claude-Routine recherchiert sie
täglich und legt sie als `boerse/aktuell.json` auf dem Branch `boerse-daten` ab. Der Build
holt die Datei von dort. Was die Routine tun soll und wie die Datei aufgebaut ist, steht in
`boerse/ROUTINE.md` – dort lassen sich Umfang und Regeln anpassen. Fehlt die Datei, zeigt
die Seite einen Platzhalter; ist sie älter als zwei Tage, erscheint ein Warnhinweis.

Die Einschätzungen sind KI-erzeugt und keine Anlageberatung.

```bash
python boerse.py --check boerse/aktuell.json   # Format prüfen
```

## Lokal ausführen

```bash
pip install -r requirements.txt
python build.py              # erzeugt public/index.html und public/boerse.html
python build.py --no-translate
```

## Einmalige Einrichtung von GitHub Pages

Im Repository unter **Settings → Pages → Build and deployment → Source** „GitHub Actions“
auswählen. Die Seite liegt danach unter `https://<benutzer>.github.io/<repository>/`.

## Übersetzung

Standardmäßig wird der frei zugängliche Google-Translate-Endpunkt genutzt (ohne Schlüssel,
inoffiziell). Für zuverlässigere Übersetzungen ein DeepL-API-Konto anlegen (Free reicht)
und den Schlüssel als Repository-Secret `DEEPL_API_KEY` hinterlegen. Schlägt die
Übersetzung fehl, bleibt der Originaltext stehen.
