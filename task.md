# Task Checklist

- [ ] Add Tailwind CSS as a local dependency using npm
- [ ] Create Tailwind configuration file (tailwind.config.js)
- [ ] Add a CSS entry file (src/tailwind.css) with Tailwind directives
- [ ] Update Dockerfile to install node, npm and build Tailwind CSS during image build
- [ ] Generate compiled CSS (static/tailwind.css) and commit it to the repository
- [ ] Update index.html to reference the local CSS file instead of CDN
- [ ] Rebuild Docker images and verify UI loads without CDN
