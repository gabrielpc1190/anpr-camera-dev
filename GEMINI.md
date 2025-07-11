# ANPR System Project Overview (GEMINI.MD)

This document provides a high-level overview of the ANPR (Automatic Number Plate Recognition) system for quick context understanding by an LLM.

## Core Objective

The system captures license plate data (including images and metadata) from multiple Dahua ANPR cameras, stores this information in a MariaDB database, and provides a web interface for querying and viewing the captured data.

## System Components

The project is structured as a multi-component system orchestrated by Docker Compose:

1.  **`anpr_listener` (`app/anpr_listener.py`)**
    *   **Purpose**: Connects to Dahua ANPR cameras using the Dahua NetSDK.
    *   **Functionality**:
        *   Reads camera connection details from `app/config.ini`.
        *   Subscribes to ANPR events from cameras.
        *   On receiving an event, extracts plate number, timestamp, vehicle details, and the event image.
        *   Sends this data (metadata as JSON, image as file) via an HTTP POST request to the `anpr_db_manager` service.
        *   Designed for high speed and reliability; communication with `anpr_db_manager` is asynchronous (threaded) to prevent blocking.
    *   **Key Technologies**: Python, Dahua NetSDK, `requests` library, `configparser`.

2.  **`anpr_db_manager` (`app/anpr_db_manager.py`)**
    *   **Purpose**: Manages the storage of ANPR event data and images.
    *   **Functionality**:
        *   A Flask web application exposing an HTTP API endpoint (typically `/event` on port 5001).
        *   Receives event data (JSON) and image files from `anpr_listener`.
        *   Saves received images to a shared volume (configured via `ImageDirectory` in `app/config.ini`, typically `/app/anpr_images`).
        *   Inserts event metadata (including the filename of the saved image) into the `anpr_events` table in the MariaDB database.
        *   Handles database connections and table creation/verification.
    *   **Key Technologies**: Python, Flask, `mysql-connector-python`, `configparser`.

3.  **`anpr_web` (`app/anpr_web.py`)**
    *   **Purpose**: Provides the backend for the web-based user interface.
    *   **Functionality**:
        *   A Flask web application (typically running on port 5000).
        *   Serves the main `index.html` page (`app/templates/index.html`).
        *   Exposes several API endpoints that are consumed by `index.html`:
            *   `/api/events`: Fetches ANPR events from MariaDB with support for filtering (plate, camera, vehicle type/color, direction, dates) and pagination.
            *   `/api/cameras`: Provides a list of unique camera IDs found in the database.
            *   `/api/events/latest_timestamp`: Returns the timestamp of the most recent event, used for notifying the user of new data.
            *   `/images/<filename>`: Serves captured images stored in the shared image volume.
    *   **Key Technologies**: Python, Flask, `mysql-connector-python`.

4.  **`mariadb` (Docker Service)**
    *   **Purpose**: The relational database used to store ANPR event metadata.
    *   **Functionality**: Runs a MariaDB 10.6 instance. Data is persisted using a Docker named volume (`anpr_db_data`).
    *   **Schema**: Contains the `anpr_events` table with columns for timestamp, plate number, camera ID, vehicle details, image filename, etc.

5.  **`cloudflared-tunnel` (Docker Service)**
    *   **Purpose**: Provides a secure reverse proxy tunnel to expose the `anpr_web` service to the internet via Cloudflare.
    *   **Functionality**: Uses a Cloudflare token (from `.env`) to establish the tunnel.

## Configuration

*   **`.env` / `.env.example`**: Contains environment-specific settings like Cloudflare token and MariaDB credentials.
*   **`app/config.ini` / `app/config.ini.example`**: Contains camera connection details (IPs, credentials, channels) and paths (e.g., `ImageDirectory`).
*   **`docker-compose.yml`**: Defines how all services are built, configured, networked, and run.
*   **`Dockerfile`**: Defines the common Docker image for the Python services, including Python environment, system dependencies, Dahua SDK installation, and application code copying.
*   **`requirements.txt`**: Lists Python package dependencies (Flask, requests, mysql-connector-python, gunicorn).

## Workflow Summary

1.  `anpr_listener` connects to cameras specified in `config.ini`.
2.  Upon detecting a license plate, the camera sends an event (with image) to `anpr_listener`.
3.  `anpr_listener` processes this and forwards the data (JSON metadata + image file) to `anpr_db_manager`'s `/event` API endpoint.
4.  `anpr_db_manager` saves the image to the `/app/anpr_images` volume and writes the event metadata (including image filename) to the MariaDB database.
5.  The user accesses the web interface via the Cloudflare tunnel URL, which points to `anpr_web`.
6.  `anpr_web` serves `index.html`, which then makes API calls to `anpr_web` itself to fetch and display event data from the database, including images.

## Key File Locations

*   Python application scripts: `app/`
*   Web templates: `app/templates/`
*   Docker configuration: `Dockerfile`, `docker-compose.yml`
*   Host-mounted image storage (example): `./app/anpr_images/`
*   Host-mounted log storage (example): `./app/logs/`
*   Setup script: `setup.sh` (for host environment setup and service management)
