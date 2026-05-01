# Beakon Feature Requests & Backlog

## Database Migration System
- [ ] **Transition to Flask-Migrate (Alembic)**
    - *Goal*: Automate schema upgrades to replace manual `migration.sql` patches.
    - *Plan*:
        1. Add `Flask-Migrate` dependency.
        2. Initialize `migrations/` directory.
        3. Perform baseline stamp on existing production database.
        4. Integrate automatic migration generation into development workflow.
    - *Benefit*: Tracking version history, automated schema diffs, and ability to downgrade if migrations fail.

## Health Monitoring Enhancements
- [ ] **Webhook Retry Logic**: Add a "Retry" button on the Health page for failed webhook deliveries.
- [ ] **Webhook Analytics**: Visualize successful vs. failed webhook trends on the Health dashboard.

## Dashboard & UI
- [ ] **Map Clustering**: Improve performance for users with extensive location histories.
- [ ] **Geofence Radius Rendering**: Refine visual representation of geofences on the map.
