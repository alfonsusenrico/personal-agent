## MODIFIED Requirements

### Requirement: Every inbound event is kept unchanged and channel-neutral

The assistant SHALL record each accepted inbound event once, permanently and
without later modification, with: the person, channel, external identifiers,
kind (message, edit, unsupported content, button press, poll vote, stop,
command, or reaction), modality, the time it occurred
in UTC, the person's timezone at that moment, the trust level, any forward
origin, any reply reference, the text, and the raw channel payload. Edits SHALL
be recorded as new events that reference the original.

#### Scenario: Message recorded

- **WHEN** the owner sends "lunch with Rina tomorrow"
- **THEN** one event exists with the text, its UTC time, the owner's
  timezone, channel `telegram`, and trust `owner_direct`

#### Scenario: Edit recorded

- **WHEN** the owner edits that message
- **THEN** a new edit event references the original, and the original event
  is unchanged

#### Scenario: Button press recorded

- **WHEN** the owner presses a quick reply labelled "Tomorrow"
- **THEN** one event of kind `button` exists with the text "Tomorrow" and a
  link to the message that carried the button

#### Scenario: Reaction recorded

- **WHEN** the owner reacts ❤️ to one of the assistant's replies
- **THEN** one event of kind `reaction` exists with the raw payload, and no
  reply is sent
