# Forma YouTube Studio

## What is available

YouTube Studio is a private built-in agent by default. Administrators can open it from Agent management or the sidebar. Switch Live on only after testing. Ordinary users cannot read its APIs while private. Daily video automation requires an active Pro/Premium entitlement or administrator status; channel analytics can be connected independently.

The dashboard includes Overview, Videos, Audience & discovery, Automation, Competitor Watch, and Settings. It shows cached YouTube reports and their source/timestamps rather than invented performance scores. Optional revenue reports require separate consent. Competitor Watch supports five confirmed public channels and their latest twenty uploads. It neither scrapes nor copies videos. Private analytics are not passed to Gemini.

**No free AI-footage vendor is bundled or verified.** The default provider is None and jobs are blocked. A compatible reviewed adapter must enforce zero spending and permit commercial use. Quota exhaustion holds work; there is no paid fallback and no substitution of local graphics for generated footage. Configured targets are maximums, not guaranteed daily deliveries. Publication, generation, email and analytics are independent states.

## Administrator integration

Open Admin dashboard → Integrations → YouTube.

1. Create a Google Cloud project, enable YouTube Data API v3 and YouTube Analytics API, and create a Web application OAuth client.
2. For local use, register `http://localhost:8000/api/youtube/callback` exactly. For HTTPS hosting, register the matching public HTTPS callback.
3. Configure the OAuth consent screen/test users as required by Google. Add the client ID and secret in Forma; the secret is encrypted and never returned by settings APIs.
4. Users first connect publishing/channel identity. Analytics and revenue permissions are requested separately, and declining them does not remove existing upload access. Tokens refresh when possible; revoked permissions clear associated cached data and require reconnection. Use Google Account permissions to revoke the app itself.
5. Install FFmpeg/FFprobe and `espeak-ng` from your operating system’s official package repository. Verify the installed voice’s commercial-use/license terms, select a standard voice identifier, and confirm its license in the integration form. Do not upload or clone a person’s voice.
6. Configure a reviewed `forma-video-v1` adapter only when an actual free provider has been verified. A vendor URL alone is insufficient; see the protocol below.

Google determines account upload limits, content eligibility, API quota, processing behavior, and actual publication. Forma does not bypass these limits or promise monetization. Long uploads may require channel verification. YouTube’s interpretation of Shorts and analytics can change; maintain the integration against official documentation.

## Local worker

The control service starts its YouTube scheduling loop with the regular Forma server. YouTube generation/upload work runs in a separately registered worker; a green saved schedule without an online worker will not produce videos.

From the project folder, after the web server is running:

```bash
python3 youtube_worker.py --enroll-local
python3 youtube_worker.py
```

Enrollment writes `.forma/youtube-worker.json` with permissions 0600 and refuses to overwrite an existing file. It prints no secret. For a smoke check without a continuous loop, `python3 youtube_worker.py --once` sends a heartbeat and processes at most one eligible video job. **Do not run it against authorized live schedules just to test connectivity:** a queued job can invoke providers and upload. No worker was enrolled or launched against real accounts during implementation.

The laptop must remain awake. The worker runs one video job at a time with bounded FFmpeg memory/threads and a separate maintenance loop for analytics/email. Reports sync every six hours and competitors daily while a selected worker is online. Manual refresh is throttled to once per report every fifteen minutes, with twenty manual requests per user per hour. Views may be delayed; unavailable numbers are not zero. Queued refreshes need a worker.

## Channel onboarding

Complete your content brief, separate Shorts/long topics, audience/language/region, original expertise, boundaries, brand, standard voice, media rights, audience designation, disclosure setting and visibility. Confirm the channel identity.

Set Shorts to 0–15/day and long videos to 0–5/day. Shorts are vertical and at most 180 seconds. Long-video target seconds multiplied by the daily count cannot exceed 14,400 seconds. Actual render duration is measured before upload. Reservations persist across retries and cannot be reset by changing the timezone. A channel with reservations in the last 24 hours cannot switch to a conflicting quota timezone.

The configured times start eligible generation jobs; free footage/rendering may delay actual publication. Missed earlier slots and historical days are not replayed in a burst. Jobs from an expired quota day do not initiate late uploads. Google may finish processing later than upload acceptance. Existing channel videos are not counted as Forma-created videos and cannot reliably be classified as Shorts from duration alone.

Generate a private preview first. Previews never upload. Daily automation remains off until all onboarding checks and publishing authorization are saved. Default visibility is private. Pause stops the next external operation; an in-flight request may already have been submitted.

## Notifications

Open My email notifications in the main navigation. Configure your SMTP/app-password credentials, sender, port and TLS transport. Saving sends nothing. Send a verification code to your primary Forma email and/or Google-account email, then enter it to verify that recipient. Each recipient is notified only after verification and after you enable that recipient in YouTube Settings. Identical addresses are deduplicated.

Email uses your own public SMTP service, not Google upload authorization. Nonpublic SMTP destinations are blocked. Ready, published and action-needed events use an outbox. Failures before submission receive at most three attempts; an uncertain submission is marked unknown and is not resent automatically. Email failure never uploads a duplicate video. A crash during SMTP submission can require manual inspection.

## Free adapter protocol

`provider=forma-video-v1` is an integration contract, not a verified free service. An administrator reviews and hosts or configures an adapter for a real provider. HTTPS, public DNS, pinned connections, certificate validation and no redirects are required. Tokens are sent only to the reviewed adapter host. Artifact downloads must use that same host, so signed vendor/CDN URLs must be proxied by the adapter. No arbitrary code executes in Forma.

All actions use POST JSON under the configured base URL:

- `/capabilities`: request `{"protocol":"forma-video-v1"}`; response must include `protocol: "forma-video-v1"`, `zero_spend_enforced: true`, `commercial_use_allowed: true`, and `free_available: true`. If the adapter cannot guarantee zero spending, it must refuse the request.
- `/generate`: receives `protocol`, `idempotency_key`, `prompt`, `seconds`, `format`, `model`, `max_charge: 0`. Return a durable alphanumeric/underscore/hyphen task `id`. Repeated idempotency keys must resolve to the same task, never a second paid/free generation.
- `/status`: receives `id`; return `state: "pending"`, `"blocked"`, `"failed"`, or `"completed"`. Completed tasks include an HTTPS `url` on the adapter host.
- Artifact GET: MP4 clip, at most 128 MB per segment. Each scene is 1–30 seconds and must be at least the requested duration. Clips are not looped to fill missing footage.

Blocked jobs recheck availability at most hourly once runtime prerequisites are configured. Ambiguous submission/upload results are held, not blindly retried. Provider tasks, generated segments and upload sessions are persisted before advancing. Public API data is never treated as instructions.

## Rendering and upload limits

Narration must fit its scene. FFmpeg composes generated footage with local narration and burned-in captions, plus an SRT and thumbnail. Production narration requires actual `espeak-ng`; the test suite substitutes a labelled test-audio fixture only to verify media composition.

Each stored asset is capped at 512 MB, each segment download at 128 MB, and free-storage checks preserve space for the application. Those operational ceilings can hold large requested videos. Extend storage/transfer limits deliberately before using multi-hour videos; the four-hour setting is a quota ceiling, not a promise that this laptop can render that volume.

Google uploads use persisted resumable sessions and check the existing session before transmitting more bytes. Unknown initialization never starts a fresh upload automatically. After upload, Forma verifies processing and intended visibility. The generated thumbnail is uploaded separately; thumbnail failure does not create another video. Videos remain in the job history with their actual state.

## Analytics, retention and deletion

Reports use YouTube reporting dates (`America/Los_Angeles`), while automation uses the saved schedule timezone. Presets cover 7/28/90 days and custom ranges up to one year. Supported metric combinations vary by report and channel; rejected combinations show an error and preserve prior cache data. Revenue is estimated provider data, not eligibility or guaranteed income. Watch-time minutes are not shown as monetization-eligible public hours.

Competitor data is public-only, refreshed daily, and removed if older than thirty days. Owner caches are also refreshed/removed within thirty days; account authorization is revalidated through successful provider requests. Disconnect deletes tokens and caches and pauses automation. Delete my Forma YouTube data additionally removes jobs/media/notifications locally and never deletes YouTube videos.

Published/preview renders are retained seven days; failed/held artifacts three days. Cleanup preserves job/publication history. Private assets require the owning user’s session, including range requests for video playback. Competitor metrics are not used to calculate viral scores, growth estimates or private earnings.

## Oracle mode and migration

Templates in `deploy/oracle` are examples for an existing server, not automatic provisioning. Use HTTPS and a trusted reverse proxy. Configure `FORMA_PUBLIC_ORIGIN`, `FORMA_DB`, and `FORMA_SECRET_KEY_FILE` via a private environment file; the session cookie becomes Secure on HTTPS.

Before migration, pause all LinkedIn and YouTube schedules, stop the original control service and workers, and wait at least two minutes. `migrate_youtube.py --backup PRIVATE_ARCHIVE.tar.gz` creates a 0600 archive of the entire Forma database, encryption key and retained YouTube media. It refuses active YouTube workers or enabled schedules. Transfer the archive only over your trusted encrypted channel. Restore into an empty directory with:

```bash
python3 migrate_youtube.py --restore PRIVATE_ARCHIVE.tar.gz --destination EMPTY_DIRECTORY
```

Restore does not overwrite existing data, install packages, provision a server, enable schedules or change OAuth callbacks. Protect the archive: it contains all Forma accounts and encrypted integration data plus their encryption key. Retire the old control service after migration; never run two independent copies of the database as live schedulers.

Register a Server worker in Admin → YouTube and save its one-time configuration on Oracle with permissions 0600. Configure the laptop worker to use the same new HTTPS control URL. Both workers use the one control database and private artifact store; no shared filesystem is required across hosts.

Apply Server execution mode only after its worker is online. A pending switch stops new claims, drains existing jobs, verifies target liveness, and increments the execution generation. If the target goes offline while draining, work remains paused until it returns or the admin reapplies the current mode to cancel. No silent failover occurs. An expired lease holds unknown provider outcomes for inspection. A persisted Google upload session or known video ID is recovered by checking that same session/video; it never starts a fresh duplicate upload.

## Verification status

Automated tests mock Google, Gemini, video-provider and mail calls. A separate local FFmpeg fixture test verifies composition. Actual Google OAuth, owner analytics, free-provider generation, standard narration, SMTP delivery and a private YouTube upload require credentials/dependencies and explicit owner testing. Public automation must stay disabled until that testing is complete.
