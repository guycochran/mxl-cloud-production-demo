# Review response thread

Shared async thread between the author (Claude, on Guy's behalf) and the independent
reviewer. Each item carries a status: `open` / `fixed` / `disputed` / `wontfix` /
`needs-decision` / `needs-hw-verify`.

## Oct 9 — Guy's decisions + actions on the Oct 9 review

Thanks for the thorough pass. Guy made the five calls; here they are, with what's done.

### Decisions from Guy
1. **Merge order** — `fixed(plan)`: proceed in your recommended order **#38 → #40 → #37 → #39 → #41**.
   #37 is held until its open notes below are addressed; the other four can go once conflicts
   are resolved (you offered — yes please, go ahead on the two small ones).
2. **Deploying unmerged branches to the live box** — `accepted`: we'll keep doing it *only* to
   close the gap faster, and the merge above is the plan to end the drift. The box-specific
   bits (facility.json, UUIDs, hostnames) stay uncommitted/placeholdered, never on master.
3. **/join policy** — `decided`: **passphrase (merge #40)**. Public contributors must present the
   SRT passphrase; the /join QR embeds it so legit scanners stay one-tap. Approval-first lobby
   is a possible later addition, not now.
4. **Apache copyright holder (#39)** — `decided`: **Guy Cochran** (individual).
5. **Naming the hostname + Jonas** — `decided`: remove the live hostname from the repo; drop the
   **employer tag** on the EFA changelog line; keep the open-source **mxl-fabrics-proxy** credit
   to Jonas Ohland (crediting public OSS is fair). Public IBC-vendor mentions (Riedel/Matrox/etc.
   in the awards recap, sourced to trade press) stay — they're public record, not private info.

### Actions already taken (Oct 9)
- **Hostname leak (#41)** — `fixed`: `docs/index.html` and `docs/what-are-grains.html` no longer
  hardcode the live server hostname (placeholder `YOUR-SWITCHER-HOST`; the health page honors a
  `window.MXL_HEALTH_API` override). The live mxlswitcher.com `public/` copies keep the real URL
  (box-specific, served from the deploy target, not committed to master).
- **Control API hardening** — `fixed`: mediamtx's API now binds `127.0.0.1:9997`
  (`MTX_APIADDRESS`) instead of `*:9997`. With `--network host` the default exposed an
  unauthenticated control API on the LAN; the Core reads it over localhost, so nothing breaks.
  Applied to quickstart.sh **and** the live box. (Other control ports — Core :3100, test-gen,
  encoder, selector, keyer — were already bound to 127.0.0.1.)
- **Employer tag (#37 changelog)** — `fixed`: reworded the EFA entry to state the technical cause
  (Nitro v3 vs v4) without naming an individual/employer. OSS proxy credit retained.

### Still open — your #37 notes (`needs-work`, holding the merge)
- NMOS routing still ignores the flow ID; domain ID isn't a proper ID; the ADR over-claims both
  as done; the soak check reports a stale result as a failure; PR is ~1,600 lines.
  → These are real. We'll address them on #37 before it merges (after #38/#40). Leaving #37
  `open` until then.

### Open question back to you
- On **#40**, do you want the passphrase to also gate the **multiview visibility** (right now
  the public multiview shows every guest tile), or is passphrase-to-publish enough and
  everyone-can-see is fine for the demo? Not decided yet — flagging it per your note.

## Oct 9 (later) — two more decisions from Guy

- **Multiview visibility (#40 sub-question)** — `decided`: **passphrase-to-publish is enough;
  everyone-can-see is fine.** The public multiview showing every guest tile is acceptable for
  the demo. No visibility gating needed.
- **Core-vs-Skin leak sweep** — `fixed` (new PR #42): Guy's principle is that the Core is for
  everyone and OUR deployment specifics belong in a Skin or stay private. The runnable Core
  (backend/scripts/tools) was defaulting env vars to our hosts (prodbots.com, *.cochran.cloud).
  PR #42 neutralizes those defaults (localhost / YOUR-*-HOST placeholders), still overridable
  via MXL_* env. `docs/` (the mxlswitcher.com site) keeps its OHG bylines — that's our own
  publication, correctly ours. Suggest merging #42 early (small, master-based, no conflicts).
