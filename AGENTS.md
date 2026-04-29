# Beakon Agent Instructions

## Quick Start
- **Initialization**: Run `./setup.sh` to install dependencies, generate `config.cfg`, and initialize the database.
- **Development**:
  - Run the server: `source .venv/bin/activate && python -c "import sys; sys.path.insert(0, 'src'); from app import app; app.run()"`

## Architecture & Workflows

### Flask-SQLAlchemy Circular Import Issue
- **The Quirk**: `src/models.py` and `src/app.py` have a circular dependency. 
- **The Fix**: Always import `db` from `src/models.py`. When initializing the app or running `db.create_all()`, wrap calls in `with app.app_context()` and explicitly import models (e.g., `from src.models import UserModel, ...`) within that block to ensure they register with the `db` registry.

### Database Operations
- **Schema Updates**: `db.create_all()` at startup is used for schema management. If a table (like `known_places`) is missing in production, run manual `CREATE TABLE` SQL command via `sqlite3 instance/beakon.db`.
- **Performance**: Use `.yield_per(100)` for large dataset queries in `src/app.py`.

### Security
- **Talisman**: Uses `Flask-Talisman` for security headers. 
- **CSP**: Includes `'unsafe-eval'` (for Flatpickr date picker) and `'unsafe-inline'` (for legacy JS and inline styling).
- **Cross-Origin**: Policies (`COOP`, `CORP`, `COEP`) are set via `@app.after_request` to ensure compatibility with Mapbox tiles and `credentialless` embedding.

### Operational Gotchas
- **Map Loading**: If map tiles fail to load, ensure `mapboxapi` is passed to the `render_template` context.
- **Auto-Refresh**: The dashboard auto-refreshes every 10s via JS, but only recenters on the map when `data.latest.timestamp` changes (smart-refresh).
- **Live Tracking**: Tracking toggle state is persisted in `sessionStorage`. Battery data is polled via `navigator.getBattery()` on every 10-second location tick.
