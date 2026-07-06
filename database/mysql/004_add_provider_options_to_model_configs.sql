SET @column_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'model_configs'
      AND COLUMN_NAME = 'provider_options'
);

SET @ddl := IF(
    @column_exists = 0,
    'ALTER TABLE model_configs ADD COLUMN provider_options JSON NULL AFTER api_key',
    'SELECT 1'
);

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE model_configs
SET provider_options = JSON_OBJECT()
WHERE provider_options IS NULL;
