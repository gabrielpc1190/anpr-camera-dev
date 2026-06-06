# JXL Compression — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend transparently serves `.jxl` files as JPEG via `djxl` decoding, then reclaim ~20 GB by transcoding the 127,967-file historical corpus to JXL lossless.

**Architecture:** Modify `serve_image()` in `anpr_web.py` to add a JXL fallback path: when the requested `.jpg` is missing on disk, look for a sibling `.jxl`, decode it with `djxl` (subprocess, written to a temp file because v0.7.0 does not support stdout), and return the resulting JPEG bytes. After deployment is verified, run the pre-installed `scripts/transcode_jxl.py --replace` over the corpus.

**Tech Stack:** Flask, Python 3.11, libjxl-tools 0.7.0 (`djxl`), Docker Compose.

**Operational reminders for the executor:**
- Production is at `/root/anpr-camera-dev`, root-owned. Use `sudo` for everything that touches it. Edit pattern: `sudo cp` to `/tmp`, `sudo chown gabriel`, edit, `sudo cp` back, `sudo chown root:root`.
- Python files and templates are baked into the Docker image. NEVER use `docker restart` alone after editing code. The deploy idiom is `sudo bash -c "cd /root/anpr-camera-dev && docker-compose build anpr-web && docker-compose up -d anpr-web"`.
- The corpus directory is `/root/anpr-camera-dev/app/anpr_images/` on the host, bind-mounted to `/app/anpr_images` inside containers.
- The historical-sweep CLI `scripts/transcode_jxl.py` is already installed and tested. This plan only invokes it.
- The benchmark sample at `/tmp/anpr-compression-test/` is intact and serves as the source for known-good `.jxl` test files (encoded directly from production samples).
- Git push uses the deploy key: `sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master`.

---

## File Structure

**Modified:**
- `anpr_web.Dockerfile` — add `libjxl-tools` to the apt install line.
- `app/anpr_web.py` — add imports (`subprocess`, `tempfile`, `Response`), add helper `_send_image_or_jxl_fallback()`, replace both `send_from_directory(images_dir, filename)` calls inside `serve_image()` with calls to the helper.

**Created (temporary, deleted at end of plan):**
- `/tmp/jxl_smoke.sh` — a smoke-test script used to validate the fallback works end-to-end via `curl`. Deleted after Task 4 passes.

**Not touched in Phase 1:**
- `app/anpr_db_manager.py` — does not serve images (verified: only references `IMAGE_DIR` as config, no image-serving route).
- `app/anpr_listener.py` — listener writes JPEGs; Phase 1 doesn't change ingestion.
- DB schema — no migrations.
- `scripts/transcode_jxl.py` — already installed and tested.

---

## Task 1: Add `libjxl-tools` to the anpr-web Docker image

**Files:**
- Modify: `/root/anpr-camera-dev/anpr_web.Dockerfile` (single line)

- [ ] **Step 1: Stage the Dockerfile for editing**

```bash
sudo cp /root/anpr-camera-dev/anpr_web.Dockerfile /tmp/anpr_web.Dockerfile
sudo chown gabriel:gabriel /tmp/anpr_web.Dockerfile
```

- [ ] **Step 2: Edit `/tmp/anpr_web.Dockerfile` — extend the apt install line**

Find the line:
```dockerfile
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
```

Replace it with:
```dockerfile
RUN apt-get update && apt-get install -y curl libjxl-tools && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Copy back and reset ownership**

```bash
sudo cp /tmp/anpr_web.Dockerfile /root/anpr-camera-dev/anpr_web.Dockerfile
sudo chown root:root /root/anpr-camera-dev/anpr_web.Dockerfile
rm /tmp/anpr_web.Dockerfile
```

- [ ] **Step 4: Rebuild the anpr-web image**

```bash
sudo bash -c "cd /root/anpr-camera-dev && docker-compose build anpr-web"
```

Expected: build succeeds. The apt install line will report `libjxl-tools` getting installed (look for `Setting up libjxl-tools` or similar in the build output).

- [ ] **Step 5: Verify `djxl` is available inside a temporary container**

```bash
sudo bash -c "cd /root/anpr-camera-dev && docker-compose run --rm --no-deps anpr-web djxl --version"
```

Expected: prints `JPEG XL decoder vX.Y.Z ...` to stdout. If you see `command not found`, Step 2 was not saved correctly — go back and re-do.

- [ ] **Step 6: No commit yet** — wait until Task 5 to commit Dockerfile + anpr_web.py together as one feature.

---

## Task 2: Implement JXL fallback in `serve_image()`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py` lines 1-12 (imports) and 519-544 (`serve_image` function).

- [ ] **Step 1: Stage the file for editing**

```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo chown gabriel:gabriel /tmp/anpr_web.py
```

- [ ] **Step 2: Add three new imports near the top of `/tmp/anpr_web.py`**

Find these existing import lines (around lines 1-7):

```python
import os
from datetime import timedelta
from functools import wraps
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_session import Session
```

Modify the `from flask import ...` line to add `Response` and `current_app`:

```python
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session, make_response, Response, current_app
```

Add two new stdlib imports immediately after `import os`:

```python
import os
import subprocess
import tempfile
```

- [ ] **Step 3: Add helper function `_send_image_or_jxl_fallback()` right BEFORE the `@app.route('/images/<path:filename>')` decorator**

In `/tmp/anpr_web.py`, locate the line:

```python
@app.route('/images/<path:filename>')
@login_required
def serve_image(filename):
```

Insert this helper function immediately before it:

```python
def _send_image_or_jxl_fallback(images_dir, filename):
    """Serve filename from images_dir, decoding from sibling .jxl if the .jpg is absent.

    Returns the Flask response, or aborts 404 if neither file is available or djxl fails.
    """
    jpg_path = os.path.join(images_dir, filename)
    if os.path.exists(jpg_path):
        return send_from_directory(images_dir, filename)

    if not filename.endswith('.jpg'):
        abort(404)
    jxl_path = jpg_path[:-4] + '.jxl'
    if not os.path.exists(jxl_path):
        abort(404)

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tf:
        tmp_path = tf.name
    try:
        result = subprocess.run(
            ['djxl', jxl_path, tmp_path],
            capture_output=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            current_app.logger.warning(
                "djxl rc=%d on %s: %s",
                result.returncode, jxl_path, result.stderr[:200].decode('utf-8', errors='replace'),
            )
            abort(404)
        with open(tmp_path, 'rb') as f:
            data = f.read()
        return Response(data, mimetype='image/jpeg')
    except subprocess.TimeoutExpired:
        current_app.logger.warning("djxl timeout on %s", jxl_path)
        abort(404)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
```

- [ ] **Step 4: Replace both `send_from_directory(images_dir, filename)` calls inside `serve_image()`**

In `/tmp/anpr_web.py`, the function `serve_image(filename)` currently has TWO calls to `send_from_directory(images_dir, filename)`. Both must be replaced with `_send_image_or_jxl_fallback(images_dir, filename)`.

The final `serve_image()` body should look exactly like this:

```python
@app.route('/images/<path:filename>')
@login_required
def serve_image(filename):
    """Serve images from the anpr_images directory, gated by group access for viewers."""
    images_dir = '/app/anpr_images'

    # Admin: serve unconditionally
    if current_user.is_admin:
        return _send_image_or_jxl_fallback(images_dir, filename)

    # Viewer: look up the event's camera_id and check it's in allowed list
    allowed = get_allowed_camera_ids(current_user)
    if not allowed:
        abort(403)

    row = db.session.execute(
        db.text("SELECT camera_id FROM anpr_events WHERE image_filename = :fn LIMIT 1"),
        {"fn": filename}
    ).fetchone()
    if row is None:
        abort(404)
    camera_id = row[0]
    if camera_id is None or camera_id not in allowed:
        abort(403)

    return _send_image_or_jxl_fallback(images_dir, filename)
```

- [ ] **Step 5: Validate Python syntax**

```bash
python3 -m py_compile /tmp/anpr_web.py && echo "OK syntax"
```

Expected: `OK syntax`. If syntax error, fix indentation/typo and retry.

- [ ] **Step 6: Copy back and reset ownership**

```bash
sudo cp /tmp/anpr_web.py /root/anpr-camera-dev/app/anpr_web.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_web.py
rm /tmp/anpr_web.py
```

- [ ] **Step 7: No commit yet** — Tasks 1 + 2 ship together, commit happens in Task 5.

---

## Task 3: Deploy and smoke-test with `curl`

**Files:** (none modified — runtime verification only)
**Creates:** `/tmp/jxl_smoke.sh` (smoke-test script, deleted at end of plan).

- [ ] **Step 1: Rebuild and redeploy anpr-web**

```bash
sudo bash -c "cd /root/anpr-camera-dev && docker-compose build anpr-web && docker-compose up -d anpr-web"
```

Expected: build succeeds, container restarts cleanly.

- [ ] **Step 2: Wait 5 seconds, then check the container is healthy and listening**

```bash
sleep 5 && sudo docker ps --filter name=anpr-web --format "{{.Names}}\t{{.Status}}"
sudo bash -c "cd /root/anpr-camera-dev && docker-compose logs --tail=30 anpr-web"
```

Expected: container status `Up ... (healthy)`. Logs show Flask startup with no traceback. If logs show `ImportError` or syntax error → revert anpr_web.py from git and go back to Task 2.

- [ ] **Step 3: Write the smoke-test script**

Write the following content to `/tmp/jxl_smoke.sh` (use the Write tool):

```bash
#!/usr/bin/env bash
# Smoke test: log in as admin, pick a real event image, swap .jpg → .jxl on disk,
# request via /images/ endpoint, verify response is a valid JPEG, restore.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"
ADMIN_USER="${ADMIN_USER:?must export ADMIN_USER}"
ADMIN_PASS="${ADMIN_PASS:?must export ADMIN_PASS}"
COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "=== 1. Log in as admin ==="
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    -d "username=$ADMIN_USER&password=$ADMIN_PASS" \
    -o /dev/null -w "login HTTP %{http_code}\n" \
    "$BASE_URL/login"

echo "=== 2. Pick a real image filename from DB ==="
IMG_NAME=$(sudo bash -c "cd /root/anpr-camera-dev && docker-compose exec -T mariadb mysql -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -se 'SELECT image_filename FROM anpr_events WHERE image_filename IS NOT NULL ORDER BY id DESC LIMIT 1'")
echo "Sample image: $IMG_NAME"
JPG="/root/anpr-camera-dev/app/anpr_images/$IMG_NAME"
JXL="${JPG%.jpg}.jxl"

echo "=== 3. Verify the .jpg exists ==="
sudo test -f "$JPG" && echo "OK .jpg exists"

echo "=== 4. Baseline: fetch via /images/ (should be served as plain JPEG) ==="
curl -sS -b "$COOKIE_JAR" -o /tmp/jxl_smoke_a.bin -w "baseline HTTP %{http_code} content-type=%{content_type} size=%{size_download}\n" \
    "$BASE_URL/images/$IMG_NAME"
head -c 3 /tmp/jxl_smoke_a.bin | od -An -tx1 | grep -q "ff d8 ff" && echo "OK baseline is JPEG"

echo "=== 5. Encode .jxl, hide .jpg ==="
sudo cjxl -d 0 -j 1 "$JPG" "$JXL" >/dev/null 2>&1
sudo mv "$JPG" "${JPG}.smoketest_bak"

echo "=== 6. Fetch via /images/ again — should now be served from .jxl via djxl ==="
curl -sS -b "$COOKIE_JAR" -o /tmp/jxl_smoke_b.bin -w "fallback HTTP %{http_code} content-type=%{content_type} size=%{size_download}\n" \
    "$BASE_URL/images/$IMG_NAME"
head -c 3 /tmp/jxl_smoke_b.bin | od -An -tx1 | grep -q "ff d8 ff" && echo "OK fallback is JPEG"

echo "=== 7. Bit-exact: baseline JPEG and fallback JPEG must be identical ==="
A_HASH=$(sha256sum /tmp/jxl_smoke_a.bin | cut -d' ' -f1)
B_HASH=$(sha256sum /tmp/jxl_smoke_b.bin | cut -d' ' -f1)
if [ "$A_HASH" = "$B_HASH" ]; then
    echo "OK bit-exact: both fetches returned identical bytes"
else
    echo "FAIL bit-exact mismatch"; echo "  baseline=$A_HASH"; echo "  fallback=$B_HASH"; exit 1
fi

echo "=== 8. Restore original .jpg, remove .jxl ==="
sudo mv "${JPG}.smoketest_bak" "$JPG"
sudo rm -f "$JXL"
rm -f /tmp/jxl_smoke_a.bin /tmp/jxl_smoke_b.bin

echo ""
echo "==== SMOKE TEST PASSED ===="
```

```bash
chmod +x /tmp/jxl_smoke.sh
```

- [ ] **Step 4: Run the smoke test**

You need an admin user's credentials. The username/password live in the `user` table; ask the user for them or use an existing test account.

```bash
ADMIN_USER='<admin-username>' ADMIN_PASS='<admin-password>' /tmp/jxl_smoke.sh
```

Expected final line: `==== SMOKE TEST PASSED ====`.

Expected intermediate output:
- `login HTTP 200` (or 302 on redirect — both acceptable as long as the cookie jar gets populated)
- `baseline HTTP 200 content-type=image/jpeg size=<N>`
- `OK baseline is JPEG`
- `fallback HTTP 200 content-type=image/jpeg size=<N>`
- `OK fallback is JPEG`
- `OK bit-exact: both fetches returned identical bytes`

If any step fails:
- `fallback HTTP 404` → `serve_image()` did not find the .jxl. Check log: `sudo bash -c "cd /root/anpr-camera-dev && docker-compose logs anpr-web | tail -50"`. Look for `djxl rc=` or `djxl timeout` warnings.
- bit-exact FAIL → libjxl bug or wrong invocation. Restore the .jpg manually (`sudo mv "${JPG}.smoketest_bak" "$JPG"`) and investigate.
- Login 401 → wrong credentials.

- [ ] **Step 5: Hard cleanup if the script aborted mid-way**

If the script aborted between Steps 5 and 8 (encoded .jxl but didn't restore .jpg), recover:

```bash
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.smoketest_bak" -exec bash -c 'mv "$0" "${0%.smoketest_bak}"' {} \;
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jxl" -delete  # only safe if no real .jxl exist yet (true at this point)
```

---

## Task 4: End-to-end browser verification

**Files:** (none modified)

This task does NOT depend on Task 3 having run; it's a second, independent verification with a real human in front of the browser. Coordinate with the user.

- [ ] **Step 1: Pick a recent event from the dashboard**

Ask the user to open the dashboard in the browser, log in as admin, and identify one specific event (note the `image_filename` shown in the event row). The user should screenshot or note the row so they can re-find it.

- [ ] **Step 2: Encode the selected event's image to JXL on the host**

Replace `<filename>` with the actual filename from Step 1:

```bash
FN="<filename>"
sudo cjxl -d 0 -j 1 "/root/anpr-camera-dev/app/anpr_images/$FN" "/root/anpr-camera-dev/app/anpr_images/${FN%.jpg}.jxl"
sudo mv "/root/anpr-camera-dev/app/anpr_images/$FN" "/root/anpr-camera-dev/app/anpr_images/${FN}.e2e_bak"
```

- [ ] **Step 3: Ask the user to reload the dashboard and re-open that event's image**

The image should display exactly as before (no broken image icon, no visible quality difference). Ask the user to confirm.

- [ ] **Step 4: Restore the original .jpg, remove the .jxl**

```bash
FN="<filename>"
sudo mv "/root/anpr-camera-dev/app/anpr_images/${FN}.e2e_bak" "/root/anpr-camera-dev/app/anpr_images/$FN"
sudo rm "/root/anpr-camera-dev/app/anpr_images/${FN%.jpg}.jxl"
```

- [ ] **Step 5: Verify nothing leaked**

```bash
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.e2e_bak" -o -name "*.jxl" | head -5
```

Expected: empty output. If anything is listed, repeat Step 4 with the correct filename.

---

## Task 5: Commit and push the Phase 1 backend change

**Files:** (no further edits — committing what Tasks 1 & 2 produced)

- [ ] **Step 1: Confirm only the expected files are changed**

```bash
sudo git -C /root/anpr-camera-dev status --short
```

Expected output (exact, in some order):

```
 M anpr_web.Dockerfile
 M app/anpr_web.py
```

If you see any other modified file, investigate before committing — those could be leftovers from an aborted smoke test.

- [ ] **Step 2: Review the diff one more time**

```bash
sudo git -C /root/anpr-camera-dev diff anpr_web.Dockerfile app/anpr_web.py | head -120
```

Expected: Dockerfile gains `libjxl-tools` on the apt install line; anpr_web.py gains 3 imports + the helper function + two single-line call replacements.

- [ ] **Step 3: Delete the smoke-test script**

```bash
rm -f /tmp/jxl_smoke.sh
```

- [ ] **Step 4: Stage and commit**

```bash
sudo git -C /root/anpr-camera-dev add anpr_web.Dockerfile app/anpr_web.py
sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
feat(jxl): serve .jxl images transparently via djxl fallback

When an image is missing as .jpg on disk, serve_image() now looks for
a sibling .jxl and decodes it on the fly with djxl. The endpoint
contract is unchanged: clients keep requesting /images/<name>.jpg and
get JPEG bytes back. Adds libjxl-tools to the anpr-web container so
djxl is available at runtime.

This unblocks the historical sweep (scripts/transcode_jxl.py --replace),
which will reclaim ~20 GB measured against the current corpus.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push to origin/master**

```bash
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master
```

Expected: push succeeds, prints `To github.com:gabrielpc1190/anpr-camera-dev.git` with a fast-forward to the new SHA.

---

## Task 6: Historical sweep

**Files:** (none modified — runtime job)

This is the long-running task that actually reclaims disk. It uses the pre-installed CLI `scripts/transcode_jxl.py`.

- [ ] **Step 1: Record baseline disk state**

```bash
sudo df -h /root/anpr-camera-dev/app/anpr_images
sudo du -sh /root/anpr-camera-dev/app/anpr_images
```

Save both numbers — the report at the end of the plan compares against these.

- [ ] **Step 2: Confirm `tmux` or `screen` is installed**

```bash
which tmux || which screen
```

Expected: at least one of them. If neither, install tmux: `sudo apt-get install -y tmux`.

- [ ] **Step 3: Dry-run preflight on 100 files**

```bash
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py \
    --age-min-hours 24 --limit 100 --dry-run -v --no-log-file \
    --lock-file /tmp/jxl-preflight.lock 2>&1 | tail -15
sudo rm -f /tmp/jxl-preflight.lock
```

Expected: `Found N eligible JPEG files` with N > 100; `scanned=100 transcoded=100 verified=0 cjxl_fail=0 djxl_fail=0 verify_fail=0`. (`verified=0` is correct for dry-run; nothing was actually written.)

- [ ] **Step 4: Small `--replace` batch (100 files) against production corpus**

This is the first time `--replace` runs on real production data. Limiting to 100 means at most 100 JPEGs are deleted if anything is wrong, and the script's internal bit-exact check is the safety net (if it fails, the `.jpg` is kept and the run is reported).

```bash
sudo mkdir -p /var/log/anpr
sudo python3 /root/anpr-camera-dev/scripts/transcode_jxl.py \
    --age-min-hours 24 --limit 100 --throttle-ms 50 --replace -v 2>&1 | tail -15
```

Expected: `scanned=100 transcoded=100 verified=100 cjxl_fail=0 djxl_fail=0 verify_fail=0 ... deleted=100 bytes_saved=<N>`. If `verify_fail > 0` or `deleted < transcoded`, STOP and escalate to the user — bit-exact failures must be investigated, not ignored.

- [ ] **Step 5: Spot-check that 100 JPEGs were deleted and replaced by JXLs**

```bash
JPG_COUNT=$(sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jpg" | wc -l)
JXL_COUNT=$(sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jxl" | wc -l)
echo "jpg=$JPG_COUNT jxl=$JXL_COUNT (expect jxl ≈ 100 and jpg ≈ baseline - 100)"
```

Expected: `*.jxl` count ≈ 100; `*.jpg` count ≈ baseline minus 100 (a few new files may have been ingested while the script was running — that's normal).

- [ ] **Step 5b: Verify dashboard still serves images for one of the just-replaced files**

```bash
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jxl" -printf "%f\n" | head -3
```

Take one of the printed `.jxl` filenames, strip `.jxl` and append `.jpg` to get the URL filename. Ask the user (or yourself, with admin credentials) to curl `http://localhost:5000/images/<that-jpg-name>` and confirm a JPEG body comes back (`head -c 3 | od -An -tx1` shows `ff d8 ff`). This validates the backend fallback (Task 2) is doing its job on real replaced files, not just the smoke test of Task 3.

- [ ] **Step 6: Start the full sweep with `--replace` inside `tmux`**

This step actually deletes JPEGs after bit-exact verification. Estimated wall-clock: ~6.7 h single-threaded.

```bash
sudo tmux new-session -d -s jxl-sweep "python3 /root/anpr-camera-dev/scripts/transcode_jxl.py --age-min-hours 24 --throttle-ms 100 --replace -v 2>&1 | tee -a /var/log/anpr/jxl-sweep.log"
sudo tmux ls
```

Expected: `jxl-sweep: 1 windows (created ...)` in the tmux list. The sweep is now running detached.

- [ ] **Step 7: Tail the log to confirm it's making progress**

```bash
sleep 30 && sudo tail -20 /var/log/anpr/jxl-sweep.log
```

Expected: at least 20-100 `REPLACE` log lines (5.4 img/s × 30 s ≈ 160 imgs, modulo throttle).

- [ ] **Step 8: Monitor periodically until done**

There is no fixed step here — the sweep takes ~6.7 hours. Check progress every 1-2 hours:

```bash
sudo tmux capture-pane -t jxl-sweep -p | tail -10
sudo df -h /root/anpr-camera-dev/app/anpr_images
```

The user can interrupt the sweep with `sudo tmux send-keys -t jxl-sweep C-c` (SIGINT — the script will finish the current file and exit cleanly) and resume later by re-running Step 6's command; idempotency means it will skip files that already have a `.jxl` sibling.

- [ ] **Step 9: Sweep completion**

The sweep is done when `sudo tmux ls` no longer lists `jxl-sweep`, OR when the captured pane shows a final `Done in <seconds>s | scanned=... transcoded=... ...` line.

```bash
sudo tail -3 /var/log/anpr/jxl-sweep.log
```

Expected last line: `Done in <Ns> | scanned=<N> transcoded=<N> verified=<N> cjxl_fail=0 djxl_fail=0 verify_fail=0 already_done=0 deleted=<N> bytes_saved=<N>`. The cumulative `deleted` (Step 4's 100 + this run's count) should be the eligible-file count minus any per-file failures.

---

## Task 7: Post-sweep verification and report

**Files:** (none modified)

- [ ] **Step 1: Measure final disk state**

```bash
sudo df -h /root/anpr-camera-dev/app/anpr_images
sudo du -sh /root/anpr-camera-dev/app/anpr_images
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jpg" | wc -l
sudo find /root/anpr-camera-dev/app/anpr_images -name "*.jxl" | wc -l
```

Expected:
- `du` total dropped by roughly 17 % (target ~20 GB recovered).
- `.jpg` count drops to whatever wasn't eligible (newer than 24 h plus any files that hit cjxl/djxl/verify errors).
- `.jxl` count is approximately the original `.jpg` count minus residual `.jpg`s.

- [ ] **Step 2: Pick three sample event images from the DB at random and verify they render**

```bash
sudo bash -c "cd /root/anpr-camera-dev && docker-compose exec -T mariadb mysql -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -se 'SELECT image_filename FROM anpr_events WHERE image_filename IS NOT NULL ORDER BY RAND() LIMIT 3'"
```

For each filename printed, ask the user to open the dashboard and locate the event; the image should display normally. Alternatively, replay Step 4 of Task 3 (`/tmp/jxl_smoke.sh` style curl) with these specific filenames if the user prefers not to fish in the UI.

- [ ] **Step 3: Confirm no error spam in anpr-web logs**

```bash
sudo bash -c "cd /root/anpr-camera-dev && docker-compose logs --since 1h anpr-web | grep -iE 'djxl|error|traceback' | head -30"
```

Expected: zero or a handful of `djxl` warnings from the sweep's coexistence with live traffic. A flood of warnings means something is wrong — investigate.

- [ ] **Step 4: Write a short post-sweep summary in the runbook**

Append the actual numbers to the bottom of `docs/deployment/jxl-transcode-runbook.md`:

```bash
sudo bash -c 'cat >> /root/anpr-camera-dev/docs/deployment/jxl-transcode-runbook.md << EOF

## Phase 1 sweep result — 2026-06-05

- Files processed: <N>
- Files deleted (.jpg → .jxl): <N>
- Disk reclaimed: <X> GB (from <Y> GB to <Z> GB)
- Errors: cjxl=<N>, djxl=<N>, verify=<N>
EOF'
```

Fill in the actual numbers from Step 1. Replace placeholders manually with `sudo nano` or by editing in `/tmp` and copying back.

- [ ] **Step 5: Commit the runbook update**

```bash
sudo git -C /root/anpr-camera-dev add docs/deployment/jxl-transcode-runbook.md
sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
docs(jxl): record Phase 1 sweep numbers in runbook

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master
```

- [ ] **Step 6: Report to the user**

Tell the user the headline number ("Reclaimed X GB; corpus is now Y GB on Z GB partition; backend serving Z % of images via JXL fallback without errors"). Ask whether they want to schedule Phase 2 planning or pause.

---

## Phase 1 done

At the end of Task 7 the urgent disk problem is solved. Phase 2 (toggle UI + inotify watcher for ongoing ingestion) is a separate plan, to be written when the user wants to address future growth — Phase 1 alone makes the disk pressure non-urgent.
