UPDATE agents
SET node_schema = JSON_ARRAY(
    JSON_OBJECT(
        'key', 'brief',
        'display_name', 'Brief Research',
        'description', 'Extract the audience, goal, language, and fact sheet used by downstream nodes.',
        'requires_model', true
    ),
    JSON_OBJECT(
        'key', 'outline',
        'display_name', 'Outline Planning',
        'description', 'Build the presentation storyline, section order, and slide-level key points.',
        'requires_model', true
    ),
    JSON_OBJECT(
        'key', 'style',
        'display_name', 'Art Direction',
        'description', 'Prepare visual theme tokens and style samples.',
        'requires_model', true
    ),
    JSON_OBJECT(
        'key', 'writer',
        'display_name', 'Slide Writer',
        'description', 'Expand each outline item into structured slide content.',
        'requires_model', true
    ),
    JSON_OBJECT(
        'key', 'designer',
        'display_name', 'Slide Designer',
        'description', 'Render slide content into local SVG, HTML previews, and PPT assets.',
        'requires_model', true
    )
WHERE agent_key = 'ppt_agent';

DELETE binding
FROM agent_config_node_bindings AS binding
INNER JOIN agent_configs AS config
    ON config.id = binding.agent_config_id
INNER JOIN agents AS agent
    ON agent.id = config.agent_id
WHERE agent.agent_key = 'ppt_agent'
  AND binding.node_key NOT IN ('brief', 'outline', 'style', 'writer', 'designer');

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
