UPDATE agents
SET node_schema = JSON_ARRAY(
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
)
WHERE agent_key = 'ppt_agent';

DELETE binding
FROM agent_config_node_bindings AS binding
INNER JOIN agent_configs AS config
    ON config.id = binding.agent_config_id
INNER JOIN agents AS agent
    ON agent.id = config.agent_id
WHERE agent.agent_key = 'ppt_agent'
  AND binding.node_key NOT IN ('outline', 'style', 'designer');

-- PPT generation is now an in-memory, single-run stream. The former chat,
-- resumable job, persisted event, action, and versioned asset tables are no
-- longer part of the product model.
DROP TABLE IF EXISTS ppt_deck_job_assets;
DROP TABLE IF EXISTS ppt_deck_job_actions;
DROP TABLE IF EXISTS ppt_deck_job_events;
DROP TABLE IF EXISTS ppt_deck_job_sources;
DROP TABLE IF EXISTS ppt_deck_jobs;
DROP TABLE IF EXISTS ppt_chat_messages;
DROP TABLE IF EXISTS ppt_chat_conversations;
