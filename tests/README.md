# Tests

Run the suite from the plugin directory:

```text
python -m pytest
```

The CI verification job also emits a branch-aware coverage report:

```text
python -m pytest --cov=astrbot_plugin_beszel --cov-branch --cov-report=term-missing
```

All synthetic monitoring records, configuration mappings, webhook bodies, and
HTTP responses live under `tests/fixtures`. Tests load those files through
`tests/conftest.py`; do not copy production payloads or add equivalent inline
dictionaries to test modules. Rendering tests use the bundled font and validate
generated PNG bytes in memory without writing output to the repository.
