# Adding an agent to Forma

Open **Admin dashboard → Agent management** (also available in the main navigation). New agents start **Private**. Only administrators can see, test or use them. A successful test of the saved version is required to switch **Live** on. Live agents appear in the library and can be run by signed-in members. Turning Live off immediately denies future ordinary-user catalog, workspace and execution requests; an already-running external request may finish. Administrators retain access. Editing an agent returns it to Private and invalidates its test.

LinkedIn Content remains Live for admins and members. Its toggle also gates direct LinkedIn/workspace/media APIs and ordinary users' scheduled publishing, rather than merely hiding a card.

## Hosted agent APIs and webhooks

Use a public HTTPS endpoint on port 443. This can be your own service or a compatible n8n webhook. Forma sends:

```json
{"protocol":"forma-agent-v1","input":"The user's task","context":{"run_id":"generated-run-reference"}}
```

Return HTTP 200 and JSON:

```json
{"output":"The agent's textual result"}
```

Provide any bearer token through the password field. It is encrypted and never returned to the browser; blank on editing keeps it. Do not embed credentials in a URL. Review the external service's privacy and billing settings. Forma cannot infer charges from a URL or token, so the administrator must confirm no usage fees before testing or use. Other frameworks may need a small adapter around their native input/output schema.

HTTPS certificate validation is enabled. Resolved nonpublic/loopback addresses are rejected; the public address is pinned for the request. Redirects are not followed. Only the supplied task and generated run reference are sent: Forma does not forward users' resumes, provider secrets, LinkedIn tokens or other users' records. Output is displayed as escaped text, never executable HTML.

## Python package uploads

Upload a ZIP of at most 256 KB, with root `manifest.json` and `main.py`:

```json
{"protocol":"forma-agent-v1","entrypoint":"main.py","name":"My Agent"}
```

```python
import json, sys
request = json.load(sys.stdin)
print(json.dumps({"output": "Processed: " + request["input"]}))
```

Use the Python standard library; this version does not install pip dependencies. Up to 30 files are supported: Python modules, manifest.json and README.md, with a maximum expanded total of 256 KB. Traversal paths, symlinks, duplicate paths, suspicious compression ratios and unsupported files are rejected. Uploading saves the bytes without executing them.

Execution requires a running local Docker daemon and an already-present `python:3.12-slim` image (override with `FORMA_AGENT_IMAGE`). There are no automatic downloads or image builds. The image ID is resolved before execution. Scripts run in a container as an unprivileged user with a read-only root filesystem, no network, dropped capabilities, no-new-privileges, bounded CPU/memory/processes and a small temporary filesystem. The only host mount is that execution's read-only package and request. No Forma database, encryption key, host directory or provider secret is mounted. There is a 25-second execution timeout and 256 KB captured-output limit; returned output text is limited to 20,000 characters. Container execution is isolation, not a substitute for reviewing hostile code or a hardened public multi-tenant sandbox. Keep uploaded agents administrator-reviewed; public production still needs a dedicated worker/service boundary and operational monitoring.

Agents requiring network APIs or third-party libraries should run in their own hosted environment and use the HTTPS adapter. Raw arbitrary projects are not universally compatible: adapt them to this protocol. Arbitrary code is never imported into the Forma web-server process.

Download the included [example ZIP](examples/agents/text-utility.zip). It counts words; it is a protocol/runtime example, not an AI model. Enter nonsensitive test input, run Test private agent, then enable Live after a successful test. The example was executed successfully in the real isolated runtime without network access.

## Access and run records

Only administrators can register, edit, test and toggle agents. Ordinary members can run tested Live versions only. Run history is queried by authenticated user ID; members cannot read another user's prompts/results. Private agents and their histories disappear from ordinary-user APIs. Per-user limits allow at most three attempts per minute, and three simultaneous generic agent runs globally. Failed runs record status without returning raw provider/container errors or stored tokens.

Generic agents are on-demand adapters in this release. They do not automatically inherit LinkedIn's daily scheduler, publishing permissions, notification lifecycle or agent-specific UI. Add those capabilities deliberately in backend modules. No new outbound email, LinkedIn post, paid service or real hosted API was used to verify this release. Hosted execution was tested with mocks; local package execution was tested with the included harmless example.
