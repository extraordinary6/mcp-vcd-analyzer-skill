# VCD Analyzer REST API

Thin HTTP/JSON wrapper around the VCD Analyzer Skills. Useful when an Agent
or downstream service can't run the CLI directly (cloud agents, non-Python
runtimes, distributed deployments).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness probe |
| GET | `/api/v1/skills` | Full Skill manifest |
| GET | `/api/v1/skills/<name>` | One capability entry |
| POST | `/api/v1/skills/<name>` | Execute a Skill (body = JSON args) |
| POST | `/api/v1/protocol-decode` | Shortcut for vcd_protocol_decode |
| POST | `/api/v1/fsm-trace` | Shortcut for vcd_fsm_trace |
| POST | `/api/v1/causality` | Shortcut for vcd_causality |
| POST | `/api/v1/anomaly-detect` | Shortcut for vcd_anomaly_detect |

`<name>` accepts any of these spellings: `protocol_decode`, `protocol-decode`,
or `vcd_protocol_decode`.

## Run

```bash
pip install -r vcd_integrations/rest_api/requirements.txt
python vcd_integrations/rest_api/server.py --host 0.0.0.0 --port 5000
```

## Request / Response

POST bodies are JSON. The fields match the Skill's `input_schema` in
`vcd_skill_manifest.json`:

```bash
curl -X POST http://localhost:5000/api/v1/protocol-decode \
  -H 'Content-Type: application/json' \
  -d '{"file": "sim.vcd", "protocol": "axi4", "signals": "m_axi_*"}'
```

The response is the standardized VCD envelope:

```json
{
  "status": "success",
  "skill": "protocol_decode",
  "execution_time_ms": 4,
  "input": {...},
  "result": {
    "transactions": [...],
    "violations": [...],
    "statistics": {...}
  },
  "metadata": {...},
  "suggestions": [...]
}
```

HTTP **always returns 200** when the Skill executed successfully (even if
the envelope contains `status: "error"`). HTTP **non-200** is reserved for
transport-level problems (404 for unknown skill name, 400 for malformed
JSON body).

## Listing Capabilities

```bash
curl http://localhost:5000/api/v1/skills | jq '.capabilities[].skill'
```

## Deployment Notes

- The server runs each request synchronously and shells out to
  `vcd_analyzer.py`. For high-throughput deployments, run behind gunicorn
  with multiple workers:

  ```bash
  pip install gunicorn
  gunicorn -w 4 -b 0.0.0.0:5000 'vcd_integrations.rest_api.server:create_app()'
  ```

- The file paths in the JSON body are interpreted by the **server's**
  filesystem. If the Agent runs elsewhere, you'll need a shared volume or
  an upload endpoint (not provided here — out of scope for an MVP).

- Add authentication (API key middleware, JWT, etc.) before exposing this
  beyond localhost. Out-of-the-box, anyone with network access can run
  analyzer commands.

## Why Not Async?

Flask is enough for the typical use case: per-request analysis of a single
VCD file. The underlying `vcd_analyzer.py` invocation dominates wall time
and is already CPU-bound, so async I/O wouldn't help. If you need
concurrency, gunicorn workers + process-level isolation is the right
trade-off.
