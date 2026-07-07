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
            'display_name', '大纲规划',
            'description', '生成叙事结构、章节顺序和每页核心信息。',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'slides',
            'display_name', '页面内容',
            'description', '把大纲扩写成每页标题、要点、视觉建议和讲稿备注。',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'review',
            'display_name', '质量审核',
            'description', '检查页数、重复标题、信息完整性和改进建议。',
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
