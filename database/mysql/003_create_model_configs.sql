CREATE TABLE IF NOT EXISTS model_configs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    base_url VARCHAR(255) NOT NULL DEFAULT '',
    api_key VARCHAR(512) NOT NULL,
    temperature DECIMAL(4,2) NOT NULL DEFAULT 1.00,
    max_tokens INT NOT NULL DEFAULT 4096,
    is_default TINYINT(1) NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_model_configs_user_id FOREIGN KEY (user_id) REFERENCES users (id),
    UNIQUE KEY uk_model_configs_user_name (user_id, name),
    KEY idx_model_configs_user_id (user_id),
    KEY idx_model_configs_user_default (user_id, is_default)
);
