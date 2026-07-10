INSERT INTO users (username, password_hash)
VALUES ('admin', '$2a$10$vdIhytmwyKoWEPgcdR8yne0HT4Q2ZDVy/L6GDyFINkSPFG9XTYetG')
ON DUPLICATE KEY UPDATE
    password_hash = VALUES(password_hash),
    updated_at = CURRENT_TIMESTAMP;
