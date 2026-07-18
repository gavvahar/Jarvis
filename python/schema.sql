CREATE TABLE IF NOT EXISTS user_configs (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'user',
    provider    TEXT NOT NULL DEFAULT 'anthropic',
    api_key     TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT 'claude-haiku-4-5',
    base_url    TEXT NOT NULL DEFAULT '',
    ha_url      TEXT NOT NULL DEFAULT '',
    ha_token    TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS webhook_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS myq_email TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS myq_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_method TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_fleet_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_access_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_token_expiry DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS apple_music_user_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS apple_music_storefront TEXT NOT NULL DEFAULT 'us';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_url TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_username TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_url TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_username TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS voice_embedding JSONB;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS is_kid_safe BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS face_embedding JSONB;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS is_home BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS briefing_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS briefing_morning_time TEXT NOT NULL DEFAULT '07:00';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS briefing_evening_time TEXT NOT NULL DEFAULT '18:00';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS briefing_last_morning_sent DATE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS briefing_last_evening_sent DATE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS habit_nudges_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS habit_nudge_last_sent DATE;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS email_host TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS email_username TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS email_password TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS shared_lists (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    items       JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_lists_name ON shared_lists (name);

INSERT INTO shared_lists (name, items) VALUES ('shopping', '[]'), ('todo', '[]') ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS timers (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    label       TEXT NOT NULL DEFAULT 'Timer',
    fire_at     TIMESTAMPTZ NOT NULL,
    fired       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_timers_user ON timers (user_id, fire_at);

CREATE TABLE IF NOT EXISTS reminders (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    text                TEXT NOT NULL,
    fire_at             TIMESTAMPTZ NOT NULL,
    recurring_minutes   INTEGER,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id, fire_at);

CREATE TABLE IF NOT EXISTS routines (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    trigger_phrases JSONB NOT NULL DEFAULT '[]',
    steps           JSONB NOT NULL DEFAULT '[]',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_routines_user ON routines (user_id);

CREATE TABLE IF NOT EXISTS device_alert_rules (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    condition        TEXT NOT NULL,
    value            TEXT NOT NULL DEFAULT '',
    message          TEXT NOT NULL,
    cooldown_minutes INTEGER NOT NULL DEFAULT 30,
    last_fired       TIMESTAMPTZ,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_device_alerts_user ON device_alert_rules (user_id);

CREATE TABLE IF NOT EXISTS phone_messages (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    sender      TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    important   BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_phone_messages_user ON phone_messages (user_id, received_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS meetings (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    transcript  TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meetings_user ON meetings (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS doorbell_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doorbell_events_user ON doorbell_events (user_id, received_at DESC);

CREATE TABLE IF NOT EXISTS cameras (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    room        TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'ha',
    source      TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    privacy     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cameras_user ON cameras (user_id);

CREATE TABLE IF NOT EXISTS person_detections (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    camera_id        BIGINT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    detected_user_id TEXT,
    confidence       REAL NOT NULL DEFAULT 0.0,
    room             TEXT NOT NULL DEFAULT '',
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_person_detections_user ON person_detections (user_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS presence_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_presence_events_user ON presence_events (user_id, event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS security_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    camera_id   BIGINT REFERENCES cameras(id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    room        TEXT NOT NULL DEFAULT '',
    snapshot    BYTEA,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vigil_state (
    id          BIGINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    mode        TEXT NOT NULL DEFAULT 'auto',
    updated_by  TEXT REFERENCES user_configs(user_id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO vigil_state (id, mode) VALUES (1, 'auto') ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, endpoint)
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions (user_id);

CREATE TABLE IF NOT EXISTS plaid_items (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    item_id          TEXT NOT NULL UNIQUE,
    access_token     TEXT NOT NULL,
    institution_id   TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    cursor           TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plaid_items_user ON plaid_items (user_id);

CREATE TABLE IF NOT EXISTS plaid_accounts (
    id                 BIGSERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    item_id            BIGINT NOT NULL REFERENCES plaid_items(id) ON DELETE CASCADE,
    account_id         TEXT NOT NULL UNIQUE,
    name               TEXT NOT NULL DEFAULT '',
    official_name      TEXT NOT NULL DEFAULT '',
    mask               TEXT NOT NULL DEFAULT '',
    type               TEXT NOT NULL DEFAULT '',
    subtype            TEXT NOT NULL DEFAULT '',
    balance_current    REAL,
    balance_available  REAL,
    balance_limit      REAL,
    iso_currency       TEXT NOT NULL DEFAULT 'USD',
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plaid_accounts_user ON plaid_accounts (user_id);
CREATE INDEX IF NOT EXISTS idx_plaid_accounts_item ON plaid_accounts (item_id);

CREATE TABLE IF NOT EXISTS plaid_transactions (
    id                        BIGSERIAL PRIMARY KEY,
    user_id                   TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    account_id                TEXT NOT NULL REFERENCES plaid_accounts(account_id) ON DELETE CASCADE,
    transaction_id            TEXT NOT NULL UNIQUE,
    amount                    REAL NOT NULL DEFAULT 0.0,
    iso_currency              TEXT NOT NULL DEFAULT 'USD',
    date                      DATE NOT NULL,
    merchant_name             TEXT NOT NULL DEFAULT '',
    name                      TEXT NOT NULL DEFAULT '',
    category                  TEXT NOT NULL DEFAULT '',
    personal_finance_category TEXT NOT NULL DEFAULT '',
    category_override         TEXT,
    pending                   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plaid_transactions_user ON plaid_transactions (user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_plaid_transactions_account ON plaid_transactions (account_id);

CREATE INDEX IF NOT EXISTS idx_security_events_user ON security_events (user_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS travel_trips (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    airline          TEXT NOT NULL,
    flight_number    TEXT NOT NULL,
    flight_date      DATE NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Scheduled',
    gate             TEXT NOT NULL DEFAULT '',
    terminal         TEXT NOT NULL DEFAULT '',
    departure_time   TIMESTAMPTZ,
    last_checked_at  TIMESTAMPTZ,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_travel_trips_user ON travel_trips (user_id);
CREATE INDEX IF NOT EXISTS idx_travel_trips_active ON travel_trips (active, flight_date);
