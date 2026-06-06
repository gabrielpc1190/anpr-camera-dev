# JXL Transcode Runbook

Operational guide for the ANPR JPEG → JPEG XL lossless transcoder.

## What this does

Converts JPEG files in `/root/anpr-camera-dev/app/anpr_images/` to JPEG XL using
`cjxl -d 0 -j 1` (lossless transcode of JPEG coefficients). Each `.jxl` is
verified bit-exact: the script runs `djxl` on it, reconstructs a JPEG, and
compares SHA-256 against the original. Only after that match does it count as
done.

Empirical measurement on a 100-image sample of the corpus
(`/tmp/anpr-compression-test/`, June 2026):

| Metric | Value |
| --- | --- |
| Average JPEG → JXL size ratio | 0.83 (17.0 % saved) |
| Bit-exact reconstruction rate | 100 / 100 |
| Encoding speed (1 thread) | ~5.4 images/s |
| Camera-emitted JPEG quality | Q = 71 |

Extrapolation to the full corpus (~119 GB, ~128 k files):
~20 GB recoverable, full pass takes ~6.7 h single-threaded.

## Status: code is NOT activated

The script and example cron file ship with the repo, but no cron entry is
installed. The script must be activated manually following the steps below.

## Files

- `scripts/transcode_jxl.py` — the transcoder
- `scripts/transcode_jxl.cron.example` — cron entry template
- `docs/deployment/jxl-transcode-runbook.md` — this document

## Modes

### Preserve mode (default — SAFE)

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py --age-min-hours 24
```

Writes `.jxl` alongside each `.jpg`. **Does NOT delete the JPEG.** Disk usage
*increases* by the size of the JXL files (~83 % of the JPEGs). The backend
keeps serving `.jpg` URLs unchanged.

This mode is the right starting point: it validates that bit-exact verification
holds across the real corpus before any data is removed.

### Replace mode (deletes the JPEG)

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py --age-min-hours 24 --replace
```

After bit-exact verification succeeds for a file, deletes the original `.jpg`.
This is the mode that actually saves disk.

**Prerequisite:** the backend (`app/anpr_web.py` and `app/anpr_db_manager.py`)
must be modified to serve `.jxl` files. Concretely: any code path that today
opens a `.jpg` by name must fall back to the `.jxl` sibling and decode on the
fly with `djxl`. If `--replace` is run before this change, existing image URLs
will return 404 for replaced files.

## Activation procedure

Do **not** install the cron entry until the steps below are complete.

### Step 1 — Dry-run end to end against the production corpus

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py \
    --age-min-hours 24 --limit 100 --dry-run -v
```

Expected: `Found N eligible JPEG files`, no errors. Confirms the script can
read the corpus, the lock file location is writable, and the binaries are in
PATH.

### Step 2 — Run preserve mode on a small batch

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py \
    --age-min-hours 24 --limit 100
```

Expected: 100 transcoded, 100 verified, 0 cjxl_fail, 0 djxl_fail, 0 verify_fail.
Disk should grow by roughly `0.83 × sum(jpeg sizes of those 100)`. Inspect the
`.jxl` files manually with `djxl` to confirm the reconstructed JPEG opens.

### Step 3 — Decide on backend support

Two paths:

1. **Backend supports .jxl** (recommended). Update `app/anpr_web.py` (and any
   other endpoint that opens an image by name) to look for the `.jxl` sibling
   and decode on the fly via `djxl` if the `.jpg` is absent. Cache the decoded
   bytes if useful. Then `--replace` is safe.
2. **Backend untouched.** Skip `--replace`. Run only in preserve mode and accept
   that disk does not shrink. There is no net benefit; do not activate cron in
   this case.

### Step 4 — Install the cron entry

After backend support is in place:

```bash
sudo mkdir -p /var/log/anpr /var/lock
sudo cp /root/anpr-camera-dev/scripts/transcode_jxl.cron.example \
        /etc/cron.d/anpr-jxl-transcode
sudo chown root:root /etc/cron.d/anpr-jxl-transcode
sudo chmod 644 /etc/cron.d/anpr-jxl-transcode
```

Edit `/etc/cron.d/anpr-jxl-transcode` to add `--replace` to the command line:

```
15 3 * * * root /usr/bin/python3 /root/anpr-camera-dev/scripts/transcode_jxl.py --age-min-hours 24 --throttle-ms 50 --replace >> /var/log/anpr/jxl-transcode.cron.log 2>&1
```

Tail the log the next morning:

```bash
sudo tail -200 /var/log/anpr/jxl-transcode.cron.log
sudo tail -200 /var/log/anpr/jxl-transcode.log
```

### Step 5 — One-time historical sweep

The cron job above only processes files older than 24 h, so the entire
historical corpus is eligible immediately. To process it without competing with
the daily run, do a controlled foreground pass:

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py \
    --age-min-hours 24 --throttle-ms 100 --replace -v
```

The script holds a lock, so the nightly cron run will be skipped while this is
running; that is expected and harmless.

## Operational notes

- **Idempotency:** if a `.jxl` already exists next to a `.jpg`, the file is
  skipped. Re-running the script is safe.
- **Concurrency:** an `fcntl` lock at `/var/lock/anpr-jxl-transcode.lock`
  prevents two instances from running at the same time. Second invocations
  exit with code 2.
- **Live ingest safety:** `--age-min-hours 24` makes the script ignore any file
  whose `mtime` is within the last 24 h. The Dahua callback path is therefore
  unaffected.
- **Failure handling:** any of cjxl error, djxl error, or SHA-256 mismatch
  leaves the original `.jpg` in place and removes any partial `.jxl.tmp`. The
  file is reported in the error summary and retried on the next run.
- **Stop signals:** `SIGINT` and `SIGTERM` finish the current file and exit
  cleanly.

## Rollback

If `--replace` was used and you need to recover the original JPEGs:

```bash
cd /root/anpr-camera-dev/app/anpr_images
for f in *.jxl; do
    djxl "$f" "${f%.jxl}.jpg"
done
```

Because the transcode is bit-exact, the recovered `.jpg` files are identical
to the originals (same SHA-256). You can then delete the `.jxl` files.

If only preserve mode was used, both files are already present and no recovery
is required — just delete the `.jxl` files.

## Tooling references

- `cjxl` / `djxl`: `libjxl-tools` package (verified working on v0.7.0).
- Sample of empirical measurements: `/tmp/anpr-compression-test/REPORT.md`
  (kept on the host as the baseline benchmark).

## Phase 1 sweep result — 2026-06-06

Initial historical sweep against production corpus (run inside `tmux` with
`--age-min-hours 24 --throttle-ms 100 --replace`).

| Metric | Value |
| --- | --- |
| Files scanned | 128 047 |
| Files transcoded and verified bit-exact | 128 047 (100 %) |
| Files deleted (.jpg → .jxl) | 128 047 |
| Bytes saved (reported by script) | 21 756 576 929 (≈ 20.3 GB) |
| Disk freed (`df` delta) | 19 GB (137 → 118 GB used) |
| Disk free | 49 → 69 GB |
| Disk usage % | 74 % → 64 % |
| Wall time | 73 972 s (≈ 20.5 h) |
| Errors (cjxl / djxl / verify) | 0 / 0 / 0 |
| Effective rate | ~1.73 files/s (single thread + 100 ms throttle) |

The `df` delta is smaller than the script-reported `bytes_saved` because ingest
continued during the sweep (~22 GB/month rate ⇒ ~600 MB during 20.5 h) and FS
journaling/block alignment absorbs a few hundred MB.

Post-sweep spot-check confirmed three random `.jxl` files decode to valid JPEG
(`FF D8 FF` magic) inside the running `anpr-web` container. `anpr-web` logs show
zero `djxl`/error lines during and after the sweep window. Backend transparent
fallback (`_send_image_or_jxl_fallback` in `app/anpr_web.py`) is serving these
files to the dashboard with no user-visible change.

**State of `app/anpr_images/` after sweep**: 128 147 `.jxl` (100 from the
pre-sweep limit-100 batch + 128 047 from the main sweep) and ~1 500 `.jpg`
(recently ingested, below the 24 h eligibility threshold). New cameras keep
writing `.jpg` directly — Phase 2 (toggle UI + inotify watcher) is required
to compress ongoing ingestion automatically; see Phase 7 Part 2 in `ROADMAP.md`.
