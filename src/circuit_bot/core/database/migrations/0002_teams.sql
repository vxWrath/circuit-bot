CREATE TABLE teams (
    id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                      text NOT NULL,
    owner                     bigint NOT NULL REFERENCES players(snowflake),
    role_snowflake            bigint,
    emoji_snowflake           bigint,
    logo_url                  text,
    request_message_snowflake bigint,

    status                    text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied', 'inactive')),
    approved_at               timestamptz,
    inactivated_at            timestamptz,

    previous_team_id          bigint UNIQUE REFERENCES teams(id),

    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- A name is claimed while its team is pending or approved; denied and
-- inactive teams free it.
CREATE UNIQUE INDEX teams_lower_name_key
    ON teams (lower(name))
    WHERE status IN ('pending', 'approved');

-- One live team per owner; the slot frees when the team is denied or leaves.
CREATE UNIQUE INDEX teams_one_team_per_owner
    ON teams (owner)
    WHERE status IN ('pending', 'approved');
