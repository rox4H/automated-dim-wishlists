#!/usr/bin/env python3
"""Read the canonical Destiny 2 catalogue from Supabase or a Bungie manifest snapshot.

Sources, chosen by ``open_catalogue`` (override with ``CATALOGUE_SOURCE``):

* local ``data/`` JSON fixtures (tests only);
* ``bungie-manifest``: ``catalogue/curated-overlay.json`` plus the committed
  ``catalogue/manifest-snapshot.json``, built offline by
  ``scripts/build_manifest_catalogue.mjs``;
* ``supabase``: the public read-only REST tables.

With ``CATALOGUE_SOURCE`` unset (``auto``) the manifest source is used only when
both catalogue files exist, so behaviour is unchanged until they are committed.

Only the public publishable key is used by read-only workflows. Never put a
Supabase secret/service-role key in GitHub Actions or in this module.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_SUPABASE_URL = "https://zpxeusonadqchhsspkxe.supabase.co"
# A publishable key is designed to be public. RLS and SELECT-only grants protect
# the three catalog tables; this is not a secret or service-role key.
DEFAULT_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_RBECWzuCIWIZXM1za1DULA_KmPmtsO1"
PAGE_SIZE = 1000
DEFAULT_TIMEOUT_SECONDS = 25

_TABLES = {
    "weapons": ("d2wd_weapons", "id", "weapons"),
    "perks": ("d2wd_perks", "id", "perks"),
    "pools": ("d2wd_weapon_perk_pools", "weapon_id", "weapon-perk-pools"),
}


class CatalogueError(RuntimeError):
    """Raised when the canonical catalogue cannot be loaded safely."""


def _key_field(record_type: str) -> str:
    if record_type not in _TABLES:
        raise CatalogueError("Unsupported catalogue record type: " + str(record_type))
    return _TABLES[record_type][1]


def _record_key(record_type: str, record: dict[str, Any]) -> str | None:
    return record.get(_key_field(record_type))


class JsonCatalogue:
    """Local JSON adapter retained for isolated unit-test fixtures only."""

    is_local = True

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _directory(self, record_type: str) -> Path:
        try:
            _, _, directory = _TABLES[record_type]
        except KeyError as exc:
            raise CatalogueError("Unsupported catalogue record type: " + str(record_type)) from exc
        return self.root / "data" / directory

    def list_records(self, record_type: str) -> list[dict[str, Any]]:
        directory = self._directory(record_type)
        if not directory.is_dir():
            raise CatalogueError("Fixture catalogue directory is missing: " + str(directory))
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CatalogueError(f"{path}: could not read a valid JSON record: {exc}") from exc
            if not isinstance(record, dict):
                raise CatalogueError(f"{path}: expected a JSON object")
            record_id = _record_key(record_type, record)
            if not isinstance(record_id, str) or not record_id:
                raise CatalogueError(f"{path}: missing canonical {_key_field(record_type)}")
            if path.stem != record_id:
                raise CatalogueError(f"{path}: filename does not match canonical ID {record_id!r}")
            if record_id in seen:
                raise CatalogueError(f"{path}: duplicate canonical ID {record_id!r}")
            seen.add(record_id)
            result.append(record)
        return result

    def get_record(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        path = self._directory(record_type) / (record_id + ".json")
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogueError(f"{path}: could not read a valid JSON record: {exc}") from exc
        if not isinstance(record, dict) or _record_key(record_type, record) != record_id:
            raise CatalogueError(f"{path}: record ID does not match its filename")
        return record

    def counts(self) -> dict[str, int]:
        return {kind: len(self.list_records(kind)) for kind in _TABLES}


class SupabaseCatalogue:
    """Read-only Supabase REST adapter for public catalogue tables."""

    is_local = False

    def __init__(
        self,
        url: str | None = None,
        publishable_key: str | None = None,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        page_size: int = PAGE_SIZE,
    ):
        self.url = (url or os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL).rstrip("/")
        self.publishable_key = (
            publishable_key
            or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
            or DEFAULT_SUPABASE_PUBLISHABLE_KEY
        ).strip()
        self.timeout = timeout
        self.page_size = page_size
        self._record_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._list_cache: dict[str, list[dict[str, Any]]] = {}
        if not self.url.startswith("https://"):
            raise CatalogueError("SUPABASE_URL must be an HTTPS URL.")
        if not self.publishable_key:
            raise CatalogueError(
                "SUPABASE_PUBLISHABLE_KEY is required to read the canonical catalogue."
            )

    def _request_rows(
        self,
        record_type: str,
        *,
        filters: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            table, key_field, _ = _TABLES[record_type]
        except KeyError as exc:
            raise CatalogueError("Unsupported catalogue record type: " + str(record_type)) from exc

        query: dict[str, str] = {"select": key_field + ",data"}
        if filters:
            query.update(filters)
        query["order"] = key_field + ".asc"
        page_ranges = [None] if limit is not None else range(0, 10**12, self.page_size)
        if limit is not None:
            query["limit"] = str(limit)

        rows: list[dict[str, Any]] = []
        for offset in page_ranges:
            url = self.url + "/rest/v1/" + table + "?" + urlencode(query)
            headers = {
                "apikey": self.publishable_key,
                "Accept": "application/json",
            }
            if offset is not None:
                headers["Range"] = f"{offset}-{offset + self.page_size - 1}"
                headers["Range-Unit"] = "items"
            # Legacy JWT anon keys also work as bearer tokens; new sb_publishable_
            # keys are only API keys and must not be sent as Authorization tokens.
            if self.publishable_key.startswith("eyJ"):
                headers["Authorization"] = "Bearer " + self.publishable_key
            request = Request(url, headers=headers, method="GET")
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8")
            except HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")[:800]
                raise CatalogueError(
                    f"Supabase REST returned HTTP {exc.code} for {table}: {details}"
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise CatalogueError(
                    f"Could not reach Supabase table {table}: {exc}"
                ) from exc
            try:
                page = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise CatalogueError(
                    f"Supabase table {table} returned invalid JSON."
                ) from exc
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise CatalogueError(f"Supabase table {table} did not return a JSON array of rows.")
            rows.extend(page)
            if offset is None or len(page) < self.page_size:
                break
        return rows

    def _decode_row(self, record_type: str, row: dict[str, Any]) -> dict[str, Any]:
        table, key_field, _ = _TABLES[record_type]
        row_id = row.get(key_field)
        record = row.get("data")
        if not isinstance(row_id, str) or not row_id:
            raise CatalogueError(f"Supabase table {table} returned a row without {key_field}.")
        if not isinstance(record, dict):
            raise CatalogueError(f"Supabase table {table} row {row_id!r} has no JSON object in data.")
        if _record_key(record_type, record) != row_id:
            raise CatalogueError(
                f"Supabase table {table} row {row_id!r} does not match its canonical JSON ID."
            )
        return record

    def list_records(self, record_type: str) -> list[dict[str, Any]]:
        if record_type in self._list_cache:
            return [dict(record) for record in self._list_cache[record_type]]
        rows = self._request_rows(record_type)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            record = self._decode_row(record_type, row)
            record_id = _record_key(record_type, record)
            if record_id in seen:
                raise CatalogueError(
                    f"Supabase table {_TABLES[record_type][0]} returned duplicate ID {record_id!r}."
                )
            seen.add(record_id)
            result.append(record)
            self._record_cache[(record_type, record_id)] = record
        self._list_cache[record_type] = result
        return [dict(record) for record in result]

    def get_record(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        cached = self._record_cache.get((record_type, record_id))
        if cached is not None:
            return dict(cached)
        try:
            _, key_field, _ = _TABLES[record_type]
        except KeyError as exc:
            raise CatalogueError("Unsupported catalogue record type: " + str(record_type)) from exc
        rows = self._request_rows(
            record_type,
            filters={key_field: "eq." + record_id},
            limit=2,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise CatalogueError(
                f"Supabase table {_TABLES[record_type][0]} returned multiple rows for ID {record_id!r}."
            )
        record = self._decode_row(record_type, rows[0])
        self._record_cache[(record_type, record_id)] = record
        return dict(record)

    def counts(self) -> dict[str, int]:
        return {kind: len(self.list_records(kind)) for kind in _TABLES}


OVERLAY_RELATIVE = Path("catalogue") / "curated-overlay.json"
SNAPSHOT_RELATIVE = Path("catalogue") / "manifest-snapshot.json"
MANIFEST_BUILDER = Path(__file__).resolve().parent / "build_manifest_catalogue.mjs"
MANIFEST_BUILD_TIMEOUT_SECONDS = 300
CATALOGUE_SOURCES = ("auto", "supabase", "bungie-manifest")


class BungieManifestCatalogue:
    """Catalogue built offline from a committed Bungie manifest snapshot.

    Curated weapon and perk records, and human-verified pools, come from
    ``catalogue/curated-overlay.json``. Every other pool is generated from
    ``catalogue/manifest-snapshot.json`` by the same extraction code that
    ``generate_weapon_perk_pools.mjs`` uses. The records are materialised once,
    lazily, into a temporary directory and served through ``JsonCatalogue``.
    Nothing touches the network.
    """

    is_local = False

    def __init__(self, root: Path, *, node: str = "node"):
        root = Path(root).resolve()
        self.overlay_path = root / OVERLAY_RELATIVE
        self.snapshot_path = root / SNAPSHOT_RELATIVE
        self.node = node
        missing = [str(path.relative_to(root)) for path in (self.overlay_path, self.snapshot_path) if not path.is_file()]
        if missing:
            raise CatalogueError(
                "The Bungie manifest catalogue needs " + ", ".join(missing) + ". Create the overlay with "
                "scripts/export_catalogue_overlay.py and the snapshot with "
                "'node --max-old-space-size=8192 scripts/build_manifest_catalogue.mjs fetch' "
                "(see catalogue/README.md)."
            )
        self._built: JsonCatalogue | None = None
        self.report: dict[str, Any] = {}

    def _catalogue(self) -> JsonCatalogue:
        if self._built is not None:
            return self._built
        directory = Path(tempfile.mkdtemp(prefix="d2wd-manifest-catalogue-"))
        atexit.register(shutil.rmtree, directory, ignore_errors=True)
        command = [
            self.node, str(MANIFEST_BUILDER), "build",
            "--overlay", str(self.overlay_path),
            "--snapshot", str(self.snapshot_path),
            "--out", str(directory),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=MANIFEST_BUILD_TIMEOUT_SECONDS, check=False
            )
        except FileNotFoundError as exc:
            raise CatalogueError("Node.js is required to build the Bungie manifest catalogue.") from exc
        except subprocess.TimeoutExpired as exc:
            raise CatalogueError("Building the Bungie manifest catalogue timed out.") from exc
        if completed.returncode != 0:
            raise CatalogueError(
                "Building the Bungie manifest catalogue failed: " + (completed.stderr or completed.stdout).strip()[:800]
            )
        report_path = directory / "manifest-catalogue-report.json"
        try:
            self.report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.report = {}
        self._built = JsonCatalogue(directory)
        return self._built

    def list_records(self, record_type: str) -> list[dict[str, Any]]:
        return self._catalogue().list_records(record_type)

    def get_record(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        return self._catalogue().get_record(record_type, record_id)

    def counts(self) -> dict[str, int]:
        return self._catalogue().counts()


def open_catalogue(root: Path) -> JsonCatalogue | SupabaseCatalogue | BungieManifestCatalogue:
    """Pick the catalogue source for ``root``.

    Local ``data/`` JSON fixtures win when all three directories exist. Otherwise
    ``CATALOGUE_SOURCE`` decides: ``supabase``, ``bungie-manifest``, or ``auto``
    (the default), which uses the Bungie manifest catalogue only when both of its
    committed files exist and Supabase otherwise.
    """
    root = Path(root).resolve()
    directories = [
        root / "data" / _TABLES[kind][2]
        for kind in ("weapons", "perks", "pools")
    ]
    if all(directory.is_dir() for directory in directories):
        return JsonCatalogue(root)
    source = (os.environ.get("CATALOGUE_SOURCE") or "auto").strip().lower()
    if source not in CATALOGUE_SOURCES:
        raise CatalogueError(
            "CATALOGUE_SOURCE must be one of " + ", ".join(CATALOGUE_SOURCES) + "; got " + repr(source) + "."
        )
    if source == "supabase":
        return SupabaseCatalogue()
    has_manifest_files = (root / OVERLAY_RELATIVE).is_file() and (root / SNAPSHOT_RELATIVE).is_file()
    if source == "bungie-manifest" or has_manifest_files:
        return BungieManifestCatalogue(root)
    return SupabaseCatalogue()
