CREATE TABLE IF NOT EXISTS ppt_chat_conversations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    agent_config_id BIGINT UNSIGNED NOT NULL,
    agent_config_name VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    latest_request_id VARCHAR(64) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_chat_conversations_user_id FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_ppt_chat_conversations_agent_config_id FOREIGN KEY (agent_config_id) REFERENCES agent_configs (id),
    KEY idx_ppt_chat_conversations_user_updated (user_id, updated_at),
    KEY idx_ppt_chat_conversations_request_id (latest_request_id)
);

CREATE TABLE IF NOT EXISTS ppt_chat_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT UNSIGNED NOT NULL,
    role VARCHAR(16) NOT NULL,
    kind VARCHAR(24) NOT NULL,
    request_id VARCHAR(64) NOT NULL DEFAULT '',
    content_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ppt_chat_messages_conversation_id
        FOREIGN KEY (conversation_id) REFERENCES ppt_chat_conversations (id)
        ON DELETE CASCADE,
    KEY idx_ppt_chat_messages_conversation_created (conversation_id, created_at),
    KEY idx_ppt_chat_messages_conversation_request_kind (conversation_id, request_id, kind)
);
