-- The owner profile: versioned values, removals, pacing of getting-to-know
-- questions, code-handled buttons, the /profile command, and the bot's own name
-- sent through the outbox.

-- One row per saved value. A superseded row is a value a later one replaced; the
-- later row names it in replaces_id, so Undo can restore it. Removing a field
-- deletes all of its rows.
CREATE TABLE profile_entry (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    field text NOT NULL CHECK (
        field IN (
            'address',
            'assistant_name',
            'language',
            'reply_style',
            'timezone',
            'work',
            'routine',
            'interests'
        )
    ),
    value text NOT NULL CHECK (char_length(value) BETWEEN 1 AND 300),
    status text NOT NULL CHECK (status IN ('active', 'superseded')),
    -- The inbound events of the turn whose reply marked this value.
    source_event_ids bigint[] NOT NULL,
    turn_id bigint REFERENCES turn (id),
    replaces_id bigint REFERENCES profile_entry (id),
    saved_at timestamptz NOT NULL,
    superseded_at timestamptz,
    -- The Telegram bot name limit (setMyName).
    CHECK (field <> 'assistant_name' OR char_length(value) <= 64),
    CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))
);

CREATE UNIQUE INDEX profile_entry_one_active ON profile_entry (person_id, field)
    WHERE status = 'active';
CREATE INDEX profile_entry_turn ON profile_entry (turn_id);

-- When a field was removed, without its value: replies are told not to use what
-- the owner said about it in messages still in the recent window.
CREATE TABLE profile_removal (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES person (id),
    field text NOT NULL,
    removed_at timestamptz NOT NULL
);

CREATE INDEX profile_removal_person ON profile_removal (person_id, removed_at);

-- Getting-to-know questions per topic: how often asked, and whether the owner
-- declined, so questions stay rare and never repeat a declined topic.
CREATE TABLE profile_topic (
    person_id bigint NOT NULL REFERENCES person (id),
    field text NOT NULL,
    asks smallint NOT NULL DEFAULT 0 CHECK (asks >= 0),
    last_asked_at timestamptz,
    declined_at timestamptz,
    PRIMARY KEY (person_id, field)
);

-- Set by /start: ask the open topics now, one per reply, until a reply asks
-- nothing.
CREATE TABLE profile_pacing (
    person_id bigint PRIMARY KEY REFERENCES person (id),
    asking_now_since timestamptz
);

-- Buttons handled by code instead of the model. Undo targets the turn whose
-- values it reverts; Remove targets the active entry.
ALTER TABLE reply_button ADD COLUMN action text CHECK (
    action IN ('profile_undo', 'profile_remove')
);
ALTER TABLE reply_button ADD COLUMN target_id bigint;
ALTER TABLE reply_button ADD CONSTRAINT reply_button_target_check CHECK (
    (action IS NULL) = (target_id IS NULL)
);

-- Commands answered by code: /profile.
ALTER TABLE inbound_event DROP CONSTRAINT inbound_event_kind_check;
ALTER TABLE inbound_event ADD CONSTRAINT inbound_event_kind_check CHECK (
    kind IN ('message', 'edit', 'unsupported', 'button', 'poll_answer', 'stop', 'command')
);
-- A command turn holds only its command and is answered without a model call.
ALTER TABLE turn ADD COLUMN command text CHECK (command IN ('profile'));

-- The bot's own name is set through the outbox, so it is retried and sent once.
-- It belongs to no turn or message.
ALTER TABLE outbox DROP CONSTRAINT outbox_kind_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_kind_check CHECK (
    kind IN ('rich', 'plain', 'reaction', 'poll', 'bot_name')
);
ALTER TABLE outbox DROP CONSTRAINT outbox_text_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_text_check CHECK (
    (kind IN ('rich', 'plain', 'bot_name')) = (text IS NOT NULL)
);
ALTER TABLE outbox ALTER COLUMN turn_id DROP NOT NULL;
ALTER TABLE outbox ALTER COLUMN message_id DROP NOT NULL;
ALTER TABLE outbox ADD CONSTRAINT outbox_turn_check CHECK (
    kind = 'bot_name' OR (turn_id IS NOT NULL AND message_id IS NOT NULL)
);
