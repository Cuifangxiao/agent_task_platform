-- Keep the administrator-facing Agent definition aligned with the model
-- assignments consumed by the current HTML-first PPT generation pipeline.
-- This is an upsert because an operator may have removed the seeded row after
-- the original 005 migration was recorded as applied.
INSERT INTO agents (
    agent_key,
    name,
    description,
    node_schema,
    status
) VALUES (
    'ppt_agent',
    'PPTAgent',
    'HTML-first PPT deck generation agent.',
    JSON_ARRAY(
        JSON_OBJECT(
            'key', 'brief',
            'display_name', 'Brief Research',
            'description', 'Normalize the request and extract the audience, goal, language, and source facts.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'outline',
            'display_name', 'Outline Planning',
            'description', 'Plan the presentation storyline, section order, and slide-level intent.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'style',
            'display_name', 'Art Direction',
            'description', 'Select the visual template and create the deck-wide design specification.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'writer',
            'display_name', 'Slide Writer',
            'description', 'Generate structured content for each planned slide.',
            'requires_model', true
        ),
        JSON_OBJECT(
            'key', 'designer',
            'display_name', 'HTML Slide Designer',
            'description', 'Generate and repair slide HTML before rendering previews and the final PPTX.',
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

DELETE binding
FROM agent_config_node_bindings AS binding
INNER JOIN agent_configs AS config
    ON config.id = binding.agent_config_id
INNER JOIN agents AS agent
    ON agent.id = config.agent_id
WHERE agent.agent_key = 'ppt_agent'
  AND binding.node_key NOT IN ('brief', 'outline', 'style', 'writer', 'designer');
