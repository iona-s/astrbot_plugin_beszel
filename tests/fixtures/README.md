# Public Demo Fixtures

This directory contains deterministic, fully synthetic Beszel data intended for
public screenshots, documentation examples, and rendering checks.

The fixtures copy the shape and realistic value ranges of Beszel records, but they
do not contain transformed production records. System names, record IDs, hostnames,
timestamps, hardware identities, container names, and metric sequences were created
specifically for this repository. All hostnames use the reserved `.example` domain.

## Files

- `overview.json`: a list of `SystemSummary` records covering online, offline, and
  unknown states plus normal, warning, and critical metrics.
- `status.json`: one rich `SystemDetailView` with hardware, GPU, storage, network,
  battery, and container data.
- `history_1h.json`: one `SystemHistoryView` containing fixed one-minute samples for
  history chart rendering.
- `history_gap.json`: a compact history view with a deliberate missing interval
  for chart segment tests.
- `models.json`: model-validation edge cases, including removed fields and a
  missing `stats` object.
- `config.json`: minimal, complete, inherited-timezone, and invalid plugin
  configuration mappings.
- `webhook.json`: normalized notification formats, authentication headers, and
  malformed or unsupported requests.
- `client.json`: PocketBase pagination metadata, retry responses, and HTTP error
  payloads; successful records are sourced from the monitoring fixtures.
- `query.json`: selectors, system IDs, and history-range inputs for query service
  scenarios.
- `rendering.json`: stable presentation identity and timezone settings.

## Loading

```python
import json
from pathlib import Path

from core.beszel.models import SystemDetailView, SystemHistoryView, SystemSummary

fixture_dir = Path("tests/fixtures")
overview = [
    SystemSummary.model_validate(item)
    for item in json.loads((fixture_dir / "overview.json").read_text("utf-8"))
]
status = SystemDetailView.model_validate(
    json.loads((fixture_dir / "status.json").read_text("utf-8"))
)
history = SystemHistoryView.model_validate(
    json.loads((fixture_dir / "history_1h.json").read_text("utf-8"))
)
```

Keep the timestamps and values stable so generated README images and checks remain
reproducible. Extend the fixtures with fictional data rather than copying new
API responses or logs into this directory.
