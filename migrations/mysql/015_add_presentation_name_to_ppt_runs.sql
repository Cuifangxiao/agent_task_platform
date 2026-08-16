ALTER TABLE ppt_runs
    ADD COLUMN presentation_name VARCHAR(160) NOT NULL DEFAULT '' AFTER agent_config_id;

UPDATE ppt_runs
SET presentation_name = LEFT(TRIM(REPLACE(REPLACE(description, '\r', ' '), '\n', ' ')), 120)
WHERE presentation_name = '';
