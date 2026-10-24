# Docker Update UI

Weboberfläche für Docker-Updates – als eigenständiges Alpine-Image. Sie übernimmt
die Funktionen des Shell-Skripts `update.sh`: Container auswählen, Update prüfen,
Daten-Backup erstellen, Image als Rollback-Punkt taggen, Update per Watchtower
ausführen, Rollback-Skript erzeugen und alte Rollback-Punkte aufräumen.

Statt `dialog`/`whiptail` gibt es eine Weboberfläche (FastAPI + HTMX), die den
Ablauf inklusive Live-Log im Browser zeigt.

## Funktionsumfang

- **Container-Übersicht** mit Image, Image-ID, Status und Update-Hinweis
  (`update-fähig`, `fester Tag`, `ROLLBACK-AKTIV`).
- **Update-Prüfung** per Pull: das gepullte Image wird mit dem Image des
  laufenden Containers verglichen (zuverlässiger als Textauswertung).
- **Snapshot auf Knopfdruck**: Sicherungspunkt (Daten, Image-Tag,
  Konfiguration, Rollback-Skript) anlegen, ohne das Image zu aktualisieren.
- **Backup des Datenverzeichnisses** (`tar.gz`) bei gestopptem Container.
- **Rollback-Punkt**: das aktuelle Image wird als
  `<repo>:rollback-<zeitstempel>` getaggt.
- **Rollback-Skript** (`rollback_<container>_<zeitstempel>.sh`) für die
  Ausführung auf dem Host sowie ein **JSON-Snapshot** der Container-Konfiguration.
- **Update per Watchtower** (`--run-once --include-stopped`) wie im Original.
  Läuft ein Container auf einem Rollback-Image, wird er stattdessen mit dem
  regulären Tag (`:latest`) neu erstellt.
- **Rollback per Klick**: Container stoppen/entfernen, Daten aus dem Backup
  wiederherstellen, Container aus dem gespeicherten Snapshot mit dem
  Rollback-Image neu erstellen und starten. Punkte der alten Skript-Version
  (ohne JSON-Snapshot) lassen sich ebenfalls wiederherstellen; die
  Konfiguration wird dann vom laufenden Container übernommen.
- **Aufräumen**: pro Container bleiben die neuesten `max_rollbacks`
  Rollback-Punkte (Backup, Skript, Snapshot, Image); ältere werden gelöscht,
  verwaiste Images bereinigt.
- **Einstellungen**: Backup-Verzeichnis, Startverzeichnis der Ordnerauswahl,
  Watchtower-Image und Anzahl der Rollback-Punkte – dauerhaft in `/config`.
- **Ordnerauswahl** im Browser statt Pfad-Tippen (mit manueller Eingabe).

## Schnellstart

```bash
cp .env.example .env
# .env öffnen und WEB_PASSWORD setzen

docker compose up -d --build
```

Danach `http://<host>:8080` öffnen und mit dem Passwort aus `WEB_PASSWORD`
anmelden.

Ohne Compose:

```bash
docker build -t docker-update-ui:latest .

docker run -d --name docker-update-ui \
  --restart unless-stopped \
  -p 8080:8080 \
  -e WEB_PASSWORD='bitte-aendern' \
  -e TZ=Europe/Berlin \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /docker/backups:/docker/backups \
  -v "$PWD/data:/config" \
  -v /docker/homeassistant:/docker/homeassistant \
  docker-update-ui:latest
```

## Volumes und Pfade

Die Anwendung arbeitet mit den Pfaden, die sie **im eigenen Container** sieht.
Damit Rollback-Skripte und Zeitstempel auf dem Host passen, werden Host-Pfade
**1:1** eingebunden – Host-Pfad = Container-Pfad:

| Host | Container | Zweck |
| --- | --- | --- |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker-API |
| `/docker/backups` | `/docker/backups` | Backups, Skripte, Snapshots |
| `./data` | `/config` | Einstellungen, Datenverzeichnis-Zuordnung |
| `/docker/homeassistant` | `/docker/homeassistant` | Beispiel-Datenverzeichnis |

Jedes Datenverzeichnis, das gesichert oder zurückgespielt werden soll, muss
eingebunden sein. Für reine Backups genügen Leserechte, für Rollbacks werden
Schreibrechte benötigt. Die Oberfläche warnt, wenn das Backup-Verzeichnis nicht
auf einem Volume liegt, weil Backups dann in der Container-Schicht verschwinden
würden.

## Bedienung

1. **Container öffnen** (Übersicht → Details).
2. Beim ersten Mal **Datenverzeichnis** wählen (Durchsuchen oder manuell) und
   speichern.
3. **Update prüfen** – das Image wird gepullt und verglichen.
4. **Backup erstellen & aktualisieren** bestätigen. Danach läuft der Ablauf mit
   Live-Log:
   - Image taggen (`<repo>:rollback-<zeit>`)
   - Container stoppen
   - Daten archivieren
   - Snapshot + Rollback-Skript schreiben
   - alte Rollback-Punkte löschen
   - Update per Watchtower (oder Neuerstellen mit regulärem Tag)
   - Container starten
5. **Snapshot** neben *Update prüfen* legt jederzeit einen Sicherungspunkt an,
   ohne das Image zu ändern. Der Container wird dafür kurz gestoppt und danach
   wieder gestartet.
6. Auf der Seite **Backups & Rollbacks** stellt **Wiederherstellen** in der
   Zeile eines Zeitpunkts genau diesen Stand wieder her (Daten + Container mit
   dem Rollback-Image).

Der Rückweg ist jederzeit möglich: Das Rollback-Skript liegt im Backup-Ordner
und funktioniert unabhängig von der Weboberfläche:

```bash
sudo bash /docker/backups/<container>/rollback_<container>_<zeitstempel>.sh
```

## Einstellungen

In der Oberfläche unter **Einstellungen** oder per Umgebungsvariable:

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `WEB_PASSWORD` | – (**Pflicht**) | Passwort für die Anmeldung |
| `BACKUP_DIR` | `/docker/backups` | Basisverzeichnis der Backups |
| `BROWSE_DIR` | `/docker` | Startverzeichnis der Ordnerauswahl |
| `WATCHTOWER_IMAGE` | `nickfedor/watchtower` | Image für den Update-Schritt |
| `MAX_ROLLBACKS` | `3` | Rollback-Punkte pro Container |
| `CONFIG_DIR` | `/config` | Persistente Einstellungen |
| `TZ` | `Europe/Berlin` | Zeitzone für Logs und Zeitstempel |
| `SESSION_TTL` | `43200` | Sitzungsdauer in Sekunden |
| `COOKIE_SECURE` | `0` | `1`, wenn die UI nur über HTTPS erreichbar ist |
| `ALLOWED_ORIGINS` | – | Zusätzliche Hostnamen für die CSRF-Prüfung (Reverse Proxy) |
| `CSRF_PROTECTION` | `1` | `0` deaktiviert die Herkunftsprüfung (nur mit anderer Absicherung) |
| `STOP_TIMEOUT` | `30` | Wartezeit beim Stoppen eines Containers |
| `DOCKER_TIMEOUT` | `600` | HTTP-Timeout für Docker-Aufrufe |
| `SELF_CONTAINER` | Hostname | Name/ID des Tool-Containers (Schutz vor Selbst-Update) |

## Migration vom Shell-Skript

Die bisherige Konfiguration kann übernommen werden. Dazu `settings.conf` und
`backup_paths.conf` in den Config-Ordner legen (z. B. `data/`) und den Container
einmal neu starten. Beim ersten Start importiert die Anwendung beide Dateien,
solange noch keine `settings.json`/`data_dirs.json` existiert:

```bash
mkdir -p data
cp /docker/settings.conf /docker/backup_paths.conf data/   # falls vorhanden
docker compose restart
```

Die vorhandenen Backups unter `/docker/backups` bleiben gültig; bereits
erzeugte Rollback-Skripte funktionieren weiterhin auf dem Host.

## Unterschiede zum Shell-Skript

- **Kein `runlike`, kein Zusatz-Image:** Der Container wird für Rollback und
  Umschaltung aus der gespeicherten Docker-Konfiguration (Inspect-Snapshot)
  neu erstellt. Exotische Optionen bleiben dabei erhalten, weil die
  `HostConfig` vollständig übernommen wird.
- **Update-Prüfung** vergleicht Image-IDs statt Pull-Textausgaben.
- **Backups werden nicht mehr verschoben, wenn das Verzeichnis ein Mountpunkt
  ist:** Statt `mv` wird der Inhalt ersetzt (das Backup bleibt als
  Sicherheitsnetz); nur bei nicht gemounteten Verzeichnissen legt die App wie
  bisher `<verzeichnis>.before-rollback.<timestamp>` an.
- **Selbst-Update blockiert:** Der eigene Container lässt sich nicht über die
  Oberfläche aktualisieren.
- **Aufräumen** berücksichtigt Backups, Skripte, Snapshots und Images
  gemeinsam. Rollback-Images, die zu keinem Zeitpunkt gehören (etwa Reste
  abgebrochener Läufe), werden als „ohne Sicherung“ markiert und lassen sich
  über **Ohne Sicherung löschen** entfernen.
- **Legacy-Punkte** aus dem Shell-Skript (nur `.tar.gz` + `.sh`, kein JSON)
  sind wiederherstellbar; der fehlende Snapshot wird dabei aus dem laufenden
  Container ergänzt.

## Sicherheit

Der Zugriff auf `/var/run/docker.sock` entspricht Root-Rechten auf dem Host.
Deshalb:

- `WEB_PASSWORD` immer setzen und ein starkes Passwort verwenden,
- den Port nicht ungeschützt ins Internet stellen (VPN/LAN oder Reverse Proxy
  mit TLS),
- `COOKIE_SECURE=1`, sobald HTTPS vorgeschaltet ist,
- optional `cap_drop`/`read_only` für den Tool-Container prüfen.

Die Anwendung bringt Login mit signierten Session-Cookies (serverseitig,
`HttpOnly`, `SameSite=Strict`), Brute-Force-Bremse, Origin-Prüfung für
schreibende Anfragen und eine restriktive Content-Security-Policy mit.

### „Anfrage abgelehnt (CSRF-Schutz)“

Die Herkunftsprüfung vergleicht den `Origin`-Header mit dem aufgerufenen Host.
Sie akzeptiert denselben Hostnamen auch bei abweichendem Port und berücksichtigt
`X-Forwarded-Host`, damit Reverse Proxys funktionieren. Meldet die Oberfläche
trotzdem einen abgelehnten Zugriff:

1. Im Fehlertext stehen `Origin` und `Host` – damit ist die Ursache sichtbar;
2. öffentliche Adresse(n) in `ALLOWED_ORIGINS` eintragen, z. B.
   `ALLOWED_ORIGINS=https://docker.example.com` und den Container neu starten;
3. nur falls ein vorgeschalteter Auth-Proxy den Zugriff ohnehin absichert:
   `CSRF_PROTECTION=0` setzen.

## Grenzen

- Nur einzelne Container, kein Docker Swarm/Stack-Update.
- Compose-Dateien werden nicht bearbeitet; das Tool aktualisiert Images und
  Container, nicht die `compose.yaml` des Hosts.
- Watchtower muss das Registry erreichen; private Registries benötigen
  Anmeldedaten im Docker-Daemon.
- Beim Backup wird der Container gestoppt (kurze Ausfallzeit), damit die Daten
  konsistent sind.
- Ein mehrteiliger `ENTRYPOINT` lässt sich im generierten Shell-Skript nicht
  als ein `--entrypoint`-Argument abbilden; das Skript enthält dann einen
  Hinweis. Der Rollback über die Oberfläche ist davon nicht betroffen.

## Projektstruktur

```
app/
  main.py          FastAPI-Routen, Auth-Middleware, Templates
  auth.py          Passwort-Login, Sessions, Rate-Limit
  config.py        Einstellungen, Datenverzeichnisse, Legacy-Import
  docker_ops.py    Docker-API: Liste, Pull, Tagging, Recreate, Watchtower
  operations.py    Update-, Rollback- und Backup-Abläufe
  jobs.py          Hintergrund-Jobs mit Live-Log
  paths.py         Ordnerauswahl
  templates/       Jinja2 + HTMX
  static/          CSS, JS, lokal eingebundenes htmx
tests/             Pytest-Suite (ohne Docker-Daemon lauffähig)
Dockerfile         python:3.12-alpine, non-interactive Uvicorn
docker-compose.yml Socket, Backups, Config, Beispiel-Mounts
```

## Entwicklung und Tests

```bash
python3 -m pip install --target .devdeps fastapi uvicorn jinja2 python-multipart docker httpx pytest
PYTHONPATH=.devdeps:. CONFIG_DIR=/tmp/dui-config WEB_PASSWORD=dev \
  BACKUP_DIR=/tmp/dui-backups BROWSE_DIR=/tmp \
  python3 -m uvicorn app.main:app --reload --port 8080

PYTHONPATH=.devdeps:. python3 -m pytest -q tests
```

Die Tests decken Login/Rechte, Seitenrendering, Einstellungen, Ordnerauswahl,
Backup/Restore, Rollback-Skripte, Aufräumen und den Job-Ablauf ab. Sie benötigen
keinen Docker-Daemon.
