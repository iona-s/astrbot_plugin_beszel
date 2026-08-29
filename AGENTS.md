# AGENTS.md

## Scope

These instructions apply to the entire repository.

This project is an AstrBot plugin that provides read-only Beszel queries, rendered
monitoring reports, and authenticated webhook forwarding. Keep changes focused on
that role and preserve compatibility with the supported platform versions.

## Working Rules

- Inspect the working tree before editing and preserve unrelated user changes.
- Keep implementation changes narrowly scoped and follow existing module boundaries.
- Prefer existing helpers and data models over parallel implementations.
- Do not commit generated caches, local configuration, credentials, render output,
  or other machine-specific artifacts.
- Use repository-relative paths in documentation and examples.
- Do not commit, push, tag, or publish a release unless explicitly requested.

## Repository Structure

- `main.py` is the AstrBot loader shim. Runtime implementation belongs under
  `core/`.
- `core/plugin.py` owns framework registration, lifecycle, commands, and LLM tools.
- `core/config.py` owns configuration parsing and normalization; `_conf_schema.json`
  defines the AstrBot-facing configuration form.
- `core/beszel/` owns Beszel transport, API models, and query services.
- `core/rendering/` owns presentation models, formatting, templates, styles, and the
  rendering engine.
- `core/webhook/` owns HTTP ingress, payload parsing, and message delivery.
- `core/assets/` contains runtime assets and their license notices.
- Repository-root metadata, documentation, and automation files must not contain
  runtime business logic.

Keep transport, domain/query, presentation, rendering, and delivery concerns
separate. Framework handlers should coordinate these layers instead of duplicating
their logic.

## Compatibility And Dependencies

- Target Python `>=3.12,<4`, AstrBot `>=4.25,<5`, and Beszel `>=0.18.8`.
- Keep Beszel access read-only. Do not add mutating Hub operations without an
  explicit product decision.
- Declare every required third-party runtime dependency in `requirements.txt`.
- Preserve asynchronous network I/O and plugin lifecycle cleanup. Move blocking
  rendering work off the event loop.
- Avoid background workers, queues, locks, or concurrency controls unless a measured
  need justifies their lifecycle and failure handling.
- Do not introduce a browser-based runtime renderer. The renderer is intentionally
  based on Pytakumi to keep memory use predictable.

## Configuration And State

- Treat configuration as untrusted input and normalize optional strings, lists,
  numeric values, URLs, and time ranges at the configuration boundary.
- When configuration changes, update the parser, schema, user documentation, and
  changelog together.
- Never place real credentials or deployment-specific endpoints in defaults,
  examples, fixtures, or committed files.
- Do not write mutable runtime state into the plugin source tree. Use AstrBot's data
  facilities for any persistent state that is genuinely required.

## Security And Logging

- Always redact passwords, PocketBase tokens, and webhook Bearer tokens. Bearer
  diagnostics may only state `<present>` or `<invalid>`.
- Redact Beszel `host`, `ip`, and `port` fields in full API payload dumps.
- Plain operational logs may retain message origins, webhook target origins, Beszel
  account identity, hostnames, hardware and OS metadata, monitoring samples, Hub
  base URLs, selectors, ranges, and notification titles.
- Never log raw authorization headers or unreviewed request bodies.
- Authenticate webhook requests before reading and parsing their body. Keep token
  comparisons timing-safe.
- Preserve template autoescaping and do not execute untrusted template source or
  markup supplied by webhook payloads.
- Keep logs useful but concise: expected user input errors should become normal
  replies, not framework tracebacks.

## Rendering And Assets

- Keep page structure in checked-in Jinja templates and styling in the shared CSS.
  Do not rebuild full HTML documents through Python string concatenation.
- When changing report structure or styling, use the corresponding Beszel Hub
  frontend implementation as the primary visual reference while adapting it to
  static chat images.
- Render through the established presentation-model boundary rather than passing
  transport responses directly into templates.
- Use the bundled CJK font by default. A configured font file may override it; do
  not probe arbitrary system font locations or download fonts at runtime.
- Preserve attribution and license files for all bundled fonts and upstream-derived
  assets.
- Keep temporary render artifacts scoped, cleaned up, and outside the source tree.

## Quality Checks

Run the checks relevant to the change before handing it off:

```text
ruff check --config ruff.toml .
ruff format --check --config ruff.toml .
python -m compileall -q main.py core
pre-commit run --all-files --show-diff-on-failure
```

For rendering, transport, or webhook changes, also exercise the affected path with
focused smoke checks when its required local services are available. Report any
check that could not be run.

## Documentation And Release Hygiene

- Keep `README.md`, `metadata.yaml`, `_conf_schema.json`, and `CHANGELOG.md`
  synchronized with public behavior.
- Keep the metadata version and the leading changelog release heading aligned.
- Preserve the root plugin logo and required marketplace metadata.
- Keep the distributable archive below the AstrBot marketplace size limit and omit
  development-only files from release archives.
- Use Conventional Commit subjects without scopes. Keep each commit focused on one
  concern.
