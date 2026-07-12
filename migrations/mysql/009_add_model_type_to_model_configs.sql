SET @column_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'model_configs'
      AND COLUMN_NAME = 'model_type'
);

SET @ddl := IF(
    @column_exists = 0,
    'ALTER TABLE model_configs ADD COLUMN model_type VARCHAR(16) NOT NULL DEFAULT ''text'' AFTER model_name',
    'SELECT 1'
);

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE model_configs
SET model_type = 'text'
WHERE model_type IS NULL
   OR TRIM(model_type) = ''
   OR model_type NOT IN ('text', 'multimodal', 'image');

UPDATE model_configs
SET model_type = 'multimodal'
WHERE (
        LOWER(provider) = 'openai'
        AND LOWER(model_name) IN ('gpt-5.5', 'gpt-5.4-mini', 'gpt-4o-mini')
    )
    OR (
        LOWER(provider) = 'qwen'
        AND LOWER(model_name) IN ('qwen3-vl-plus', 'qwen3-vl-flash', 'qwen-vl-plus')
    );
