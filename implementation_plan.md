# Add Local Tailwind CSS Support

## Goal Description
Replace the CDN‑based Tailwind CSS import with a locally built Tailwind stylesheet so the ANPR web UI works without internet access. This involves adding Node.js tooling, compiling Tailwind during the Docker build, and updating the HTML to reference the compiled CSS.

## User Review Required
- Confirm that adding a Node.js build step is acceptable for the production image size.
- Approve the location of the compiled CSS (`/app/app/static/tailwind.css`).
- Approve the new `package.json` and `tailwind.config.js` files will be added to the repository.

## Proposed Changes
---
### 1. Add Node.js build environment
- Create `package.json` with Tailwind and PostCSS dependencies.
- Add `tailwind.config.js` with default content paths.
- Add `src/tailwind.css` containing Tailwind directives.

### 2. Update Dockerfile (`anpr_web.Dockerfile`)
- Install Node.js and npm.
- Copy the new Node files into the image.
- Run `npm install` and `npx tailwindcss -i ./src/tailwind.css -o ./app/static/tailwind.css --minify` during the build.
- Ensure the compiled CSS ends up in `/app/app/static/` where Flask serves static files.

### 3. Update HTML (`app/templates/index.html`)
- Remove the `<script src="https://cdn.tailwindcss.com"></script>` line.
- Add `<link rel="stylesheet" href="/static/tailwind.css">` before custom styles.

### 4. Rebuild Docker images
- Run `docker compose up --build -d` to rebuild `anpr-web` with the new assets.

## Verification Plan
- After rebuilding, open the web UI and confirm no network requests to `cdn.tailwindcss.com`.
- Verify the UI renders correctly and images load.
- Check that the container can start without internet connectivity.
