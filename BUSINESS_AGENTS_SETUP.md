# Forma business agents and operations monitoring

The five new agents are Software QA, Quotation Assistant, Client Onboarding, Revenue Watchdog and Sales Journey Orchestrator. Their workspaces share user-owned projects. Existing LinkedIn, YouTube and Naukri integrations, schedules and data are preserved. No live provider actions or hosting provisioning are performed during installation.

## Local installation and startup

```sh
python3 -m venv --system-site-packages .venv-naukri
.venv-naukri/bin/python -m pip install -r requirements-business.txt
PLAYWRIGHT_BROWSERS_PATH=.forma/naukri-browsers .venv-naukri/bin/python -m playwright install chromium
.venv-naukri/bin/python start_local.py
```

`start_local.py` starts the control service and a separate monitoring dispatcher. Foreground alternative: run `server.py` and `monitoring_worker.py` in separate terminals. The dispatcher monitors heartbeats and sends configured admin alerts; it never executes business tasks. A deliberately stopped control service records a stopped heartbeat. Sudden power loss, network failure or a stopped monitor cannot guarantee email delivery. Oracle systemd units restart crashed processes.

No business worker is silently enrolled or started. In Projects, create a project, choose agents, configure facts and connect services. All new schedules and external rules start off. To enroll a worker for an existing account:

```sh
.venv-naukri/bin/python business_worker.py --enroll-local --user YOUR_ACCOUNT_EMAIL --browser
.venv-naukri/bin/python business_worker.py
```

Enrollment creates a private `0600` `.forma/business-worker.json` and executes no tasks. Starting the worker may execute queued tasks, enabled schedules and approved/rule-authorized external actions. Use separate `--config` files for additional users. `--once` processes at most one task or external action. Stop with Ctrl+C or SIGTERM for a planned worker shutdown. Do not start a worker against live authorized queues merely to test connectivity.

For a remote browser-only worker, create credentials in Projects → Connections, save the returned configuration locally with mode 0600, and run `business_worker.py --config PRIVATE_CONFIG --remote-browser`. Only the owner's leased browser task receives its authorized sites and test credentials; email, Google refresh tokens and GitHub tokens stay on the control host. Business provider tasks run in the co-located worker using the control database. Worker revocation takes effect at claim and result-commit boundaries.

## Project setup and workflows

- Configure customer, currency, approved catalogue, tax/discount rates and requirements. Quote prices are never invented. Quantities may be supplied per run or in the project requirements. Unsupported catalogue items produce a needs-input result.
- Onboarding checklists can track completion, required items, owners, deadlines and document references. Use detailed checklist configuration for optional fields. Completion remains owner-supplied; the agent reports readiness without inventing approvals.
- Sales Journey qualifies only structured project requirements (`scope` and `timeline`) and can import the latest twenty messages from the selected email folder for owner review. Messages become private enquiry artifacts; they do not automatically change prices, recipients or acceptance. After reviewing an enquiry, update project requirements before running the quotation flow.
- Record customer acceptance using the explicit acceptance form. This queues onboarding exactly once for that quotation. Follow-up scheduling requires an enabled exact-destination `followup_v1` email rule and skips accepted quotations.
- Browser journeys use up to three explicitly authorized origins and five journeys per site. Steps: `goto` with a relative path, `fill` with an accessible label and `value_key`, `click` with accessible role/label and `safe: true`, `expect_text`, and `expect_url` with an expected path.
- Store test credentials in a Test account connection, not project configuration. QA uses fresh ephemeral browser contexts per journey and viewport; journeys can include login steps using those test values. Screenshots mask form fields; sanitized step traces exclude cookies, request bodies and test values.
- All browser requests are limited to the approved origins with pinned host resolution. Explicit loopback sites require `allow_local`; private network and cloud metadata addresses remain blocked. Third-party assets are blocked and can appear as network failures; include another explicitly authorized origin only where appropriate. Mutation requests are restricted to test/sandbox/login/signin/auth URL paths. Known payment/destructive paths and clicks are blocked. Real checkout transactions and destructive testing are unsupported.
- Watchdog defaults to six-hour checks; no schedule runs until enabled. Only failure/recovery transitions generate incident notifications and QA handoffs. Repeat failures remain in run history without repeated incident notifications.
- Use Projects → Approvals & actions for exact-recipient email, GitHub issues, Google documents/folders or Sheets exports. Rules select a fixed action, destination, report template and rolling daily limit. Changed projects invalidate prior approvals. Unknown external outcomes are held for inspection and never automatically replayed.

Example journey:

```json
[{"id":"homepage","site":"https://your-owned-test-site.example","authorized":true,"steps":[{"action":"expect_text","text":"Welcome"}]}]
```

Each agent has a separate workspace and runs independently. Sales Journey delegates quotation/onboarding; Watchdog delegates reproduction to Software QA. Handoffs stay within one user's project, with five depth levels and twenty tasks per workflow. Two business tasks and one browser task can execute concurrently.

## Email, Google and GitHub

**Email:** Projects → Connections → Email. Enter sender email, username, provider-issued app password and IMAP/SMTP TLS settings. Gmail presets use smtp.gmail.com:465 and imap.gmail.com:993. Provider account policy may require two-step verification and may not offer app passwords. Import only the configured folder/label. Import uses message UID/UIDVALIDITY deduplication and skips oversized messages and attachments. Credentials are encrypted and never returned. Blank replacement secrets keep saved values.

**Google:** Admin → Business integrations. Configure a separate OAuth web client, client secret and exact callback `/api/connections/google/callback`. Enable Drive, Docs, Sheets and Picker APIs. Configure a restricted browser Picker key and the numeric Cloud project number. Users sign in via Projects → Connections and choose specific files in Google Picker. The Picker receives a short-lived, drive.file-scoped access token for its standard browser selection flow; refresh tokens and the client secret remain server-side. Restrict the browser key according to Google's current documentation. This Google connection never alters YouTube authorization.

Drive scope is `drive.file`, not whole-Drive access. Google Picker grants access to selected files. App-created files are also accessible. Add selected IDs to the project to read supported Docs/Sheets during onboarding; other file types appear as metadata. Imported private contents stay local. Exports create files in an explicitly selected folder or `root`. Google testing-mode grants can expire after seven days; reconnect when requested. Public OAuth verification and provider configuration remain deployment prerequisites, not features the application can bypass.

**GitHub:** store a fine-grained token and explicit `owner/repository` allowlist. Enable issue creation only if required. A project action still needs a matching rule or exact-content approval.

**Gemini:** uses the existing admin key and verified no-billing confirmation. Only the manually authored, explicitly consented project brief goes to Gemini. No email or Google document is automatically routed into that field. Business calls have a separate UTC daily budget. Template workflows do not require AI access; quotas cause a held task without retries or paid fallback.

## Admin incidents and email alerts

Open Administration → Errors & Incidents. Configure the dedicated admin recipient and enable alerts. Configure the sender/app password in the existing Email notifications integration. Both settings are required; per-user business email credentials are not used for admin alerts.

Every operational occurrence gets an error ID, incident reference, source location, sanitized stack frames and correlation references. No raw exception message, traceback source lines, local variable values, request payloads, tokens, email bodies or resume text is logged. Search by error/request/task references and filter by agent, component, status, severity, time and delivery. Admins can acknowledge, resolve, reopen and export sanitized records.

The monitor attempts the first incident email promptly and groups repeats for fifteen minutes. It includes the error reference, component, operation, location, impact and investigation steps. Suggestions are generic diagnostic guidance, not an AI claim of root cause. Confirmed pre-delivery failures retry after one, five and fifteen minutes. Ambiguous delivery becomes unknown; inspect the mailbox before any manual resend. Mail/logging failures never recursively send alerts. A SQLite failure uses rotating sanitized JSON logs and a bounded 1,000-record local spool for later reconciliation. Unresolved incidents are retained; resolved incidents expire after thirty days. QA evidence expires after seven days.

## Backup, restore and Oracle readiness

`forma_backup.py` uses a consistent SQLite snapshot and streams an AES-GCM authenticated encrypted archive containing the database, server encryption key and retained private state. The separate backup encryption key is not included in the archive. Keep it offline/separate and protect both files; loss of this key makes recovery impossible.

```sh
.venv-naukri/bin/python forma_backup.py --backup /PRIVATE_PATH/forma-backup.enc --key-file /SEPARATE_PRIVATE_PATH/backup.key
.venv-naukri/bin/python forma_backup.py --restore /PRIVATE_PATH/forma-backup.enc --destination /EMPTY_RESTORE_PATH --key-file /SEPARATE_PRIVATE_PATH/backup.key
```

Restore authenticates before extracting, rejects traversal/symlinks/duplicate entries and refuses a nonempty destination. Restored schedules are preserved exactly, not automatically paused or enabled. Restore does not start services. For host migration, agree a maintenance window and stop all writers first; the tool never silently changes schedules. Restore the database and key to paths configured by `FORMA_DB` and `FORMA_SECRET_KEY_FILE`, place retained `state/` under `.forma`, and inspect worker URLs and local process IDs before starting services. Browser binaries and old backup archives are excluded and should be reinstalled separately.

`deploy/oracle` includes the existing control service plus business-worker and monitoring units. Use Ubuntu, a compatible Python/Chromium build, HTTPS, private environment files, writable `.forma`, and the same database/key on the control host. Set `PLAYWRIGHT_BROWSERS_PATH=/opt/forma/.forma/naukri-browsers`. Remote browser workers remain useful for interactive local login. Measure memory/CPU with actual workflows and do not run heavy YouTube rendering and browser jobs beyond host capacity. No cloud resources, paid services or public launch are provisioned here. Oracle availability/reclamation and provider quotas prevent an uptime guarantee.

## Verification

Run `python3 -m unittest discover -s tests -v` and JavaScript syntax checks. Browser fixture tests require the installed Chromium and local socket access. Tests use temporary databases and fake provider responses; real browser tests visit only their explicitly authorized local fixture website. Live email, Google, GitHub and Gemini execution requires real configured connections and explicit user rules.
