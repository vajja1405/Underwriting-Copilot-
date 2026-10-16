# Security: roles, document permissions, encryption at rest and audit events (Phase 5 B)

Authors: Rahul Vajja and Bhavani Adula (joint project). Code: `src/uwcopilot/security/`. Tests: `tests/security/`.

This is a **demonstration on SYNTHETIC data**, not a compliance program. Nothing here claims that the project meets
any regulation or standard; whether a control like this is needed, and in what form, depends on the institution and
its risk profile. No cloud service is used: everything runs locally with the `cryptography` package.

**Roles are CLAIMED in this demo.** The command line and the agent have no identity provider. A reviewer's role is a
string in the resume payload (`--role`), so every rule below shows how authority is enforced *once a role is known*;
it does not prove who someone is. Each audit event records where the role came from (`role_source: "claimed"`), so a
claimed role is never presented as a verified one. (The website is different: Hugging Face sign-in authenticates the
account and roles come from an operator allowlist, see "Web adoption".)

## 1. What is enforced, what is only available, what is not built

| Control | Status today | Where |
|---|---|---|
| One authorization function, `authorize(principal, action, resource) -> Decision{allowed, reason}` | Enforced in code, against a **claimed** role | `security/rbac.py` |
| The human checkpoint refuses decisions from `model_validator` and `admin` | **Enforced** in the graph (every resume goes through `validate_decision`) | `agent/hitl.py` (first check), `rbac.checkpoint_role_refusal` |
| Guideline authority limits (who may approve or decline what, overrides) | Enforced (unchanged, Phase 5) | `agent/hitl.py` |
| Senior-approval threshold per business line | **Available** in `authorize("approve_file")`; **no runtime path calls it**. At runtime the checkpoint enforces the same numbers from its own copy, `hitl.APPROVAL_LIMITS`. A test checks the two agree, so editing `config/security.yaml` alone changes nothing at runtime (it only fails that test) | `config/security.yaml`, `agent/hitl.py` |
| Packet access lists and hiding document content in the review payload | **Available** in `review_for_principal` / `read_packet_for`; **no caller yet**. The packet intake (Phase 5 A) is not built. The web lender pages exist (`web/routers/lender.py`, `web/services/lender_demo.py`) and build their file view from the review payload without `review_for_principal`. Today that payload holds no packet document text (structured SYNTHETIC files; injection-hit excerpts quote the application's own fields), so nothing is hidden from anyone yet. Both must call `review_for_principal` before the review carries document content (sections 8 and 9) | `security/document_access.py`, `config/security.yaml` |
| Encrypted decision log (Fernet payload, hash chain in clear) | **Available** drop-in for `DecisionLog`; the tests run the real agent with it. `scripts/run_agent.py` still writes plaintext | `security/encrypted_log.py` |
| Encrypted packet store | **Available**; no upload path calls it yet | `security/packet_store.py` |
| Audit events (hash-chained, PII-redacted) | **Available**; the tests build events from a real agent run. The CLI and the web do not write audit events yet | `security/audit.py`, `scripts/verify_audit.py` |
| Identity (proving who a reviewer is) | **Not built** for the CLI and the agent (roles are claimed) | — |
| Model promotion, migration-certificate signing, permission management | The authorization rule exists; **no code path performs these actions yet** (Phase 6 governance should call `authorize` first) | `security/rbac.py` |
| Cloud KMS (envelope encryption, platform key rotation), signed or write-once audit anchors | **Not built** (Phase 7) | — |

"Available" means: written, tested, and usable by any caller, but not yet called by an entry point a user runs.
Sections 8 and 9 say exactly how the agent CLI and the web should adopt each piece.

## 2. Roles and the authorization table

Four access roles (`agent.vocab.ACCESS_ROLES`). Only the first two can ever decide a file (`REVIEWER_ROLES`).

| Action | underwriter | senior_underwriter | model_validator | admin | Why |
|---|---|---|---|---|---|
| `view_file` (the review payload) | yes | yes | no | no | Only people who act on files see them |
| `view_documents` (packet pages, extraction) | if the packet ACL allows | if the packet ACL allows | no | no | Document-level permission, section 3 |
| `run_agent` | yes | yes | no | no | Starting a review is part of handling a file |
| `approve_file` | up to the threshold | yes | no | no | Above `senior_approval_above[line]` only a senior underwriter may approve |
| `decline_file` | no | yes | no | no | NBC-7.2 / HM-9.2 (SYNTHETIC guidelines) |
| `refer_file`, `request_more_information` | yes | yes | no | no | Never a final decision |
| `promote_model`, `sign_migration_certificate` | no | no | yes | no | Model risk is validated by someone who does not underwrite |
| `manage_permissions`, `view_audit_log` | no | no | no | yes | An admin runs the system but decides nothing |

Rules around the table:
- **An admin can never approve, decline or promote**, and a model validator can never decide a file: separation of
  duties. `hitl.validate_decision` refuses both roles first (case and spaces ignored), with the reason from
  `authorize`, and the refusal is kept in the run's `rejected_decisions` like any other refused decision.
- Two pseudo-roles exist only for audit events: `system` (the agent process itself, which calls tools only from its
  own allow-list and is never authorized for any action here) and `unknown` (a claimed role that was not recognised).
- Anything not in the table, an unknown action or an unknown role, is denied. Reasons never echo the principal id.
- `authorize` is the coarse "may this role act at all" gate. A file decision it allows is still checked by
  `validate_decision` (blocking referrals, the senior underwriter's own ceiling, startup rules, override reasons).
  Both must say yes.

Thresholds (`config/security.yaml`, USD): `credit: 150000`, `insurance: 5000000` (schedule TIV), `startup_credit: 0`
(only a senior underwriter approves a startup file, NSP-9.1). They mirror the underwriter limits in
`hitl.APPROVAL_LIMITS`; `tests/security/test_rbac.py` fails if the two disagree. An amount exactly at the threshold is
allowed, as in `hitl`.

## 3. Document-level permissions (packet access lists)

`config/security.yaml`, section `packet_acl`:

```yaml
packet_acl:
  default_roles: [underwriter, senior_underwriter]   # packets without an entry of their own
  packets:
    PKT-C-024:                    # SYNTHETIC example: this entry REPLACES the default for this packet
      roles: [senior_underwriter]
      principals: [uw.jdoe]       # a named principal still needs a document-viewing role
```

- Viewing a packet's documents needs **both** a document-viewing role (underwriter or senior underwriter) **and** a
  grant: the default roles, or the packet's own entry (by role or by principal id). A model validator or admin is
  never granted documents, even if named; the file refuses to load if it tries (`SecurityConfigError`).
- The file is validated on load: unknown keys anywhere, negative thresholds, NaN or infinite thresholds (YAML `.nan`,
  `.inf`), packet ids with `/` or `..`, two packet ids that differ only in case, principal ids with spaces or
  wildcards and unknown roles are all refused, so a typo fails loudly instead of granting access.
- **Packet ids are matched exactly and must be plain.** `authorize("view_documents")` denies an id that is not a
  plain packet id (`config.is_plain_packet_id`: letters, digits, `_`, `.`, `-`, no `..`; so no trailing space or
  newline, no slash), and denies an id that differs from a listed packet only in case (`pkt-c-024` for `PKT-C-024`)
  instead of giving it the default roles. The packet store uses the same id check.
- **Which packet's list applies.** Only the review's own `packet_id` (written by whoever built the review from the
  packet) decides it. A `packet_id` passed by the caller, for example from a URL, can only confirm it: if the two
  differ, or the caller names a packet and the review names none, every document part is withheld. With `store=`
  the packet must also be in the encrypted packet store and the review's `packet_sha256` must equal the stored
  SHA-256, which ties the label to the bytes. The packet intake should always pass the store.
- `review_for_principal(review, principal, packet_id=None, audit=None, config=None, store=None)` returns what a
  principal may see of the review payload:
  - no `view_file`: nothing (`AccessDenied`);
  - `view_file` but no document permission: the payload with document parts replaced by
    `[WITHHELD: no document permission for this packet]`, and `document_access` saying why and which keys;
  - both: everything.
- **Fail closed.** Without document permission only the review keys known to hold no document content stay visible
  (`REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT`: ids, rule codes, numbers, statuses, the memo, limits). Any other top-level
  key is withheld, including the keys the packet intake is specified to add (`extraction`, `document_checks`,
  `contradictions`, `field_provenance`, page texts). Text that quotes a document (`excerpt`, `quote`, `snippet`,
  `page_text`, ...) is withheld at any depth. A test runs the real agent and fails if `build_review_request` gains a
  key that nobody has classified. A review that holds document keys but names no packet is withheld too.
- `read_packet_for(principal, packet_id, store, audit)` decrypts a packet PDF only after `authorize` allows it.
- Every check writes an audit event when an `AuditLog` is passed (`view` or `access_denied`).

## 4. Encryption at rest and key handling

**Algorithm.** Fernet from the `cryptography` package: AES-128-CBC for confidentiality plus HMAC-SHA256 for
integrity; a tampered token is refused. One implementation is shared with the website: `security/crypto.py`
re-exports `Encryptor`, `CryptoError`, `generate_key` and `parse_fernet_keys` from `web/crypto.py` instead of keeping
a second copy (a test checks they are the same objects). The dependency points from `security/` to `web/` only
because `web/` could not be edited in this phase; section 9 moves the class into `security/`.

**Keys.**
- `UWC_FERNET_KEY` in `.env` (gitignored) or the environment: one key, or several separated by commas, newest
  first. The first key encrypts, every key decrypts (`MultiFernet`). Generate one with
  `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- **Rotation, honestly.** Putting a new key in front makes all NEW data use it. Nothing re-encrypts old data: there
  is no re-encryption tool (`Encryptor` has no `rotate`), the packet store never overwrites a packet, and in the
  encrypted decision log the chain hashes cover the ciphertext, so re-encrypting a record would change its
  `record_sha256` and every later hash, which is exactly what a forgery looks like and would orphan the
  `record_sha256` values decision records were cited by. So an old key must stay in `UWC_FERNET_KEY` for as long as
  data encrypted with it is kept. Retiring keys needs either a chain over something that survives re-encryption
  (with its own privacy cost: a hash of the plaintext lets anyone confirm a guessed record) or platform key
  management (Cloud KMS, Phase 7, not built).
- **Never commit a key.** The tests generate fresh keys with `generate_key()`; no test reads `.env`.
- The agent side has **no fallback**: `encryptor_from_env()` raises `CryptoError` when the key is unset or invalid,
  because records encrypted with a throwaway key would be unreadable later. (The website falls back to a local
  development key outside a Space, or a random in-memory key inside one, with a warning; see `web/crypto.py`.)
- Losing the key loses the encrypted data. The hash chains can still be checked without it (below).
- **Cloud KMS is Phase 7 and is not built.** With KMS, data would be encrypted under data keys wrapped by a key that
  never leaves the KMS, and rotation would be done by the platform.

**Encrypted decision log** (`EncryptedDecisionLog`, a subclass of `agent.decision_record.DecisionLog`, so
`AgentDeps.decision_log` and `evidence.record.append_record` accept it unchanged). One JSON line per record:

```json
{"record_id": "DR-...", "payload_encryption": "fernet", "payload": "<Fernet token>",
 "schema_version": 1, "previous_record_sha256": "...", "record_sha256": "..."}
```

The chain fields are computed over this envelope (record id plus ciphertext), exactly as `DecisionLog` computes them
over a plaintext record. The record itself (names, tool outputs, memo, the reviewer's rationale) is only inside the
encrypted payload.

Trade-off of keeping the chain fields in clear:
- **gained:** anyone can verify the chain **without the key** (an auditor, a CI job, `scripts/verify_audit.py`), and
  `append` stays idempotent by `record_id` without decrypting the file;
- **given up:** the clear fields show how many records there are, their order, their opaque ids (`DR-` plus 24 hex
  characters of a SHA-256) and their approximate size. A Fernet token also carries its creation time in clear;
- the envelope hash covers the ciphertext and Fernet is randomized, so the same record encrypted twice gets two
  different `record_sha256` values. Replay checks compare decrypted records, not hashes;
- a payload moved to another line is refused on decryption (the decrypted `record_id` must match the line's), even if
  the forger re-chains every hash; without the key nobody can write a valid payload;
- like `DecisionLog`, the chain alone does not detect a truncated tail;
- re-encrypting a payload (to retire a key) changes the chain (see "Rotation, honestly" above);
- never mix plaintext and encrypted records in one file: `append` refuses a file that already holds plaintext
  records. Give the encrypted log its own path.

**Encrypted packet store** (`EncryptedPacketStore`). Per packet: `<root>/<packet_id>/packet.pdf.enc` (Fernet token of
a header naming the packet plus the PDF bytes) and `packet.meta.json` (packet id, SHA-256 and size of the PDF, time
stored, in clear). Files are written with mode 0600 through a temporary file and a rename. It refuses non-PDF bytes,
odd packet ids, overwriting a stored packet, a ciphertext copied from another packet's folder, and bytes that do not
match the recorded SHA-256. `decrypted_file()` gives the PDF reader a private temporary copy (folder 0700, file 0600)
that is deleted when the block ends. The SHA-256 stays in clear on purpose: decision records cite it, and checking it
must not need the key.

## 5. Audit events

`security/audit.py`: an append-only JSON Lines file (default `data/processed/security/audit_events.jsonl`,
`UWC_AUDIT_LOG`; gitignored). One event per line:

```json
{"schema_version": 1, "seq": 0, "event_id": "AE-00000000", "occurred_at": "...", "event_type": "view",
 "actor": {"principal_id": "uw.1", "role": "underwriter", "role_source": "claimed"},
 "action": "view_file", "resource": {"kind": "file", "id": "APP-C-001"}, "outcome": "allowed",
 "reason": "...", "details": {}, "data_label": "SYNTHETIC", "prev_hash": "000...", "record_hash": "..."}
```

Event types: `view`, `access_denied`, `run_started`, `tool_call`, `recommendation`, `approval`, `decline`,
`referral`, `information_request`, `override` (logged in addition to the decision when it differs from the
recommendation), `decision_refused`, `permission_change`, `model_promotion`, `migration_certificate`.
`record_access()` logs an authorization outcome; `audit_decision_record()` turns one stored agent decision record
into its events (each tool call and the recommendation with the agent as actor; each refused attempt, the decision
and any override with the claimed reviewer as actor). It is **idempotent per event**: every event carries
`details.decision_record_id` and a stable `details.event_key` (`tool_call:0`, `recommendation`, `decision_refused:0`,
`decision`, `override`), and a replay writes only the keys that are missing, so a run that died after its first
events is completed, not skipped. Call it from the one process that wrote the record; two processes replaying the
same record at the same moment could both write a missing event.

**Integrity.** `record_hash` is the SHA-256 of the event's canonical JSON without `record_hash`; `prev_hash` is the
previous event's `record_hash` (64 zeros for the first); `seq` counts from 0. A POSIX file lock keeps one writer at a
time. After each append the **head anchor** `audit_events.head.json` (event count and last hash) is rewritten.
Each event is written as ASCII-only JSON and files are split on `\n` only, so a reviewer-typed U+2028, U+2029 or
U+0085 cannot cut an event in two (the decision logs split on `\n` only as well).

| Tampering | How it is detected | Test |
|---|---|---|
| An event is **edited** | its `record_hash` no longer matches its content (or the line is not valid JSON) | `test_an_edited_event_is_detected` |
| An event is **deleted** | `seq` gap and `prev_hash` mismatch | `test_a_deleted_event_is_detected`, `test_deleting_the_first_event_is_detected` |
| Events are **reordered** | `seq` and `prev_hash` mismatch | `test_reordered_events_are_detected` |
| The tail is **truncated** | the chain of what remains is intact, so only the head anchor catches it (count and last hash) | `test_a_truncated_log_is_detected`, `test_a_cut_off_last_line_is_detected` |
| History before an anchor kept elsewhere is **rewritten and re-chained**, head file included, while the log keeps growing | the event at the anchor's position no longer has the anchor's hash | `test_an_anchor_kept_elsewhere_catches_a_rewrite_before_it_after_growth` |

**The honest limit.** Someone who can rewrite both the log and its head file can rebuild a consistent chain
(`test_rewriting_the_head_too_needs_an_anchor_kept_elsewhere`). Copy the head that `scripts/verify_audit.py` prints
somewhere the writer cannot change, and pass it back later with `--expect-count` and `--expect-head`. The copy is
checked as a **prefix**: the log may have grown since (normal for an append-only log,
`test_an_anchor_kept_elsewhere_allows_normal_growth`), but it must still hold at least that many events and the
event at that position must still carry that hash; since every hash covers the one before it, all history up to the
copy is pinned. Events appended after the copy are protected only by the stored head, so refresh the copy
regularly. A signed or write-once anchor (KMS signature, object lock) is Phase 7 and not built.

**Privacy.** Every `reason` and every `details` value goes through `observability.redaction.PiiRedactor` before it is
written: FEINs, SSN-shaped numbers, e-mails, phone numbers and account numbers are masked by pattern, and whole values
under sensitive keys (`rationale`, `notes`, `excerpt`, `address`, ...) are replaced. Names cannot be found by pattern,
so pass `redactor_for_application(application)` to mask the applicant's and owners' names. The actor id and the
resource id are kept on purpose (an audit trail must say who did what to which file); they must be opaque ids:
reviewer ids are staff identifiers, file ids are `APP-...`, and applicant data never goes into an id. Two details
keys are kept too, but only when the value has an id shape (`audit.KEPT_ID_PATTERNS`): `decision_record_id`
(`DR-`/`EV-` plus hex; 24 hex characters often hold 8 or more digits in a row, which the redactor would otherwise
mask as an account number) and `event_key`. A packet id that is not plain is logged as `not-a-plain-packet-id`.

**Checking.** `make verify-audit` (or `uv run python scripts/verify_audit.py [--log ...] [--expect-count N
--expect-head H] [--decision-log FILE ...]`) exits 0 when everything checked is intact and 1 otherwise. It needs no
key: the audit log is redacted plaintext, and an encrypted decision log keeps its chain fields in clear. Decision logs
have no head anchor, so for them it detects edits, deletions and reordering, not a truncated tail; a line that is no
longer valid JSON is reported by its line number.

## 6. Configuration

| Setting | Default | Read by |
|---|---|---|
| `config/security.yaml` | committed; thresholds and packet ACLs | `security/config.py` (loaded once per process) |
| `UWC_FERNET_KEY` | none (the agent side refuses to run without it) | `security/crypto.encryptor_from_env` |
| `UWC_AUDIT_LOG` | `data/processed/security/audit_events.jsonl` | `security/settings.py` |
| `UWC_PACKET_STORE` | `data/processed/security/packets` | `security/settings.py` |

All are documented in `.env.example`. `.env` wins over the process environment, as for every other setting.

## 7. Tests and commands

- `make security-access-test`: `tests/security/test_rbac.py` (the authorization matrix for every role and action,
  thresholds, the checkpoint refusal, a real graph run where an admin's approval is refused),
  `test_document_access.py` (packet ACLs, config validation, other spellings of a packet id, the review's own
  packet id versus the caller's, the store binding, withheld review content, a real review payload),
  `test_crypto.py` (shared encryptor, several keys, encrypted decision log, packet store) and `test_audit.py` (chain,
  redaction, edit/delete/reorder/truncate, anchors kept elsewhere on a growing log, line separators typed by a
  reviewer, an interrupted audit completed on replay, record ids kept from the redactor, events from a real agent
  run, the verify script).
- `make test-security` runs the whole `tests/security` folder, including the S2 containment tests.
- `make verify-audit` checks the configured audit log.
All tests are offline, use SYNTHETIC applications and packets, and generate their own keys.

## 8. Agent CLI adoption (for the Phase 5 A workflow or later)

`scripts/run_agent.py` does not use these components yet. To adopt them without changing the graph:
1. Encrypted records: build `EncryptedDecisionLog(<a new path, e.g. decision_records.enc.jsonl>,
   encryptor_from_env())` instead of `DecisionLog(settings.decision_log)` behind an explicit flag such as
   `--encrypt-records`. Do not point it at the existing plaintext file (it refuses), and do not silently fall back to
   plaintext when the key is missing (`encryptor_from_env` raises; report it and exit).
2. Audit events: create `AuditLog(load_security_settings().audit_log, redactor=redactor_for_application(app_json))`;
   log `record_access(log, Principal(reviewer_id, role), "view_file", Resource("file", app_id), authorize(...))`
   when the review is printed, and `audit_decision_record(log, record)` after the decision record is written.
3. Packet intake (Phase 5 A): store uploaded PDFs with `EncryptedPacketStore.put`, read them for docintel through
   `store.decrypted_file(packet_id)`, put `packet_id` and `packet_sha256` into the review payload
   (`build_review_request`), and pass the review through `review_for_principal(review, principal, audit=log,
   store=store)` before showing it. The CLI prints the review at the checkpoint before anyone has given
   `--reviewer-id`/`--role`, so it needs a principal at that point (for example require both flags when the file
   has a packet, or print only the withheld view). Classify every new review key in
   `document_access.REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT` only if it holds no document text; otherwise leave it out
   so it is withheld.
4. Phase 6 governance: call `authorize(principal, "promote_model" | "sign_migration_certificate", ...)` before
   recording a promotion or a certificate signature, and log it with `record_access`.

## 9. Web adoption (do not edit `src/uwcopilot/web/**` in Phase 5 B; these are the steps for the web workflow)

1. **One Fernet implementation, in `security/`.** Move `CryptoError`, `generate_key`, `parse_fernet_keys` and
   `Encryptor` from `web/crypto.py` into `security/crypto.py`. Keep the web-only development fallback
   (`DEV_KEY_FILE_NAME`, `load_or_create_dev_key`, `encryptor_for_settings`) in `web/crypto.py`, which then imports
   the four names from `uwcopilot.security.crypto`, so `web/settings.py`, `web/store.py`, `web/app.py` and the web tests keep their imports. `security/crypto.py` must then stop importing from `web/`. The identity test in
   `tests/security/test_crypto.py` keeps passing either way.
2. **Roles.** Replace `services/lender_demo.reviewer_role_for` with
   `rbac.acting_reviewer_role(rbac.access_roles_for_web_roles(principal.roles))` (`lender_senior` ->
   `senior_underwriter`, `lender_reviewer` -> `underwriter`, `admin` -> `admin`, `founder` -> no access role).
   **Behavior change to decide:** today every signed-in user (founder, and so also admin) acts as `underwriter` on
   the SYNTHETIC demo files in a private DEMO sandbox (`tests/web/test_web_lender.py::
   test_web_roles_map_to_reviewer_roles` expects this). Concretely, `routers/lender.py` accepts any signed-in user
   (`require_role()` with no roles) and `reviewer_role_for` returns `underwriter` for anyone without
   `lender_senior`, so a web admin's `POST /api/lender/files/{id}/decision` reaches `validate_decision` as an
   underwriter and is accepted within underwriter limits. This is the one path found where an admin can approve or
   decline; it is limited to SYNTHETIC demo files. (The CLI restricts `--role` to the reviewer roles, the MCP server
   has read-only tools, and the graph checkpoint refuses admin and model_validator.) The owners choose between (a) keeping that sandbox, labelled
   as a demo role, for SYNTHETIC files only, or (b) answering 403 to users without a reviewer role. Real lender files
   must use (b): an admin never decides a file.
3. **Principal.** Build the security principal from the web one:
   `Principal(owner_digest(web_principal.owner_id), acting_role, "dev_login" if web_principal.auth_mode == "dev"
   else "session_allowlist")`. Use the digest, as the web already does for thread ids: `hf_oauth:<sub>` can exceed
   the 128-character principal id limit and should not be shown.
4. **Checks and events.** `GET /api/lender/files/{id}`: `authorize(principal, "view_file", ...)` plus
   `record_access`; a lender documents view (web v0.2): `review_for_principal(review, principal, packet_id=<id from
   the URL>, audit=log, store=store)` (the URL id only confirms the review's own) / `read_packet_for`; after a decision
   is stored: `audit_decision_record`; an admin page for the audit log: `audit_events_for(principal, log)`.
5. **Where the audit log lives in the Space.** The Space disk is not persistent, so an audit log on it is lost on
   restart. Mirror it (it is redacted plaintext; encrypting it with the store key is also possible) to the private
   dataset, and keep the head anchor outside the Space. Not decided yet.

## 10. Likely interview questions (for Rahul and Bhavani)

1. **Roles are claimed. Then what does the RBAC actually protect?** It enforces the rules once a role is known and
   makes them testable in one place (`authorize`). It does not prove identity. In production the role would come from
   an identity provider (SSO claims), never from the request; the audit event's `role_source` field already records
   which kind of role it was.
2. **Why both `authorize()` and `validate_decision()`?** Different questions. `authorize` asks "may this role take
   this kind of action at all?" (an admin may not approve anything). `validate_decision` asks "do the guidelines allow
   this decision on this file?" (blocking referrals, overrides, ceilings). Both must say yes.
3. **Why can an admin not approve, even in an emergency?** Separation of duties: the person who manages permissions
   could otherwise grant themselves anything and act on it. Emergencies go to a senior underwriter.
4. **Why does a model validator not decide files?** The validator independently checks the models underwriters rely
   on; deciding files with them would mix the two roles.
5. **How do document permissions work, and why "fail closed"?** A packet's documents need a document role and a
   grant in the ACL. Without it the reviewer still sees the recommendation and findings but no document text. Any
   review key nobody classified is withheld, so a new field cannot leak by default; a test forces the decision.
6. **Why keep the hash chain fields in clear in the encrypted decision log?** So anyone can verify integrity without
   the key. The cost is that record count, order, ids, sizes and Fernet timestamps are visible. Payload swaps are
   still caught because the decrypted record id must match its line.
7. **What does the audit chain detect, and what can it not?** Edits, deletions and reordering through the chain;
   truncation through the head anchor. Someone who controls both the log and the head can rebuild it, so the head
   must be copied somewhere they cannot write. That copy is a prefix check: the log may grow after it, but the
   history up to it cannot change unseen; what was appended after it is only as safe as the stored head, so the copy
   is refreshed regularly (signed or write-once anchors are Phase 7).
8. **How do you keep personal data out of the audit log?** Every reason and detail goes through the same redactor as
   the traces: patterns for FEINs, SSNs, e-mails, phones and account numbers, whole sensitive fields, and the
   application's names when a per-application redactor is passed. Ids are opaque by design.
9. **How are keys handled and rotated?** `UWC_FERNET_KEY` in a gitignored `.env`, never committed; tests generate
   keys. Several keys can be listed: the first encrypts, all decrypt, so new data moves to a new key at once. Old
   data is not re-encrypted (no tool for it, and re-encrypting the decision log would break its chain), so old keys
   stay listed while their data is kept. The agent side refuses to run without a key. Cloud KMS would hold the
   master key in Phase 7 (not built).
10. **Why re-export the website's encryptor instead of writing one for the agent?** One implementation means one
    place to review and one key variable. The temporary cost is that `security/` imports from `web/`; the web
    adoption step moves the class into `security/`.
