#!/usr/bin/env python3
"""Transcode ANPR JPEG corpus to JPEG XL (lossless, bit-exact reversible).

Default mode (PRESERVE): writes .jxl alongside .jpg without deleting the .jpg.
Disk does not shrink — but the run is fully reversible and the backend keeps
serving JPEGs unchanged.

Use --replace ONLY after:
  1. anpr_web.py supports serving .jxl by decoding to JPEG on-the-fly,
     OR you accept that legacy .jpg URLs will 404 for files already replaced.
  2. A reversibility dry-run has confirmed bit-exact reconstruction on a
     non-trivial sample.
  3. An off-box backup exists.

See docs/deployment/jxl-transcode-runbook.md for activation procedure.
"""

import argparse
import fcntl
import hashlib
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_ROOT = "/root/anpr-camera-dev/app/anpr_images"
DEFAULT_LOG = "/var/log/anpr/jxl-transcode.log"
DEFAULT_LOCK = "/var/lock/anpr-jxl-transcode.lock"
DEFAULT_AGE_HOURS = 24
DEFAULT_THROTTLE_MS = 50

CJXL_ARGS = ["-d", "0", "-j", "1"]


@dataclass
class Stats:
    scanned: int = 0
    skipped_recent: int = 0
    skipped_already_done: int = 0
    transcoded: int = 0
    verified_bit_exact: int = 0
    verification_failed: int = 0
    cjxl_failed: int = 0
    djxl_failed: int = 0
    jpg_deleted: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    errors: list[str] = field(default_factory=list)


_stop_requested = False


def _handle_signal(signum, frame):
    global _stop_requested
    _stop_requested = True
    logging.warning("Signal %d received, stopping after current file", signum)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def transcode_one(
    jpg: Path,
    stats: Stats,
    *,
    dry_run: bool,
    replace: bool,
    cjxl_bin: str,
    djxl_bin: str,
) -> None:
    jxl = jpg.with_suffix(".jxl")
    if jxl.exists():
        stats.skipped_already_done += 1
        return

    orig_size = jpg.stat().st_size
    stats.bytes_before += orig_size

    if dry_run:
        logging.info("DRY-RUN cjxl %s -> %s", jpg.name, jxl.name)
        stats.transcoded += 1
        return

    tmp_jxl = jpg.with_suffix(".jxl.tmp")
    try:
        result = subprocess.run(
            [cjxl_bin, *CJXL_ARGS, str(jpg), str(tmp_jxl)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not tmp_jxl.exists():
            stats.cjxl_failed += 1
            stats.errors.append(f"cjxl {jpg.name}: rc={result.returncode} {result.stderr[:200]}")
            if tmp_jxl.exists():
                tmp_jxl.unlink()
            return

        if not _verify_bit_exact(jpg, tmp_jxl, djxl_bin, stats):
            tmp_jxl.unlink()
            return

        os.replace(tmp_jxl, jxl)
        stats.transcoded += 1
        stats.verified_bit_exact += 1
        stats.bytes_after += jxl.stat().st_size

        if replace:
            jpg.unlink()
            stats.jpg_deleted += 1
            logging.info("REPLACE %s (saved %d bytes)", jpg.name, orig_size - jxl.stat().st_size)
        else:
            stats.bytes_after += orig_size
            logging.info("PRESERVE %s (jxl=%d, jpg kept)", jpg.name, jxl.stat().st_size)
    except subprocess.TimeoutExpired:
        stats.cjxl_failed += 1
        stats.errors.append(f"cjxl timeout: {jpg.name}")
        if tmp_jxl.exists():
            tmp_jxl.unlink()


def _verify_bit_exact(jpg: Path, jxl: Path, djxl_bin: str, stats: Stats) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        reconstructed = Path(tf.name)
    try:
        result = subprocess.run(
            [djxl_bin, str(jxl), str(reconstructed)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            stats.djxl_failed += 1
            stats.errors.append(f"djxl {jpg.name}: rc={result.returncode} {result.stderr[:200]}")
            return False

        original_hash = sha256_file(jpg)
        reconstructed_hash = sha256_file(reconstructed)
        if original_hash != reconstructed_hash:
            stats.verification_failed += 1
            stats.errors.append(
                f"sha256 mismatch {jpg.name}: orig={original_hash[:12]} reconstructed={reconstructed_hash[:12]}"
            )
            return False
        return True
    finally:
        reconstructed.unlink(missing_ok=True)


def find_eligible_jpgs(root: Path, min_age_seconds: int) -> list[Path]:
    cutoff = time.time() - min_age_seconds
    eligible = []
    for jpg in root.glob("*.jpg"):
        try:
            if jpg.stat().st_mtime < cutoff:
                eligible.append(jpg)
        except FileNotFoundError:
            continue
    eligible.sort()
    return eligible


def acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"Another instance is running (lock held: {lock_path})", file=sys.stderr)
        sys.exit(2)
    fd.write(str(os.getpid()))
    fd.flush()
    return fd


def setup_logging(log_path: Optional[Path], verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=Path(DEFAULT_ROOT),
                   help=f"JPEG corpus root (default: {DEFAULT_ROOT})")
    p.add_argument("--age-min-hours", type=float, default=DEFAULT_AGE_HOURS,
                   help=f"Skip files younger than N hours (default: {DEFAULT_AGE_HOURS})")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N files (0 = unlimited)")
    p.add_argument("--throttle-ms", type=int, default=DEFAULT_THROTTLE_MS,
                   help=f"Sleep N ms between files (default: {DEFAULT_THROTTLE_MS})")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write or delete anything; just count and log")
    p.add_argument("--replace", action="store_true",
                   help="After bit-exact verification, DELETE the .jpg. "
                        "Requires backend support for serving .jxl.")
    p.add_argument("--log-file", type=Path, default=Path(DEFAULT_LOG),
                   help=f"Append-only log file (default: {DEFAULT_LOG})")
    p.add_argument("--no-log-file", action="store_true",
                   help="Disable log file; log only to stdout")
    p.add_argument("--lock-file", type=Path, default=Path(DEFAULT_LOCK),
                   help=f"Lock file path (default: {DEFAULT_LOCK})")
    p.add_argument("--cjxl", default="cjxl", help="cjxl binary path (default: cjxl in PATH)")
    p.add_argument("--djxl", default="djxl", help="djxl binary path (default: djxl in PATH)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(None if args.no_log_file else args.log_file, args.verbose)

    if args.replace and args.dry_run:
        logging.info("--dry-run forces preserve mode; --replace is a no-op")

    for binary in (args.cjxl, args.djxl):
        if subprocess.run(["which", binary], capture_output=True).returncode != 0:
            logging.error("Required binary not found in PATH: %s", binary)
            return 3

    if not args.root.is_dir():
        logging.error("Root does not exist or is not a directory: %s", args.root)
        return 4

    lock_fd = acquire_lock(args.lock_file)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stats = Stats()
    start = time.monotonic()
    min_age_seconds = int(args.age_min_hours * 3600)
    logging.info("Scanning %s (age >= %s h, dry_run=%s, replace=%s)",
                 args.root, args.age_min_hours, args.dry_run, args.replace)

    candidates = find_eligible_jpgs(args.root, min_age_seconds)
    logging.info("Found %d eligible JPEG files", len(candidates))

    for i, jpg in enumerate(candidates):
        if _stop_requested:
            logging.warning("Stop requested; processed %d/%d", i, len(candidates))
            break
        if args.limit and stats.transcoded >= args.limit:
            logging.info("Reached --limit %d", args.limit)
            break
        stats.scanned += 1
        transcode_one(
            jpg, stats,
            dry_run=args.dry_run,
            replace=args.replace,
            cjxl_bin=args.cjxl,
            djxl_bin=args.djxl,
        )
        if args.throttle_ms > 0:
            time.sleep(args.throttle_ms / 1000.0)

    elapsed = time.monotonic() - start
    saved = stats.bytes_before - stats.bytes_after if args.replace else 0
    logging.info(
        "Done in %.1fs | scanned=%d transcoded=%d verified=%d "
        "cjxl_fail=%d djxl_fail=%d verify_fail=%d already_done=%d "
        "deleted=%d bytes_saved=%d",
        elapsed, stats.scanned, stats.transcoded, stats.verified_bit_exact,
        stats.cjxl_failed, stats.djxl_failed, stats.verification_failed,
        stats.skipped_already_done, stats.jpg_deleted, saved,
    )
    if stats.errors:
        logging.warning("First 10 errors:")
        for err in stats.errors[:10]:
            logging.warning("  %s", err)

    lock_fd.close()
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    sys.exit(main())
