-- The owner profile: versioned entries, the introduction, code-handled buttons,
-- the /profile command, and the bot's own name sent through the outbox.

-- One row per value. A draft is an introduction answer not yet saved; a pending
-- row is a stated change waiting for Confirm; an active row is in force; a
-- superseded row is a confirmed value that a later one replaced. Removing a
-- field deletes all of its rows.
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
    status text NOT NULL CHECK (status IN ('draft', 'pending', 'active', 'superseded')),
    -- The inbound events of the turn whose reply marked this value.
    source_event_ids bigint[] NOT NULL,
    turn_id bigint REFERENCES turn (id),
    created_at timestamptz NOT NULL,
    confirmed_at timestamptz,
    superseded_at timestamptz,
    -- The Telegram name limit (setMyName).
    CHECK (field <> 'assistant_name' OR char_length(value) <= 64),
    CHECK ((status IN ('active', 'superseded')) = (confirmed_at IS NOT NULL)),
    CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))
);

CREATE UNIQUE INDEX profile_entry_one_active ON profile_entry (person_id, field)
    WHERE status = 'active';
CREATE UNIQUE INDEX profile_entry_one_pending ON profile_entry (person_id, field)
    WHERE status = 'pending';
CREATE UNIQUE INDEX profile_entry_one_draft ON profile_entry (person_id, field)
    WHERE status = 'draft';
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

-- The introduction, one per person: offered once, then declined, active, or
-- completed. Skipped topics are kept so a resumed introduction does not ask them.
CREATE TABLE introduction (
    person_id bigint PRIMARY KEY REFERENCES person (id),
    state text NOT NULL CHECK (state IN ('offered', 'declined', 'active', 'completed')),
    skipped text[] NOT NULL DEFAULT '{}',
    offered_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL,
    CHECK (state <> 'active' OR started_at IS NOT NULL),
    CHECK ((state = 'completed') = (completed_at IS NOT NULL))
);

-- Buttons handled by code instead of the model. The target is the introduction's
-- person, the turn whose pending changes Confirm or Cancel act on, or the
-- profile entry Remove deletes.
ALTER TABLE reply_button ADD COLUMN action text CHECK (
    action IN (
        'intro_start',
        'intro_later',
        'intro_save',
        'intro_edit',
        'profile_confirm',
        'profile_cancel',
        'profile_remove'
    )
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
