-- People, their channel identities, turns, and the permanent conversation log.
-- Every table carries person_id; times are UTC, and each logged row also stores
-- the person's IANA timezone at that moment.

CREATE TABLE person (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- IANA name. NULL means the configured fallback timezone applies.
    timezone text,
    created_at timestamptz NOT NULL
);

CREATE TABLE identity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    channel text NOT NULL CHECK (channel IN ('telegram')),
    external_id text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (channel, external_id)
);

-- One continuous conversation per person, across channels.
CREATE TABLE conversation (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    kind text NOT NULL CHECK (kind IN ('main')),
    created_at timestamptz NOT NULL,
    UNIQUE (person_id, kind)
);

-- A burst of inbound events answered by one reply. Its latest event, which the
-- debounce runs from, is read from inbound_event.
CREATE TABLE turn (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    conversation_id bigint NOT NULL REFERENCES conversation (id),
    -- Names this turn's model calls. Random, so a reset or restored database never
    -- reuses a call ID the gateway has already seen.
    call_key uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    status text NOT NULL CHECK (status IN ('collecting', 'generating', 'answered', 'failed')),
    -- Generations started: the first, plus at most one after a mid-turn message.
    generation smallint NOT NULL DEFAULT 0 CHECK (generation BETWEEN 0 AND 2),
    -- The current generation's frozen inputs, so a restart rebuilds an identical call.
    generation_started_at timestamptz,
    generated_through_event_id bigint,
    failure_code text,
    opened_at timestamptz NOT NULL,
    closed_at timestamptz,
    CHECK ((status IN ('answered', 'failed')) = (closed_at IS NOT NULL)),
    CHECK ((status = 'failed') = (failure_code IS NOT NULL)),
    CHECK ((generation = 0) = (generation_started_at IS NULL)),
    CHECK ((generation = 0) = (generated_through_event_id IS NULL))
);

-- One turn collects and one generates per conversation at a time.
CREATE UNIQUE INDEX turn_one_collecting ON turn (conversation_id) WHERE status = 'collecting';
CREATE UNIQUE INDEX turn_one_generating ON turn (conversation_id) WHERE status = 'generating';

-- The channel-neutral envelope of every accepted update, stored once and never changed.
CREATE TABLE inbound_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    conversation_id bigint NOT NULL REFERENCES conversation (id),
    -- NULL for events that need no answer, such as service messages.
    turn_id bigint REFERENCES turn (id),
    channel text NOT NULL CHECK (channel IN ('telegram')),
    external_update_id text NOT NULL,
    external_chat_id text NOT NULL,
    external_message_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('message', 'edit', 'unsupported')),
    modality text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    person_tz text NOT NULL,
    trust text NOT NULL CHECK (trust IN ('owner_direct', 'owner_forwarded')),
    forward_origin jsonb,
    reply_to_external_message_id text,
    edit_of bigint REFERENCES inbound_event (id),
    text text,
    raw jsonb NOT NULL,
    UNIQUE (channel, external_update_id),
    CHECK ((trust = 'owner_forwarded') = (forward_origin IS NOT NULL)),
    CHECK (edit_of IS NULL OR kind = 'edit')
);

CREATE INDEX inbound_event_turn ON inbound_event (turn_id);
CREATE INDEX inbound_event_external_message
    ON inbound_event (channel, external_chat_id, external_message_id);

-- The user and assistant text the model sees; later memory derives from these rows.
CREATE TABLE message (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    conversation_id bigint NOT NULL REFERENCES conversation (id),
    turn_id bigint NOT NULL REFERENCES turn (id),
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    text text NOT NULL,
    occurred_at timestamptz NOT NULL,
    person_tz text NOT NULL,
    trust text NOT NULL CHECK (trust IN ('owner_direct', 'owner_forwarded', 'assistant')),
    source_event_ids bigint[] NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (turn_id, role),
    CHECK ((role = 'assistant') = (trust = 'assistant'))
);

CREATE INDEX message_conversation ON message (conversation_id, id);

-- The log is append-only. Deletion arrives with the owner's memory commands; nothing
-- ever rewrites a logged row.
CREATE FUNCTION refuse_log_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER inbound_event_immutable BEFORE UPDATE ON inbound_event
    FOR EACH ROW EXECUTE FUNCTION refuse_log_update();
CREATE TRIGGER message_immutable BEFORE UPDATE ON message
    FOR EACH ROW EXECUTE FUNCTION refuse_log_update();
