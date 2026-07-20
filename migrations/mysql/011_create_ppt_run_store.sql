CREATE TABLE IF NOT EXISTS ppt_runs (
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    owner_id BIGINT UNSIGNED NOT NULL,
    thread_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    client_request_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    request_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    agent_config_id BIGINT UNSIGNED NULL,
    description TEXT NOT NULL,
    options_json JSON NOT NULL,
    schema_version VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT '1',
    event_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stage VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    message TEXT NOT NULL,
    last_event_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0,
    result_json JSON NULL,
    error_json JSON NULL,
    file_url VARCHAR(1024) NOT NULL DEFAULT '',
    worker_active TINYINT(1) NOT NULL DEFAULT 1,
    active_thread_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
        GENERATED ALWAYS AS (
            CASE WHEN worker_active = 1 THEN thread_id ELSE NULL END
        ) STORED,
    heartbeat_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    worker_finished_at DATETIME(6) NULL,
    CONSTRAINT fk_ppt_runs_owner_id
        FOREIGN KEY (owner_id) REFERENCES users (id),
    UNIQUE KEY uk_ppt_runs_owner_client_request (owner_id, client_request_id),
    UNIQUE KEY uk_ppt_runs_owner_active_thread (owner_id, active_thread_id),
    KEY idx_ppt_runs_owner_created (owner_id, created_at, run_id),
    KEY idx_ppt_runs_owner_thread_created (owner_id, thread_id, created_at, run_id),
    KEY idx_ppt_runs_status_updated (status, updated_at)
);

CREATE TABLE IF NOT EXISTS ppt_run_events (
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    sequence BIGINT UNSIGNED NOT NULL,
    event_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    schema_version VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    event_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    channel VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stage VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (run_id, sequence),
    UNIQUE KEY uk_ppt_run_events_event_id (event_id),
    KEY idx_ppt_run_events_run_created (run_id, created_at),
    CONSTRAINT fk_ppt_run_events_run_id
        FOREIGN KEY (run_id) REFERENCES ppt_runs (run_id)
        ON DELETE CASCADE
);
