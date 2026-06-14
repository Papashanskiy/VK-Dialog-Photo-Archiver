#!/usr/bin/env python3
"""
Copy JPEG photos from nested folders, derive the date from the filename, and
write that date into EXIF metadata on the copied file.

Edit the global variables below before running.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path


# --- Global settings -------------------------------------------------------

# Folder that contains subfolders with photos.
SOURCE_ROOT = Path("/mnt/d/Images/Vk")

# Folder where processed copies will be placed.
DEST_ROOT = Path("/mnt/d/Images/Vk_processed")

# Keep this True for the first run. It prints planned work without copying or
# changing files. Set to False after checking the output.
DRY_RUN = False

# All photos are copied into DEST_ROOT by default. Set to True if you want to
# preserve the source folder structure under DEST_ROOT.
KEEP_SOURCE_SUBFOLDERS = False

# Exact duplicates are skipped by SHA-256 hash.
DEDUPLICATE_BY_HASH = True

# Also hash existing photos in DEST_ROOT. This prevents duplicates on reruns.
INDEX_EXISTING_DESTINATION = True

# Time is not present in the filenames, so this value is used for EXIF time.
DEFAULT_TIME = "00:00:00"

PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
MANIFEST_FILENAME = ".add_photo_date_manifest.json"
VERBOSE = True


DATE_RE = re.compile(r"(?<!\d)(\d{4})\.(\d{2})\.(\d{2})(?!\d)")
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class Stats:
    found: int = 0
    planned_or_copied: int = 0
    skipped_no_date: int = 0
    skipped_duplicate: int = 0
    skipped_errors: int = 0
    exif_errors: int = 0
    indexed_destination_files: int = 0
    indexed_manifest_entries: int = 0


def main() -> int:
    source_root = SOURCE_ROOT.expanduser()
    dest_root = DEST_ROOT.expanduser()
    default_time = parse_default_time(DEFAULT_TIME)

    if not source_root.exists():
        print(f"Source folder does not exist: {source_root}", file=sys.stderr)
        return 1
    if not source_root.is_dir():
        print(f"Source path is not a folder: {source_root}", file=sys.stderr)
        return 1

    exiftool_path = shutil.which("exiftool")
    if not DRY_RUN and not exiftool_path:
        print(
            "exiftool was not found. Install it with: "
            "sudo apt install libimage-exiftool-perl",
            file=sys.stderr,
        )
        return 1

    print_run_header(source_root, dest_root, exiftool_path)

    stats = Stats()
    known_hashes: dict[str, Path] = {}
    reserved_targets: set[Path] = set()
    manifest_entries: dict[str, str] = {}

    if DEDUPLICATE_BY_HASH and dest_root.exists():
        manifest_entries = load_manifest(dest_root, known_hashes, stats)

        if INDEX_EXISTING_DESTINATION:
            index_destination_hashes(dest_root, known_hashes, stats)

    if not DRY_RUN:
        dest_root.mkdir(parents=True, exist_ok=True)

    for source_path in iter_photo_files(source_root, dest_root):
        stats.found += 1
        photo_date = date_from_filename(source_path.name)

        if photo_date is None:
            stats.skipped_no_date += 1
            log(f"SKIP no date: {source_path}")
            continue

        exif_datetime = build_exif_datetime(photo_date, default_time)

        try:
            source_hash = file_sha256(
                source_path) if DEDUPLICATE_BY_HASH else None
        except OSError as exc:
            stats.skipped_errors += 1
            log(f"SKIP hash error: {source_path} ({exc})")
            continue

        if source_hash and source_hash in known_hashes:
            stats.skipped_duplicate += 1
            log(
                f"SKIP duplicate: {source_path} == {known_hashes[source_hash]}")
            continue

        try:
            target_path = choose_target_path(
                source_path=source_path,
                source_root=source_root,
                dest_root=dest_root,
                source_hash=source_hash,
                reserved_targets=reserved_targets,
            )
        except OSError as exc:
            stats.skipped_errors += 1
            log(f"SKIP target error: {source_path} ({exc})")
            continue

        if target_path is None:
            stats.skipped_duplicate += 1
            log(f"SKIP duplicate already in destination: {source_path}")
            continue

        if DRY_RUN:
            stats.planned_or_copied += 1
            log(f"DRY-RUN copy: {source_path} -> {target_path}")
            log(f"DRY-RUN EXIF AllDates={exif_datetime}")
            if source_hash:
                known_hashes[source_hash] = target_path
            reserved_targets.add(target_path.resolve(strict=False))
            continue

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        except OSError as exc:
            stats.skipped_errors += 1
            log(f"SKIP copy error: {source_path} -> {target_path} ({exc})")
            continue

        exif_ok = write_exif_dates(target_path, exif_datetime, exiftool_path)
        if exif_ok:
            stats.planned_or_copied += 1
            log(f"COPIED: {source_path} -> {target_path}")
            if source_hash:
                manifest_entries[source_hash] = manifest_relative_path(
                    dest_root, target_path
                )
        else:
            stats.exif_errors += 1
            log(f"EXIF ERROR: {target_path}")

        if source_hash:
            known_hashes[source_hash] = target_path
        reserved_targets.add(target_path.resolve(strict=False))

    if not DRY_RUN and DEDUPLICATE_BY_HASH:
        save_manifest(dest_root, manifest_entries)

    print_summary(stats)
    return 0 if stats.skipped_errors == 0 and stats.exif_errors == 0 else 2


def parse_default_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(
            f"DEFAULT_TIME must be HH:MM:SS, got: {value}") from exc


def print_run_header(source_root: Path, dest_root: Path, exiftool_path: str | None) -> None:
    print("Photo EXIF date copier")
    print(f"Source: {source_root}")
    print(f"Destination: {dest_root}")
    print(f"Dry run: {DRY_RUN}")
    print(f"Keep source subfolders: {KEEP_SOURCE_SUBFOLDERS}")
    print(f"Deduplicate by hash: {DEDUPLICATE_BY_HASH}")
    print(f"Index existing destination: {INDEX_EXISTING_DESTINATION}")
    print(f"exiftool: {exiftool_path or 'not checked in dry-run'}")
    print()


def iter_photo_files(source_root: Path, dest_root: Path):
    dest_resolved = dest_root.resolve(strict=False)

    for root, dirs, files in os.walk(source_root):
        root_path = Path(root)

        # Avoid processing destination files when DEST_ROOT is inside SOURCE_ROOT.
        dirs[:] = [
            dirname
            for dirname in dirs
            if not is_relative_to((root_path / dirname).resolve(strict=False), dest_resolved)
        ]

        if is_relative_to(root_path.resolve(strict=False), dest_resolved):
            continue

        for filename in sorted(files):
            path = root_path / filename
            if path.suffix.lower() in PHOTO_EXTENSIONS:
                yield path


def is_relative_to(path: Path, possible_parent: Path) -> bool:
    try:
        path.relative_to(possible_parent)
        return True
    except ValueError:
        return False


def date_from_filename(filename: str) -> date | None:
    match = DATE_RE.search(filename)
    if not match:
        return None

    year, month, day = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_exif_datetime(photo_date: date, photo_time: time) -> str:
    value = datetime.combine(photo_date, photo_time)
    return value.strftime("%Y:%m:%d %H:%M:%S")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_destination_hashes(
    dest_root: Path, known_hashes: dict[str, Path], stats: Stats
) -> None:
    for path in iter_destination_photo_files(dest_root):
        try:
            digest = file_sha256(path)
        except OSError as exc:
            log(f"Could not hash destination file: {path} ({exc})")
            continue
        known_hashes.setdefault(digest, path)
        stats.indexed_destination_files += 1


def load_manifest(
    dest_root: Path, known_hashes: dict[str, Path], stats: Stats
) -> dict[str, str]:
    manifest_path = dest_root / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read manifest: {manifest_path} ({exc})")
        return {}

    raw_entries = data.get("source_hashes", {})
    if not isinstance(raw_entries, dict):
        log(f"Manifest has an unexpected format: {manifest_path}")
        return {}

    entries: dict[str, str] = {}
    for digest, relative_path in raw_entries.items():
        if not isinstance(digest, str) or not isinstance(relative_path, str):
            continue
        target_path = dest_root / relative_path
        if not target_path.exists():
            continue
        entries[digest] = relative_path
        known_hashes.setdefault(digest, target_path)
        stats.indexed_manifest_entries += 1

    return entries


def save_manifest(dest_root: Path, manifest_entries: dict[str, str]) -> None:
    manifest_path = dest_root / MANIFEST_FILENAME
    data = {
        "version": 1,
        "source_hashes": dict(sorted(manifest_entries.items())),
    }
    manifest_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def manifest_relative_path(dest_root: Path, target_path: Path) -> str:
    try:
        return target_path.relative_to(dest_root).as_posix()
    except ValueError:
        return str(target_path)


def iter_destination_photo_files(dest_root: Path):
    for root, _, files in os.walk(dest_root):
        root_path = Path(root)
        for filename in sorted(files):
            path = root_path / filename
            if path.suffix.lower() in PHOTO_EXTENSIONS:
                yield path


def choose_target_path(
    source_path: Path,
    source_root: Path,
    dest_root: Path,
    source_hash: str | None,
    reserved_targets: set[Path],
) -> Path | None:
    if KEEP_SOURCE_SUBFOLDERS:
        relative_parent = source_path.parent.relative_to(source_root)
        target_dir = dest_root / relative_parent
    else:
        target_dir = dest_root

    target_path = target_dir / source_path.name
    if not target_taken(target_path, reserved_targets):
        return target_path

    if target_path.exists() and source_hash and same_hash(target_path, source_hash):
        return None

    stem = source_path.stem
    suffix = source_path.suffix
    counter = 2

    while True:
        candidate = target_dir / f"{stem}__{counter}{suffix}"
        if not target_taken(candidate, reserved_targets):
            return candidate
        if candidate.exists() and source_hash and same_hash(candidate, source_hash):
            return None
        counter += 1


def target_taken(path: Path, reserved_targets: set[Path]) -> bool:
    return path.exists() or path.resolve(strict=False) in reserved_targets


def same_hash(path: Path, expected_hash: str) -> bool:
    try:
        return file_sha256(path) == expected_hash
    except OSError:
        return False


def write_exif_dates(path: Path, exif_datetime: str, exiftool_path: str | None) -> bool:
    if not exiftool_path:
        return False

    command = [
        exiftool_path,
        "-overwrite_original",
        "-P",
        f"-AllDates={exif_datetime}",
        str(path),
    ]
    result = subprocess.run(command, check=False,
                            capture_output=True, text=True)

    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        if result.stdout.strip():
            print(result.stdout.strip(), file=sys.stderr)
        return False

    return True


def log(message: str) -> None:
    if VERBOSE:
        print(message)


def print_summary(stats: Stats) -> None:
    label = "Planned" if DRY_RUN else "Copied"
    print()
    print("Summary")
    print(f"Found photos: {stats.found}")
    print(f"{label}: {stats.planned_or_copied}")
    print(f"Skipped duplicates: {stats.skipped_duplicate}")
    print(f"Skipped without valid date: {stats.skipped_no_date}")
    print(f"Skipped due to errors: {stats.skipped_errors}")
    print(f"EXIF write errors: {stats.exif_errors}")
    print(f"Indexed destination files: {stats.indexed_destination_files}")
    print(f"Indexed manifest entries: {stats.indexed_manifest_entries}")


if __name__ == "__main__":
    raise SystemExit(main())
