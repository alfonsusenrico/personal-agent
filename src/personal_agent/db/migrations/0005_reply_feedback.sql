-- Reply feedback: the owner flags or notes an assistant reply. A flag points at
-- the assistant's message in the conversation record, never at a channel's
-- message; the channel's own gesture stays in inbound_event.

ALTER TABLE inbound_event DROP CONSTRAINT inbound_event_kind_check;
ALTER TABLE inbound_event ADD CONSTRAINT inbound_event_kind_check CHECK (
    kind IN (
        'message', 'edit', 'unsupported', 'button', 'poll_answer', 'stop', 'command', 'reaction'
    )
);

CREATE TABLE feedback (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    -- The assistant message the flag is about.
    message_id bigint NOT NULL REFERENCES message (id),
    kind text NOT NULL CHECK (kind IN ('dislike', 'note')),
    -- The owner's words; the longest Telegram message fits.
    note text CHECK (char_length(note) BETWEEN 1 AND 4096),
    source_event_id bigint NOT NULL REFERENCES inbound_event (id),
    created_at timestamptz NOT NULL,
    withdrawn_at timestamptz,
    closed_at timestamptz,
    outcome text CHECK (outcome IN ('test', 'dismissed')),
    CHECK ((kind = 'note') = (note IS NOT NULL)),
    CHECK ((closed_at IS NULL) = (outcome IS NULL))
);

-- One open dislike per message: a second 👎 changes nothing.
CREATE UNIQUE INDEX feedback_one_open_dislike ON feedback (message_id)
    WHERE kind = 'dislike' AND withdrawn_at IS NULL AND closed_at IS NULL;
CREATE INDEX feedback_open ON feedback (person_id, created_at)
    WHERE withdrawn_at IS NULL AND closed_at IS NULL;

-- A reaction that acknowledges a command belongs to no turn or message.
ALTER TABLE outbox DROP CONSTRAINT outbox_turn_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_turn_check CHECK (
    kind IN ('bot_name', 'reaction') OR (turn_id IS NOT NULL AND message_id IS NOT NULL)
);
