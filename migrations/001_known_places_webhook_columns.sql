-- Migration to add webhook tracking columns to known_places table
ALTER TABLE known_places ADD COLUMN last_webhook_time DATETIME;
ALTER TABLE known_places ADD COLUMN last_webhook_status VARCHAR(20);

