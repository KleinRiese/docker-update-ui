import json
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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


def test_image_refs_are_compared_without_registry_prefix():
    assert (
        docker_ops.normalize_image_ref(
            "/docker.io/nginx:rollback-20240101-000000"
        )
        == "nginx:rollback-20240101-000000"
    )
    assert docker_ops.normalize_image_ref("docker.io/library/nginx:rolling") == (
        "nginx:rolling"
    )
    assert docker_ops.normalize_image_ref("nginx") == "nginx:latest"


def test_list_rollback_images_matches_prefixed_tags(monkeypatch):
    images = [
        SimpleNamespace(
            tags=["/docker.io/nginx:rollback-20240101-000000"],
            short_id="sha256:aaa",
            attrs={"Created": 0, "Size": 1},
        )
    ]
    monkeypatch.setattr(
        docker_ops,
        "get_client",
        lambda: SimpleNamespace(images=SimpleNamespace(list=lambda: images)),
    )

    found = docker_ops.list_rollback_images("nginx")
    assert [image["ref"] for image in found] == ["nginx:rollback-20240101-000000"]
    assert found[0]["timestamp"] == "20240101-000000"


def test_list_backups_resolves_prefixed_image_by_timestamp(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    base = Path(settings.backup_dir) / "web"
    base.mkdir(parents=True)
    (base / "web-20240101-000000.tar.gz").write_bytes(b"x")
    (base / "rollback_web_20240101-000000.json").write_text(
        json.dumps(
            {
                "rollback_image": "/docker.io/nginx:rollback-20240101-000000",
                "data_dir": "/srv/web",
            }
        )
    )
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(docker_ops, "list_rollback_images", lambda repo: [])
    monkeypatch.setattr(
        docker_ops,
        "find_rollback_image",
        lambda timestamp: f"nginx:rollback-{timestamp}",
    )

    backup = operations.list_backups("web", settings)["backups"][0]
    assert backup["rollback_image"] == "nginx:rollback-20240101-000000"
    assert backup["image_present"] is True
    assert backup["restorable"] is True


def test_rollback_plan_normalizes_snapshot_image(tmp_path, monkeypatch):
    settings = _snapshot_settings(tmp_path)
    base = Path(settings.backup_dir) / "web"
    base.mkdir(parents=True)
    (base / "web-20240101-000000.tar.gz").write_bytes(b"x")
    (base / "rollback_web_20240101-000000.json").write_text(
        json.dumps(
            {
                "rollback_image": "/docker.io/nginx:rollback-20240101-000000",
                "data_dir": "/srv/web",
                "inspect": {"Config": {"Image": "nginx:latest"}},
            }
        )
    )
    monkeypatch.setattr(
        docker_ops,
        "image_exists",
        lambda ref: ref == "nginx:rollback-20240101-000000",
    )

    plan = operations.rollback_plan("web", "20240101-000000", settings)
    assert plan["image"] == "nginx:rollback-20240101-000000"
    assert plan["payload"]["rollback_image"] == "nginx:rollback-20240101-000000"


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

    monkeypatch.setattr(docker_ops, "pull_image", no_update)
    monkeypatch.setattr(docker_ops, "recreate_container", no_update)

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


# ---------------------------------------------------------------------------
# Zweisprachigkeit
# ---------------------------------------------------------------------------


def test_every_translation_has_both_languages():
    from app import i18n

    empty = [
        key
        for key, entry in i18n._STRINGS.items()
        if not entry[0].strip() or not entry[1].strip()
    ]
    assert empty == []


def test_all_translation_keys_used_exist():
    from app import i18n

    missing: list[tuple[str, str]] = []
    for path in (Path("app/templates")).glob("*.html"):
        for key in re.findall(
            r"(?<![\w.])t\('([^']+)'", path.read_text("utf-8")
        ):
            if key.endswith("."):
                continue
            if key not in i18n._STRINGS:
                missing.append((path.name, key))
    for path in Path("app").glob("*.py"):
        for key in re.findall(r'i18n\.t\(\s*"([^"]+)"', path.read_text("utf-8")):
            if key not in i18n._STRINGS:
                missing.append((path.name, key))
    assert missing == []


def test_language_switch_sets_cookie(logged_in, monkeypatch):
    monkeypatch.setattr(docker_ops, "list_containers", lambda: [])
    response = logged_in.get("/language/en?next=/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.cookies["dui_lang"] == "en"

    page = logged_in.get("/")
    assert '<html lang="en">' in page.text
    assert "Settings" in page.text
    assert "Sign out" in page.text
    assert "Einstellungen" not in page.text


def test_accept_language_header_selects_english(client):
    page = client.get("/login", headers={"accept-language": "en-US,en;q=0.9"})
    assert page.status_code == 200
    assert "Sign in" in page.text
    assert "Password" in page.text


def test_german_is_default(client):
    page = client.get("/login")
    assert "Anmelden" in page.text
    assert "Passwort" in page.text


def test_language_switch_rejects_external_redirect(logged_in):
    response = logged_in.get(
        "/language/en?next=//evil.example", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Update ohne Watchtower, optional ohne Datenbackup
# ---------------------------------------------------------------------------


def _stateless_detail():
    return dict(
        CONTAINER_DETAIL,
        image="nginx:latest",
        status="running",
        attrs={
            "Name": "/stateless",
            "Config": {"Image": "nginx:latest"},
            "HostConfig": {},
            "Mounts": [],
            "NetworkSettings": {"Networks": {}},
        },
    )


def _patch_docker(monkeypatch, calls: list, detail: dict, rollback_ref: dict | None = None):
    monkeypatch.setattr(config, "path_is_persistent", lambda path: True)
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: dict(detail))
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
    monkeypatch.setattr(
        docker_ops, "pull_image", lambda ref, log=None: calls.append(("pull", ref))
    )
    monkeypatch.setattr(
        docker_ops,
        "recreate_container",
        lambda attrs, ref, log=None: calls.append(("recreate", ref)),
    )
    monkeypatch.setattr(
        docker_ops,
        "get_container_attrs",
        lambda name: {"Config": {"Image": "nginx:latest"}},
    )
    monkeypatch.setattr(
        docker_ops,
        "check_update",
        lambda name, log=None: {
            "status": "UPDATE VERFÜGBAR",
            "detail": "Test",
            "updateable": True,
            "target": "nginx:latest",
        },
    )
    images = (
        [
            {
                "ref": rollback_ref,
                "id": "abc",
                "created": 0,
                "size": 1,
                "timestamp": rollback_ref.rsplit("rollback-", 1)[1],
            }
        ]
        if rollback_ref
        else []
    )
    monkeypatch.setattr(docker_ops, "list_rollback_images", lambda repo: images)
    monkeypatch.setattr(docker_ops, "image_exists", lambda ref: True)
    monkeypatch.setattr(docker_ops, "prune_dangling", lambda: None)


def test_update_without_data_backup_uses_native_path(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    config.set_backup_data("stateless", False)
    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())

    def no_data_dir(name):
        return None

    monkeypatch.setattr(config, "get_data_dir", no_data_dir)

    result = operations.execute_update(
        "stateless", settings, log=lambda message: None
    )

    assert result["data_backup"] is False
    assert result["backup_file"] is None
    assert ("pull", "nginx:latest") in calls
    assert ("recreate", "nginx:latest") in calls
    assert not any(call[0] for call in calls if call[0] == "watchtower")

    point = Path(settings.backup_dir) / "stateless"
    assert not (point / f"stateless-{result['timestamp']}.tar.gz").exists()
    snapshot = json.loads(
        (point / f"rollback_stateless_{result['timestamp']}.json").read_text("utf-8")
    )
    assert snapshot["data_backup"] is False
    assert snapshot["backup_file"] is None

    script = Path(result["rollback_script"]).read_text("utf-8")
    assert "docker run -d --name stateless" in script
    assert "tar -xzf" not in script


def test_manual_update_replaces_stale_check_result(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    config.set_backup_data("stateless", False)
    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())
    monkeypatch.setattr(config, "get_data_dir", lambda name: None)
    config.record_auto_check(
        "stateless",
        {"at": time.time(), "state": "available", "target": "nginx:latest"},
    )
    try:
        operations.execute_update("stateless", settings, log=lambda message: None)
        last = config.get_auto_check("stateless")["last"]
        assert last["state"] == "current"
        assert last["target"] == "nginx:latest"
    finally:
        config.record_auto_check("stateless", None)


def test_tag_switch_clears_stale_check_result(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    config.set_backup_data("stateless", False)
    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())
    monkeypatch.setattr(config, "get_data_dir", lambda name: None)
    config.record_auto_check(
        "stateless",
        {"at": time.time(), "state": "available", "target": "nginx:latest"},
    )
    try:
        operations.execute_switch_tag(
            "stateless", "1.25", settings, log=lambda message: None
        )
        assert config.get_auto_check("stateless")["last"] is None
    finally:
        config.record_auto_check("stateless", None)


def test_image_only_point_is_listed_and_restorable(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    config.set_backup_data("stateless", False)
    calls: list[tuple] = []
    detail = _stateless_detail()
    _patch_docker(monkeypatch, calls, detail)
    monkeypatch.setattr(config, "get_data_dir", lambda name: None)

    result = operations.execute_update(
        "stateless", settings, log=lambda message: None
    )
    rollback_ref = result["rollback_image"]
    _patch_docker(monkeypatch, calls, detail, rollback_ref=rollback_ref)

    data = operations.list_backups("stateless", settings)
    assert len(data["backups"]) == 1
    point = data["backups"][0]
    assert point["has_data_backup"] is False
    assert point["data_backup"] is False
    assert point["size_text"] == "–"
    assert point["restorable"] is True

    plan = operations.rollback_plan("stateless", point["timestamp"], settings)
    assert plan["data_backup"] is False
    assert plan["archive"] is None

    def no_restore(*args, **kwargs):
        raise AssertionError("Ohne Datenbackup darf nichts entpackt werden")

    monkeypatch.setattr(operations, "restore_data_dir", no_restore)
    calls.clear()
    operations.execute_rollback(
        "stateless", point["timestamp"], settings, log=lambda message: None
    )
    assert ("recreate", rollback_ref) in calls


def test_data_backup_checkbox_is_saved(logged_in):
    data_dir = Path(tempfile.mkdtemp(prefix="dui-data-nobackup-"))
    response = logged_in.post(
        "/containers/stateless/data-dir",
        data={"path": str(data_dir), "backup_data_set": "1", "backup_disabled": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Datenbackup+abgeschaltet" in response.headers["location"]
    assert config.get_backup_data("stateless") is False
    assert config.get_data_dir("stateless") == str(data_dir)

    response = logged_in.post(
        "/containers/stateless/data-dir",
        data={"path": str(data_dir), "backup_data_set": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert config.get_backup_data("stateless") is True


def test_container_detail_shows_backup_checkbox(logged_in, monkeypatch):
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())

    page = logged_in.get("/containers/web")
    assert 'name="backup_disabled"' in page.text
    assert "Keine Daten sichern" in page.text
    # Standard: Datenbackup aktiv -> Checkbox "Keine Daten sichern" ist aus.
    assert 'name="backup_disabled" value="1" checked' not in page.text

    logged_in.get("/language/en?next=/containers/web")
    page = logged_in.get("/containers/web")
    assert "Do not back up data" in page.text
    assert "Data directory" in page.text


def test_check_partial_without_data_backup(logged_in, monkeypatch):
    monkeypatch.setattr(
        operations,
        "prepare_update",
        lambda name, settings, log=print: {
            "mode": "update",
            "current": "nginx:latest",
            "target": "nginx:latest",
            "backup_data": False,
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
    assert page.status_code == 200
    assert "Image sichern" in page.text
    assert "Datenbackup ist für diesen Container abgeschaltet" in page.text
    assert "Bitte zuerst ein Datenverzeichnis hinterlegen" not in page.text
    assert "Daten werden nicht gesichert" in page.text


# ---------------------------------------------------------------------------
# Tag-Wechsel
# ---------------------------------------------------------------------------


def test_resolve_target_image_accepts_tag_and_reference():
    current = "ghcr.io/dispatcharr/dispatcharr:latest"
    repo = "ghcr.io/dispatcharr/dispatcharr"
    assert operations.resolve_target_image(current, "1.2.3") == f"{repo}:1.2.3"
    assert operations.resolve_target_image(current, " latest ") == f"{repo}:latest"
    assert operations.resolve_target_image(current, f"{repo}:1.2.3") == f"{repo}:1.2.3"
    digest = "sha256:" + "a" * 64
    assert operations.resolve_target_image(current, f"{repo}@{digest}") == f"{repo}@{digest}"
    assert operations.resolve_target_image(current, f"@{digest}") == f"{repo}@{digest}"


def test_resolve_target_image_rejects_other_repo_and_invalid():
    with pytest.raises(operations.OperationError):
        operations.resolve_target_image(
            "nginx:latest", "ghcr.io/other/app:1.0"
        )
    with pytest.raises(operations.OperationError):
        operations.resolve_target_image("nginx:latest", "1.0 mit leerzeichen")
    with pytest.raises(operations.OperationError):
        operations.resolve_target_image("nginx:latest", "   ")
    with pytest.raises(operations.OperationError):
        operations.resolve_target_image("nginx:latest", "sha256:kurz")


def test_switch_tag_pulls_and_recreates(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "app.conf").write_text("inhalt")
    config.set_data_dir("tagtest", str(data_dir))
    config.set_backup_data("tagtest", True)

    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())
    monkeypatch.setattr(
        docker_ops,
        "get_container_attrs",
        lambda name: {"Config": {"Image": "nginx:1.2.3"}},
    )

    result = operations.execute_switch_tag(
        "tagtest", "1.2.3", settings, log=lambda message: None
    )

    assert result["mode"] == "tag"
    assert result["image"] == "nginx:1.2.3"
    assert ("pull", "nginx:1.2.3") in calls
    assert ("recreate", "nginx:1.2.3") in calls
    assert ("tag", result["rollback_image"]) in calls
    assert Path(result["backup_file"]).is_file()
    assert Path(result["rollback_script"]).is_file()


def test_switch_tag_rejects_same_tag(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())

    with pytest.raises(operations.OperationError):
        operations.execute_switch_tag(
            "tagtest", "latest", settings, log=lambda message: None
        )
    assert calls == []


def test_switch_tag_needs_data_dir_when_backup_enabled(tmp_path, monkeypatch):
    settings = config.Settings(
        backup_dir=str(tmp_path / "backups"),
        browse_dir=str(tmp_path),
        max_rollbacks=3,
    )
    config.set_backup_data("tagtest-ohne-dir", True)
    config.remove_data_dir("tagtest-ohne-dir")
    calls: list[tuple] = []
    _patch_docker(monkeypatch, calls, _stateless_detail())

    with pytest.raises(operations.OperationError):
        operations.execute_switch_tag(
            "tagtest-ohne-dir", "1.2.3", settings, log=lambda message: None
        )
    assert calls == []


def test_tag_route_starts_job(logged_in, monkeypatch):
    def fake_switch(container, tag, settings, log):
        log(f"Ziel: {tag}")
        return {"mode": "tag", "status": "TAG-WECHSEL", "image": f"nginx:{tag}"}

    monkeypatch.setattr(operations, "execute_switch_tag", fake_switch)
    response = logged_in.post("/containers/web/tag", data={"tag": "1.2.3"})
    assert response.status_code == 200
    job_id = re.search(r'id="job-([0-9a-f]+)"', response.text).group(1)

    for _ in range(100):
        page = logged_in.get(f"/partials/jobs/{job_id}")
        if "abgeschlossen" in page.text:
            break
        time.sleep(0.05)
    assert "Ziel: 1.2.3" in page.text
    assert "TAG-WECHSEL" in page.text
    assert "nginx:1.2.3" in page.text


def test_container_detail_has_tag_form(logged_in, monkeypatch):
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert 'hx-post="/containers/web/tag"' in page.text
    assert 'name="tag"' in page.text
    assert "Tag umstellen" in page.text


def test_overview_shows_na_without_data_backup(logged_in, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config.set_data_dir("stateless", str(data_dir))
    config.set_backup_data("stateless", False)
    try:
        row = dict(CONTAINER_ROW, name="stateless")
        monkeypatch.setattr(docker_ops, "list_containers", lambda: [row])
        page = logged_in.get("/")
        assert page.status_code == 200
        assert "N/A" in page.text
        assert str(data_dir) not in page.text
    finally:
        config.set_backup_data("stateless", True)


def test_settings_shows_no_data_backup(logged_in, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config.set_data_dir("homeassistant", str(data_dir))
    config.set_backup_data("homeassistant", False)
    try:
        page = logged_in.get("/settings")
        assert page.status_code == 200
        assert "Keine Datensicherung" in page.text
        assert str(data_dir) not in page.text

        logged_in.get("/language/en?next=/settings")
        page = logged_in.get("/settings")
        assert "No data backup" in page.text
        assert str(data_dir) not in page.text
    finally:
        config.set_backup_data("homeassistant", True)


# ---------------------------------------------------------------------------
# Automatische Update-Prüfung
# ---------------------------------------------------------------------------

import types  # noqa: E402

from app import autocheck, cron  # noqa: E402


def test_cron_next_run_daily():
    schedule = cron.parse("30 4 * * *")
    assert schedule.next_after(datetime(2026, 10, 25, 12, 0)) == datetime(
        2026, 10, 26, 4, 30
    )


def test_cron_steps_and_lists():
    schedule = cron.parse("*/15 0,6 * * *")
    assert schedule.next_after(datetime(2026, 10, 25, 6, 30)) == datetime(
        2026, 10, 25, 6, 45
    )
    assert schedule.next_after(datetime(2026, 10, 25, 6, 46)) == datetime(
        2026, 10, 26, 0, 0
    )


def test_cron_weekday_counts_from_sunday():
    # 2026-10-25 ist ein Sonntag.
    schedule = cron.parse("0 9 * * 0")
    assert schedule.next_after(datetime(2026, 10, 25, 10, 0)) == datetime(
        2026, 11, 1, 9, 0
    )


def test_cron_day_and_weekday_are_combined_with_or():
    schedule = cron.parse("0 3 1 * 0")
    # 1. des Monats ODER Sonntag.
    assert schedule.next_after(datetime(2026, 10, 25, 12, 0)) == datetime(
        2026, 11, 1, 3, 0
    )


def test_cron_rejects_invalid_expressions():
    for expression in (
        "",
        "* * *",
        "60 * * * *",
        "0 24 * * *",
        "*/0 * * * *",
        "5-1 * * * *",
        "kaputt * * * *",
        "0 0 30 2 *",
    ):
        with pytest.raises(cron.CronError):
            cron.parse(expression)


def test_cron_validate_normalizes_whitespace():
    assert cron.validate("  0   */6 *  * * ") == "0 */6 * * *"


def test_auto_check_settings_roundtrip():
    config.set_auto_check("auto-settings", enabled=True, mode="notify", cron="0 6 * * *")
    assert config.get_auto_check("auto-settings") == {
        "enabled": True,
        "mode": "notify",
        "cron": "0 6 * * *",
        "last": None,
    }
    with pytest.raises(ValueError):
        config.set_auto_check("auto-settings", enabled=True, mode="notify", cron="x")
    with pytest.raises(ValueError):
        config.set_auto_check(
            "auto-settings", enabled=True, mode="unbekannt", cron="* * * * *"
        )
    # Ungültige Eingabe darf den alten Zeitplan nicht überschreiben.
    assert config.get_auto_check("auto-settings")["cron"] == "0 6 * * *"


def _fake_docker(monkeypatch, *, repo_digests, registry_digest, platforms=None,
                 arch="amd64", container_repo_digests=None):
    image = types.SimpleNamespace(
        attrs={
            "RepoDigests": repo_digests,
            "Architecture": arch,
            "Os": "linux",
        }
    )
    # Image, mit dem der Container läuft; ohne eigene Angabe dasselbe Image.
    container_image = types.SimpleNamespace(
        attrs={
            "RepoDigests": (
                repo_digests
                if container_repo_digests is None
                else container_repo_digests
            ),
            "Architecture": arch,
            "Os": "linux",
        }
    )
    data = types.SimpleNamespace(
        id=registry_digest,
        attrs={"Descriptor": {"digest": registry_digest}},
    )
    if platforms is not None:
        data.attrs["Platforms"] = platforms
    container = types.SimpleNamespace(
        attrs={
            "Config": {"Image": "nginx:latest"},
            "Image": "sha256:" + "b" * 64,
        }
    )

    def get_image(ref):
        # Die Container-Kennung liefert das laufende Image, ein Tag das lokal
        # markierte Image.
        return container_image if str(ref).startswith("sha256:") else image

    client = types.SimpleNamespace(
        images=types.SimpleNamespace(
            get=get_image, get_registry_data=lambda ref: data
        ),
        containers=types.SimpleNamespace(get=lambda name: container),
    )
    monkeypatch.setattr(docker_ops, "get_client", lambda: client)
    return client


def test_quick_check_reports_available_update(monkeypatch):
    _fake_docker(
        monkeypatch,
        repo_digests=["nginx@sha256:" + "a" * 64],
        registry_digest="sha256:" + "c" * 64,
    )
    result = docker_ops.quick_check("web")
    assert result["state"] == "available"
    assert result["updateable"] is True
    assert result["method"] == "registry"


def test_quick_check_reports_current_image(monkeypatch):
    digest = "sha256:" + "a" * 64
    _fake_docker(
        monkeypatch, repo_digests=[f"nginx@{digest}"], registry_digest=digest
    )
    result = docker_ops.quick_check("web")
    assert result["state"] == "current"
    assert result["updateable"] is False


def test_quick_check_compares_running_image_not_tag(monkeypatch):
    # Nach einem Pull zeigt der Tag auf das neue Image, der Container läuft
    # aber noch mit dem alten. Die automatische Prüfung muss das Update
    # melden, sonst widerspricht sie der manuellen Prüfung.
    running_digest = "sha256:" + "a" * 64
    registry_digest = "sha256:" + "c" * 64
    _fake_docker(
        monkeypatch,
        repo_digests=[f"nginx@{registry_digest}"],
        registry_digest=registry_digest,
        container_repo_digests=[f"nginx@{running_digest}"],
    )
    result = docker_ops.quick_check("web")
    assert result["state"] == "available"
    assert result["updateable"] is True
    assert result["method"] == "registry"
    assert result["local"] == [running_digest]


def test_quick_check_uses_platform_digest(monkeypatch):
    # Der Index-Digest unterscheidet sich vom Plattform-Digest; verglichen
    # werden muss der Digest der eigenen Architektur.
    index_digest = "sha256:" + "b" * 64
    platform_digest = "sha256:" + "d" * 64
    _fake_docker(
        monkeypatch,
        repo_digests=[f"nginx@{platform_digest}"],
        registry_digest=index_digest,
        platforms=[
            {"architecture": "arm64", "os": "linux", "digest": "sha256:" + "e" * 64},
            {"architecture": "amd64", "os": "linux", "digest": platform_digest},
        ],
    )
    result = docker_ops.quick_check("web")
    assert result["state"] == "current"
    assert result["remote"] == platform_digest


def test_quick_check_falls_back_to_pull(monkeypatch):
    _fake_docker(
        monkeypatch,
        repo_digests=[],
        registry_digest="sha256:" + "c" * 64,
    )
    calls = []

    def fake_check(name, log=print):
        calls.append(name)
        return {
            "status": docker_ops.i18n.t("update.status.available"),
            "detail": "neu",
            "updateable": True,
            "target": "nginx:latest",
        }

    monkeypatch.setattr(docker_ops, "check_update", fake_check)
    result = docker_ops.quick_check("web")
    assert calls == ["web"]
    assert result["state"] == "available"
    assert result["method"] == "pull"


def test_auto_check_records_result(monkeypatch):
    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(
        autocheck.docker_ops,
        "quick_check",
        lambda name, log=print: {
            "state": "available",
            "updateable": True,
            "target": "nginx:latest",
            "detail": "neu",
            "method": "registry",
            "remote": "sha256:" + "c" * 64,
            "local": ["sha256:" + "a" * 64],
        },
    )
    result = autocheck.run_once("auto-run", mode="check")
    assert result["state"] == "available"
    last = config.get_auto_check("auto-run")["last"]
    assert last["state"] == "available"
    assert last["target"] == "nginx:latest"
    assert last["method"] == "registry"


def test_auto_check_skips_self_container(monkeypatch):
    detail = dict(CONTAINER_DETAIL, is_self=True)
    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: detail
    )
    result = autocheck.run_once("auto-self", mode="update")
    assert result["state"] == "skipped"
    assert config.get_auto_check("auto-self")["last"] is None


def test_auto_check_runs_on_fixed_tag(monkeypatch):
    detail = dict(CONTAINER_DETAIL, image="nginx:1.25", hint_class="fixed")
    assert autocheck.skip_reason(detail) is None

    calls: list[str] = []

    def fake_check(name, log=print):
        calls.append(name)
        return {
            "state": "current",
            "updateable": False,
            "target": "nginx:1.25",
            "detail": "gleich",
            "method": "registry",
            "remote": "sha256:" + "a" * 64,
            "local": ["sha256:" + "a" * 64],
        }

    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: dict(detail)
    )
    monkeypatch.setattr(autocheck.docker_ops, "quick_check", fake_check)
    try:
        result = autocheck.run_once("auto-fixed-tag", mode="check")
    finally:
        config.record_auto_check("auto-fixed-tag", None)
    assert calls == ["auto-fixed-tag"]
    assert result["state"] == "current"


def test_auto_check_skips_fixed_digest():
    ref = "nginx@sha256:" + "a" * 64
    detail = dict(CONTAINER_DETAIL, image=ref, hint_class="fixed")
    assert "Digest" in (autocheck.skip_reason(detail) or "")


def _smtp_settings(original, **overrides):
    values = dict(
        backup_dir=original.backup_dir,
        browse_dir=original.browse_dir,
        max_rollbacks=original.max_rollbacks,
        smtp_host="mail.example.invalid",
        smtp_port=587,
        smtp_user="ui@example.invalid",
        smtp_from="ui@example.invalid",
        smtp_to="admin@example.invalid",
        smtp_security="starttls",
    )
    values.update(overrides)
    return config.Settings(**values)


def test_auto_check_notify_sends_email(monkeypatch):
    original = config.load_settings()
    config.save_settings(_smtp_settings(original))
    calls = []
    monkeypatch.setattr(
        autocheck,
        "send_email",
        lambda settings, name, state, target="", error="": calls.append(
            (settings.smtp_to, name, state, target)
        ),
    )
    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(
        autocheck.docker_ops,
        "quick_check",
        lambda name, log=print: {
            "state": "available",
            "updateable": True,
            "target": "nginx:latest",
            "detail": "neu",
            "method": "registry",
            "remote": "",
            "local": [],
        },
    )
    try:
        autocheck.run_once("auto-notify", mode="notify")
        assert calls == [
            ("admin@example.invalid", "auto-notify", "available", "nginx:latest")
        ]
    finally:
        config.save_settings(original)


def test_auto_check_update_mode_runs_update(monkeypatch):
    original = config.load_settings()
    config.save_settings(_smtp_settings(original))
    monkeypatch.setattr(autocheck, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(
        autocheck.docker_ops,
        "quick_check",
        lambda name, log=print: {
            "state": "available",
            "updateable": True,
            "target": "nginx:latest",
            "detail": "neu",
            "method": "registry",
            "remote": "",
            "local": [],
        },
    )
    updated = []
    monkeypatch.setattr(
        autocheck.operations,
        "execute_update",
        lambda name, settings, log: updated.append(name) or {"container": name},
    )
    try:
        result = autocheck.run_once("auto-update", mode="update")
        assert updated == ["auto-update"]
        assert result["state"] == "updated"
        assert config.get_auto_check("auto-update")["last"]["state"] == "updated"
    finally:
        config.save_settings(original)


def test_auto_check_update_mode_records_failure(monkeypatch):
    original = config.load_settings()
    config.save_settings(_smtp_settings(original))
    monkeypatch.setattr(autocheck, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autocheck.docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(
        autocheck.docker_ops,
        "quick_check",
        lambda name, log=print: {
            "state": "available",
            "updateable": True,
            "target": "nginx:latest",
            "detail": "neu",
            "method": "registry",
            "remote": "",
            "local": [],
        },
    )

    def broken(name, settings, log):
        raise operations.OperationError("Pull fehlgeschlagen")

    monkeypatch.setattr(autocheck.operations, "execute_update", broken)
    try:
        result = autocheck.run_once("auto-fail", mode="update")
        assert result["state"] == "error"
        last = config.get_auto_check("auto-fail")["last"]
        assert last["state"] == "error"
        assert "Pull fehlgeschlagen" in last["error"]
    finally:
        config.save_settings(original)


def test_due_entries_respects_schedule():
    now = datetime(2026, 10, 25, 4, 0).timestamp()
    config.set_auto_check("auto-due", enabled=True, mode="check", cron="0 4 * * *")
    # Noch nie gelaufen: der nächste Slot liegt in der Zukunft.
    assert "auto-due" not in [name for name, _ in autocheck.due_entries(now)]
    config.record_auto_check(
        "auto-due", {"at": now - 24 * 3600, "state": "current"}
    )
    assert ("auto-due", "check") in autocheck.due_entries(now)
    # Nach dem Lauf (Ergebnis mit "at" = jetzt) ist der nächste Slot erst
    # beim nächsten Cron-Termin fällig.
    config.record_auto_check("auto-due", {"at": now, "state": "current"})
    assert "auto-due" not in [
        name for name, _ in autocheck.due_entries(now + 60)
    ]


def test_due_entries_ignores_disabled_containers():
    config.set_auto_check(
        "auto-off", enabled=False, mode="check", cron="*/5 * * * *"
    )
    config.record_auto_check("auto-off", {"at": 0.0, "state": "current"})
    assert "auto-off" not in [name for name, _ in autocheck.due_entries(time.time())]


def test_auto_check_route_saves_schedule(logged_in):
    response = logged_in.post(
        "/containers/auto-route/auto-check",
        data={"enabled": "1", "cron": "0 */6 * * *", "mode": "notify"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = config.get_auto_check("auto-route")
    assert entry["enabled"] is True
    assert entry["mode"] == "notify"
    assert entry["cron"] == "0 */6 * * *"

    bad = logged_in.post(
        "/containers/auto-route/auto-check",
        data={"cron": "kaputt", "mode": "check"},
        follow_redirects=False,
    )
    assert bad.status_code == 303
    assert "err=" in bad.headers["location"]
    assert config.get_auto_check("auto-route")["cron"] == "0 */6 * * *"


def test_detail_shows_auto_check_panel(logged_in, monkeypatch):
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert "Automatische Update-Prüfung" in page.text
    assert "Zeitplan (Cron)" in page.text
    assert 'name="cron"' in page.text
    assert 'action="/containers/web/auto-check"' in page.text

    logged_in.get("/language/en?next=/containers/web")
    page = logged_in.get("/containers/web")
    assert "Automatic update check" in page.text
    assert "Schedule (cron)" in page.text


def test_detail_offers_schedule_for_fixed_tag(logged_in, monkeypatch):
    detail = dict(CONTAINER_DETAIL, image="nginx:1.25", hint_class="fixed")
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: detail)
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert 'action="/containers/web/auto-check"' in page.text
    assert "kein bewegliches Tag" not in page.text


def test_detail_hides_schedule_for_digest_pin(logged_in, monkeypatch):
    ref = "nginx@sha256:" + "a" * 64
    detail = dict(CONTAINER_DETAIL, image=ref, hint_class="fixed")
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: detail)
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert 'action="/containers/web/auto-check"' not in page.text
    assert "dieses Image kann sich nicht ändern" in page.text


def test_detail_shows_next_run_and_last_result(logged_in, monkeypatch):
    config.set_auto_check(
        "web", enabled=True, mode="check", cron="0 4 * * *"
    )
    config.record_auto_check(
        "web",
        {
            "at": time.time(),
            "state": "available",
            "target": "nginx:latest",
            "method": "registry",
            "remote": "sha256:" + "c" * 64,
            "local": ["sha256:" + "a" * 64],
            "error": "",
        },
    )
    monkeypatch.setattr(
        docker_ops, "container_detail", lambda name: dict(CONTAINER_DETAIL)
    )
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    try:
        page = logged_in.get("/containers/web")
        assert "Nächste Prüfung:" in page.text
        assert "Registry-Abgleich" in page.text
        assert "cccccccccccc" in page.text
    finally:
        config.set_auto_check("web", enabled=False, mode="check", cron="0 4 * * *")
        config.record_auto_check("web", None)


def test_detail_hides_schedule_for_self_container(logged_in, monkeypatch):
    detail = dict(CONTAINER_DETAIL, is_self=True)
    monkeypatch.setattr(docker_ops, "container_detail", lambda name: detail)
    monkeypatch.setattr(operations, "list_backups", lambda name, settings: _backups())
    page = logged_in.get("/containers/web")
    assert 'action="/containers/web/auto-check"' not in page.text
    assert "prüft sich nicht selbst" in page.text


def test_overview_shows_auto_check_pill(logged_in, monkeypatch):
    row = dict(CONTAINER_ROW, name="auto-row")
    config.record_auto_check(
        "auto-row",
        {
            "at": time.time(),
            "state": "available",
            "target": "nginx:latest",
            "method": "registry",
            "remote": "",
            "local": [],
            "error": "",
        },
    )
    monkeypatch.setattr(docker_ops, "list_containers", lambda: [row])
    try:
        page = logged_in.get("/")
        assert "UPDATE VERFÜGBAR" in page.text
    finally:
        config.record_auto_check("auto-row", None)


def test_settings_rejects_invalid_smtp(logged_in):
    original = config.load_settings()
    response = logged_in.post(
        "/settings",
        data={
            "backup_dir": original.backup_dir,
            "browse_dir": original.browse_dir,
            "max_rollbacks": str(original.max_rollbacks),
            "smtp_host": "mail.example.invalid",
            "smtp_port": "70000",
            "smtp_to": "admin@example.invalid",
            "smtp_security": "starttls",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert config.load_settings().smtp_host == original.smtp_host


def test_settings_saves_smtp(logged_in):
    original = config.load_settings()
    try:
        response = logged_in.post(
            "/settings",
            data={
                "backup_dir": original.backup_dir,
                "browse_dir": original.browse_dir,
                "max_rollbacks": str(original.max_rollbacks),
                "smtp_host": "mail.example.invalid",
                "smtp_port": "465",
                "smtp_security": "ssl",
                "smtp_user": "ui@example.invalid",
                "smtp_from": "ui@example.invalid",
                "smtp_to": "admin@example.invalid, ops@example.invalid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        saved = config.load_settings()
        assert saved.smtp_host == "mail.example.invalid"
        assert saved.smtp_port == 465
        assert saved.smtp_security == "ssl"
        assert saved.smtp_to == "admin@example.invalid, ops@example.invalid"
        assert saved.smtp_ready is True
    finally:
        config.save_settings(original)


def test_send_email_uses_starttls_and_login(monkeypatch):
    settings = _smtp_settings(config.load_settings())
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["target"] = (host, port, timeout)
            sent["starttls"] = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            return None

        def starttls(self, context=None):
            sent["starttls"] = context is not None

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(autocheck.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "SMTP_PASSWORD", "geheim")

    autocheck.send_email(
        settings,
        "homeassistant",
        "available",
        "ghcr.io/home-assistant/home-assistant:latest",
    )

    assert sent["target"][:2] == ("mail.example.invalid", 587)
    assert sent["starttls"] is True
    assert sent["login"] == ("ui@example.invalid", "geheim")
    message = sent["message"]
    assert message["To"] == "admin@example.invalid"
    assert message["From"] == "ui@example.invalid"
    assert "homeassistant" in message["Subject"]
    assert "ghcr.io/home-assistant" in message.get_content()


def test_quick_check_matches_index_digest_without_platform_digests(monkeypatch):
    # Docker mit containerd-Store legt den Index-Digest in RepoDigests ab,
    # die Platforms enthalten dann keine eigenen Digests.
    index_digest = "sha256:" + "f" * 64
    _fake_docker(
        monkeypatch,
        repo_digests=[f"nginx@{index_digest}"],
        registry_digest=index_digest,
        platforms=[
            {"architecture": "amd64", "os": "linux"},
            {"architecture": "unknown", "os": "unknown"},
            {"architecture": "arm64", "os": "linux"},
        ],
    )
    result = docker_ops.quick_check("web")
    assert result["state"] == "current"
    assert result["method"] == "registry"


def test_quick_check_falls_back_when_index_is_not_comparable(monkeypatch):
    # Geänderter Index, aber kein Plattform-Digest zum Vergleich: lieber die
    # vollständige Pull-Prüfung als ein falsches "Update verfügbar".
    _fake_docker(
        monkeypatch,
        repo_digests=["nginx@sha256:" + "a" * 64],
        registry_digest="sha256:" + "b" * 64,
        platforms=[
            {"architecture": "amd64", "os": "linux"},
            {"architecture": "arm64", "os": "linux"},
        ],
    )
    calls = []

    def fake_check(name, log=print):
        calls.append(name)
        return {
            "status": docker_ops.i18n.t("update.status.current"),
            "detail": "gleich",
            "updateable": False,
            "target": "nginx:latest",
        }

    monkeypatch.setattr(docker_ops, "check_update", fake_check)
    result = docker_ops.quick_check("web")
    assert calls == ["web"]
    assert result["method"] == "pull"
    assert result["state"] == "current"
