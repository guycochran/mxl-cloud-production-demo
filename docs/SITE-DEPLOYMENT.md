# Informational site deployment

The informational site is deliberately static and lives in `docs/`:

- `index.html`
- `styles.css`
- `site.js`
- existing screenshots in `docs/images/`

It has no build step, cookies, analytics, external fonts or runtime dependency
on the live switcher.

## Preview locally

From the repository root:

```bash
python3 -m http.server 8080 --directory docs
```

Open <http://localhost:8080>.

## Publish without the custom domain

GitHub Pages can publish directly from the `docs/` directory on the default
branch. Keep the generated `github.io` URL as the preview/staging address while
trademark guidance is pending.

## Custom domain, only after written permission

1. Move the current live control surface to `demo.mxlswitcher.com`.
2. Publish this directory as the root site.
3. Add the custom domain in the chosen hosting provider.
4. Update DNS only after the provider supplies the exact target.
5. Preserve the independent-project notice and trademark footer.
6. Add a `CNAME` file only if GitHub Pages is the chosen production host.

Do not commit a `CNAME` yet: the domain currently serves the live system and the
project is waiting for trademark guidance.
