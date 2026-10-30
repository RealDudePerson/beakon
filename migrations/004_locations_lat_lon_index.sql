-- Presence hybrid bounding-box prefilter.
-- Lets sparse presence search points be answered from an indexed lat/lon range
-- instead of scanning a user's entire location history.
--
-- Deliberately NOT (userid, lat, lon): a userid-leading index competes with
-- idx_userid_timestamp, making the dense-path "ORDER BY timestamp DESC" early-exit
-- scan fall back to a full TEMP B-TREE sort of the whole table.
DROP INDEX IF EXISTS idx_locations_userid_lat_lon;
CREATE INDEX IF NOT EXISTS idx_locations_lat_lon
    ON locations_table (lat, lon);
