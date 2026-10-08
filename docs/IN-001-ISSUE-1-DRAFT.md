<!-- SPDX-License-Identifier: Apache-2.0 -->
# Draft GitHub Issue for AMWA-TV/in-001 — review before posting

**Where to post:** https://github.com/AMWA-TV/in-001/issues → "New issue"
**Suggested title:** `§6.2 — in practice, "started" didn't tell us if media was actually flowing. Is content-liveness in scope?`

---

## Issue body (copy everything below the line)

---

Hi — first, thank you for opening this up for review. We've been running a small live production that uses an MXL-based switcher (browser → a REST control API → MXL readers/writers, on cloud VMs) with real cameras and remote phone contributors. We're broadcast/production people rather than standards folks, so please take this as field feedback rather than a formal proposal — and tell us if we've misunderstood the intent.

**What happened to us (re: §6.2 state reporting):**

§6.2 says readers/writers expose `started` / `stopped`. In our setup we found that wasn't enough to actually run a show, because a reader/writer could report `started` and be attached, yet no *new* media was arriving — the flow's grain head simply stopped advancing while everything still looked healthy.

The concrete case: our remote-guest inputs get their flows created at startup, so an idle guest slot (nobody connected, no phone streaming) still reported as `started` / "receiving." An operator cutting to that source would have put a dead input on air. We ended up adding our own extra check — treat a source as "live" only if it's attached **and** its grain head advanced within the last few seconds — because "is it started?" and "is fresh content actually flowing?" turned out to be two different questions.

(One more wrinkle we hit: when several sources are composited into one picture, overall "it's moving" can hide a single frozen source among the others, so we found we had to judge freshness per source, not on the combined output.)

**Our question for the working group:**

Is distinguishing *operational state* (`started`/`stopped`) from *content liveness* (is fresh media actually flowing?) something this API is meant to cover, or is that considered the media function's / monitoring's job and deliberately out of scope?

If it is in scope, even a simple per-flow "last grain / last update" timestamp exposed through the status query would have let our controller tell "started but stale" from "started and flowing" — which in practice was the difference between a usable control surface and one we couldn't trust for a live cut.

Happy to share more detail on what we saw if it's useful. Thanks again for the work on this.

---

---

## ALTERNATE: shorter / more casual version (pick whichever feels more like you)

**Title:** `§6.2 — "started" didn't tell us if media was actually flowing. In scope?`

---

Thanks for opening this up for review! We're a production team running a live show on an MXL-based switcher (browser → REST control API → MXL readers/writers on cloud VMs), so this is field feedback from an operator, not a formal proposal — happy to be told we've got it wrong.

Quick thing on §6.2: we found `started` / `stopped` wasn't enough on its own. A reader/writer could say `started` and be attached, but no new media was actually arriving — the grain head just stopped advancing while everything still looked fine. Our clearest example: remote-guest inputs create their flow at startup, so an idle guest slot (nobody streaming) still read as `started` / "receiving." Cutting to it would've put a dead source on air. We had to add our own rule — only call a source "live" if it's attached **and** its grain head moved in the last few seconds.

So the question: is telling "started but stale" apart from "started and flowing" meant to be in scope here, or is that the media function's / monitoring's job? If it's in scope, even a per-flow "last updated" timestamp in the status would've done it for us.

Thanks again — glad to share more if it helps.

---

---

## VERSION 3: your voice, walks the SRT chain end-to-end (your requested version)

**Title:** `§6.2 — a "started" writer isn't always flowing media. Where should liveness live?`

---

Thanks for putting this out for review. We run a live show on an MXL-based switcher — browser front end talking to a REST control API over MXL readers and writers, all on cloud VMs — so this is coming from the operator side, not the standards side. Tell me if I'm reading the intent wrong.

Let me walk you through how a remote guest comes into our show, because that's where §6.2 bit us.

A guest joins from their phone. The phone contributes over **SRT into our first VM** — `srt://<host>:8890?streamid=publish:guest1`. A small ingest process picks that up, conforms it to our domain's format (1080p30, v210), and **writes it into an MXL flow** (a fixed flow ID that maps to the "Guest 1" button on the switcher). From there it crosses our fabric to the production VM where the selector/keyer live, and it's cuttable on air.

Here's the catch. That guest flow and its writer get **created at startup** so the slot is ready the moment someone connects. So before anyone actually streams — phone off, nothing pushing SRT — the writer is already `started` and attached, and the status reads "receiving." Everything in the control API looks healthy. But there's no media: the SRT source isn't sending, so the flow's grain head never advances.

If an operator trusts `started` and builds a shot on that guest, they put a dead source on air. The case that really got us was the M/E: we set up a 2-up of Host + Guest and took it to air, and the Guest half was just **black** — the writer said `started`, the slot said "receiving," but nothing was flowing into it. And because it's a composite, the M/E as a whole still looks alive (the Host side is moving), so nothing downstream flags it. We ended up adding our own rule — only treat a source as "live" if it's attached **and** its grain head actually advanced in the last few seconds — and we had to check it **per source**, not on the combined M/E output, exactly because the moving Host side masks a frozen Guest.

So my question for the group: `started`/`stopped` describes the *writer*, but not whether *media is moving through it* — and operationally those are different questions. Is distinguishing them in scope for this API, or is it the media function's / monitoring's job by design? If it is in scope, even a per-flow "last grain / last updated" timestamp in the status query would've covered us.

Appreciate the work on this — glad to go deeper on any part of that chain if it's useful.

---

## WHY SCREENSHOTS AREN'T NEEDED FOR THIS ISSUE (read before firing up the VMs)

A GitHub issue on a standards repo is a text discussion, not a demo. Reviewers engage with the *question* ("should state and liveness be separate?"), which stands on its own — it's a design question, not a "prove it happened" claim. Spec issues almost never carry screenshots; a screenshot of a UI doesn't validate a requirements argument. If anyone wants evidence, the natural form is a few log lines (which we already have in MXL-NOTES.md), not a picture. **Recommendation: post the text as-is; don't spend ~$3-5 + 30 min spinning the lab back up for images this issue won't use.** Save the VM-up time for the proxy failure-mode A/B (test-plan item #2), which genuinely needs live runs.

---

## Notes for Guy (NOT part of the issue — delete before posting / don't paste this section)

- **Tone check:** this is written as an operator sharing experience and *asking a question*, not telling them what to do. You can't "get this wrong" — it's a true account of what happened on your show, ending in a genuine question. The worst realistic outcome is "thanks, that's handled elsewhere," which is a perfectly fine answer.
- **You can post it in your own words** — feel free to soften or shorten anything. It should sound like you.
- **To post:** you need a (free) GitHub account; click "New issue" on the repo, paste title + body. That's it. No code, no PR.
- If you'd rather I tweak the wording (more casual, shorter, add/remove the compositing wrinkle), just say so.
