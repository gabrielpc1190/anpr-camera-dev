#!/bin/sh
# Exit immediately if a command exits with a non-zero status.
set -e

# Run the database creation/migration script
echo "--- Running database initialization script... ---"
python /app/create_db.py

# Now, execute the main command (gunicorn)
echo "--- Starting Gunicorn server... ---"
exec "$@"
