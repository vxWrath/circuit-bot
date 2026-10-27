CREATE TABLE teams (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL,
    role_snowflake   bigint,
    emoji_snowflake  bigint,
    logo_url         text,
    active           boolean NOT NULL DEFAULT false,
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    previous_team_id bigint UNIQUE REFERENCES teams(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- A name is claimed while its team is pending, or approved and active; a
-- denied or deactivated team frees it.
CREATE UNIQUE INDEX teams_lower_name_key ON teams (lower(name)) WHERE status = 'pending' OR (status = 'approved' AND active);