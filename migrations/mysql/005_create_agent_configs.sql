CREATE TABLE IF NOT EXISTS agents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    agent_key VARCHAR(64) NOT NULL,
    name VARCHAR(64) NOT NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    node_schema JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_agents_agent_key (agent_key),
    KEY idx_agents_status (status)
);

INSERT IGNORE INTO agents (
    agent_key,
    name,
    description,
    node_schema,
    status
) VALUES (
    'ppt_agent',
    'PPTAgent',
    'Python PPT deck generation agent.',
    JSON_ARRAY(
        JSON_OBJECT(
            'key', 'outline',
            'display_name', 'Deck Outline Planning',
            'description', 'Create the deck-wide outline, global style palette, and slide-level content plan.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'style',
            'display_name', 'Slide Visual Planning',
            'description', 'Create an auditable visual design plan for each slide using the deck-wide style.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'designer',
            'display_name', 'HTML Slide Generation',
            'description', 'Generate and repair safe local slide HTML from the corresponding visual design plan.',
            'requires_model', true
        )
    ),
    'active'
);

CREATE TABLE IF NOT EXISTS agent_configs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    agent_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(64) NOT NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    is_default TINYINT(1) NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_agent_configs_user_id FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_agent_configs_agent_id FOREIGN KEY (agent_id) REFERENCES agents (id),
    UNIQUE KEY uk_agent_configs_user_agent_name (user_id, agent_id, name),
    KEY idx_agent_configs_user_agent (user_id, agent_id),
    KEY idx_agent_configs_user_agent_default (user_id, agent_id, is_default)
);

CREATE TABLE IF NOT EXISTS agent_config_node_bindings (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    agent_config_id BIGINT UNSIGNED NOT NULL,
    node_key VARCHAR(64) NOT NULL,
    model_config_id BIGINT UNSIGNED NOT NULL,
    temperature DECIMAL(4,2) NULL,
    max_tokens INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_agent_config_node_bindings_config_id
        FOREIGN KEY (agent_config_id) REFERENCES agent_configs (id)
        ON DELETE CASCADE,
    UNIQUE KEY uk_agent_config_node_bindings_config_node (agent_config_id, node_key),
    KEY idx_agent_config_node_bindings_config_id (agent_config_id),
    KEY idx_agent_config_node_bindings_model_config_id (model_config_id)
);
