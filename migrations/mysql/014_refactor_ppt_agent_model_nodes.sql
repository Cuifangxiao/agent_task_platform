-- Align the administrator-facing Agent definition with the three-stage PPT
-- generation flow. The upsert and cleanup are intentionally repeatable because
-- the migration runner records a migration only after every statement succeeds.
INSERT INTO agents (
    agent_key,
    name,
    description,
    node_schema,
    status
) VALUES (
    'ppt_agent',
    'PPTAgent',
    'Three-stage HTML-first PPT deck generation agent.',
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
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    node_schema = VALUES(node_schema),
    status = VALUES(status),
    updated_at = CURRENT_TIMESTAMP;

-- Keep the surviving bindings untouched, including their model, temperature,
-- and max-token overrides. This removes legacy brief/writer bindings as well as
-- any other node that the current PPT Agent no longer exposes.
DELETE binding
FROM agent_config_node_bindings AS binding
INNER JOIN agent_configs AS config
    ON config.id = binding.agent_config_id
INNER JOIN agents AS agent
    ON agent.id = config.agent_id
WHERE agent.agent_key = 'ppt_agent'
  AND binding.node_key NOT IN ('outline', 'style', 'designer');
