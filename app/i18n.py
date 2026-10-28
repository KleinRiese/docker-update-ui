"""Zweisprachige Texte (Deutsch/Englisch).

Die Sprache folgt dem Cookie ``dui_lang`` und ersatzweise dem
``Accept-Language``-Header. Sie liegt in einer ContextVar, damit auch
Hintergrund-Jobs (Kopieren des Kontexts in ``jobs.start``) die Sprache des
auslösenden Aufrufs verwenden.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - nur für Typprüfer
    from fastapi import Request

DEFAULT_LANGUAGE = "de"
LANGUAGES = ("de", "en")
LANGUAGE_COOKIE = "dui_lang"
LANGUAGE_COOKIE_MAX_AGE = 365 * 24 * 3600
LANGUAGE_LABELS = {"de": "Deutsch", "en": "English"}
DATE_FORMATS = {"de": "%d.%m.%Y %H:%M", "en": "%m/%d/%Y %I:%M %p"}

# key: (Deutsch, Englisch)
_STRINGS: dict[str, tuple[str, str]] = {
    # ------------------------------------------------------------------ Basis
    "nav.containers": ("Container", "Containers"),
    "nav.settings": ("Einstellungen", "Settings"),
    "nav.logout": ("Abmelden", "Sign out"),
    "brand.name": ("Docker Update", "Docker Update"),
    "brand.sub": ("UI", "UI"),
    "footer.text": (
        "Docker Update UI · Alpine-Container mit Zugriff auf",
        "Docker Update UI · Alpine container with access to",
    ),
    "language.switch": ("Sprache", "Language"),
    # -------------------------------------------------------------- Übersicht
    "containers.title": ("Container", "Containers"),
    "containers.subtitle": (
        "Updates prüfen, Daten sichern und Rollbacks verwalten.",
        "Check for updates, back up data and manage rollbacks.",
    ),
    "containers.docker_down": ("Docker nicht erreichbar.", "Docker is not reachable."),
    "containers.stat.containers": ("Container", "Containers"),
    "containers.stat.backup_dir": ("Backup-Verzeichnis", "Backup directory"),
    "containers.stat.method": ("Update-Methode", "Update method"),
    "containers.stat.method_value": ("Docker-API (nativ)", "Docker API (native)"),
    "containers.stat.rollbacks": ("Rollback-Punkte", "Rollback points"),
    "containers.backup_not_persistent": (
        "Das Backup-Verzeichnis {path} liegt nicht auf einem eingebundenen "
        "Volume. Backups überleben damit kein Neuerstellen des Tool-Containers "
        "– bitte als Volume einbinden.",
        "The backup directory {path} is not on a mounted volume. Backups will "
        "not survive the tool container being recreated – please mount it as a "
        "volume.",
    ),
    "containers.all": ("Alle Container", "All containers"),
    "containers.refresh": ("Aktualisieren", "Refresh"),
    "containers.flow.title": ("Ablauf eines Updates", "How an update works"),
    "containers.flow.1": (
        "Container öffnen und <strong>Update prüfen</strong> – das Image wird "
        "gepullt und mit dem laufenden Image verglichen.",
        "Open a container and <strong>check for an update</strong> – the image is "
        "pulled and compared with the running one.",
    ),
    "containers.flow.2": (
        "Datenverzeichnis kontrollieren, dann Update bestätigen.",
        "Check the data directory, then confirm the update.",
    ),
    "containers.flow.3": (
        "Das Tool taggt das laufende Image als Rollback-Punkt, stoppt den "
        "Container, archiviert die Daten und erzeugt ein Rollback-Skript.",
        "The tool tags the running image as a rollback point, stops the "
        "container, archives the data and writes a rollback script.",
    ),
    "containers.flow.4": (
        "Das neue Image wird gepullt, der Container mit identischer "
        "Konfiguration neu erstellt und gestartet.",
        "The new image is pulled, the container is recreated with the same "
        "configuration and started.",
    ),
    "containers.flow.5": (
        "Bei Bedarf in der Container-Ansicht auf einen früheren Stand "
        "zurückrollen.",
        "Roll back to an earlier point from the container view if needed.",
    ),
    # ------------------------------------------------------------- Containertabelle
    "table.name": ("Name", "Name"),
    "table.image": ("Image", "Image"),
    "table.status": ("Status", "Status"),
    "table.update": ("Update", "Update"),
    "table.data_dir": ("Datenverzeichnis", "Data directory"),
    "table.self": ("dieses Tool", "this tool"),
    "table.details": ("Details", "Details"),
    "table.empty": ("Keine Container gefunden.", "No containers found."),
    # --------------------------------------------------------------- Container-Detail
    "detail.back": ("← Alle Container", "← All containers"),
    "detail.update.title": ("Update", "Update"),
    "detail.image_id": ("Image-ID", "Image ID"),
    "detail.state": ("Zustand", "State"),
    "detail.container_id": ("Container-ID", "Container ID"),
    "detail.restart": ("Neustart-Richtlinie", "Restart policy"),
    "detail.networks": ("Netzwerke", "Networks"),
    "detail.ports": ("Ports", "Ports"),
    "detail.mounts": ("Mounts", "Mounts"),
    "detail.self_note": (
        "Dieses Tool aktualisiert sich nicht selbst. Bitte auf dem Host mit "
        "docker compose pull && docker compose up -d "
        "erneuern.",
        "This tool does not update itself. Please refresh it on the host with "
        "docker compose pull && docker compose up -d.",
    ),
    "detail.no_data_dir": (
        "Für diesen Container ist noch kein Datenverzeichnis hinterlegt. Bitte "
        "rechts auswählen – ohne Datenverzeichnis wird kein Update gestartet.",
        "No data directory is configured for this container yet. Please choose "
        "one on the right – no update starts without a data directory.",
    ),
    "detail.check_anyway": ("Update dennoch prüfen", "Check anyway"),
    "detail.update_intro": (
        "Prüft per Pull, ob ein neueres Image vorliegt, und zeigt die geplanten "
        "Schritte. Der Pull kann einen Moment dauern.",
        "Pulls the image to see whether a newer one exists and shows the planned "
        "steps. The pull can take a moment.",
    ),
    "detail.check_button": ("Update prüfen", "Check for update"),
    "detail.snapshot": ("Snapshot", "Snapshot"),
    "detail.checking": ("prüfe Registry …", "checking registry …"),
    "detail.snapshot_hint": (
        "Snapshot = Sicherungspunkt ohne Update (Daten, Rollback-Image, "
        "Konfiguration). Anschließend im Bereich „Backups & Rollbacks“ "
        "wiederherstellbar.",
        "Snapshot = a restore point without an update (data, rollback image, "
        "configuration). It can be restored later under 'Backups & "
        "Rollbacks'.",
    ),
    "detail.snapshot_confirm": (
        "Snapshot für '{container}' erstellen?\n\nDer Container wird kurz "
        "gestoppt, Daten, Image-Tag und Konfiguration werden gesichert. Es wird "
        "nichts aktualisiert.",
        "Create a snapshot for '{container}'?\n\nThe container is stopped "
        "briefly; data, image tag and configuration are saved. Nothing is updated.",
    ),
    "detail.data.title": ("Datenverzeichnis", "Data directory"),
    "detail.data.configured": ("Hinterlegt:", "Configured:"),
    "detail.data.not_persistent": (
        "Dieses Verzeichnis ist im Tool-Container nicht als Volume eingebunden. "
        "Bitte - {path}:{path} ergänzen, sonst schlägt das Backup "
        "fehl oder landet in der Container-Schicht.",
        "This directory is not mounted as a volume in the tool container. Please "
        "add - {path}:{path}, otherwise the backup fails or ends up "
        "in the container layer.",
    ),
    "detail.data.intro": (
        "Dieses Verzeichnis wird vor jedem Update archiviert und beim Rollback "
        "wiederhergestellt.",
        "This directory is archived before every update and restored on rollback.",
    ),
    "detail.data.intro_nodata": (
        "Datenbackup ist abgeschaltet: Es wird nur das Image als Rollback-Punkt "
        "gesichert, das Verzeichnis bleibt unverändert.",
        "Data backup is disabled: only the image is saved as a rollback point, "
        "the directory stays untouched.",
    ),
    "detail.data.placeholder": ("/docker/mein-container", "/docker/my-container"),
    "detail.data.save": ("Speichern", "Save"),
    "detail.data.browse": ("Durchsuchen", "Browse"),
    "detail.data.hint": (
        "Der Ordner muss im Tool-Container unter demselben Pfad verfügbar sein, "
        "z. B. - {path}:{path}",
        "The folder must be available in the tool container under the same path, "
        "e.g. - {path}:{path}",
    ),
    "detail.data.no_backup": (
        "Keine Daten sichern (nur das Image als Rollback)",
        "Do not back up data (image only as rollback)",
    ),
    "tag.placeholder": ("z. B. 1.2.3 oder latest", "e.g. 1.2.3 or latest"),
    "tag.button": ("Tag umstellen", "Switch tag"),
    "tag.aria": ("Ziel-Tag", "Target tag"),
    "tag.hint": (
        "Ziel-Tag oder vollständige Referenz im selben Repository. Das aktuell "
        "laufende Image ({image}) wird vorher als Rollback-Punkt gesichert.",
        "Target tag or full reference in the same repository. The currently "
        "running image ({image}) is saved as a rollback point first.",
    ),
    "tag.confirm": (
        "Container '{container}' auf ein anderes Tag umstellen?\n\nEs wird ein "
        "Rollback-Punkt gesichert, das Ziel-Image gepullt und der Container mit "
        "derselben Konfiguration neu erstellt.",
        "Switch container '{container}' to a different tag?\n\nA rollback point "
        "is saved, the target image is pulled and the container is recreated with "
        "the same configuration.",
    ),
    "detail.data.no_backup_hint": (
        "Für zustandslose Container: Das Update taggt dann nur das laufende "
        "Image, ein Rollback stellt ausschließlich Image und Konfiguration "
        "wieder her.",
        "For stateless containers: the update then only tags the running image, "
        "and a rollback restores the image and configuration only.",
    ),
    "detail.backup_not_persistent": (
        "Das Backup-Verzeichnis ist nicht als Volume eingebunden – erstellte "
        "Backups gehen beim Neuerstellen des Tool-Containers verloren.",
        "The backup directory is not mounted as a volume – backups are lost when "
        "the tool container is recreated.",
    ),
    "detail.picker.title": ("Verzeichnis wählen", "Choose a directory"),
    "detail.picker.close": ("Schließen", "Close"),
    "detail.picker.start": ("Start:", "Start:"),
    "detail.picker.hint": (
        "Auswahl übernimmt den Pfad in das Feld – anschließend speichern.",
        "The selection fills the field – save afterwards.",
    ),
    "detail.backups.title": ("Backups & Rollbacks", "Backups & rollbacks"),
    "detail.backups.all": ("Alle anzeigen", "Show all"),
    # -------------------------------------------------------------- Update-Prüfung
    "check.current": ("Aktuelles Image", "Current image"),
    "check.target": ("Ziel", "Target"),
    "check.data_dir": ("Datenverzeichnis", "Data directory"),
    "check.not_configured": ("nicht hinterlegt", "not configured"),
    "check.backups": ("Backups", "Backups"),
    "check.backup_not_persistent": (
        "Das Backup-Verzeichnis liegt nicht auf einem eingebundenen Volume – "
        "die Sicherung wäre nicht dauerhaft. Bitte zuerst die Volumes prüfen.",
        "The backup directory is not on a mounted volume – the backup would not "
        "persist. Please check the volumes first.",
    ),
    "check.need_data_dir": (
        "Bitte zuerst ein Datenverzeichnis hinterlegen – ohne Backup wird kein "
        "Update gestartet.",
        "Please configure a data directory first – no update starts without a "
        "backup.",
    ),
    "check.no_data_note": (
        "Datenbackup ist für diesen Container abgeschaltet – gesichert wird nur "
        "das Image.",
        "Data backup is disabled for this container – only the image is saved.",
    ),
    "check.confirm.switch": (
        "Container '{container}' auf das reguläre Image umstellen?\n\n"
        "Ablauf: aktuelles Image als Rollback-Punkt taggen, Container stoppen, "
        "Daten nach {data_dir} archivieren, Container neu erstellen und starten.",
        "Switch container '{container}' to the regular image?\n\n"
        "Steps: tag the current image as a rollback point, stop the container, "
        "archive the data to {data_dir}, recreate and start the container.",
    ),
    "check.confirm.switch_nodata": (
        "Container '{container}' auf das reguläre Image umstellen?\n\n"
        "Ablauf: aktuelles Image als Rollback-Punkt taggen, Container neu "
        "erstellen und starten. Daten werden nicht gesichert.",
        "Switch container '{container}' to the regular image?\n\n"
        "Steps: tag the current image as a rollback point, recreate and start the "
        "container. No data is backed up.",
    ),
    "check.confirm.update": (
        "Container '{container}' jetzt aktualisieren?\n\nAblauf: aktuelles "
        "Image als Rollback-Punkt taggen, Container stoppen, Daten nach "
        "{data_dir} archivieren, Rollback-Skript erstellen, neues Image pullen "
        "und Container neu erstellen.",
        "Update container '{container}' now?\n\nSteps: tag the current "
        "image as a rollback point, stop the container, archive the data to "
        "{data_dir}, write a rollback script, pull the new image and recreate the "
        "container.",
    ),
    "check.confirm.update_nodata": (
        "Container '{container}' jetzt aktualisieren?\n\nAblauf: aktuelles "
        "Image als Rollback-Punkt taggen, Rollback-Skript erstellen, neues Image "
        "pullen und Container neu erstellen. Daten werden nicht gesichert.",
        "Update container '{container}' now?\n\nSteps: tag the current "
        "image as a rollback point, write a rollback script, pull the new image "
        "and recreate the container. No data is backed up.",
    ),
    "check.button.switch": (
        "Auf reguläres Image umstellen",
        "Switch to regular image",
    ),
    "check.button.update": (
        "Backup erstellen & aktualisieren",
        "Create backup & update",
    ),
    "check.button.update_nodata": (
        "Image sichern & aktualisieren",
        "Save image & update",
    ),
    "check.none": (
        "Kein Update verfügbar – es wird kein Backup erstellt und nichts "
        "geändert.",
        "No update available – nothing is backed up and nothing changes.",
    ),
    "check.digest_current": (
        "Registry und lokales Image haben denselben Digest ({digest}).",
        "Registry and local image share the same digest ({digest}).",
    ),
    "check.digest_available": (
        "Die Registry liefert einen anderen Manifest-Digest "
        "(Registry: {remote}, lokal: {local}).",
        "The registry reports a different manifest digest "
        "(registry: {remote}, local: {local}).",
    ),
    "check.fallback_pull": (
        "Registry-Abfrage nicht möglich ({error}) – vollständige Pull-Prüfung.",
        "Registry query failed ({error}) – using the full pull check.",
    ),
    "check.platform_missing": (
        "Die Registry bietet keine Manifest-Angabe für {os}/{arch}.",
        "The registry has no manifest entry for {os}/{arch}.",
    ),
    "check.no_digest": (
        "Für '{ref}' liefert die Registry keinen Digest.",
        "The registry returns no digest for '{ref}'.",
    ),
    # ------------------------------------------------- Automatische Prüfung
    "auto.title": (
        "Automatische Update-Prüfung",
        "Automatic update check",
    ),
    "auto.hint": (
        "Der Zeitplan läuft im Tool-Container, solange dieser läuft. Geprüft "
        "wird über den Manifest-Digest der Registry – das Image wird dabei "
        "nicht heruntergeladen. Auch Container mit festem Tag werden geprüft; "
        "ist kein Digest-Abgleich möglich, wird das Image gepullt.",
        "The schedule runs inside the tool container while it is running. "
        "Checks compare the registry manifest digest – the image is not "
        "downloaded. Containers with a fixed tag are checked too; if the "
        "digest comparison is not possible, the image is pulled.",
    ),
    "auto.enable": (
        "Automatische Prüfung aktivieren",
        "Enable the automatic check",
    ),
    "auto.cron": ("Zeitplan (Cron)", "Schedule (cron)"),
    "auto.cron_hint": (
        "Fünf Felder: Minute Stunde Tag Monat Wochentag (0 = Sonntag). "
        "Beispiele: »0 */6 * * *« alle sechs Stunden, »30 4 * * *« täglich "
        "04:30, »0 5 * * 1« montags 05:00.",
        "Five fields: minute hour day month weekday (0 = Sunday). "
        "Examples: »0 */6 * * *« every six hours, »30 4 * * *« daily at "
        "04:30, »0 5 * * 1« Mondays at 05:00.",
    ),
    "auto.mode": (
        "Aktion bei verfügbarem Update",
        "Action when an update is available",
    ),
    "auto.mode.check": ("Nur prüfen", "Check only"),
    "auto.mode.notify": (
        "Prüfen und benachrichtigen",
        "Check and notify",
    ),
    "auto.mode.update": (
        "Prüfen und automatisch aktualisieren",
        "Check and update automatically",
    ),
    "auto.mode_hint": (
        "»Automatisch aktualisieren« nutzt denselben Ablauf wie ein manuelles "
        "Update: Rollback-Punkt, Datenbackup laut Container-Einstellung, neues "
        "Image, Container neu erstellen. Benachrichtigungen gehen per E-Mail "
        "an den SMTP-Server aus den Einstellungen.",
        "»Update automatically« uses the same flow as a manual update: rollback "
        "point, data backup according to the container setting, new image, "
        "recreate the container. Notifications are sent by email through the "
        "SMTP server from the settings.",
    ),
    "auto.save": ("Zeitplan speichern", "Save schedule"),
    "auto.run_now": ("Jetzt prüfen", "Check now"),
    "auto.last_run": ("Zuletzt geprüft:", "Last checked:"),
    "auto.never": ("noch nie geprüft", "never checked"),
    "auto.next_run": ("Nächste Prüfung:", "Next check:"),
    "auto.disabled": (
        "Die automatische Prüfung ist ausgeschaltet.",
        "The automatic check is disabled.",
    ),
    "auto.state.updated": ("aktualisiert", "updated"),
    "auto.state.error": ("Fehler", "Error"),
    "auto.state.skipped": ("übersprungen", "skipped"),
    "auto.state.unknown": ("unbekannt", "unknown"),
    "auto.method.registry": ("Registry-Abgleich", "registry digest"),
    "auto.method.pull": ("voller Pull", "full pull"),
    "auto.method.none": ("ohne Prüfung", "no check"),
    "auto.method_label": ("Methode:", "Method:"),
    "auto.digests": (
        "Registry {remote} · lokal {local}",
        "registry {remote} · local {local}",
    ),
    "auto.skip_self": (
        "Dieses Tool prüft sich nicht selbst.",
        "This tool does not check itself.",
    ),
    "auto.skip_rollback": (
        "Rollback-Tag aktiv – dieser Container wird nicht automatisch geprüft.",
        "Rollback tag active – this container is not checked automatically.",
    ),
    "auto.skip_fixed": (
        "Fester Digest – dieses Image kann sich nicht ändern.",
        "Fixed digest – this image cannot change.",
    ),
    "auto.checking": (
        "Prüfe {ref} gegen die Registry ...",
        "Checking {ref} against the registry ...",
    ),
    "auto.busy": (
        "Für diesen Container läuft bereits eine Prüfung.",
        "A check is already running for this container.",
    ),
    "auto.no_smtp": (
        "Kein SMTP-Server hinterlegt – die Benachrichtigung entfällt.",
        "No SMTP server configured – the notification is skipped.",
    ),
    "auto.mailed": (
        "Benachrichtigung per E-Mail an {to} gesendet.",
        "Notification sent by email to {to}.",
    ),
    "auto.mail.subject": (
        "Docker Update: {container} – {status}",
        "Docker update: {container} – {status}",
    ),
    "auto.notify_failed": (
        "Benachrichtigung fehlgeschlagen: {error}",
        "Notification failed: {error}",
    ),
    "auto.notify.message": (
        "Docker Update: {container} – {status} ({target})",
        "Docker update: {container} – {status} ({target})",
    ),
    "auto.smtp_missing": (
        "Für Benachrichtigungen fehlen noch SMTP-Server und Empfänger in den "
        "Einstellungen.",
        "Notifications still need an SMTP server and recipient in the settings.",
    ),
    "auto.table_title": (
        "Letzte automatische Prüfung: {time}",
        "Last automatic check: {time}",
    ),
    # ------------------------------------------------------------------ Backups
    "backups.title": ("Backups & Rollbacks", "Backups & rollbacks"),
    "backups.subtitle": (
        "Sicherungen, Rollback-Skripte und Rollback-Images für",
        "Backups, rollback scripts and rollback images for",
    ),
    "backups.section": ("Sicherungen", "Backups"),
    "backups.refresh": ("Aktualisieren", "Refresh"),
    "backups.empty": (
        "Noch keine Sicherungspunkte für diesen Container. Ein Snapshot oder ein "
        "Update legt automatisch einen an.",
        "No restore points for this container yet. A snapshot or an update "
        "creates one automatically.",
    ),
    "backups.col.time": ("Zeitpunkt", "Point in time"),
    "backups.col.size": ("Größe", "Size"),
    "backups.col.image": ("Rollback-Image", "Rollback image"),
    "backups.col.config": ("Konfiguration", "Configuration"),
    "backups.no_snapshot": ("ohne Snapshot", "no snapshot"),
    "backups.no_snapshot_title": (
        "Von der alten Skript-Version erstellt: Die Container-Konfiguration wird "
        "beim Wiederherstellen aus dem laufenden Container übernommen.",
        "Created by the old script version: the container configuration is taken "
        "from the running container during restore.",
    ),
    "backups.image_missing": ("Image fehlt", "Image missing"),
    "backups.image_only": ("nur Image", "image only"),
    "backups.image_only_title": (
        "Für diesen Punkt wurde kein Datenbackup erstellt – wiederhergestellt "
        "werden Image und Konfiguration.",
        "No data backup was created for this point – only image and "
        "configuration are restored.",
    ),
    "backups.script": ("Rollback-Skript", "Rollback script"),
    "backups.no_script": ("kein Skript", "no script"),
    "backups.snapshot": ("Snapshot", "Snapshot"),
    "backups.restore": ("Wiederherstellen", "Restore"),
    "backups.restore_confirm": (
        "Wiederherstellen für '{container}' auf {created}?\n\nDer Container "
        "wird gestoppt und entfernt, das Datenverzeichnis {data_dir} wird aus dem "
        "Backup ({size}) wiederhergestellt und der Container mit dem "
        "Rollback-Image neu erstellt.",
        "Restore '{container}' to {created}?\n\nThe container is stopped and "
        "removed, the data directory {data_dir} is restored from the backup "
        "({size}) and the container is recreated with the rollback image.",
    ),
    "backups.restore_confirm_nodata": (
        "Wiederherstellen für '{container}' auf {created}?\n\nDer Container "
        "wird gestoppt und entfernt und mit dem Rollback-Image neu erstellt. Ein "
        "Datenbackup gibt es für diesen Punkt nicht.",
        "Restore '{container}' to {created}?\n\nThe container is stopped and "
        "removed, then recreated with the rollback image. There is no data backup "
        "for this point.",
    ),
    "backups.delete": ("Löschen", "Delete"),
    "backups.delete_confirm": (
        "Backup vom {created} inklusive Skript, Snapshot und Rollback-Vermerk "
        "löschen?",
        "Delete the backup from {created} including script, snapshot and rollback "
        "note?",
    ),
    "backups.not_restorable": (
        "Wiederherstellen nicht möglich:",
        "Restore not possible:",
    ),
    "backups.images": ("Rollback-Images", "Rollback images"),
    "backups.delete_orphans": ("Ohne Sicherung löschen", "Delete without backup"),
    "backups.delete_orphans_confirm": (
        "{count} Rollback-Image(s) ohne zugehörige Sicherung löschen?\n\n"
        "{images}",
        "Delete {count} rollback image(s) without a matching restore point?"
        "\n\n{images}",
    ),
    "backups.orphan": ("ohne Sicherung", "no restore point"),
    "backups.orphan_title": (
        "Zu diesem Image gibt es keinen Sicherungspunkt – typisch für einen "
        "abgebrochenen Lauf oder einen von Hand gesetzten Tag.",
        "There is no restore point for this image – typical for an aborted run or "
        "a manually set tag.",
    ),
    "backups.hint": (
        "Backup-Verzeichnis: {path} · Aufbewahrung: {count} Rollback-Punkt(e) pro "
        "Container. Rollback-Images „ohne Sicherung“ lassen sich oben löschen.",
        "Backup directory: {path} · Retention: {count} rollback point(s) per "
        "container. Rollback images without a restore point can be deleted "
        "above.",
    ),
    "backups.rollback_title": ("Rollback", "Rollback"),
    "backups.rollback_text": (
        "Ein Rollback über die Oberfläche stoppt und entfernt den Container, "
        "spielt die gesicherten Daten zurück und erstellt den Container mit dem "
        "beim Update getaggten Rollback-Image neu (Konfiguration aus dem "
        "gespeicherten Snapshot).",
        "A rollback from the UI stops and removes the container, restores the "
        "backed-up data and recreates the container with the rollback image "
        "tagged during the update (configuration from the saved snapshot).",
    ),
    "backups.rollback_script_note": (
        "Das herunterladbare Rollback-Skript führt dieselben Schritte auf dem "
        "Docker-Host aus, falls die Oberfläche nicht erreichbar ist.",
        "The downloadable rollback script performs the same steps on the Docker "
        "host in case the UI is not reachable.",
    ),
    # --------------------------------------------------------------------- Jobs
    "job.status.running": ("läuft", "running"),
    "job.status.success": ("abgeschlossen", "done"),
    "job.status.error": ("Fehler", "error"),
    "job.action": ("Aktion", "Action"),
    "job.image": ("Image", "Image"),
    "job.rollback_image": ("Rollback-Image", "Rollback image"),
    "job.backup": ("Backup", "Backup"),
    "job.script": ("Rollback-Skript", "Rollback script"),
    "job.data_dir": ("Datenverzeichnis", "Data directory"),
    "job.to_container": ("Zur Container-Ansicht", "To the container view"),
    "job.gone": (
        "Dieser Auftrag ist nicht mehr verfügbar.",
        "This job is no longer available.",
    ),
    "job.log_error": ("FEHLER", "ERROR"),
    # -------------------------------------------------------------------- Login
    "login.title": ("Anmelden", "Sign in"),
    "login.subtitle": (
        "Container-Updates, Daten-Backups und Rollbacks für diesen Docker-Host.",
        "Container updates, data backups and rollbacks for this Docker host.",
    ),
    "login.password": ("Passwort", "Password"),
    "login.submit": ("Anmelden", "Sign in"),
    "login.hint": (
        "Das Passwort wird über WEB_PASSWORD gesetzt.",
        "The password is set via WEB_PASSWORD.",
    ),
    "login.wrong": ("Falsches Passwort.", "Wrong password."),
    "login.rate_limited": (
        "Zu viele Fehlversuche. Bitte ein paar Minuten warten.",
        "Too many failed attempts. Please wait a few minutes.",
    ),
    # -------------------------------------------------------------------- Fehler
    "error.not_found": ("Nicht gefunden", "Not found"),
    "error.docker": ("Docker-Fehler", "Docker error"),
    "error.request_failed": (
        "Die Anfrage konnte nicht abgeschlossen werden.",
        "The request could not be completed.",
    ),
    "error.back": ("Zur Übersicht", "Back to overview"),
    "error.container_missing": (
        "Container '{name}' existiert nicht.",
        "Container '{name}' does not exist.",
    ),
    "error.container_gone": (
        "Container existiert nicht mehr.",
        "The container no longer exists.",
    ),
    "error.csrf.title": (
        "Anfrage abgelehnt (CSRF-Schutz)",
        "Request rejected (CSRF protection)",
    ),
    "error.csrf.body": (
        "Die Herkunft der Anfrage passt nicht zum aufgerufenen Host. Origin: "
        "{origin} · Host: {host}{forwarded}. Läuft die Oberfläche hinter einem "
        "Reverse Proxy, trage die öffentliche Adresse in ALLOWED_ORIGINS ein "
        "(z. B. https://docker.example.com) und starte den Container neu. "
        "Alternativ lässt sich die Prüfung mit CSRF_PROTECTION=0 abschalten – "
        "dann muss der Zugriff anderweitig abgesichert sein.",
        "The origin of the request does not match the requested host. Origin: "
        "{origin} · Host: {host}{forwarded}. If the UI runs behind a reverse "
        "proxy, add the public address to ALLOWED_ORIGINS (e.g. "
        "https://docker.example.com) and restart the container. Alternatively "
        "the check can be disabled with CSRF_PROTECTION=0 – then access must be "
        "protected another way.",
    ),
    "error.no_password": (
        "WEB_PASSWORD ist nicht gesetzt. Bitte die Umgebungsvariable "
        "WEB_PASSWORD setzen – ohne Passwort startet die Oberfläche nicht.",
        "WEB_PASSWORD is not set. Please set the WEB_PASSWORD environment "
        "variable – the UI does not start without a password.",
    ),
    # --------------------------------------------------------------- Einstellungen
    "settings.title": ("Einstellungen", "Settings"),
    "settings.subtitle": (
        "Verzeichnisse und Aufbewahrung von Rollback-Punkten.",
        "Directories and retention of rollback points.",
    ),
    "settings.backup_not_persistent": (
        "Das aktuelle Backup-Verzeichnis liegt nicht auf einem eingebundenen "
        "Volume. Backups wären nicht dauerhaft – bitte den Pfad als Volume "
        "einbinden.",
        "The current backup directory is not on a mounted volume. Backups would "
        "not persist – please mount the path as a volume.",
    ),
    "settings.backup_dir": ("Backup-Verzeichnis", "Backup directory"),
    "settings.backup_dir_hint": (
        "Im Container unter demselben Pfad wie auf dem Host einbinden (z. B. "
        "- /docker/backups:/docker/backups). Standard: "
        "{default}",
        "Mount it in the container under the same path as on the host (e.g. "
        "- /docker/backups:/docker/backups). Default: "
        "{default}",
    ),
    "settings.browse_dir": (
        "Startverzeichnis für die Ordnerauswahl",
        "Starting directory for the folder picker",
    ),
    "settings.browse_dir_hint": (
        "Muss im Container sichtbar sein, z. B. /docker.",
        "Must be visible in the container, e.g. /docker.",
    ),
    "settings.max_rollbacks": (
        "Rollback-Punkte pro Container",
        "Rollback points per container",
    ),
    "settings.max_rollbacks_hint": (
        "Ältere Backups, Skripte und Rollback-Images werden automatisch gelöscht.",
        "Older backups, scripts and rollback images are deleted automatically.",
    ),
    "settings.smtp_title": ("E-Mail-Benachrichtigungen", "Email notifications"),
    "settings.smtp_host": ("SMTP-Server", "SMTP server"),
    "settings.smtp_host_hint": (
        "Hostname ohne Schema, z. B. mail.example.com oder die IP des "
        "Docker-Hosts.",
        "Hostname without a scheme, e.g. mail.example.com or the IP of the "
        "Docker host.",
    ),
    "settings.smtp_port": ("Port", "Port"),
    "settings.smtp_security": ("Verschlüsselung", "Encryption"),
    "settings.smtp_security.none": ("Keine", "None"),
    "settings.smtp_security.starttls": ("STARTTLS", "STARTTLS"),
    "settings.smtp_security.ssl": ("SSL/TLS", "SSL/TLS"),
    "settings.smtp_user": ("Benutzername", "Username"),
    "settings.smtp_from": ("Absender", "Sender"),
    "settings.smtp_from_hint": (
        "Leer lassen, dann wird der Benutzername als Absender verwendet.",
        "Leave empty to use the username as sender.",
    ),
    "settings.smtp_to": ("Empfänger", "Recipient"),
    "settings.smtp_to_hint": (
        "Mehrere Adressen mit Komma trennen. Ohne Empfänger werden keine "
        "Benachrichtigungen versendet.",
        "Separate multiple addresses with commas. No notifications are sent "
        "without a recipient.",
    ),
    "settings.smtp_password_hint": (
        "Das SMTP-Passwort wird ausschließlich über die Umgebungsvariable "
        "SMTP_PASSWORD gesetzt und nie in der Oberfläche angezeigt.",
        "The SMTP password is only read from the SMTP_PASSWORD environment "
        "variable and never shown in the interface.",
    ),
    "settings.save": ("Speichern", "Save"),
    "settings.back": ("Zurück zur Übersicht", "Back to the overview"),
    "settings.data_dirs": ("Datenverzeichnisse", "Data directories"),
    "settings.col_container": ("Container", "Container"),
    "settings.col_dir": ("Verzeichnis", "Directory"),
    "settings.remove": ("Entfernen", "Remove"),
    "settings.remove_confirm": (
        "Zuordnung für {name} entfernen?",
        "Remove the mapping for {name}?",
    ),
    "settings.no_data_backup": (
        "Keine Datensicherung",
        "No data backup",
    ),
    "settings.no_data_dirs": (
        "Noch keine Datenverzeichnisse hinterlegt – das passiert beim ersten "
        "Update pro Container.",
        "No data directories configured yet – this happens on the first update of "
        "a container.",
    ),
    "settings.volumes.title": (
        "Volumes des Tool-Containers",
        "Volumes of the tool container",
    ),
    "settings.volumes.text": (
        "Die Oberfläche arbeitet mit den Pfaden, die sie im eigenen Container "
        "sieht. Deshalb müssen Host-Pfade 1:1 eingebunden werden:",
        "The UI works with the paths it sees inside its own container. That is "
        "why host paths must be mounted 1:1:",
    ),
    "settings.volumes.note": (
        "Jedes Datenverzeichnis, das gesichert oder zurückgespielt werden soll, "
        "muss ebenfalls 1:1 gemountet sein. Für Backups genügt Leserechte, für "
        "Rollbacks werden Schreibrechte benötigt.",
        "Every data directory that should be backed up or restored must be "
        "mounted 1:1 as well. Read access is enough for backups, write access is "
        "needed for rollbacks.",
    ),
    # ------------------------------------------------------------------ Browser
    "browse.up": ("↑ Übergeordneter Ordner", "↑ Parent folder"),
    "browse.hidden": ("Versteckte Ordner", "Hidden folders"),
    "browse.select": ("Auswählen", "Select"),
    "browse.empty": ("Keine Unterordner.", "No subfolders."),
    "browse.placeholder": ("/vollständiger/pfad", "/full/path"),
    "browse.take": ("Diesen Pfad übernehmen", "Use this path"),
    # --------------------------------------------------------------- Meldungen
    "msg.data_saved": (
        "Datenverzeichnis gespeichert: {path}",
        "Data directory saved: {path}",
    ),
    "msg.data_removed": ("Datenverzeichnis entfernt.", "Data directory removed."),
    "msg.backup_disabled": (
        "Datenbackup abgeschaltet – gesichert wird nur das Image.",
        "Data backup disabled – only the image is saved.",
    ),
    # ------------------------------------------------------------ Konfiguration
    "label.backup_dir": ("Backup-Verzeichnis", "Backup directory"),
    "label.browse_dir": ("Startverzeichnis", "Starting directory"),
    "label.data_dir": ("Datenverzeichnis", "Data directory"),
    "label.cron_minute": ("Minute", "Minute"),
    "label.cron_hour": ("Stunde", "Hour"),
    "label.cron_day": ("Tag", "Day"),
    "label.cron_month": ("Monat", "Month"),
    "label.cron_weekday": ("Wochentag", "Weekday"),
    "err.path_required": ("Bitte einen Pfad angeben.", "Please enter a path."),
    "err.path_absolute": (
        "'{path}' ist kein absoluter Pfad. Bitte mit / beginnen (Pfade im "
        "Container = Pfade auf dem Host).",
        "'{path}' is not an absolute path. Please start with / (container paths = "
        "host paths).",
    ),
    "err.dir_missing": (
        "{label} '{path}' ist im Container nicht vorhanden. Bitte den Ordner 1:1 "
        "als Volume einbinden (- {path}:{path}).",
        "{label} '{path}' does not exist in the container. Please mount the folder "
        "1:1 as a volume (- {path}:{path}).",
    ),
    "err.dir_create": (
        "{label} '{path}' kann nicht angelegt werden: {error}",
        "{label} '{path}' cannot be created: {error}",
    ),
    "err.not_a_dir": (
        "{label} '{path}' ist kein Verzeichnis.",
        "{label} '{path}' is not a directory.",
    ),
    "err.dir_not_writable": (
        "{label} '{path}' ist nicht beschreibbar.",
        "{label} '{path}' is not writable.",
    ),
    "err.max_rollbacks_int": (
        "Max. Rollbacks muss eine ganze Zahl sein.",
        "Max. rollbacks must be an integer.",
    ),
    "err.max_rollbacks_min": (
        "Max. Rollbacks muss mindestens 1 sein.",
        "Max. rollbacks must be at least 1.",
    ),
    "err.smtp_host": (
        "Der SMTP-Server muss ein Hostname ohne Schema und ohne Pfad sein.",
        "The SMTP server must be a hostname without a scheme or path.",
    ),
    "err.smtp_port": (
        "Der SMTP-Port muss eine Zahl zwischen 1 und 65535 sein.",
        "The SMTP port must be a number between 1 and 65535.",
    ),
    "err.smtp_security": (
        "Unbekannte Verschlüsselung für den SMTP-Versand.",
        "Unknown encryption mode for SMTP.",
    ),
    "err.smtp_from": (
        "Der Absender ist keine gültige E-Mail-Adresse.",
        "The sender is not a valid email address.",
    ),
    "err.smtp_to": (
        "Der Empfänger ist keine gültige E-Mail-Adresse.",
        "The recipient is not a valid email address.",
    ),
    "err.auto_mode": (
        "Unbekannte Aktion für die automatische Prüfung.",
        "Unknown action for the automatic check.",
    ),
    # -------------------------------------------------------------------- Cron
    "cron.field_count": (
        "'{expression}' ist kein Cron-Ausdruck mit fünf Feldern "
        "(Minute Stunde Tag Monat Wochentag).",
        "'{expression}' is not a five-field cron expression "
        "(minute hour day month weekday).",
    ),
    "cron.invalid_value": (
        "Feld {field}: '{value}' ist keine gültige Angabe.",
        "Field {field}: '{value}' is not a valid value.",
    ),
    "cron.invalid_step": (
        "Feld {field}: Schrittweite '{value}' muss eine Zahl größer 0 sein.",
        "Field {field}: step '{value}' must be a number greater than 0.",
    ),
    "cron.invalid_range": (
        "Feld {field}: '{value}' ist kein gültiger Bereich.",
        "Field {field}: '{value}' is not a valid range.",
    ),
    "cron.out_of_range": (
        "Feld {field}: erlaubt sind {low} bis {high}.",
        "Field {field}: allowed values are {low} to {high}.",
    ),
    "cron.no_match": (
        "Der Ausdruck '{expression}' trifft nie zu.",
        "The expression '{expression}' never matches.",
    ),
    "err.no_rollback_image": (
        "Zu diesem Zeitpunkt existiert kein Rollback-Image.",
        "There is no rollback image for this point in time.",
    ),
    "err.rollback_image_deleted": (
        "Das Rollback-Image wurde gelöscht.",
        "The rollback image has been deleted.",
    ),
    "err.no_data_dir": (
        "Es ist kein Datenverzeichnis hinterlegt.",
        "No data directory is configured.",
    ),
    "err.backup_missing": (
        "Die Backup-Datei fehlt.",
        "The backup file is missing.",
    ),
    "msg.options_saved": ("Einstellung gespeichert.", "Setting saved."),
    "msg.settings_saved": ("Einstellungen gespeichert.", "Settings saved."),
    "msg.auto_saved": (
        "Zeitplan für die automatische Prüfung gespeichert.",
        "Schedule for the automatic check saved.",
    ),
    "msg.auto_checked": (
        "Prüfung ausgeführt: {status}",
        "Check finished: {status}",
    ),
    "msg.data_mapping_removed": (
        "Zuordnung für '{name}' entfernt.",
        "Mapping for '{name}' removed.",
    ),
    "msg.orphans_removed": (
        "{count} Rollback-Image(s) ohne Sicherung gelöscht.",
        "Deleted {count} rollback image(s) without a restore point.",
    ),
    "msg.no_orphans": (
        "Keine Rollback-Images ohne Sicherung gefunden.",
        "No rollback images without a restore point found.",
    ),
    "msg.deleted": ("Gelöscht.", "Deleted."),
    "msg.job_prefix": ("Update", "Update"),
    "msg.job_snapshot": ("Snapshot", "Snapshot"),
    "msg.job_rollback": ("Rollback", "Rollback"),
    "msg.job_tag": ("Tag-Wechsel", "Tag switch"),
    # ---------------------------------------------------------------- Hinweise
    "hint.updateable": ("update-fähig", "updatable"),
    "hint.fixed_tag": ("fester Tag", "fixed tag"),
    "hint.fixed_digest": ("fester Digest", "fixed digest"),
    "hint.rollback": ("ROLLBACK-AKTIV", "ROLLBACK ACTIVE"),
    "status.running": ("läuft", "running"),
    "status.exited": ("gestoppt", "stopped"),
    "status.created": ("erstellt", "created"),
    "status.paused": ("pausiert", "paused"),
    "status.restarting": ("startet neu", "restarting"),
    "status.dead": ("defekt", "dead"),
    # ----------------------------------------------------------------- Docker
    "docker.unavailable": (
        "Docker-Daemon nicht erreichbar. Ist /var/run/docker.sock eingebunden "
        "und lesbar? ({error})",
        "Docker daemon not reachable. Is /var/run/docker.sock mounted and "
        "readable? ({error})",
    ),
    "docker.pull_error": ("Pull-Fehler: {error}", "Pull error: {error}"),
    "docker.pull_failed": (
        "Image '{ref}' konnte nicht gepullt werden: {error}",
        "Image '{ref}' could not be pulled: {error}",
    ),
    "docker.stop": ("Stoppe Container '{name}' ...", "Stopping container '{name}' ..."),
    "docker.stopped": ("Container gestoppt.", "Container stopped."),
    "docker.not_running": (
        "Container '{name}' läuft nicht (Status: {status}).",
        "Container '{name}' is not running (status: {status}).",
    ),
    "docker.start": ("Starte Container '{name}' ...", "Starting container '{name}' ..."),
    "docker.started": ("Container gestartet.", "Container started."),
    "docker.running": ("Container läuft.", "Container is running."),
    "docker.gone": (
        "Container '{name}' existiert nicht (mehr).",
        "Container '{name}' does not exist (anymore).",
    ),
    "docker.remove": ("Entferne Container '{name}' ...", "Removing container '{name}' ..."),
    "docker.removed": ("Container entfernt.", "Container removed."),
    "docker.image_removed": ("Image '{ref}' gelöscht.", "Image '{ref}' deleted."),
    "docker.image_remove_failed": (
        "Image '{ref}' konnte nicht gelöscht werden: {error}",
        "Image '{ref}' could not be deleted: {error}",
    ),
    "docker.recreate": (
        "Erstelle Container '{name}' mit Image '{image}' neu ...",
        "Recreating container '{name}' with image '{image}' ...",
    ),
    "docker.recreate_failed": (
        "Container '{name}' konnte nicht erstellt werden: {error}",
        "Container '{name}' could not be created: {error}",
    ),
    "docker.network_connected": (
        "Mit Netzwerk '{name}' verbunden.",
        "Connected to network '{name}'.",
    ),
    "docker.network_failed": (
        "WARNUNG: Netzwerk '{name}' nicht verbunden: {error}",
        "WARNING: network '{name}' not connected: {error}",
    ),
    "update.status.available": ("UPDATE VERFÜGBAR", "UPDATE AVAILABLE"),
    "update.status.current": ("aktuell", "up to date"),
    "update.status.unknown": ("nicht prüfbar", "cannot be checked"),
    "update.digest_detail": (
        "Container nutzt einen festen Digest bzw. eine Image-ID ohne Tag – es "
        "gibt keinen aktualisierbaren Tag.",
        "The container uses a fixed digest or an image ID without a tag – there "
        "is no tag to update.",
    ),
    "update.image_missing_after_pull": (
        "Image '{ref}' ist nach dem Pull nicht vorhanden.",
        "Image '{ref}' is not present after the pull.",
    ),
    "update.available_detail": (
        "Das gepullte Image unterscheidet sich vom Image des Containers "
        "(Container: {running}, Registry: {pulled}).",
        "The pulled image differs from the container image (container: {running}, "
        "registry: {pulled}).",
    ),
    "update.current_detail": (
        "Der Container nutzt bereits das zuletzt gepullte Image.",
        "The container already uses the most recently pulled image.",
    ),
    # -------------------------------------------------------------- Operationen
    "op.self_update": (
        "Dieses Tool kann sich nicht selbst aktualisieren.",
        "This tool cannot update itself.",
    ),
    "op.self_snapshot": (
        "Für dieses Tool selbst wird kein Snapshot erstellt.",
        "No snapshot is created for this tool itself.",
    ),
    "op.no_data_dir": (
        "Für diesen Container ist kein Datenverzeichnis hinterlegt. Bitte zuerst "
        "das Datenverzeichnis auswählen.",
        "No data directory is configured for this container. Please choose one "
        "first.",
    ),
    "op.data_dir_invisible": (
        "Datenverzeichnis '{path}' ist im Container nicht sichtbar. Bitte '- "
        "{path}:{path}' als Volume einbinden.",
        "Data directory '{path}' is not visible in the container. Please mount "
        "'- {path}:{path}' as a volume.",
    ),
    "op.data_dir_missing": (
        "Datenverzeichnis '{path}' nicht gefunden.",
        "Data directory '{path}' not found.",
    ),
    "op.backup_creating": (
        "Erstelle Daten-Backup: {path}",
        "Creating data backup: {path}",
    ),
    "op.backup_entries": (
        "  ... {count} Einträge archiviert",
        "  ... {count} entries archived",
    ),
    "op.backup_failed": (
        "Archivierung fehlgeschlagen: {error}",
        "Archiving failed: {error}",
    ),
    "op.backup_done": (
        "Backup fertig: {size} ({count} Einträge)",
        "Backup complete: {size} ({count} entries)",
    ),
    "op.remove_warning": (
        "WARNUNG: '{path}' konnte nicht entfernt werden: {error}",
        "WARNING: '{path}' could not be removed: {error}",
    ),
    "op.backup_not_found": (
        "Backup '{path}' nicht gefunden.",
        "Backup '{path}' not found.",
    ),
    "op.moved_aside": (
        "Vorherige Daten nach '{path}' verschoben.",
        "Previous data moved to '{path}'.",
    ),
    "op.move_failed": (
        "WARNUNG: Verschieben nicht möglich ({error}), Inhalt wird ersetzt.",
        "WARNING: could not move ({error}), contents are replaced.",
    ),
    "op.mountpoint_cleared": (
        "Datenverzeichnis ist ein Mountpunkt – Inhalt wird vor dem Restore "
        "geleert.",
        "The data directory is a mount point – contents are cleared before the "
        "restore.",
    ),
    "op.restoring": (
        "Stelle Daten aus '{path}' wieder her ...",
        "Restoring data from '{path}' ...",
    ),
    "op.restore_failed": (
        "Wiederherstellung fehlgeschlagen: {error}",
        "Restore failed: {error}",
    ),
    "op.restored": ("Daten wiederhergestellt.", "Data restored."),
    "op.prune_backup": ("Lösche altes Backup {name}", "Deleting old backup {name}"),
    "op.prune_file_warning": (
        "  WARNUNG: {name} nicht löschbar: {error}",
        "  WARNING: {name} cannot be deleted: {error}",
    ),
    "op.prune_images_warning": (
        "WARNUNG: Rollback-Images nicht prüfbar: {error}",
        "WARNING: rollback images cannot be checked: {error}",
    ),
    "op.prune_image": (
        "Lösche altes Rollback-Image {ref}",
        "Deleting old rollback image {ref}",
    ),
    "op.orphans_none": (
        "Keine Rollback-Images ohne Sicherung gefunden.",
        "No rollback images without a restore point found.",
    ),
    "op.orphan_delete": (
        "Lösche verwaistes Rollback-Image {ref}",
        "Deleting orphaned rollback image {ref}",
    ),
    "op.orphans_deleted": (
        "{removed} von {total} Image(s) gelöscht.",
        "Deleted {removed} of {total} image(s).",
    ),
    "op.deleted_files": ("Gelöscht: {files}", "Deleted: {files}"),
    "op.no_files": ("Keine Dateien gefunden.", "No files found."),
    "op.snapshot_unreadable": (
        "Snapshot '{path}' ist nicht lesbar: {error}",
        "Snapshot '{path}' cannot be read: {error}",
    ),
    "op.snapshot_save_failed": (
        "WARNUNG: Snapshot konnte nicht gespeichert werden: {error}",
        "WARNING: snapshot could not be saved: {error}",
    ),
    "op.no_data_dir_snapshot": (
        "Im Snapshot ist kein Datenverzeichnis hinterlegt.",
        "No data directory is stored in the snapshot.",
    ),
    "op.no_script": (
        "Kein Rollback-Skript für diesen Zeitpunkt vorhanden.",
        "No rollback script exists for this point in time.",
    ),
    "op.container": ("Container: {name}", "Container: {name}"),
    "op.data": ("Daten:     {path}", "Data:      {path}"),
    "op.backup_path": ("Backup:    {path}", "Backup:    {path}"),
    "op.rollback_image": ("Rollback:  {ref}", "Rollback:  {ref}"),
    "op.backup_not_persistent_warning": (
        "WARNUNG: Das Backup-Verzeichnis liegt nicht auf einem eingebundenen "
        "Volume. Backups gehen beim Neuerstellen des Tool-Containers verloren.",
        "WARNING: the backup directory is not on a mounted volume. Backups are "
        "lost when the tool container is recreated.",
    ),
    "op.tag_image": (
        "Tagge aktuelles Image als Rollback-Punkt ...",
        "Tagging the current image as a rollback point ...",
    ),
    "op.image_tagged": ("Image getaggt: {ref}", "Image tagged: {ref}"),
    "op.snapshot_saved": (
        "Container-Konfiguration gesichert: {path}",
        "Container configuration saved: {path}",
    ),
    "op.script_written": (
        "Rollback-Skript erstellt: {path}",
        "Rollback script written: {path}",
    ),
    "op.start_failed": (
        "WARNUNG: Container konnte nicht gestartet werden: {error}",
        "WARNING: container could not be started: {error}",
    ),
    "op.action": ("Aktion:    {status}", "Action:    {status}"),
    "op.checking": (
        "Prüfe Update-Verfügbarkeit für '{ref}' ...",
        "Checking for an update of '{ref}' ...",
    ),
    "op.status": ("Status: {status}", "Status: {status}"),
    "op.no_update": (
        "Kein Update verfügbar (Status: {status}). Es wurde kein Backup erstellt.",
        "No update available (status: {status}). No backup was created.",
    ),
    "op.pull_regular": (
        "Pulle reguläres Image '{ref}' ...",
        "Pulling regular image '{ref}' ...",
    ),
    "op.pull_target": (
        "Pulle Image '{ref}' ...",
        "Pulling image '{ref}' ...",
    ),
    "op.switch_status": (
        "UMSTELLUNG AUF REGULÄRES IMAGE",
        "SWITCH TO REGULAR IMAGE",
    ),
    "op.rollback_detected": (
        "Rollback-Image erkannt",
        "Rollback image detected",
    ),
    "op.rollback_detected_detail": (
        "Der Container läuft auf einem Rollback-Image. Das Image wird nach dem "
        "Backup wieder auf ':latest' umgestellt.",
        "The container runs on a rollback image. After the backup the image is "
        "switched back to ':latest'.",
    ),
    "op.finished": ("Fertig.", "Done."),
    "op.summary.container": ("  Container:       {name}", "  Container:       {name}"),
    "op.summary.action": ("  Aktion:          {status}", "  Action:          {status}"),
    "op.summary.image": ("  Neues Image:     {ref}", "  New image:       {ref}"),
    "op.summary.rollback": (
        "  Rollback-Image:  {ref}",
        "  Rollback image:  {ref}",
    ),
    "op.summary.backup": (
        "  Daten-Backup:    {path}",
        "  Data backup:     {path}",
    ),
    "op.summary.no_backup": (
        "  Daten-Backup:    keine (nur Image)",
        "  Data backup:     none (image only)",
    ),
    "op.summary.script": (
        "  Rollback-Skript: {path}",
        "  Rollback script: {path}",
    ),
    "op.snapshot_status": ("SNAPSHOT", "SNAPSHOT"),
    "op.tag_status": ("TAG-WECHSEL", "TAG SWITCH"),
    "op.tag_same": (
        "Der Container läuft bereits auf '{ref}'.",
        "The container already runs on '{ref}'.",
    ),
    "op.tag_from_to": (
        "Image: {current} → {target}",
        "Image: {current} → {target}",
    ),
    "err.tag_required": (
        "Bitte ein Ziel-Tag angeben.",
        "Please enter a target tag.",
    ),
    "err.tag_invalid": (
        "'{value}' ist kein gültiges Tag.",
        "'{value}' is not a valid tag.",
    ),
    "err.tag_other_repo": (
        "Das Ziel muss im selben Repository liegen ({repo}).",
        "The target must be in the same repository ({repo}).",
    ),
    "op.snapshot_done": (
        "Snapshot fertig – das Image wurde nicht verändert.",
        "Snapshot complete – the image was not changed.",
    ),
    "op.rollback_image_missing": (
        "Im Snapshot ist kein Rollback-Image hinterlegt.",
        "No rollback image is stored in the snapshot.",
    ),
    "op.rollback_config_missing": (
        "Zur Sicherung fehlt die Container-Konfiguration ({name}) und der "
        "Container ist nicht lesbar: {error}",
        "The container configuration ({name}) is missing from the backup and the "
        "container cannot be read: {error}",
    ),
    "op.rollback_image_gone": (
        "Rollback-Image '{ref}' ist nicht mehr vorhanden. Ein Rollback ist nicht "
        "möglich.",
        "Rollback image '{ref}' no longer exists. A rollback is not possible.",
    ),
    "op.rollback_start": (
        "Rollback für '{name}' auf Stand {created}",
        "Rolling back '{name}' to {created}",
    ),
    "op.rollback_image_line": (
        "Rollback-Image: {ref}",
        "Rollback image: {ref}",
    ),
    "op.rollback_backup_line": ("Backup:         {path}", "Backup:         {path}"),
    "op.legacy_point": (
        "Für diesen Punkt gibt es keinen Snapshot – die Konfiguration wird vom "
        "laufenden Container übernommen.",
        "There is no snapshot for this point – the configuration is taken from "
        "the running container.",
    ),
    "op.snapshot_added": (
        "Konfiguration als Snapshot nachgetragen: {path}",
        "Configuration added as snapshot: {path}",
    ),
    "op.rollback_done": ("Rollback abgeschlossen.", "Rollback complete."),
    "op.no_data_backup": (
        "Für diesen Punkt wurde kein Datenbackup erstellt – es wird nur das "
        "Image wiederhergestellt.",
        "No data backup was created for this point – only the image is restored.",
    ),
    "op.data_warning": (
        "WARNUNG: Datenverzeichnis '{path}' ist nicht sichtbar – es wird kein "
        "Datenbackup erstellt.",
        "WARNING: data directory '{path}' is not visible – no data backup is "
        "created.",
    ),
    # ------------------------------------------------------- Rollback-Skript
    "script.title": (
        "Automatisch erzeugt von Docker Update UI am {created}",
        "Generated by Docker Update UI on {created}",
    ),
    "script.run_hint": (
        "Auf dem Docker-Host ausführen, z. B.: sudo bash",
        "Run on the Docker host, e.g.: sudo bash",
    ),
    "script.comment": (
        "Das Skript stoppt/entfernt den Container, sichert die aktuellen Daten,\n"
        "# spielt das Backup zurück und erstellt den Container mit dem "
        "Rollback-Image.",
        "The script stops/removes the container, saves the current data,\n"
        "# restores the backup and recreates the container with the rollback "
        "image.",
    ),
    "script.comment_nodata": (
        "Das Skript stoppt/entfernt den Container und erstellt ihn mit dem\n"
        "# Rollback-Image neu. Es gibt für diesen Punkt kein Datenbackup.",
        "The script stops/removes the container and recreates it with the\n"
        "# rollback image. There is no data backup for this point.",
    ),
    "script.error_prefix": ("FEHLER", "ERROR"),
    "script.stop_remove": (
        "Stoppe und entferne Container ...",
        "Stopping and removing container ...",
    ),
    "script.restore": ("Stelle Daten wieder her ...", "Restoring data ..."),
    "script.move_failed": (
        "Konnte Datenverzeichnis nicht verschieben",
        "Could not move the data directory",
    ),
    "script.restore_failed": ("Restore fehlgeschlagen", "Restore failed"),
    "script.recreate": (
        "Erstelle Container mit Rollback-Image neu ...",
        "Recreating container with the rollback image ...",
    ),
    "script.done": ("Rollback abgeschlossen.", "Rollback complete."),
}

_current: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ui_language", default=DEFAULT_LANGUAGE
)


def normalize(value: str | None) -> str | None:
    """Sprachcode ('de', 'de-DE', 'en_US') auf 'de'/'en' reduzieren."""
    if not value:
        return None
    code = value.strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else None


def detect(request: "Request") -> str:
    """Sprache aus Cookie, sonst Accept-Language, sonst Standard."""
    cookie = normalize(request.cookies.get(LANGUAGE_COOKIE))
    if cookie:
        return cookie
    header = request.headers.get("accept-language", "")
    candidates: list[tuple[float, str]] = []
    for position, part in enumerate(header.split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0 - position * 0.001
        for parameter in params.split(";"):
            name, _, raw = parameter.strip().partition("=")
            if name == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        code = normalize(tag)
        if code:
            candidates.append((quality, code))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return DEFAULT_LANGUAGE


def get_language() -> str:
    return _current.get()


def set_language(language: str) -> contextvars.Token:
    return _current.set(normalize(language) or DEFAULT_LANGUAGE)


def reset_language(token: contextvars.Token) -> None:
    _current.reset(token)


def t(key: str, lang: str | None = None, **values: Any) -> str:
    """Text in der gewünschten (sonst aktuellen) Sprache.

    ``lang`` ist nötig, weil Jinja-Templates erst beim Senden der Antwort
    gerendert werden – dann ist der ContextVar der Anfrage bereits zurückgesetzt.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    language = normalize(lang) or get_language()
    text = entry[0] if language == "de" else entry[1]
    if values:
        try:
            return text.format(**values)
        except (KeyError, IndexError):
            return text
    return text


def translate(value: str) -> str:
    """Übersetzten Text durchreichen (für Templates)."""
    return value


def date_format(lang: str | None = None) -> str:
    language = normalize(lang) or get_language()
    return DATE_FORMATS["en" if language == "en" else "de"]


def keys() -> list[str]:
    return sorted(_STRINGS)
