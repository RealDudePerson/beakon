-- Migration to add include_coords_in_webhook column to known_places table
ALTER TABLE known_places ADD COLUMN include_coords_in_webhook BOOLEAN DEFAULT 0;
