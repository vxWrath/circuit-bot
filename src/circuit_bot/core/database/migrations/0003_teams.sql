-- A team: the approved application, made real.
CREATE TABLE teams (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL,
    application_id   bigint NOT NULL UNIQUE REFERENCES team_applications(id),
    role_snowflake   bigint NOT NULL,
    emoji_snowflake  bigint,
    logo_url         text,
    status           text NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'inactive')),
    approved_at      timestamptz NOT NULL DEFAULT now(),
    inactivated_at   timestamptz,
    previous_team_id bigint UNIQUE REFERENCES teams(id),  -- one successor per team
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Close the circular reference: an application records which team it continued.
ALTER TABLE team_applications
    ADD CONSTRAINT team_applications_previous_team_id_fkey
    FOREIGN KEY (previous_team_id) REFERENCES teams(id);
