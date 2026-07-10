ALTER TABLE ppt_deck_job_sources
    MODIFY COLUMN content LONGBLOB NULL,
    ADD COLUMN resource_id CHAR(36) NOT NULL DEFAULT '' AFTER content,
    ADD COLUMN relative_path VARCHAR(1024) NOT NULL DEFAULT '' AFTER resource_id,
    ADD COLUMN sha256 CHAR(64) NOT NULL DEFAULT '' AFTER relative_path,
    ADD KEY idx_ppt_deck_job_sources_path (job_id, relative_path(255));
