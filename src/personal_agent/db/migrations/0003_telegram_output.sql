-- Telegram output: button presses, poll votes, Stop, streamed drafts, and the
-- outbox kinds for rich messages, reactions, and polls.

-- Button presses, poll votes, and Stop presses are logged events too; they carry
-- no message of their own, so the external message ID becomes optional.
ALTER TABLE inbound_event DROP CONSTRAINT inbound_event_kind_check;
ALTER TABLE inbound_event ADD CONSTRAINT inbound_event_kind_check CHECK (
    kind IN ('message', 'edit', 'unsupported', 'button', 'poll_answer', 'stop')
);
ALTER TABLE inbound_event ALTER COLUMN external_message_id DROP NOT NULL;

-- A turn the owner stopped is closed without a reply.
ALTER TABLE turn DROP CONSTRAINT turn_status_check;
ALTER TABLE turn ADD CONSTRAINT turn_status_check CHECK (
    status IN ('collecting', 'generating', 'answered', 'failed', 'stopped')
);
ALTER TABLE turn DROP CONSTRAINT turn_check;
ALTER TABLE turn ADD CONSTRAINT turn_closed_check CHECK (
    (status IN ('answered', 'failed', 'stopped')) = (closed_at IS NOT NULL)
);
-- The current generation's Telegram draft, so a Stop press finds its turn.
ALTER TABLE turn ADD COLUMN draft_id integer CHECK (draft_id > 0);
-- Set by a Stop press; the runner then closes the turn without a reply, even
-- after a restart.
ALTER TABLE turn ADD COLUMN stop_requested_at timestamptz;

ALTER TABLE model_call DROP CONSTRAINT model_call_status_check;
ALTER TABLE model_call ADD CONSTRAINT model_call_status_check CHECK (
    status IN (
        'completed',
        'incomplete',
        'superseded',
        'stopped',
        'execution_unknown',
        'replay_conflict',
        'budget_exhausted',
        'failed'
    )
);

-- Outbox rows: a rich message (Telegram Rich Markdown), a plain message, a
-- reaction to the owner's message, or a poll. Extras such as the effect, the
-- reaction emoji, and the poll live in payload.
ALTER TABLE outbox ADD COLUMN kind text;
UPDATE outbox SET kind = 'plain';
ALTER TABLE outbox ALTER COLUMN kind SET NOT NULL;
ALTER TABLE outbox ADD CONSTRAINT outbox_kind_check CHECK (
    kind IN ('rich', 'plain', 'reaction', 'poll')
);
ALTER TABLE outbox ADD COLUMN payload jsonb NOT NULL DEFAULT '{}';
ALTER TABLE outbox ALTER COLUMN text DROP NOT NULL;
ALTER TABLE outbox ADD CONSTRAINT outbox_text_check CHECK (
    (kind IN ('rich', 'plain')) = (text IS NOT NULL)
);
-- True when Telegram rejected the rich message and plain text was sent instead.
ALTER TABLE outbox ADD COLUMN fallback_used boolean NOT NULL DEFAULT false;

-- Quick-reply buttons. The token replaces the callback data the model wrote, so
-- a press always maps to a button the assistant actually sent.
CREATE TABLE reply_button (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    turn_id bigint NOT NULL REFERENCES turn (id),
    outbox_id bigint NOT NULL REFERENCES outbox (id),
    token text NOT NULL UNIQUE,
    label text NOT NULL,
    position smallint NOT NULL,
    pressed_at timestamptz,
    pressed_event_id bigint REFERENCES inbound_event (id),
    created_at timestamptz NOT NULL,
    CHECK ((pressed_at IS NULL) = (pressed_event_id IS NULL))
);

CREATE INDEX reply_button_outbox ON reply_button (outbox_id);

-- Polls the assistant sent; votes are matched by Telegram's poll ID.
CREATE TABLE reply_poll (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    turn_id bigint NOT NULL REFERENCES turn (id),
    outbox_id bigint NOT NULL UNIQUE REFERENCES outbox (id),
    external_poll_id text UNIQUE,
    question text NOT NULL,
    options jsonb NOT NULL,
    allows_multiple boolean NOT NULL,
    created_at timestamptz NOT NULL
);
