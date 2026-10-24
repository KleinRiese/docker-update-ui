import json
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient

from app import config, docker_ops, operations
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client):
    response = client.post(
        "/login", data={"password": "test-pass"}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


CONTAINER_ROW = {
    "name": "web",
    "id": "a" * 64,
    "short_id": "a" * 12,
    "image": "nginx:latest",
    "image_id": "b" * 12,
    "status": "running",
    "status_text": "Up 2 hours",
    "created": "2026-01-01T00:00:00Z",
    "hint": "update-fähig",
    "hint_class": "updateable",
    "is_self": False,
}

CONTAINER_DETAIL = dict(
    CONTAINER_ROW,
    restart_policy="unless-stopped",
    mounts=[],
    ports=["8080 → 80/tcp"],
    networks=["bridge"],
    attrs={},
)


def _backups(container="web"):
    return {
        "container": container,
        "backup_dir": "/tmp/backups/web",
        "backups": [],
        "images": [],
        "max_rollbacks": 3,
    }


def test_redirect_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_partial_requires_login(client):
    response = client.get(
        "/partials/containers",
        headers={"hx-request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_login_with_wrong_password(client):
    response = client.post("/login", data={"password": "falsch"})
    assert response.status_code == 401
    assert "Falsches Passwort" in response.text


def test_login_success_and_overview(client, monkeypatch):
    response = client.post(
        "/login", data={"password": "test-pass"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "dui_session" in response.cookies

    monkeypatch.setattr(docker_ops, "list_containers", lambda: [dict(CONTAINER_ROW)])
    page = client.get("/")
    assert page.status_code == 200
    assert "web" in page.text
    assert "update-fähig" in page.text
    assert "Content-Security-Policy" in page.headers


def test_cross_origin_post_is_rejected(client):
    response = client.post(
        "/login",
        data={"password": "test-pass"},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert "ALLOWED_ORIGINS" in response.text


def test_csrf_allows_same_hostname_with_other_port(client):
    response = client.post(
        "/login",
        data={"password": "test-pass"},
        headers={"origin": "http://testserver:8443"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_csrf_accepts_forwarded_host(client):
    response = client.post(
        "/login",
        data={"password": "test-pass"},
        headers={
            "origin": "https://ui.example.com",
            "x-forwarded-host": "ui.example.com",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_csrf_accepts_allowed_origins_env(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ui.example.com, other.example")
    response = client.post(
        "/login",
        data={"password": "test-pass"},
        headers={"origin": "https://ui.example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_csrf_can_be_disabled_for_external_auth_proxy(client, monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "0")
    response = client.post(
        "/login",
        data={"password": "test-pass"},
        headers={"origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_overview_shows_docker_error(client, monkeypatch):
    client.post("/login", data={"password": "test-pass"})

    def boom():
        raise RuntimeError("Socket fehlt")

    monkeypatch.setattr(docker_ops, "list_containers", boom)
    page = client.get("/")
    assert page.status_code == 200
    assert "Socket fehlt" in page.text


def test_container_detail(logged_in, monkeypatch):
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert page.status_code == 200
    assert "nginx:latest" in page.text
    assert "Datenverzeichnis" in page.text
    assert "Noch kein Datenverzeichnis" not in page.text


def test_check_partial_renders_plan(logged_in, monkeypatch):
    monkeypatch.setattr(
        operations,
        "prepare_update",
        lambda name, settings, log=print: {
            "mode": "update",
            "status": "UPDATE VERFÜGBAR",
            "current": "nginx:latest",
            "target": "nginx:latest",
            "check": {
                "status": "UPDATE VERFÜGBAR",
                "detail": "Neueres Image gefunden.",
                "updateable": True,
                "target": "nginx:latest",
            },
            "data_dir": "/tmp/data",
            "backup_dir": "/tmp/backups/web",
            "max_rollbacks": 3,
        },
    )
    page = logged_in.get("/containers/web/check")
    assert page.status_code == 200
    assert "Backup erstellen" in page.text
    assert "UPDATE VERFÜGBAR" in page.text
    # Block-Wrapper: die Checkbox-Klasse "check" darf hier nicht greifen,
    # sonst läuft die Meldung in einer Zeile aus dem Panel.
    assert 'class="check-result"' in page.text


def test_check_partial_without_data_dir(logged_in, monkeypatch):
    monkeypatch.setattr(
        operations,
        "prepare_update",
        lambda name, settings, log=print: {
            "mode": "update",
            "current": "nginx:latest",
            "target": "nginx:latest",
            "check": {
                "status": "UPDATE VERFÜGBAR",
                "detail": "Neueres Image gefunden.",
                "updateable": True,
                "target": "nginx:latest",
            },
            "data_dir": None,
            "backup_dir": "/tmp/backups/web",
            "max_rollbacks": 3,
        },
    )
    page = logged_in.get("/containers/web/check")
    assert "Bitte zuerst ein Datenverzeichnis hinterlegen" in page.text


def test_update_job_runs_in_background(logged_in, monkeypatch):
    def fake_execute(container, settings, log):
        log("Schritt 1: Test")
        return {
            "container": container,
            "status": "UPDATE VERFÜGBAR",
            "image": "nginx:latest",
        }

    monkeypatch.setattr(operations, "execute_update", fake_execute)
    response = logged_in.post("/containers/web/update")
    assert response.status_code == 200
    job_id = re.search(r'id="job-([0-9a-f]+)"', response.text).group(1)

    for _ in range(100):
        page = logged_in.get(f"/partials/jobs/{job_id}")
        if "abgeschlossen" in page.text:
            break
        time.sleep(0.05)
    assert "Schritt 1: Test" in page.text
    assert "abgeschlossen" in page.text


def test_rollback_without_snapshot_shows_error(logged_in, monkeypatch):
    def broken(name, timestamp, settings):
        raise operations.OperationError("Kein Backup gefunden.")

    monkeypatch.setattr(operations, "rollback_plan", broken)
    response = logged_in.post(
        "/containers/web/rollback", data={"timestamp": "20240101-000000"}
    )
    assert response.status_code == 200
    assert "Kein Backup gefunden." in response.text


def test_settings_page_and_save(logged_in):
    original = config.load_settings()
    backup_dir = Path(tempfile.mkdtemp(prefix="dui-settings-backup-"))
    browse_dir = Path(tempfile.mkdtemp(prefix="dui-settings-browse-"))
    try:
        page = logged_in.get("/settings")
        assert page.status_code == 200
        assert original.backup_dir in page.text

        response = logged_in.post(
            "/settings",
            data={
                "backup_dir": str(backup_dir),
                "browse_dir": str(browse_dir),
                "watchtower_image": "nickfedor/watchtower",
                "max_rollbacks": "5",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        saved = config.load_settings()
        assert saved.backup_dir == str(backup_dir)
        assert saved.max_rollbacks == 5

        invalid = logged_in.post(
            "/settings",
            data={
                "backup_dir": "relativ/pfad",
                "browse_dir": str(browse_dir),
                "watchtower_image": "nickfedor/watchtower",
                "max_rollbacks": "5",
            },
            follow_redirects=False,
        )
        assert invalid.status_code == 303
        assert invalid.headers["location"].startswith("/settings?err=")
    finally:
        config.save_settings(original)


def test_data_dir_save_and_remove(logged_in):
    data_dir = Path(tempfile.mkdtemp(prefix="dui-data-"))
    response = logged_in.post(
        "/containers/web/data-dir", data={"path": str(data_dir)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert config.get_data_dir("web") == str(data_dir)

    missing = logged_in.post(
        "/containers/web/data-dir",
        data={"path": str(data_dir / "gibt-es-nicht")},
        follow_redirects=False,
    )
    assert missing.headers["location"].startswith("/containers/web?err=")
    assert "nicht vorhanden" in unquote_plus(missing.headers["location"])

    logged_in.post("/containers/web/data-dir/delete", follow_redirects=False)
    assert config.get_data_dir("web") is None


def test_browse_lists_directories(logged_in, tmp_path, monkeypatch):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "datei.txt").write_text("x")
    page = logged_in.get("/browse", params={"path": str(tmp_path)})
    assert page.status_code == 200
    assert "alpha" in page.text and "beta" in page.text
    assert "datei.txt" not in page.text


def test_backup_and_restore_roundtrip(tmp_path):
    source = tmp_path / "data"
    (source / "sub").mkdir(parents=True)
    (source / "a.txt").write_text("hallo")
    (source / "sub" / "b.txt").write_text("welt")
    archive = tmp_path / "backups" / "web-20240101-000000.tar.gz"

    size = operations.backup_data_dir(str(source), str(archive))
    assert size > 0 and archive.is_file()

    shutil.rmtree(source)
    operations.restore_data_dir(str(archive), str(source))
    assert (source / "a.txt").read_text() == "hallo"
    assert (source / "sub" / "b.txt").read_text() == "welt"


def test_restore_keeps_previous_data_aside(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    (source / "alt.txt").write_text("alt")
    archive = tmp_path / "web-20240101-000000.tar.gz"
    operations.backup_data_dir(str(source), str(archive))

    (source / "neu.txt").write_text("neu")
    operations.restore_data_dir(str(archive), str(source))
    assert (source / "alt.txt").read_text() == "alt"
    aside = list(tmp_path.glob("data.before-rollback.*"))
    assert aside and (aside[0] / "neu.txt").exists()


def test_rollback_script_is_generated(tmp_path):
    attrs = {
        "Name": "/web",
        "Config": {
            "Image": "nginx:latest",
            "Env": ["TZ=Europe/Berlin"],
            "Cmd": ["nginx", "-g", "daemon off;"],
            "ExposedPorts": {"80/tcp": {}},
            "Labels": {"tier": "web"},
        },
        "HostConfig": {
            "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
            "Binds": ["/srv/web:/usr/share/nginx/html"],
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {}},
    }
    command = docker_ops.build_run_command(attrs, "nginx:rollback-20240101-000000")
    assert "--name web" in command
    assert "-p 8080:80" in command
    assert "-v /srv/web:/usr/share/nginx/html" in command
    assert "--restart unless-stopped" in command
    assert "nginx:rollback-20240101-000000" in command

    script = tmp_path / "rollback_web_20240101-000000.sh"
    operations.write_rollback_script(
        attrs,
        "web",
        "/srv/web",
        "/docker/backups/web/web-20240101-000000.tar.gz",
        "nginx:rollback-20240101-000000",
        str(script),
    )
    content = script.read_text()
    assert content.startswith("#!/bin/bash")
    assert 'CONTAINER="web"' in content
    assert 'DATA_DIR="/srv/web"' in content
    assert "tar -xzf" in content
    assert "docker run -d --name web" in content
    assert script.stat().st_mode & 0o111


def test_image_ref_helpers():
    assert docker_ops.split_image_ref("nginx:1.25") == ("nginx", "1.25")
    assert docker_ops.split_image_ref("ghcr.io/foo/bar:latest") == (
        "ghcr.io/foo/bar",
        "latest",
    )
    assert docker_ops.split_image_ref("nginx") == ("nginx", "latest")
    assert docker_ops.is_rollback_image("nginx:rollback-20240101-000000")
    assert not docker_ops.is_rollback_image("nginx:latest")
    assert docker_ops.update_hint("nginx:latest")[0] == "update-fähig"
    assert docker_ops.update_hint("nginx:1.25")[0] == "fester Tag"
    assert docker_ops.update_hint("nginx:rollback-1")[0] == "ROLLBACK-AKTIV"


def test_cleanup_rollbacks_keeps_newest(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path),
        browse_dir=str(tmp_path),
        watchtower_image="nickfedor/watchtower",
        max_rollbacks=1,
    )
    base = tmp_path / "web"
    base.mkdir()
    for stamp in ("20240101-000000", "20240102-000000"):
        (base / f"web-{stamp}.tar.gz").write_bytes(b"x")
        (base / f"rollback_web_{stamp}.sh").write_text("#!/bin/bash\n")
        (base / f"rollback_web_{stamp}.json").write_text("{}")

    monkeypatch.setattr(docker_ops, "list_rollback_images", lambda repo: [])
    monkeypatch.setattr(docker_ops, "prune_dangling", lambda: None)
    operations.cleanup_rollbacks("web", settings, "nginx", log=lambda message: None)

    assert (base / "web-20240102-000000.tar.gz").exists()
    assert not (base / "web-20240101-000000.tar.gz").exists()
    assert not (base / "rollback_web_20240101-000000.sh").exists()


def _snapshot_settings(tmp_path):
    return config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        watchtower_image="nickfedor/watchtower",
        max_rollbacks=3,
    )


def test_list_backups_marks_orphan_images(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    base = Path(settings.backup_dir) / "web"
    base.mkdir(parents=True)
    (base / "web-20240101-000000.tar.gz").write_bytes(b"x")
    (base / "rollback_web_20240101-000000.json").write_text(
        json.dumps(
            {
                "rollback_image": "nginx:rollback-20240101-000000",
                "data_dir": "/srv/web",
            }
        )
    )
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(
        docker_ops,
        "list_rollback_images",
        lambda repo: [
            {
                "ref": "nginx:rollback-20240101-000000",
                "id": "aaa",
                "created": 0,
                "size": 1,
                "timestamp": "20240101-000000",
            },
            {
                "ref": "nginx:rollback-20240102-000000",
                "id": "bbb",
                "created": 0,
                "size": 1,
                "timestamp": "20240102-000000",
            },
        ],
    )

    data = operations.list_backups("web", settings)
    assert data["backups"][0]["restorable"] is True
    assert data["backups"][0]["image_present"] is True
    orphans = [image["ref"] for image in data["orphan_images"]]
    assert orphans == ["nginx:rollback-20240102-000000"]
    assert data["images"][0]["point"] == datetime(2024, 1, 1)


def test_legacy_backup_point_without_snapshot_is_restorable(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    base = Path(settings.backup_dir) / "web"
    base.mkdir(parents=True)
    (base / "web-20240101-000000.tar.gz").write_bytes(b"x")
    (base / "rollback_web_20240101-000000.sh").write_text("#!/bin/bash\n")

    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(config, "get_data_dir", lambda name: "/srv/web")
    monkeypatch.setattr(
        docker_ops,
        "list_rollback_images",
        lambda repo: [
            {
                "ref": "nginx:rollback-20240101-000000",
                "id": "aaa",
                "created": 0,
                "size": 1,
                "timestamp": "20240101-000000",
            }
        ],
    )

    backup = operations.list_backups("web", settings)["backups"][0]
    assert backup["has_snapshot"] is False
    assert backup["rollback_image"] == "nginx:rollback-20240101-000000"
    assert backup["restorable"] is True


def test_rollback_plan_for_legacy_point_uses_live_config(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    base = Path(settings.backup_dir) / "web"
    base.mkdir(parents=True)
    (base / "web-20240101-000000.tar.gz").write_bytes(b"x")
    (base / "rollback_web_20240101-000000.sh").write_text("#!/bin/bash\n")

    detail = dict(
        CONTAINER_DETAIL,
        attrs={
            "Name": "/web",
            "Config": {"Image": "nginx:latest"},
            "HostConfig": {},
            "Mounts": [],
            "NetworkSettings": {"Networks": {}},
        },
    )
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: detail)
    monkeypatch.setattr(config, "get_data_dir", lambda name: "/srv/web")
    monkeypatch.setattr(
        docker_ops,
        "find_rollback_image",
        lambda timestamp: "nginx:rollback-20240101-000000",
    )
    monkeypatch.setattr(docker_ops, "image_exists", lambda ref: True)

    plan = operations.rollback_plan("web", "20240101-000000", settings)
    assert plan["legacy"] is True
    assert plan["has_snapshot"] is False
    assert plan["image"] == "nginx:rollback-20240101-000000"
    assert plan["data_dir"] == "/srv/web"
    assert plan["payload"]["inspect"]["Config"]["Image"] == "nginx:latest"


def test_execute_snapshot_creates_point_without_update(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "app.conf").write_text("inhalt")

    calls: list[tuple] = []
    detail = dict(CONTAINER_DETAIL, image="nginx:latest", status="running")
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: detail)
    monkeypatch.setattr(config, "get_data_dir", lambda name: str(data_dir))
    monkeypatch.setattr(config, "path_is_persistent", lambda path: True)
    monkeypatch.setattr(
        docker_ops,
        "tag_rollback_image",
        lambda name, repo, tag: calls.append(("tag", f"{repo}:{tag}")),
    )
    monkeypatch.setattr(
        docker_ops, "stop_container", lambda name, log=None: calls.append(("stop",))
    )
    monkeypatch.setattr(
        docker_ops, "start_container", lambda name, log=None: calls.append(("start",))
    )
    monkeypatch.setattr(docker_ops, "container_is_running", lambda name: False)
    monkeypatch.setattr(docker_ops, "list_rollback_images", lambda repo: [])
    monkeypatch.setattr(docker_ops, "prune_dangling", lambda: None)

    def no_update(*args, **kwargs):
        raise AssertionError("Snapshot darf kein Update auslösen")

    monkeypatch.setattr(docker_ops, "watchtower_update", no_update)

    result = operations.execute_snapshot("web", settings, log=lambda message: None)

    assert result["mode"] == "snapshot"
    archive = Path(result["backup_file"])
    assert archive.is_file()
    assert Path(result["rollback_script"]).is_file()
    snapshot = json.loads(
        (archive.parent / f"rollback_web_{result['timestamp']}.json").read_text("utf-8")
    )
    assert snapshot["rollback_image"] == result["rollback_image"]
    assert snapshot["data_dir"] == str(data_dir)
    assert calls[0] == ("tag", result["rollback_image"])
    assert ("start",) in calls


def test_snapshot_route_starts_job(logged_in, monkeypatch):
    def fake_snapshot(container, settings, log):
        log("Snapshot erstellt")
        return {"mode": "snapshot"}

    monkeypatch.setattr(operations, "execute_snapshot", fake_snapshot)
    response = logged_in.post("/containers/web/snapshot")
    assert response.status_code == 200
    job_id = re.search(r'id="job-([0-9a-f]+)"', response.text).group(1)

    for _ in range(100):
        page = logged_in.get(f"/partials/jobs/{job_id}")
        if "abgeschlossen" in page.text:
            break
        time.sleep(0.05)
    assert "Snapshot erstellt" in page.text


def test_cleanup_orphan_images_route(logged_in, monkeypatch):
    monkeypatch.setattr(
        operations,
        "cleanup_orphan_images",
        lambda name, settings, log=None: ["nginx:rollback-20240102-000000"],
    )
    response = logged_in.post(
        "/containers/web/images/cleanup", follow_redirects=False
    )
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]


def test_backup_partial_renders_restore_and_orphans(logged_in, monkeypatch):
    orphan = {
        "ref": "nginx:rollback-20240102-000000",
        "id": "bbb",
        "created": 0,
        "size": 1,
        "timestamp": "20240102-000000",
        "point": None,
        "orphan": True,
    }
    data = {
        "container": "web",
        "backup_dir": "/backups/web",
        "max_rollbacks": 2,
        "orphan_images": [orphan],
        "backups": [
            {
                "timestamp": "20240101-000000",
                "created": datetime(2024, 1, 1),
                "archive": "/backups/web/web-20240101-000000.tar.gz",
                "archive_name": "web-20240101-000000.tar.gz",
                "size": 10,
                "size_text": "10 B",
                "script": "/backups/web/rollback_web_20240101-000000.sh",
                "snapshot": None,
                "has_snapshot": False,
                "rollback_image": "nginx:rollback-20240101-000000",
                "data_dir": "/srv/web",
                "image_present": True,
                "restorable": True,
                "restore_hint": None,
            }
        ],
        "images": [
            {
                "ref": "nginx:rollback-20240101-000000",
                "id": "aaa",
                "created": 0,
                "size": 1,
                "timestamp": "20240101-000000",
                "point": datetime(2024, 1, 1),
                "orphan": False,
            },
            orphan,
        ],
    }
    monkeypatch.setattr(
        operations, "list_backups", lambda name, settings: data
    )
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )

    page = logged_in.get("/containers/web/backups")
    assert page.status_code == 200
    assert "Wiederherstellen" in page.text
    assert "ohne Snapshot" in page.text
    assert "ohne Sicherung" in page.text
    assert "Ohne Sicherung löschen" in page.text


def test_delete_backup(tmp_path):
    settings = config.Settings(
        backup_dir=str(tmp_path),
        browse_dir=str(tmp_path),
        watchtower_image="nickfedor/watchtower",
        max_rollbacks=3,
    )
    paths = operations.backup_paths(settings, "web", "20240101-000000")
    Path(paths["archive"]).parent.mkdir(parents=True)
    for key in ("archive", "script", "snapshot"):
        Path(paths[key]).write_text("x")
    operations.delete_backup("web", "20240101-000000", settings, log=lambda m: None)
    assert not Path(paths["archive"]).exists()
    assert not Path(paths["script"]).exists()
    assert not Path(paths["snapshot"]).exists()
