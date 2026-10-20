# Recorded gateway responses

HTTP responses of the gateway API that the agent's client tests replay. Most were
recorded on 2026-10-20 from the published gateway 0.13.0 image
(`ghcr.io/alfonsusenrico/llm-gateway/gateway@sha256:2e24e95b…`) with `record.py` in this
folder: a throwaway database, a throwaway project with a `reply_stub` profile on the free
stub model, and synthetic input. No key, owner content, or provider call is in them.

Each file holds `status`, the response `headers` the client reads, and `body` (JSON) or
`text` (the server-sent events of a streamed call).

The four files marked `"derived_from_source": true` (`problem--concurrency-exhausted`,
`problem--budget-exhausted`, `problem--call-in-progress`, `problem--call-result-expired`)
cannot be produced with the free stub, so they are written from the gateway 0.13.0 source:
the code, its title and status, and `Retry-After` only where the gateway sends it (429 and
503).

When the gateway's API changes, record again from the new image and review the diff:

    uv run python tests/fixtures/gateway/record.py <image@digest>
