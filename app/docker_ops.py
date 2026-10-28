"""Docker-Operationen über den Docker-Socket (docker-py)."""

from __future__ import annotations

import os
import re
import shlex
import socket

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from . import i18n

ROLLBACK_TAG = "rollback"
UPDATEABLE_TAGS = {"latest", "stable", "edge", "nightly", "rolling"}

_IMAGE_REF_RE = re.compile(r"^(.+):([^/:]+)$")
_IMAGE_ID_RE = re.compile(r"^(sha256:)?[0-9a-f]{12,64}$")


class DockerUnavailable(RuntimeError):
    """Der Docker-Daemon ist nicht erreichbar."""


class OperationError(RuntimeError):
    """Ein Schritt des Ablaufs ist fehlgeschlagen (Meldung für die UI)."""


def get_client() -> docker.DockerClient:
    try:
        client = docker.from_env(timeout=int(os.environ.get("DOCKER_TIMEOUT", "600")))
        client.ping()
    except DockerException as exc:
        raise DockerUnavailable(i18n.t("docker.unavailable", error=exc)) from exc
    return client


# ---------------------------------------------------------------------------
# Image-Referenzen
# ---------------------------------------------------------------------------


def split_image_ref(ref: str) -> tuple[str, str]:
    """Image-Referenz in (Repository, Tag) zerlegen wie das Original-Skript."""
    match = _IMAGE_REF_RE.match(ref or "")
    if match:
        return match.group(1), match.group(2)
    return ref or "", "latest"


_REGISTRY_PREFIXES = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")


def normalize_repo(repo: str) -> str:
    """Repository ohne Registry-Präfix und ohne führende Schrägstriche."""
    value = (repo or "").strip().lstrip("/")
    for prefix in _REGISTRY_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if value.startswith("library/"):
        value = value[len("library/") :]
    return value


def normalize_image_ref(ref: str) -> str:
    """Image-Referenz vereinheitlichen, damit ``/docker.io/foo`` == ``foo``.

    Docker und Portainer zeigen denselben Tag je nach Herkunft als
    ``koenkk/zigbee2mqtt:latest``, ``docker.io/koenkk/zigbee2mqtt:latest``
    oder mit führendem ``/``. Für Vergleiche zählt nur der Kern.
    """
    value = (ref or "").strip().lstrip("/")
    if not value or "@" in value:
        return value
    repo, tag = split_image_ref(value)
    return f"{normalize_repo(repo)}:{tag}"


def image_repo(ref: str) -> str:
    return split_image_ref(ref)[0]


def image_tag(ref: str) -> str:
    return split_image_ref(ref)[1]


def is_rollback_image(ref: str) -> bool:
    return image_tag(ref or "").startswith(f"{ROLLBACK_TAG}-")


def is_fixed_digest(ref: str) -> bool:
    """Image ist per Digest festgelegt und kann sich nicht ändern."""
    return "@sha256:" in (ref or "") or bool(_IMAGE_ID_RE.match(ref or ""))


def update_hint(ref: str) -> tuple[str, str]:
    """Kurzer Hinweis + CSS-Klasse für die Containerliste."""
    tag = image_tag(ref)
    if tag.startswith(f"{ROLLBACK_TAG}-"):
        return i18n.t("hint.rollback"), "rollback"
    if is_fixed_digest(ref):
        return i18n.t("hint.fixed_digest"), "fixed"
    if tag in UPDATEABLE_TAGS:
        return i18n.t("hint.updateable"), "updateable"
    return i18n.t("hint.fixed_tag"), "fixed"


# ---------------------------------------------------------------------------
# Container-Informationen
# ---------------------------------------------------------------------------


def self_container_refs() -> set[str]:
    """Kennungen des Containers, in dem dieses Tool selbst läuft."""
    refs = {"", "localhost"}
    hostname = os.environ.get("SELF_CONTAINER") or socket.gethostname()
    if hostname:
        refs.add(hostname)
    return refs


def is_self_container(container) -> bool:
    refs = self_container_refs()
    short_id = container.id[:12]
    return short_id in refs or container.name in refs or container.id in refs


def _port_summary(attrs: dict) -> list[str]:
    ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
    result: list[str] = []
    for container_port, bindings in ports.items():
        if not bindings:
            result.append(f"{container_port} (intern)")
            continue
        for binding in bindings:
            host_ip = binding.get("HostIp") or ""
            host_port = binding.get("HostPort") or ""
            target = f"{host_ip}:{host_port}" if host_ip else host_port
            result.append(f"{target} → {container_port}")
    return result


def _mount_summary(attrs: dict) -> list[dict]:
    mounts = []
    for mount in attrs.get("Mounts") or []:
        if mount.get("Type") == "volume":
            source = f"Volume {mount.get('Name')}"
        else:
            source = mount.get("Source") or ""
        mounts.append(
            {
                "type": mount.get("Type"),
                "source": source,
                "destination": mount.get("Destination"),
                "rw": bool(mount.get("RW", True)),
            }
        )
    return mounts


def container_detail(name: str) -> dict:
    client = get_client()
    container = client.containers.get(name)
    attrs = container.attrs
    cfg = attrs.get("Config") or {}
    ref = cfg.get("Image") or ""
    hint, hint_class = update_hint(ref)
    return {
        "name": container.name,
        "id": container.id,
        "short_id": container.id[:12],
        "image": ref,
        "image_id": (attrs.get("Image") or "").replace("sha256:", "")[:12],
        "image_created": (attrs.get("Created") or ""),
        "status": attrs.get("State", {}).get("Status", "unknown"),
        "status_text": attrs.get("Status", ""),
        "created": attrs.get("Created") or "",
        "restart_policy": (attrs.get("HostConfig", {}).get("RestartPolicy") or {}).get(
            "Name", ""
        ),
        "mounts": _mount_summary(attrs),
        "ports": _port_summary(attrs),
        "networks": list(
            (attrs.get("NetworkSettings", {}).get("Networks") or {}).keys()
        ),
        "hint": hint,
        "hint_class": hint_class,
        "is_self": is_self_container(container),
        "attrs": attrs,
    }


def list_containers() -> list[dict]:
    client = get_client()
    rows: list[dict] = []
    for container in client.containers.list(all=True):
        attrs = container.attrs
        cfg = attrs.get("Config") or {}
        ref = cfg.get("Image") or ""
        hint, hint_class = update_hint(ref)
        rows.append(
            {
                "name": container.name,
                "id": container.id,
                "short_id": container.id[:12],
                "image": ref,
                "image_id": (attrs.get("Image") or "").replace("sha256:", "")[:12],
                "status": attrs.get("State", {}).get("Status", "unknown"),
                "status_text": attrs.get("Status", ""),
                "created": attrs.get("Created") or "",
                "hint": hint,
                "hint_class": hint_class,
                "is_self": is_self_container(container),
            }
        )
    rows.sort(key=lambda row: row["name"].lower())
    return rows


def get_container_attrs(name: str) -> dict:
    return get_client().containers.get(name).attrs


# ---------------------------------------------------------------------------
# Update-Prüfung
# ---------------------------------------------------------------------------


def pull_image(ref: str, log=lambda message: None) -> None:
    client = get_client()
    try:
        for event in client.api.pull(ref, stream=True, decode=True):
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                raise OperationError(
                    i18n.t("docker.pull_error", error=event["error"])
                )
            status = event.get("status") or ""
            if status.startswith(("Pulling from", "Digest:", "Status:")):
                log(status)
    except APIError as exc:
        raise OperationError(
            i18n.t("docker.pull_failed", ref=ref, error=exc)
        ) from exc


def check_update(name: str, log=lambda message: None) -> dict:
    """Prüfen, ob für den Container ein neueres Image vorliegt."""
    client = get_client()
    container = client.containers.get(name)
    ref = (container.attrs.get("Config") or {}).get("Image") or ""
    running_image = container.attrs.get("Image")

    if is_fixed_digest(ref):
        return {
            "status": i18n.t("update.status.unknown"),
            "detail": i18n.t("update.digest_detail"),
            "updateable": False,
            "target": ref,
        }

    try:
        pull_image(ref, log)
    except OperationError as exc:
        return {
            "status": i18n.t("update.status.unknown"),
            "detail": str(exc),
            "updateable": False,
            "target": ref,
        }

    try:
        pulled_image = client.images.get(ref).id
    except ImageNotFound:
        return {
            "status": i18n.t("update.status.unknown"),
            "detail": i18n.t("update.image_missing_after_pull", ref=ref),
            "updateable": False,
            "target": ref,
        }

    if running_image and pulled_image != running_image:
        return {
            "status": i18n.t("update.status.available"),
            "detail": i18n.t(
                "update.available_detail",
                running=str(running_image).replace("sha256:", "")[:12],
                pulled=pulled_image.replace("sha256:", "")[:12],
            ),
            "updateable": True,
            "target": ref,
        }

    return {
        "status": i18n.t("update.status.current"),
        "detail": i18n.t("update.current_detail"),
        "updateable": False,
        "target": ref,
    }


def _repo_digests(attrs: dict) -> list[str]:
    digests = []
    for entry in attrs.get("RepoDigests") or []:
        _, _, value = str(entry).partition("@")
        if value:
            digests.append(value)
    return digests


def _registry_info(client, ref: str, arch: str, os_name: str) -> dict:
    """Digest-Angaben der Registry zu einem Image sammeln.

    ``inspect_distribution`` liefert den Digest des Manifests bzw. des
    Multi-Arch-Index. Je nach Daemon-Version enthalten die ``Platforms``
    zusätzlich die Digests der Plattform-Manifeste – oder nur Architektur und
    Betriebssystem. Beides wird berücksichtigt, damit ein unverändertes Image
    nicht fälschlich als Update gemeldet wird.
    """
    data = client.images.get_registry_data(ref)
    attrs = getattr(data, "attrs", {}) or {}
    descriptor = attrs.get("Descriptor") or {}
    media_type = str(descriptor.get("mediaType") or "")
    digest = str(descriptor.get("digest") or getattr(data, "id", "") or "")
    if not digest:
        raise OperationError(i18n.t("check.no_digest", ref=ref))

    platform = ""
    for entry in attrs.get("Platforms") or []:
        if (
            entry.get("architecture") == arch
            and entry.get("os") == os_name
            and entry.get("digest")
        ):
            platform = str(entry["digest"])
            break

    return {
        "digest": digest,
        "media_type": media_type,
        "is_index": "manifest.list" in media_type
        or "image.index" in media_type
        or bool(attrs.get("Platforms")),
        "platform": platform,
    }


def quick_check(name: str, log=lambda message: None) -> dict:
    """Update-Status ohne Download der Layer ermitteln.

    Verglichen wird der Manifest-Digest der Registry mit den ``RepoDigests``
    des Images, das der Container gerade ausführt – nicht mit dem Image, auf
    das der Tag lokal zeigt. Nach einem Pull zeigt der Tag schon auf das neue
    Image, während der Container noch mit dem alten läuft. Ist das nicht
    möglich (kein RepoDigest, alte Daemon-Version, private Registry ohne
    Zugangsdaten), greift die bisherige Prüfung per Pull.
    """
    client = get_client()
    container = client.containers.get(name)
    ref = (container.attrs.get("Config") or {}).get("Image") or ""

    if is_fixed_digest(ref):
        return {
            "state": "fixed",
            "updateable": False,
            "target": ref,
            "detail": i18n.t("update.digest_detail"),
            "method": "none",
            "remote": "",
            "local": [],
        }

    image_attrs: dict = {}
    try:
        image_attrs = client.images.get(
            str(container.attrs.get("Image") or ref)
        ).attrs or {}
    except ImageNotFound:
        image_attrs = {}

    local = sorted(_repo_digests(image_attrs))
    info: dict = {}
    remote = ""
    state: str | None = None

    if local:
        try:
            info = _registry_info(
                client,
                ref,
                str(image_attrs.get("Architecture") or ""),
                str(image_attrs.get("Os") or ""),
            )
        except (OperationError, APIError, DockerException, NotFound) as exc:
            log(i18n.t("check.fallback_pull", error=exc))
            info = {}

    if local and info:
        if info["digest"] in local:
            # Der Daemon führt den Manifest- bzw. Index-Digest in RepoDigests.
            remote = info["digest"]
            state = "current"
        elif info["platform"]:
            # Digest des Plattform-Manifests direkt vergleichen.
            remote = info["platform"]
            state = "current" if remote in local else "available"
        elif not info["is_index"]:
            # Einzelmanifest: der Descriptor-Digest ist der Plattform-Digest.
            remote = info["digest"]
            state = "current" if remote in local else "available"
        # Index ohne Plattform-Digests: lokal ist nicht eindeutig vergleichbar.

    if local and info and state:
        detail = (
            i18n.t("check.digest_current", digest=remote.replace("sha256:", "")[:12])
            if state == "current"
            else i18n.t(
                "check.digest_available",
                remote=remote.replace("sha256:", "")[:12],
                local=local[-1].replace("sha256:", "")[:12],
            )
        )
        return {
            "state": state,
            "updateable": state == "available",
            "target": ref,
            "detail": detail,
            "method": "registry",
            "remote": remote,
            "local": local,
        }

    fallback = check_update(name, log)
    if fallback["updateable"]:
        state = "available"
    elif fallback["status"] == i18n.t("update.status.current"):
        state = "current"
    else:
        state = "unknown"
    return {
        "state": state,
        "updateable": fallback["updateable"],
        "target": fallback["target"],
        "detail": fallback["detail"],
        "method": "pull",
        "remote": remote,
        "local": local,
    }


# ---------------------------------------------------------------------------
# Container-Aktionen
# ---------------------------------------------------------------------------


def stop_container(name: str, log=lambda message: None) -> None:
    client = get_client()
    container = client.containers.get(name)
    if container.status == "running":
        log(i18n.t("docker.stop", name=name))
        container.stop(timeout=int(os.environ.get("STOP_TIMEOUT", "30")))
        log(i18n.t("docker.stopped"))
    else:
        log(i18n.t("docker.not_running", name=name, status=container.status))


def start_container(name: str, log=lambda message: None) -> None:
    client = get_client()
    container = client.containers.get(name)
    container.reload()
    if container.status != "running":
        log(i18n.t("docker.start", name=name))
        container.start()
        log(i18n.t("docker.started"))
    else:
        log(i18n.t("docker.running"))


def remove_container(name: str, log=lambda message: None) -> None:
    client = get_client()
    try:
        container = client.containers.get(name)
    except NotFound:
        log(i18n.t("docker.gone", name=name))
        return
    log(i18n.t("docker.remove", name=name))
    container.remove(force=True)
    log(i18n.t("docker.removed"))


def container_is_running(name: str) -> bool:
    try:
        container = get_client().containers.get(name)
        container.reload()
        return container.status == "running"
    except NotFound:
        return False


def tag_rollback_image(name: str, repo: str, tag: str) -> str:
    client = get_client()
    container = client.containers.get(name)
    image = client.images.get(container.attrs["Image"])
    image.tag(repo, tag=tag)
    return f"{repo}:{tag}"


def image_exists(ref: str) -> bool:
    try:
        get_client().images.get(ref)
        return True
    except ImageNotFound:
        return False


def list_rollback_images(repo: str) -> list[dict]:
    client = get_client()
    prefix = f"{normalize_repo(repo)}:{ROLLBACK_TAG}-"
    found = []
    for image in client.images.list():
        for tag in image.tags or []:
            ref = normalize_image_ref(tag)
            if ref.startswith(prefix):
                found.append(
                    {
                        "ref": ref,
                        "id": image.short_id.replace("sha256:", ""),
                        "created": image.attrs.get("Created", 0),
                        "size": image.attrs.get("Size", 0),
                        "timestamp": ref[len(prefix) :],
                    }
                )
    found.sort(key=lambda item: item["timestamp"], reverse=True)
    return found


def find_rollback_image(timestamp: str) -> str | None:
    """Rollback-Image zu einem Zeitstempel suchen (unabhängig vom Repository)."""
    suffix = f":{ROLLBACK_TAG}-{timestamp}"
    client = get_client()
    for image in client.images.list():
        for tag in image.tags or []:
            ref = normalize_image_ref(tag)
            if ref.endswith(suffix):
                return ref
    return None


def remove_image(ref: str, log=lambda message: None) -> bool:
    try:
        get_client().images.remove(image=ref, force=False)
        log(i18n.t("docker.image_removed", ref=ref))
        return True
    except (APIError, NotFound) as exc:
        log(i18n.t("docker.image_remove_failed", ref=ref, error=exc))
        return False


def prune_dangling() -> None:
    try:
        get_client().images.prune(filters={"dangling": True})
    except DockerException:
        pass


# ---------------------------------------------------------------------------
# Container neu erstellen (ersetzt runlike / docker run)
# ---------------------------------------------------------------------------


def _network_connect_kwargs(network_config: dict) -> dict:
    kwargs: dict = {}
    aliases = [
        alias
        for alias in (network_config.get("Aliases") or [])
        if alias
    ]
    if aliases:
        kwargs["aliases"] = aliases
    ipv4 = (network_config.get("IPAMConfig") or {}).get("IPv4Address")
    if ipv4:
        kwargs["ipv4_address"] = ipv4
    ipv6 = (network_config.get("IPAMConfig") or {}).get("IPv6Address")
    if ipv6:
        kwargs["ipv6_address"] = ipv6
    return kwargs


def recreate_container(attrs: dict, image_ref: str, log=lambda message: None):
    """Container mit gespeicherter Konfiguration und neuem Image anlegen."""
    client = get_client()
    name = (attrs.get("Name") or "").lstrip("/")
    cfg = attrs.get("Config") or {}
    host_config = dict(attrs.get("HostConfig") or {})
    mounts = attrs.get("Mounts") or []

    # Bind-Mounts für neue Container explizit setzen: benannte Volumes
    # und Bind-Mounts aus dem Mount-Snapshot (HostConfig.Binds enthält
    # keine anonymen Volumes).
    binds: list[str] = []
    volume_decl: dict[str, dict] = {}
    for mount in mounts:
        destination = mount.get("Destination")
        if not destination:
            continue
        mode = "" if mount.get("RW", True) else ":ro"
        if mount.get("Type") == "volume" and mount.get("Name"):
            binds.append(f"{mount['Name']}:{destination}{mode}")
            volume_decl[destination] = {}
        elif mount.get("Type") == "bind" and mount.get("Source"):
            binds.append(f"{mount['Source']}:{destination}{mode}")
    if binds:
        host_config["Binds"] = binds

    remove_container(name, log)

    log(i18n.t("docker.recreate", name=name, image=image_ref))
    try:
        response = client.api.create_container(
            image=image_ref,
            name=name,
            command=cfg.get("Cmd"),
            hostname=cfg.get("Hostname") or None,
            user=cfg.get("User") or None,
            detach=True,
            stdin_open=bool(cfg.get("OpenStdin")),
            tty=bool(cfg.get("Tty")),
            ports=cfg.get("ExposedPorts") or None,
            environment=cfg.get("Env") or None,
            volumes=volume_decl or None,
            entrypoint=cfg.get("Entrypoint"),
            working_dir=cfg.get("WorkingDir") or None,
            domainname=cfg.get("Domainname") or None,
            host_config=host_config,
            mac_address=cfg.get("MacAddress") or None,
            labels=cfg.get("Labels") or None,
            stop_signal=cfg.get("StopSignal") or None,
            healthcheck=cfg.get("Healthcheck") or None,
            stop_timeout=cfg.get("StopTimeout"),
        )
    except DockerException as exc:
        raise OperationError(
            i18n.t("docker.recreate_failed", name=name, error=exc)
        ) from exc

    container = client.containers.get(response["Id"])

    primary_network = host_config.get("NetworkMode") or ""
    for network_name, network_config in (
        (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    ).items():
        if network_name in {primary_network, "bridge", "host", "none"}:
            continue
        try:
            network = client.networks.get(network_name)
            network.connect(
                container, **_network_connect_kwargs(network_config or {})
            )
            log(i18n.t("docker.network_connected", name=network_name))
        except DockerException as exc:
            log(i18n.t("docker.network_failed", name=network_name, error=exc))

    container.start()
    log(i18n.t("docker.started"))
    return container


# ---------------------------------------------------------------------------
# Rollback-Skript für die Kommandozeile
# ---------------------------------------------------------------------------


def _quote_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def build_run_command(attrs: dict, image_ref: str) -> str:
    """`docker run`-Befehl aus der Container-Konfiguration erzeugen."""
    cfg = attrs.get("Config") or {}
    host_config = attrs.get("HostConfig") or {}
    name = (attrs.get("Name") or "").lstrip("/")

    args: list[str] = ["docker", "run", "-d", "--name", name]

    restart = host_config.get("RestartPolicy") or {}
    restart_name = restart.get("Name") or ""
    if restart_name and restart_name != "no":
        retries = restart.get("MaximumRetryCount") or 0
        if restart_name == "on-failure" and retries:
            args += ["--restart", f"on-failure:{retries}"]
        else:
            args += ["--restart", restart_name]

    for container_port, bindings in (host_config.get("PortBindings") or {}).items():
        port, _, proto = container_port.partition("/")
        proto = proto or "tcp"
        suffix = f"/{proto}" if proto != "tcp" else ""
        if not bindings:
            args += ["-p", f"{port}{suffix}"]
            continue
        for binding in bindings:
            host_ip = binding.get("HostIp") or ""
            host_port = binding.get("HostPort") or ""
            if host_ip:
                args += ["-p", f"{host_ip}:{host_port}:{port}{suffix}"]
            else:
                args += ["-p", f"{host_port}:{port}{suffix}"]

    for bind in host_config.get("Binds") or []:
        args += ["-v", bind]
    for mount in attrs.get("Mounts") or []:
        if mount.get("Type") == "volume" and mount.get("Name"):
            mode = "" if mount.get("RW", True) else ":ro"
            spec = f"{mount['Name']}:{mount['Destination']}{mode}"
            if spec not in (host_config.get("Binds") or []):
                args += ["-v", spec]

    network_mode = host_config.get("NetworkMode") or ""
    if network_mode and network_mode not in {"default", "bridge"}:
        args += ["--network", network_mode]

    for entry in cfg.get("Env") or []:
        args += ["-e", entry]

    for key, value in (cfg.get("Labels") or {}).items():
        args += ["--label", f"{key}={value}"]

    if cfg.get("Hostname"):
        args += ["--hostname", cfg["Hostname"]]
    if cfg.get("User"):
        args += ["--user", cfg["User"]]
    if cfg.get("WorkingDir"):
        args += ["--workdir", cfg["WorkingDir"]]
    if cfg.get("StopSignal"):
        args += ["--stop-signal", cfg["StopSignal"]]
    if host_config.get("Privileged"):
        args += ["--privileged"]
    for cap in host_config.get("CapAdd") or []:
        args += ["--cap-add", cap]
    for cap in host_config.get("CapDrop") or []:
        args += ["--cap-drop", cap]
    for device in host_config.get("Devices") or []:
        args += [
            "--device",
            f"{device.get('PathOnHost')}:{device.get('PathInContainer')}:"
            f"{device.get('CgroupPermissions')}",
        ]
    for option in host_config.get("SecurityOpt") or []:
        args += ["--security-opt", option]
    for key, value in (host_config.get("Sysctls") or {}).items():
        args += ["--sysctl", f"{key}={value}"]
    for key, value in (host_config.get("Tmpfs") or {}).items():
        args += ["--tmpfs", f"{key}:{value}" if value else key]
    for host_entry in host_config.get("ExtraHosts") or []:
        args += ["--add-host", host_entry]
    for dns in host_config.get("Dns") or []:
        args += ["--dns", dns]
    if host_config.get("IpcMode"):
        args += ["--ipc", host_config["IpcMode"]]
    if host_config.get("PidMode"):
        args += ["--pid", host_config["PidMode"]]
    if host_config.get("ReadonlyRootfs"):
        args += ["--read-only"]
    if host_config.get("Init"):
        args += ["--init"]

    entrypoint = cfg.get("Entrypoint")
    if entrypoint:
        if isinstance(entrypoint, str):
            entrypoint = [entrypoint]
        if len(entrypoint) == 1:
            args += ["--entrypoint", entrypoint[0]]

    args.append(image_ref)

    cmd = cfg.get("Cmd")
    if cmd:
        if isinstance(cmd, str):
            args.append(cmd)
        else:
            args += list(cmd)

    return _quote_join(args)
