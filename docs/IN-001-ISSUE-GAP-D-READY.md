# IN-001 §6.2 issue — READY TO POST (Gap D: liveness)

> ✅ **POSTED Oct 1 2026 — AMWA-TV/in-001 issue #6: https://github.com/AMWA-TV/in-001/issues/6**
> DO NOT re-post. Kept for reference / voice of follow-up gaps (B/F).


Post this at **https://github.com/AMWA-TV/in-001/issues → "New issue"** under your own
GitHub account. Paste the title into the title field and everything under "Body" into
the body. Nothing else to do — no code, no PR. Soften to your own voice if you like.

**Strategy:** post THIS one (Gap D) alone first and let it land before filing the other
gaps (B/F/…) as follow-ups — don't drop four issues on a draft spec at once.

---

## Title

```
§6.2 — a "started" writer isn't always flowing media. Where should liveness live?
```

## Body

Thanks for putting this out for review. We run a live show on an MXL-based switcher — browser front end talking to a REST control API over MXL readers and writers, all on cloud VMs — so this is coming from the operator side, not the standards side. Tell me if I'm reading the intent wrong.

Let me walk you through how a remote guest comes into our show, because that's where §6.2 bit us.

A guest joins from their phone. The phone contributes over **SRT into our first VM** — `srt://<host>:8890?streamid=publish:guest1`. A small ingest process picks that up, conforms it to our domain's format (1080p30, v210), and **writes it into an MXL flow** (a fixed flow ID that maps to the "Guest 1" button on the switcher). From there it crosses our fabric to the production VM where the selector/keyer live, and it's cuttable on air.

Here's the catch. That guest flow and its writer get **created at startup** so the slot is ready the moment someone connects. So before anyone actually streams — phone off, nothing pushing SRT — the writer is already `started` and attached, and the status reads "receiving." Everything in the control API looks healthy. But there's no media: the SRT source isn't sending, so the flow's grain head never advances.

If an operator trusts `started` and builds a shot on that guest, they put a dead source on air. The case that really got us was the M/E: we set up a 2-up of Host + Guest and took it to air, and the Guest half was just **black** — the writer said `started`, the slot said "receiving," but nothing was flowing into it. And because it's a composite, the M/E as a whole still looks alive (the Host side is moving), so nothing downstream flags it. We ended up adding our own rule — only treat a source as "live" if it's attached **and** its grain head actually advanced in the last few seconds — and we had to check it **per source**, not on the combined M/E output, exactly because the moving Host side masks a frozen Guest.

So my question for the group: `started`/`stopped` describes the *writer*, but not whether *media is moving through it* — and operationally those are different questions. Is distinguishing them in scope for this API, or is it the media function's / monitoring's job by design? If it is in scope, even a per-flow "last grain / last updated" timestamp in the status query would've covered us.

Appreciate the work on this — glad to go deeper on any part of that chain if it's useful.
