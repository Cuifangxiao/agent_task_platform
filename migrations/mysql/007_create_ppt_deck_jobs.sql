CREATE TABLE IF NOT EXISTS ppt_deck_jobs (
    id CHAR(36) NOT NULL PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    conversation_id BIGINT UNSIGNED NOT NULL,
    agent_config_id BIGINT UNSIGNED NOT NULL,
    request_id VARCHAR(64) NULL,
    mode VARCHAR(24) NOT NULL DEFAULT 'step_by_step',
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    stage VARCHAR(32) NOT NULL DEFAULT 'brief',
    error_code VARCHAR(64) NOT NULL DEFAULT '',
    error_summary VARCHAR(1000) NOT NULL DEFAULT '',
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    last_event_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0,
    input_json JSON NOT NULL,
    quality_json JSON NOT NULL,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_deck_jobs_user_id FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_ppt_deck_jobs_conversation_id
        FOREIGN KEY (conversation_id) REFERENCES ppt_chat_conversations (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ppt_deck_jobs_agent_config_id FOREIGN KEY (agent_config_id) REFERENCES agent_configs (id),
    UNIQUE KEY uk_ppt_deck_jobs_request_id (request_id),
    KEY idx_ppt_deck_jobs_user_updated (user_id, updated_at),
    KEY idx_ppt_deck_jobs_recovery (status, updated_at)
);

CREATE TABLE IF NOT EXISTS ppt_deck_job_sources (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id CHAR(36) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content LONGBLOB NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_deck_job_sources_job_id
        FOREIGN KEY (job_id) REFERENCES ppt_deck_jobs (id)
        ON DELETE CASCADE,
    KEY idx_ppt_deck_job_sources_job (job_id, id)
);

CREATE TABLE IF NOT EXISTS ppt_deck_job_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id CHAR(36) NOT NULL,
    sequence BIGINT UNSIGNED NOT NULL,
    event_version SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    request_id VARCHAR(64) NOT NULL DEFAULT '',
    conversation_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    stage_status VARCHAR(16) NOT NULL,
    message VARCHAR(2000) NOT NULL DEFAULT '',
    progress_current INT NOT NULL DEFAULT 0,
    progress_total INT NOT NULL DEFAULT 0,
    slide_index INT NOT NULL DEFAULT 0,
    slide_version INT NOT NULL DEFAULT 0,
    severity VARCHAR(16) NOT NULL DEFAULT 'info',
    action_required VARCHAR(32) NULL,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    error_code VARCHAR(64) NOT NULL DEFAULT '',
    payload_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_deck_job_events_job_id
        FOREIGN KEY (job_id) REFERENCES ppt_deck_jobs (id)
        ON DELETE CASCADE,
    UNIQUE KEY uk_ppt_deck_job_events_sequence (job_id, sequence),
    KEY idx_ppt_deck_job_events_created (job_id, created_at)
);

CREATE TABLE IF NOT EXISTS ppt_deck_job_actions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id CHAR(36) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'accepted',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_deck_job_actions_job_id
        FOREIGN KEY (job_id) REFERENCES ppt_deck_jobs (id)
        ON DELETE CASCADE,
    UNIQUE KEY uk_ppt_deck_job_actions_idempotency (job_id, idempotency_key),
    KEY idx_ppt_deck_job_actions_job (job_id, id)
);

CREATE TABLE IF NOT EXISTS ppt_deck_job_assets (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id CHAR(36) NOT NULL,
    request_id VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    slide_index INT NOT NULL DEFAULT 0,
    version INT NOT NULL DEFAULT 1,
    relative_path VARCHAR(1024) NOT NULL,
    quality_status VARCHAR(32) NOT NULL DEFAULT 'ready',
    metadata_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_deck_job_assets_job_id
        FOREIGN KEY (job_id) REFERENCES ppt_deck_jobs (id)
        ON DELETE CASCADE,
    UNIQUE KEY uk_ppt_deck_job_assets_version (job_id, kind, slide_index, version),
    KEY idx_ppt_deck_job_assets_request (request_id, relative_path(255))
);
