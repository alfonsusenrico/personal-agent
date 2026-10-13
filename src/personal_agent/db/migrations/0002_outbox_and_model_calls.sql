-- Model-call records (content-free) and the reply outbox.

-- One row per gateway call: identifiers, outcome, tokens, and cost. Never content.
CREATE TABLE model_call (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    turn_id bigint NOT NULL REFERENCES turn (id),
    -- The gateway provider_call_id: pa:{turn.call_key}:{purpose}:{generation}:a{attempt}.
    call_id text NOT NULL UNIQUE,
    purpose text NOT NULL,
    generation smallint NOT NULL,
    attempt smallint NOT NULL,
    status text NOT NULL CHECK (
        status IN (
            'completed',
            'incomplete',
            'superseded',
            'execution_unknown',
            'replay_conflict',
            'budget_exhausted',
            'failed'
        )
    ),
    -- The gateway problem code or the exception class; never a message.
    error_code text,
    model text,
    input_tokens integer,
    cached_input_tokens integer,
    output_tokens integer,
    reasoning_tokens integer,
    cost_usd numeric(12, 6),
    replayed boolean NOT NULL DEFAULT false,
    latency_ms integer NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE INDEX model_call_turn ON model_call (turn_id);
CREATE INDEX model_call_created ON model_call (created_at);

-- The model call that produced an assistant message; NULL for fixed notices.
ALTER TABLE message ADD COLUMN model_call_id bigint REFERENCES model_call (id);

-- Reply chunks, written with the answer and sent afterwards, in id order.
CREATE TABLE outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    turn_id bigint NOT NULL REFERENCES turn (id),
    message_id bigint NOT NULL REFERENCES message (id),
    channel text NOT NULL CHECK (channel IN ('telegram')),
    external_chat_id text NOT NULL,
    chunk_index smallint NOT NULL,
    text text NOT NULL,
    reply_to_external_message_id text,
    status text NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts smallint NOT NULL DEFAULT 0,
    external_message_id text,
    error_code text,
    created_at timestamptz NOT NULL,
    sent_at timestamptz,
    UNIQUE (message_id, chunk_index),
    CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);

CREATE INDEX outbox_unsent ON outbox (id) WHERE status IN ('pending', 'sending');
