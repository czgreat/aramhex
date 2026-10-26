"""Official-data candidate and explicitly approved static publication tools.

Nothing runs on import. Collection can only create an isolated candidate; it has
no publication-root argument. Publishing requires the candidate's exact SHA-256.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lolhex.data.official import check_manifest, check_payload  # noqa: E402

STORE_MARKER = ".official-publication.json"
MAX_PACKED = 16 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _read(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_constant)
    if not isinstance(obj, dict):
        raise ValueError(f"JSON object required: {path.name}")
    return obj


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    """Replace only after the complete file exists on the same filesystem."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as f:
        temp = Path(f.name)
        f.write(data)
        f.flush()
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def initialize_publication(root: Path) -> Path:
    """Explicitly initialize a dedicated empty directory, never adopt other data."""
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Publication directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    (root / STORE_MARKER).write_bytes(_json_bytes({"schema_version": 1, "kind": "official-publication"}))
    return root


def _store(root: Path) -> Path:
    root = Path(root).resolve()
    if _read(root / STORE_MARKER) != {"schema_version": 1, "kind": "official-publication"}:
        raise ValueError("Not an initialized official publication directory")
    return root


def create_candidate(static_path: Path, heroes_path: Path, destination: Path) -> dict:
    """Validate supplied source files and create a new, deterministic candidate."""
    payload = {"static": _read(Path(static_path)), "heroes": _read(Path(heroes_path))}
    check_payload(payload)
    raw = _json_bytes(payload)
    packed = gzip.compress(raw, mtime=0)
    if len(raw) > MAX_UNPACKED or len(packed) > MAX_PACKED:
        raise ValueError("Candidate exceeds official-data size limits")
    # No reuse or in-place replacement of an existing candidate directory.
    destination = Path(destination).resolve()
    if any((p / STORE_MARKER).exists() for p in destination.parents):
        raise ValueError("Candidates cannot be created inside a publication directory")
    if destination.exists():
        raise ValueError("Candidate directory already exists; use a fresh directory")
    destination.mkdir(parents=True)
    meta = {
        "schema_version": 1, "kind": "official-candidate", "created_at": _now(),
        "patch": payload["static"]["patch"], "source_date": payload["heroes"]["generated"],
        "min_client": "1.1.2", "sha256": _sha(packed), "size": len(packed),
        "unpacked_size": len(raw),
        "counts": {key: len(payload["static"][key]) for key in ("heroes", "augments", "items", "spells")},
    }
    (destination / "official.json.gz").write_bytes(packed)
    (destination / "candidate.json").write_bytes(_json_bytes(meta))
    return meta


def read_candidate(directory: Path) -> tuple[dict, dict, bytes]:
    directory = Path(directory).resolve()
    meta = _read(directory / "candidate.json")
    if meta.get("schema_version") != 1 or meta.get("kind") != "official-candidate":
        raise ValueError("Not an official-data candidate")
    package = directory / "official.json.gz"
    if package.stat().st_size > MAX_PACKED:
        raise ValueError("Candidate package too large")
    packed = package.read_bytes()
    if meta.get("sha256") != _sha(packed) or meta.get("size") != len(packed):
        raise ValueError("Candidate package hash/size mismatch")
    # Bounded decompression: a malicious candidate must not allocate without limit.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as f:
        raw = f.read(MAX_UNPACKED + 1)
    if len(raw) > MAX_UNPACKED or meta.get("unpacked_size") != len(raw):
        raise ValueError("Candidate unpacked size mismatch")
    payload = json.loads(raw, parse_constant=_invalid_constant)
    check_payload(payload)
    if meta.get("patch") != payload["static"]["patch"] or meta.get("source_date") != payload["heroes"]["generated"]:
        raise ValueError("Candidate metadata differs from its payload")
    counts = {key: len(payload["static"][key]) for key in ("heroes", "augments", "items", "spells")}
    if meta.get("counts") != counts or meta.get("min_client") != "1.1.2":
        raise ValueError("Candidate description differs from its reviewed protocol")
    return meta, payload, packed


def _current(root: Path) -> dict | None:
    path = root / "latest.json"
    if not path.exists():
        return None
    return check_manifest(_read(path))


def _published_payload(root: Path, manifest: dict) -> dict:
    path = root / manifest["package"]["path"]
    if not path.resolve().is_relative_to(root):
        raise ValueError("Current package path escapes publication directory")
    if path.stat().st_size != manifest["package"]["size"]:
        raise ValueError("Current published package size mismatch")
    if _sha(path.read_bytes()) != manifest["package"]["sha256"]:
        raise ValueError("Current published package hash mismatch")
    with gzip.open(path, "rb") as f:
        raw = f.read(manifest["package"]["unpacked_size"] + 1)
    if len(raw) != manifest["package"]["unpacked_size"]:
        raise ValueError("Current published unpacked size mismatch")
    return check_payload(json.loads(raw, parse_constant=_invalid_constant), manifest)


def _patch(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)+", value):
        raise ValueError("Numeric game patch required")
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * max(0, 4 - len(parts))


def candidate_report(directory: Path, publication: Path | None = None) -> dict:
    meta, payload, _ = read_candidate(directory)
    report = {"candidate_sha256": meta["sha256"], "patch": meta["patch"],
              "source_date": meta["source_date"], "counts": meta["counts"], "published": False}
    if publication is None:
        return report
    root = _store(publication)
    current = _current(root)
    report["current_revision"] = current["revision"] if current else None
    report["unchanged"] = bool(current and current["package"]["sha256"] == meta["sha256"])
    if current:
        old = _published_payload(root, current)
        report["changed"] = {}
        for key in ("heroes", "augments", "items", "spells"):
            before, after = old["static"][key], payload["static"][key]
            report["changed"][key] = {
                "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
                "modified": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
            }
        before, after = old["heroes"]["heroes"], payload["heroes"]["heroes"]
        report["changed"]["hero_details"] = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return report


@contextmanager
def _publication_lock(root: Path):
    path = root / ".publish.lock"
    # Concurrent publishing is an error, never two competing latest.json writes.
    with path.open("x", encoding="utf-8") as f:
        f.write(_now())
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def publish_candidate(directory: Path, publication: Path, *, approved_sha256: str,
                      approved_by: str) -> dict:
    """Publish exactly the reviewed hash; collection never calls this function."""
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 100:
        raise ValueError("Explicit approver name required")
    if not isinstance(approved_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", approved_sha256):
        raise ValueError("Explicit lowercase candidate SHA-256 approval required")
    meta, payload, packed = read_candidate(directory)
    if approved_sha256 != meta["sha256"]:
        raise ValueError("Approval does not match this candidate")
    root = _store(publication)
    candidate = Path(directory).resolve()
    if root == candidate or root in candidate.parents or candidate in root.parents:
        raise ValueError("Candidate and publication directories must be separate")
    with _publication_lock(root):
        current = _current(root)
        if current and current["package"]["sha256"] == meta["sha256"]:
            return {"published": False, "reason": "unchanged", "revision": current["revision"]}
        if current and (_patch(meta["patch"]) < _patch(current["patch"])
                        or meta["source_date"] < current["source_date"]):
            raise ValueError("Older patch/source date requires a separately reviewed rollback workflow")
        if current:
            old = _published_payload(root, current)
            if not set(old["static"]["heroes"]).issubset(payload["static"]["heroes"]):
                raise ValueError("Candidate omits heroes from the current publication")
        revision = current["revision"] + 1 if current else 1
        target = root / "revisions" / str(revision)
        while True:
            if not target.resolve().is_relative_to(root):
                raise ValueError("Publication revision path escapes its dedicated root")
            if not target.exists():
                break
            # A prior interrupted publish may leave immutable files behind.
            # Skip that unused revision rather than overwrite it or block retry.
            revision += 1
            target = root / "revisions" / str(revision)
        manifest = {
            "schema_version": 1, "revision": revision, "patch": meta["patch"],
            "source_date": meta["source_date"], "generated_at": _now(), "min_client": meta["min_client"],
            "package": {"path": f"revisions/{revision}/official.json.gz", "size": len(packed),
                        "sha256": meta["sha256"], "unpacked_size": meta["unpacked_size"]},
        }
        check_manifest(manifest)
        check_payload(payload, manifest)
        target.mkdir(parents=True, exist_ok=False)
        (target / "official.json.gz").write_bytes(packed)
        (target / "manifest.json").write_bytes(_json_bytes(manifest))
        (target / "approval.json").write_bytes(_json_bytes({
            "approved_by": approved_by.strip(), "approved_sha256": approved_sha256, "approved_at": _now(),
        }))
        if _sha((target / "official.json.gz").read_bytes()) != approved_sha256:
            raise OSError("Published package verification failed")
        # Only this final replacement makes the candidate visible to clients.
        _atomic(root / "latest.json", _json_bytes(manifest))
        return {"published": True, "revision": revision, "manifest": manifest}


def collect_candidate(destination: Path) -> dict:
    """Explicit manual Tencent fetch; never writes a publication directory."""
    from lolhex.data import tencent
    from lolhex.data.store import DataStore

    versions = tencent.fetch_versions()
    patch = versions[0]["name"]
    hero_list = tencent.parse_hero_list(tencent.fetch_hero_list())
    # Tencent can return a valid JSON response containing only part of the list.
    # Compare against the complete reviewed build snapshot before fetching details.
    baseline = _read(ROOT / "lolhex" / "data" / "static_bundled.json")["heroes"]
    if not set(baseline).issubset(hero_list):
        raise ValueError("Tencent hero list is incomplete compared with the bundled baseline")
    static = {
        "patch": patch, "versions": versions[:6],
        "heroes": hero_list,
        "augments": tencent.parse_kiwi_augments(tencent.fetch_kiwi_augments()),
        "items": tencent.parse_items(tencent.fetch_items()),
        "spells": tencent.parse_summoner_spells(tencent.fetch_summoner_spells()),
    }
    DataStore._merge_cdragon(static["augments"])
    heroes = {}
    for hid in sorted(static["heroes"], key=int):
        # No offline/bundled fallback: an incomplete collection must fail.
        hero = tencent.parse_hero_hex(tencent.fetch_hero_hex(hid))
        raw = tencent.fetch_hero_file(hid)
        hero.update(spells=tencent.parse_hero_spells(raw), splash=tencent.parse_hero_splash(raw), patch=patch)
        heroes[hid] = hero
    dates = {str(h.get("date") or "") for h in heroes.values()}
    if len(dates) != 1:
        raise ValueError("Tencent hero source dates disagree; review before publication")
    source_date = datetime.strptime(dates.pop(), "%Y%m%d").date().isoformat()
    static["generated"] = source_date
    with tempfile.TemporaryDirectory(prefix="official-collection-") as temp:
        folder = Path(temp)
        a, b = folder / "static.json", folder / "heroes.json"
        a.write_bytes(_json_bytes(static))
        b.write_bytes(_json_bytes({"patch": patch, "generated": source_date, "heroes": heroes}))
        return create_candidate(a, b, destination)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-preview", help="Initialize a dedicated empty local publication directory")
    init.add_argument("directory", type=Path)
    candidate = commands.add_parser("candidate", help="Create an offline candidate from supplied JSON")
    candidate.add_argument("--static", type=Path, required=True)
    candidate.add_argument("--heroes", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    collect = commands.add_parser("collect", help="Manually collect from Tencent into a candidate only")
    collect.add_argument("--fetch-from-tencent", action="store_true", required=True)
    collect.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report", help="Inspect a candidate without publishing")
    report.add_argument("candidate", type=Path)
    report.add_argument("--publication", type=Path)
    publish = commands.add_parser("publish", help="Publish the exact explicitly approved candidate")
    publish.add_argument("candidate", type=Path)
    publish.add_argument("--publication", type=Path, required=True)
    publish.add_argument("--approve-sha256", required=True)
    publish.add_argument("--approved-by", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init-preview":
            result = {"initialized": str(initialize_publication(args.directory))}
        elif args.command == "candidate":
            result = create_candidate(args.static, args.heroes, args.output)
        elif args.command == "collect":
            result = collect_candidate(args.output)
        elif args.command == "report":
            result = candidate_report(args.candidate, args.publication)
        else:
            result = publish_candidate(args.candidate, args.publication,
                                       approved_sha256=args.approve_sha256, approved_by=args.approved_by)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
