# CourseForge Studio: hosting it on the school network

Status: design for a future phase. The local single-instructor build is what
exists today. Nothing here changes the local build except the seams in
section 11, which the local code already follows.

## 1. Goals, non-goals, threat model

**Goals.** One codebase, two deployments. Instructors sign in with their school
identity, the tool acts on Canvas as that instructor, every Canvas write is
confirmed and audited, student data stays on school infrastructure except the
pseudonymised text sent to Anthropic, and IT can run it with Docker Compose and
a nightly backup.

**Non-goals.** Not internet-facing. No student-facing UI. No replacement for
SpeedGrader, Ally, or UDOIT. No general chat with Claude. No storing anything
Canvas already holds longer than a cache TTL.

| Asset | Adversary | Control |
|---|---|---|
| Canvas OAuth tokens (read every course the instructor teaches) | Compromised instructor account; DB dump; curious admin | Envelope encryption with per-row data keys; master key in a Docker secret or KMS, never in the DB; tokens scoped by developer key; revocation on logout |
| Anthropic API key | Anyone who can read the browser, logs, or `docker inspect` | Lives only in app and worker as a secret file; never in a response, log, or client bundle |
| Student submissions, grades, pseudonym map | Anyone on the school network; another instructor | SSO required; course membership checked on every request from Canvas enrollments, not a local role table; map encrypted; default purge window |
| Live Canvas courses | A stale tab, a replayed request, a prompt-injected model, a buggy new button | Server-side confirm gate bound to user and session; fixed operation vocabulary; audit with before and after |
| Worker host | Malicious student file (PDF parser bug, `.blend` with Python, docx XXE) | Non-root worker, read-only image, no shell tools the job does not need, egress allowlist, Blender flags already in place |
| Anthropic as processor | Contractual, not technical | Commercial terms, zero-data-retention where the model tier permits, pseudonymisation by default (section 9) |

## 2. Architecture

```
                 school network only (no inbound from the internet)
 instructor browser
   | https, school-issued cert
   v
+------------------+    +----------------------+    +----------------------+
| reverse proxy    |--->| app  (FastAPI/uvicorn)|<-->| postgres 16          |
| caddy or nginx   |    | OIDC login, routes,   |    | drafts, jobs, audit, |
| TLS, headers,    |    | confirm gate, enqueue |    | encrypted tokens     |
| request limits   |    +----------------------+    +----------------------+
+------------------+          |  presigned URLs              ^  FOR UPDATE SKIP LOCKED
                              v                              |
                     +----------------------+    +----------------------+
                     | minio (S3 API)       |<-->| worker  x N          |
                     | attachments, renders,|    | 8 grading lanes,     |
                     | original + fixed PDFs|    | PDF ProcessPool,     |
                     +----------------------+    | tesseract, veraPDF,  |
                                                 | Temurin JRE          |
                                                 +----------------------+
                                                            |
                                                            v
                                                 +----------------------+
                                                 | egress proxy (squid) |
                                                 | explicit allowlist   |
                                                 +----------------------+
                                                   |          |         |
                          <school>.instructure.com |  api.anthropic.com  |  school IdP
                          + the file hosts Canvas redirects downloads to
```

**App server: move to FastAPI and uvicorn for the hosted build.** The stdlib
`http.server` has no auth middleware, no request-scoped identity, no ASGI, no
SSE, and one process. The seam that matters is not the HTTP layer but the `App`
class in `courseforge/server.py`, which already takes plain arguments and
returns dicts, and the route table in `courseforge/routing.py`, whose handlers
take a `Request` and return dicts. Hosted routes become thin FastAPI functions
that resolve the signed-in user and call the same handlers. The local build
keeps the stdlib server; the two transports share everything else.

**Jobs: a Postgres-backed queue, not Redis, RQ, or Celery.** Volume is dozens of
jobs a day. A `jobs` table polled with `SELECT ... FOR UPDATE SKIP LOCKED` every
second gives IT one fewer stateful service to run and back up, and the job
record, progress and log already need to live in Postgres for the UI and the
audit trail. Workers are separate processes (`python -m courseforge worker
--queue default`), one job at a time each; scale by adding containers. Inside a
job nothing changes: grading still fans out to eight API calls through a
`ThreadPoolExecutor`, and the PDF engine keeps its `ProcessPoolExecutor`. Two
container notes for that pool: set `multiprocessing` to `spawn`, and size
`/dev/shm` (Compose `shm_size: 1g`) or the 300 dpi renders will fail. Progress
goes to the DB throttled to twice a second; cancellation is a
`cancel_requested` flag the job polls between students or files.

**Object storage.** Pilot on a single host can use a bind-mounted volume shared
by app and worker. With a second worker host, use MinIO. Either way the code
sees one `store.files` interface (`put`, `get`, `delete`, `presign`) keyed
`{course}/{area}/{sha256}`, and the browser fetches attachments and renders
through short-lived presigned URLs rather than through the app.

**Tools.** Tesseract, veraPDF and the JRE go in the worker image, not a sidecar.
The engine calls them as subprocesses on local paths; a sidecar would need an
RPC layer for a 0.5 s per file operation. One worker image with everything, jobs
routed by queue name so PDF jobs can be pinned to bigger hosts later.

**Egress.** Docker networks cannot filter by hostname, so app and worker get
`HTTPS_PROXY` pointing at a squid container with an allowlist: the Canvas host,
the file hosts Canvas redirects downloads to (capture the real set during the
pilot), `api.anthropic.com`, `raw.githubusercontent.com` (the humanizer skill
refreshes itself from there once a day; refused, it keeps the copy it has), and
the school IdP. Everything else is refused and logged. The client already refuses to send the token anywhere but the Canvas
host (`courseforge/canvas_policy.py`); the proxy is the second layer.

## 3. Identity

| Path | What it gives | Cost | Verdict |
|---|---|---|---|
| OIDC to the school IdP (Entra ID or Okta) | Who is signed in; group claims for an admin role | Small; `authlib` in Python | Recommended for login |
| SAML to the school IdP | Same, if IT only offers SAML | Medium | Fallback for login |
| Canvas OAuth2 developer key | Acting as the instructor with a scoped, refreshable token; no pasted personal tokens | A Canvas admin creates the key once | Recommended for Canvas access |
| Admin-issued personal tokens pasted once | Same as the local build, stored encrypted | Zero admin work | Fallback if the developer key is refused |
| LTI 1.3 launch from course navigation | Identity plus course context | Canvas cloud must reach the tool, which conflicts with "school network only" | Stretch, college phase |

Recommendation: OIDC for who you are, Canvas OAuth2 for what you may touch. On
first login the app sends the instructor through the Canvas OAuth consent
screen, stores the access and refresh tokens encrypted, and records
`canvas_user_id` from `/users/self`. Authorization to a course is checked
against live Canvas enrollments, cached for minutes, never from a local role
table. One pilot risk to verify: the post-policy and Post/Hide features use
GraphQL, and Canvas scoped developer keys are documented as REST scopes. If the
scoped token cannot reach GraphQL, either run the key unscoped with the app's
own endpoint allowlist (`canvas_policy.py`), or move those two features to REST.

## 4. Data model

```sql
create table users (
  id uuid primary key default gen_random_uuid(),
  sso_subject text unique not null, email text not null, display_name text not null,
  role text not null default 'instructor',  -- instructor | admin
  created_at timestamptz default now(), last_login_at timestamptz);

create table canvas_accounts (
  user_id uuid primary key references users(id) on delete cascade,
  canvas_user_id bigint not null, canvas_host text not null,
  access_ct bytea not null, refresh_ct bytea, wrapped_dek bytea not null,
  key_version int not null, scopes text[] not null,
  expires_at timestamptz, revoked_at timestamptz);

create table courses (canvas_course_id bigint primary key, name text, course_code text,
  term_code text, synced_at timestamptz);

create table jobs (
  id uuid primary key, kind text not null, queue text not null default 'default',
  owner_id uuid not null references users(id),
  canvas_course_id bigint, canvas_assignment_id bigint,
  state text not null check (state in ('queued','running','done','error','cancelled')),
  args jsonb not null, result jsonb, error text,
  progress_done int, progress_total int, message text,
  log jsonb not null default '[]',            -- last 200 lines, pseudonyms only
  cancel_requested boolean default false, locked_by text, locked_at timestamptz,
  created_at timestamptz default now(), started_at timestamptz, finished_at timestamptz);
create index jobs_pick on jobs (queue, created_at) where state = 'queued';

create table grading_drafts (
  canvas_course_id bigint, canvas_assignment_id bigint, owner_id uuid references users(id),
  draft jsonb not null,                       -- draft.json, unchanged shape
  instructions text,
  pseudonym_map_ct bytea, wrapped_dek bytea, key_version int,
  updated_at timestamptz, purge_after timestamptz,
  primary key (canvas_course_id, canvas_assignment_id));

create table submission_cache (
  canvas_course_id bigint, canvas_assignment_id bigint,
  submissions jsonb, extracted jsonb, fetched_at timestamptz, purge_after timestamptz,
  primary key (canvas_course_id, canvas_assignment_id));

create table blobs (key text primary key, sha256 bytea, bytes bigint, content_type text,
  canvas_course_id bigint, canvas_assignment_id bigint, created_at timestamptz,
  purge_after timestamptz);

create table remediation_runs (id uuid primary key, owner_id uuid, canvas_course_id bigint,
  kind text, job_id uuid references jobs(id), summary jsonb, created_at timestamptz);

create table pdf_files (id uuid primary key, run_id uuid references remediation_runs(id),
  canvas_file_id bigint, display_name text, folder_id bigint,
  lane text, state text, verify jsonb, compliance jsonb, queue_reason text,
  original_key text, fixed_key text, updated_at timestamptz);

create table alt_text (image_sha256 bytea primary key, alt text not null,
  source text check (source in ('model','human')), author_id uuid, created_at timestamptz);

create table assistant_sessions (id uuid primary key, owner_id uuid, canvas_course_id bigint,
  mode text, model_id text, state text, cost_usd numeric(10,4) default 0,
  started_at timestamptz, ended_at timestamptz);
create table assistant_events (id bigserial primary key, session_id uuid, seq int,
  kind text, payload jsonb, created_at timestamptz);
create table assistant_permissions (id uuid primary key, session_id uuid, tool text,
  summary text, fingerprint text,
  decision text check (decision in ('pending','allow','deny','expired')),
  decided_by uuid, asked_at timestamptz, decided_at timestamptz);

create table confirm_tokens (token_hash bytea primary key, user_id uuid not null,
  session_id text not null, kind text, fingerprint text, summary text,
  created_at timestamptz, expires_at timestamptz, consumed_at timestamptz);

create table audit_log (id bigserial primary key, at timestamptz default now(),
  actor_id uuid, canvas_user_id bigint, canvas_course_id bigint,
  action text, kind text, target jsonb, before jsonb, after jsonb, fingerprint text,
  canvas_status int, canvas_request_id text, job_id uuid, ip inet);

create table llm_usage (id bigserial primary key, at timestamptz, user_id uuid,
  canvas_course_id bigint, job_id uuid, model_id text,
  input_tokens int, output_tokens int, cache_read_tokens int, cache_write_tokens int,
  cost_usd numeric(10,6));
```

**Stays in Canvas:** roster, submissions, attachments, the gradebook, post
policies, announcements, quiz settings, course content. The DB holds caches of
these with `purge_after`, never the record of truth. **Must be in the DB:**
drafts (scores, rationales, comments, curves, review flags), the pseudonym map,
job records, PDF run state, alt text decisions, assistant transcripts and
permission decisions, audit, usage. The Canvas user-files handoff
(`handoff.py`) is switched off in the hosted build; Postgres is the save point.

**Retention.** Submission cache, blobs and extracted text: purge 30 days after
the last access or at term end plus 30 days, whichever is first. Drafts and
pseudonym maps: purge at term end plus 90 days, configurable per college
policy. Audit log and `llm_usage`: keep 3 years; they carry Canvas ids and
pseudonyms, not names. A nightly `retention` job enforces this and writes what
it deleted to the audit log. An instructor can purge an assignment early from
the UI.

## 5. Secrets and crypto

- **Master key.** Use the school's KMS if IT runs one (Azure Key Vault is
  likely given an Entra tenant). Otherwise a 32-byte random key in a Docker
  secret file mounted at `/run/secrets/cf_master_key`, mode 0400. Not an
  environment variable: those show in `docker inspect`, `/proc`, and crash reports.
- **Envelope encryption.** Each `canvas_accounts` and `grading_drafts` row gets
  its own data key. Plaintext is sealed with AES-256-GCM using the data key and
  an AAD of `table:user_id:purpose`; the data key is wrapped by the master key
  and stored beside it with `key_version`. Rotation re-wraps data keys under the
  new master key in one pass without touching ciphertext. Library: `cryptography`.
  Locally, `courseforge/secrets.py` (DPAPI) is the `secrets` backend this replaces.
- **API key.** `ANTHROPIC_API_KEY` is a Docker secret read by app and worker at
  startup. No route returns it, `health()` reports only "present", and the
  browser never talks to Anthropic.
- **Logs.** Structured JSON to stdout. A logging filter applies the pseudonym
  patterns to every record and job logs use `S-004`, never names. Prompt bodies
  and submission text are never logged; a failed call logs the request id and
  token counts.
- **Session cookies.** `Secure`, `HttpOnly`, `SameSite=Lax`, signed with a key
  that is also a Docker secret, 8 hour idle timeout.

## 6. Claude in the hosted build

`courseforge/claude_api.py` already implements the same `run(prompt, model,
timeout_s, system, expect_json, images, on_activity) -> ClaudeResult` as
`claude_cli.py`, behind the `courseforge/llm.py` facade. Setting
`"llm_backend": "api"` in config switches every call.

| Local (`claude_cli`) | Hosted (`claude_api`) |
|---|---|
| `--model opus\|sonnet\|haiku` | One mapping dict, overridable in config (`api_models`) |
| `--append-system-prompt` | `system=[{"type":"text","text": style + rubric + description, "cache_control": {"type":"ephemeral"}}]`. The block is identical for every student in a batch, so the second student onward reads it from cache. Verify `usage.cache_read_input_tokens` is nonzero |
| `expect_json` plus `parse_json_ex` repair | `output_config={"format": grading_schema}` structured outputs; keep the repair path as fallback for prose calls |
| stream-json image blocks | The same base64 image blocks |
| `on_activity` from stream-json | `client.messages.stream()` events mapped to the same `{"phase","chars"}` dict |
| cost in the CLI envelope | Computed from `usage` and a price table; written to `llm_usage` |
| `timeout_s` | `client.with_options(timeout=timeout_s, max_retries=2)` |

Thinking is adaptive by default on the current models; expose `effort` in
config (`medium` for per-student grading, `high` for the class summary and
teaching plan). Do not offer the Fable tier in the hosted model list: it is not
available under zero data retention, which is the setting IT will ask for.

**Cost and rate controls.** A process-wide semaphore keeps `grading_concurrency`
at 8 per worker. `llm_usage` feeds a soft per-user daily budget and a per-course
term budget, both configurable; over budget, a job pauses with a plain message
rather than failing. Overnight grading of a large section can use the Batches
API at half price; an optimisation for the department phase.

**The Assistant.** Three options for the agentic per-course session:

| Option | What runs | Fidelity to the local Assistant | Risk and cost |
|---|---|---|---|
| A. Structured jobs only | Fixed vocabulary: grade, remediate, restyle, schedule edits, announcements | Low; no free-form course-wide requests | Lowest. Nothing executes that was not written by us |
| B. Tool Runner with a closed tool set | Messages API tool use in the worker. Tools are typed wrappers over the toolkit (dump, restyle, dry-run push, push). No shell, no filesystem. Any writing tool returns `needs_confirm` and the session parks until the instructor clicks Allow | Medium-high; same Allow/Deny rhythm, same audit | Medium. The tool surface is the allowlist, so the command classifier becomes unnecessary |
| C. Claude Agent SDK in a sandbox per session | The Claude Code harness in a rootless container: read-only image, course folder volume, CPU and memory limits, network only to the egress proxy, killed at session end. `courseforge/assistant/gate.py` ported as a PreToolUse hook talking to the app. The Canvas token never enters the container | Full | Highest. Arbitrary shell next to student files, a second sandbox technology for IT to review |

Recommendation: A for the pilot, B for the department phase, C only if B proves
too narrow and IT signs off on the sandbox design.

## 7. The confirm gate with many users

`ConfirmGate` keeps its interface (`courseforge/confirm.py`) and gains a
backend. The DB backend stores `sha256(token)`, the user id, the browser session
id, kind, fingerprint and `expires_at` (ten minutes, unchanged). Spending is one
atomic statement:

```sql
update confirm_tokens set consumed_at = now()
 where token_hash = $1 and user_id = $2 and session_id = $3
   and consumed_at is null and expires_at > now()
 returning kind, fingerprint;
```

No row means stale; a row with the wrong fingerprint means "different change",
and the token is spent either way, matching the pop-before-check behaviour of
the in-memory gate. Binding to user and session means a token minted in one
browser cannot be spent from another, and a leaked token is worthless after ten
minutes. Every write appends to `audit_log`: actor, the Canvas user it acted as,
course, kind, the fingerprint, `before`, `after`, Canvas status and request id,
and the job id. Names are never stored; the UI resolves Canvas ids on display.

## 8. Deployment mechanics

**Compose services:** `proxy` (Caddy with the school certificate), `app`
(uvicorn, 2 workers), `worker` (replicas 2, `shm_size: 1g`), `postgres:16`,
`minio`, `egress` (squid), and a one-shot `migrate` that runs `alembic upgrade
head` and that `app` and `worker` depend on.

| Variable | Purpose |
|---|---|
| `CF_BACKENDS` | `llm=api,store=postgres,auth=oidc,secrets=db,jobs=pg` (maps to `Config.backends`) |
| `CF_DATABASE_URL` | Postgres DSN; password from a secret file |
| `CF_S3_ENDPOINT`, `CF_S3_BUCKET` | MinIO; credentials from secret files |
| `CF_OIDC_ISSUER`, `CF_OIDC_CLIENT_ID` | School IdP; client secret from a secret file |
| `CF_CANVAS_HOST`, `CF_CANVAS_CLIENT_ID` | Developer key id; secret from a secret file |
| `CF_MASTER_KEY_FILE` | `/run/secrets/cf_master_key` or a KMS key URI |
| `ANTHROPIC_API_KEY_FILE` | Secret file path; the code reads it once |
| `CF_MODEL_MAP` | Overrides for the opus/sonnet/haiku mapping |
| `CF_RETENTION_DAYS`, `CF_DRAFT_RETENTION_DAYS` | Section 4 defaults |
| `HTTPS_PROXY`, `NO_PROXY` | Egress proxy; `NO_PROXY` covers postgres and minio |

**Health.** `/healthz` returns 200 when the process is up. `/readyz` checks
Postgres, MinIO, and a HEAD to the Canvas host through the egress proxy; it
reports the API key as present or missing without calling Anthropic. Workers
write a heartbeat row every 30 s that the admin page shows.

**Backups.** `pg_dump` nightly to storage IT owns, encrypted, 30 daily and 12
monthly. MinIO bucket mirrored the same way. The master key is backed up
separately and by a different person; a database backup without it is
unreadable by design. Do one full restore drill during the pilot.

**Migrations.** Alembic with SQLAlchemy Core models. Every schema change ships as
a revision; the `migrate` service applies it before the app starts.

**Monitoring.** JSON logs shipped by the Docker log driver to IT's collector.
`/metrics` in Prometheus format: jobs by state, queue wait, Claude tokens and
cost per hour, Canvas 4xx and 5xx counts. No names, no prompt text, no
submission text anywhere in either.

**Upgrade path.** Images tagged with the git tag; `docker compose pull &&
docker compose up -d`; the `migrate` service is the only step that touches data.
Roll back by pinning the previous tag; migrations are additive within a release.

## 9. Compliance checklist for IT

- **FERPA.** Submissions and grades are education records. Anthropic is a
  contractor under the school official exception: written agreement, use limited
  to providing the service, no redisclosure, records under the school's direct
  control. Ask legal to confirm the Anthropic commercial terms and a
  zero-data-retention configuration meet that bar, and record the answer here.
- **What reaches Anthropic.** Pseudonymised submission text, assignment
  description, rubric, instructor instructions, Blender contact sheets, PDF
  figure crops for alt text, course page HTML, and aggregate statistics for the
  class summary. **What never leaves the school:** names, emails, SIS and login
  ids, the pseudonym map, video, raw attachments, grades tied to identity.
  Pseudonymisation is on by default and cannot be turned off by an instructor
  in the hosted build. Free-text and in-image names can survive best-effort
  scrubbing; say so.
- **Anthropic data handling.** Commercial API data is not used for training by
  default. Request zero data retention for the organisation. Cite the current
  Commercial Terms and the ZDR addendum by URL and date here once obtained.
- **PyMuPDF (AGPL-3.0).** A hosted service is network use, so the "internal use,
  no server" reading in `docs/THIRD-PARTY-NOTICES.md` no longer applies. Decide
  before the department phase: (1) license the repository AGPL-3.0 and put a
  source link in the footer, which satisfies section 13 at no cost but ends the
  MIT licence; (2) buy an Artifex commercial licence; (3) replace PyMuPDF with
  `pypdfium2` (Apache/BSD) for rendering and keep `pikepdf` (MPL) for structure,
  a real rewrite of the verify and figure-clip paths; (4) isolate PyMuPDF in a
  separate process, a contested reading and not a safe harbour. For the pilot,
  keep PyMuPDF and show the source link anyway.
- **veraPDF** (MPLv2 arm, unmodified, licence text and source link in the
  image), **Tesseract** (Apache-2.0, ship NOTICE), **Temurin** (GPLv2 with
  Classpath Exception, ship the text). Include the files in the worker image.
- **Accessibility of the tool.** Target WCAG 2.1 AA (see `docs/UI-DESIGN.md`
  section 11); axe-core in CI. A remediation tool that fails an accessibility
  audit will not survive the review.
- **Security review items IT will ask for:** dependency pinning with hashes,
  non-root containers, read-only root filesystems, `pip-audit` in CI, a
  documented incident response (revoke the Canvas developer key, rotate the API
  key, rotate the master key, review `audit_log`).

## 10. Phased rollout

| Phase | Who | What ships | Code changes | Effort |
|---|---|---|---|---|
| Pilot | 3 instructors, one VM | Grading, schedule, accommodations, quiz edit, announcements, HTML and document remediation. Structured jobs only. Single host, bind-mounted files | `store.postgres`, `auth.oidc`, `secrets.db`, `jobs.pg`, `confirm` DB backend, `audit_log`, FastAPI routes, Compose, Alembic | 5 to 7 developer weeks plus IT time for the IdP app registration and Canvas developer key |
| Department | 15 to 30 instructors | PDF remediation and alt text at scale, MinIO, multiple workers, retention job, Assistant option B, metrics, restore drill done, accessibility audit, AGPL decision made | Tool Runner assistant, `store.files` on S3, retention, budgets, admin page | 4 to 6 weeks |
| College | All instructors | Two app replicas, Postgres replica or tested point-in-time recovery, per-department budgets, optional LTI 1.3 launch, Assistant option C only if justified | HA config, LTI, sandbox design if pursued | 6 to 8 weeks plus IT security review |

## 11. What the local build does now so this stays cheap

1. **One Claude call site.** Every model call goes through `llm.run()`
   (`courseforge/llm.py`); `claude_cli.py` and `claude_api.py` are the two
   backends. No other module spawns `claude` or parses its envelope.
2. **`Store` and `App.course_dir()` are the only things that know about `data/`.**
   Area work lives under `data/<course>/<area>/`, which maps to S3 keys later.
   `draft.json` stays one document, which maps to one JSONB row.
3. **Confirm gate.** `ConfirmGate.require()` gains an optional principal later;
   callers pass the same arguments they do now.
4. **All Canvas writes go through `App._gate()`.** Areas call it inside their
   job functions. The handoff worker's user-file writes are a separate, opt-in
   class and are feature-flagged off in the hosted build.
5. **Endpoint policy in the client.** `canvas_policy.py` decides what a
   content-scoped client may touch. Hosted, the same module runs unchanged; the
   egress proxy is the second layer.
6. **Jobs are plain functions** taking `log` and returning JSON-serialisable
   dicts. `Jobs.start(kind, fn)` becomes an enqueue; the function body does not change.
7. **No PII in job logs.** Job logs use pseudonyms, Canvas ids, file names.
8. **`App` never touches HTTP types.** Handlers take a `Request` of primitives
   and return dicts; that is what makes the FastAPI layer a set of one-line routes.
9. **Backends in config.** `Config.backends` names the five swappable parts.
10. **`CanvasClient(base_url, token, scope)` keeps its constructor.** Later it
    gains a `token_provider` for OAuth refresh and an audit hook; callers do not change.
