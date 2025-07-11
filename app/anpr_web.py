# app/anpr_web.py (Temporary ultra-minimal version)
import logging
import os
import sys

# --- Aggressive Early Logging Setup ---
EARLY_LOG_FILE_PATH = '/app/logs/anpr_web_startup.log'
early_logger = None # Define early_logger initially as None

try:
    # Try to make the directory just in case, though it should be mounted
    # Ensure /app/logs path exists from the script's perspective.
    # The docker-compose.yml mounts ./app/logs to /app/logs,
    # and setup.sh should create ./app/logs on the host.
    log_dir = os.path.dirname(EARLY_LOG_FILE_PATH)
    if not os.path.exists(log_dir):
        # This is an attempt to create it from within the container if it's missing.
        # However, permissions might be an issue if the base /app directory isn't writable by the user running the script.
        # The primary creation should be via setup.sh creating ./app/logs on host.
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception as mkdir_e:
            # If directory creation fails, log to stderr.
            sys.stderr.write(f"CRITICAL_ERROR_MINIMAL_WEB_PY_STDERR: Could not create log directory {log_dir}: {mkdir_e}\\n")
            # No point continuing if we can't make the log directory for the file handler.
            # However, Gunicorn might restart the worker, so sys.exit(1) might be too abrupt here
            # if we want to see if other workers fare better.
            # For now, let the FileHandler attempt fail and be caught below.

    early_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s')
    early_fh = logging.FileHandler(EARLY_LOG_FILE_PATH, mode='a')
    early_fh.setFormatter(early_formatter)

    early_logger_instance = logging.getLogger('anpr_web_early_startup')
    early_logger_instance.setLevel(logging.DEBUG)
    early_logger_instance.addHandler(early_fh)
    early_logger = early_logger_instance # Assign to global scope if successful

    early_logger.info('--- MINIMAL anpr_web.py script execution started (early log) ---')

    # Attempt to create the Flask app object
    early_logger.info('--- MINIMAL anpr_web.py: Attempting to import Flask ---')
    from flask import Flask, jsonify
    early_logger.info('--- MINIMAL anpr_web.py: Flask imported. Creating app object. ---')
    app = Flask(__name__)
    early_logger.info('--- MINIMAL anpr_web.py: Flask app object created ---')

    # Add a dummy health endpoint that should always work if Flask starts
    @app.route('/health')
    def health_check():
        if early_logger:
            early_logger.info('--- MINIMAL anpr_web.py: /health endpoint called ---')
        return jsonify({"status": "minimal_ok"}), 200 # Return JSON as expected by some parsers

    early_logger.info('--- MINIMAL anpr_web.py script finished initial setup (early log) ---')

except Exception as e:
    # Log to file if early_logger was successfully initialized
    if early_logger and early_logger.hasHandlers():
        early_logger.exception(f"CRITICAL_ERROR_IN_MINIMAL_WEB_PY: {e}")
    # Also print to stderr as a fallback
    sys.stderr.write(f"CRITICAL_ERROR_IN_MINIMAL_WEB_PY_STDERR: {e}\\n")
    # Optional: sys.exit(1) here could make the worker fail hard if Gunicorn is managing it.
    # This might be useful to prevent Gunicorn from endlessly restarting a fundamentally broken script.
    # However, if the goal is to see if *any* part of the script can run,
    # letting Gunicorn handle it might allow some logs to be written before it's killed.
    # For now, let Gunicorn decide based on the exception propagating.
    # If 'app' is not defined due to an exception here, Gunicorn will fail to load it.
    raise # Re-raise the exception so Gunicorn knows the app setup failed.

# If Gunicorn is running this, it needs 'app' to be defined.
# If an exception occurred above and 'app' isn't defined, Gunicorn will fail.
if 'app' not in locals():
    # This case handles if an exception occurred above before 'app' was defined,
    # and we didn't sys.exit(1). We must ensure Gunicorn doesn't start without 'app'.
    # Log this critical state to stderr, as early_logger might not be available.
    critical_fallback_msg = "CRITICAL_FALLBACK: 'app' object not defined in MINIMAL anpr_web.py. Exiting."
    sys.stderr.write(f"{critical_fallback_msg}\\n")
    if early_logger and early_logger.hasHandlers(): # Try early_logger one last time
        early_logger.critical(critical_fallback_msg)
    sys.exit(1) # Hard exit if app object isn't there.
else:
    if early_logger:
         early_logger.info("--- MINIMAL anpr_web.py: 'app' object is defined. Gunicorn should be able to find it. ---")

# To make Gunicorn happy if it tries to run this directly for some reason without the `if __name__ == '__main__'` block
# (though it should be importing 'app').
# For safety, ensure the Flask dev server part is only for direct execution.
if __name__ == '__main__':
    if early_logger:
        early_logger.info("--- MINIMAL anpr_web.py: Running in __main__ context (direct execution) ---")
    # This part is for direct execution, not for Gunicorn.
    # Gunicorn will pick up the 'app' object defined above.
    try:
        app.run(host='0.0.0.0', port=5000, debug=False) # Use a fixed port for direct run
        if early_logger:
            early_logger.info("--- MINIMAL anpr_web.py: Flask development server started via app.run() ---")
    except Exception as e:
        if early_logger:
            early_logger.exception(f"CRITICAL_ERROR_MINIMAL_FLASK_RUN_MAIN: app.run() failed: {e}")
        sys.stderr.write(f"CRITICAL_ERROR_MINIMAL_FLASK_RUN_MAIN_STDERR: {e}\\n")
        sys.exit(1)

# Final log to indicate the end of the script if reached by Gunicorn import path
if early_logger:
    early_logger.info("--- MINIMAL anpr_web.py: End of script reached (Gunicorn import path likely) ---")
