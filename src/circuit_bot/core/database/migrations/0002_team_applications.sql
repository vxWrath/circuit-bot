-- A team application: the request that becomes a team at approval, and its provenance after.
CREATE TABLE team_applications (
    id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                      text NOT NULL,
    owner                     bigint NOT NULL REFERENCES players(snowflake),
    logo_url                  text,
    request_message_snowflake bigint,
    previous_team_id          bigint,  -- FK to teams added in 0003, once teams exists
    status                    text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'denied', 'approved', 'released')),
    decided_at                timestamptz,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- A name is claimed while the application is pending or approved.
CREATE UNIQUE INDEX team_applications_lower_name_key
    ON team_applications (lower(name))
    WHERE status IN ('pending', 'approved');

-- One live application per owner; denied and released free the slot, so one
-- live team per owner follows.
CREATE UNIQUE INDEX team_applications_one_per_owner
    ON team_applications (owner)
    WHERE status IN ('pending', 'approved');
