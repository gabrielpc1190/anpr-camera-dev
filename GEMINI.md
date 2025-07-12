# ANPR System Project Overview (GEMINI.MD)

## Core Objective
Capture license plate data from Dahua ANPR cameras, store it in MariaDB, and provide a web UI for querying.

## System Components (Post-Refactor)

1.  **`anpr_listener` (`app/anpr_listener.py`)**
    *   **Purpose**: Connects to Dahua cameras, captures events.
    *   **Functionality**: Subscribes to ANPR events. On event, saves image locally (temp), then asynchronously sends event metadata (JSON) and the image file (`multipart/form-data`) via HTTP POST to `anpr_db_manager`. Async send prevents blocking & event loss. Cleans up temp image after send attempt.
    *   **Tech**: Python, Dahua NetSDK, `requests`, `threading`.

2.  **`anpr_db_manager` (`app/anpr_db_manager.py`)**
    *   **Purpose**: Sole manager of data storage (DB & images). The single source of truth.
    *   **Functionality**:
        *   Flask app with one main data ingress endpoint: `/event` (POST).
        *   Receives `multipart/form-data` (JSON metadata + image file) from `anpr_listener`.
        *   Saves image to the shared volume (`/app/anpr_images`) with a secure, unique name.
        *   Inserts event metadata (including the new image filename) into the `anpr_events` table in MariaDB.
        *   Exposes a full set of read-only API endpoints for the web UI (`/api/events`, `/api/cameras`, etc.) to query the database.
    *   **Tech**: Python, Flask, `mysql-connector-python`.

3.  **`anpr_web` (`app/anpr_web.py`)**
    *   **Purpose**: Provides the backend for the web UI, acting as a proxy.
    *   **Functionality**:
        *   A lightweight Flask app. **Contains no direct DB logic.**
        *   Serves `index.html`.
        *   Exposes API endpoints (`/api/events`, etc.) that mirror `anpr_db_manager`'s API.
        *   When its API is called by the UI, it makes a corresponding HTTP request to `anpr_db_manager` and forwards the response.
        *   Serves captured images to the user via `/images/<filename>` from the shared volume.
    *   **Tech**: Python, Flask, `requests`.

4.  **`mariadb`**: MariaDB 10.6 instance. Data persisted in a bind-mounted volume.

5.  **`cloudflared-tunnel`**: Exposes `anpr_web` to the internet.

## Configuration & Docker
*   **`.env`**: Cloudflare token, MariaDB credentials.
*   **`app/config.ini`**: Camera connection details. Contains a global `LogLevel` (0-3) to control logging verbosity across all Python services.
*   **`docker-compose.yml`**: Orchestrates all services. Uses specific Dockerfiles for each service.
*   **`anpr_listener.Dockerfile`, `anpr_db_manager.Dockerfile`, `anpr_web.Dockerfile`**: Optimized, service-specific Dockerfiles. Only `anpr_listener`'s image contains the large Dahua SDK and its dependencies.
*   **`setup.sh`**: Host setup script. **Crucially, it downloads the Dahua SDK and places the `.whl` file in the `app/` directory, which is required by `anpr_listener.Dockerfile` during the build process.**

## Refactored Workflow Summary

1.  `anpr_listener` connects to cameras.
2.  On event, `anpr_listener` saves the image to a temporary file.
3.  `anpr_listener` **asynchronously** POSTs the event JSON and the temp image file to `anpr_db_manager`'s `/event` endpoint.
4.  `anpr_db_manager` receives the data, saves the image to the persistent `/app/anpr_images` volume with a new permanent name, and writes the event metadata (with the new filename) to MariaDB.
5.  `anpr_listener` deletes the temporary image file.
6.  User accesses the UI via `anpr_web`.
7.  `index.html` calls an API on `anpr_web` (e.g., `/api/events`).
8.  `anpr_web` calls the corresponding API on `anpr_db_manager` (`/api/events`).
9.  `anpr_db_manager` queries MariaDB and returns the data to `anpr_web`.
10. `anpr_web` returns the data to the UI.

## Service Interaction Details

This section details the data flow, request/response formats, and expectations between the core services.

### 1. `anpr_listener` -> `anpr_db_manager` (Event Ingestion)

*   **Purpose:** `anpr_listener` sends detected ANPR events and associated images to `anpr_db_manager` for storage.
*   **`anpr_listener` (Sender):**
    *   **Endpoint Targeted:** `anpr_db_manager`'s `/event` (POST).
    *   **Request Format:** `multipart/form-data`.
        *   `event_data` (form field): JSON string containing event metadata (e.g., `Timestamp`, `PlateNumber`, `CameraID`, `VehicleType`, `VehicleColor`, `PlateColor`, `DrivingDirection`, `VehicleSpeed`, `Lane`).
        *   `image` (file field): The captured image file (JPEG).
    *   **Expects (Response from `anpr_db_manager`):**
        *   `201 Created` with JSON: `{"status": "success", "message": "Event and image processed"}`.
        *   Handles `requests.exceptions.RequestException` (e.g., connection errors, non-2xx status codes) and `FileNotFoundError`.
*   **`anpr_db_manager` (Receiver - `/event` endpoint):**
    *   **Data Processing:**
        1.  Validates presence of `event_data` and `image` file.
        2.  Parses `event_data` JSON.
        3.  Sanitizes and saves image to `/app/anpr_images`.
        4.  Parses `EventTimeUTC` string into a `datetime` object.
        5.  Inserts event metadata (including image filename and full `event_data` as JSON) into the `anpr_events` table.
    *   **Response Emitted:**
        *   `201 Created` on success.
        *   `400 Bad Request` for missing/invalid data.
        *   `500 Internal Server Error` for image saving/DB insertion errors.
        *   `503 Service Unavailable` if DB connection fails.

### 2. `anpr_web` -> `anpr_db_manager` (API Proxying)

*   **Purpose:** `anpr_web` acts as a proxy, forwarding API requests from the web UI to `anpr_db_manager`.
*   **`anpr_web` (Sender/Proxy):**
    *   **Endpoints Targeted:**
        *   `anpr_db_manager`'s `/api/events` (GET).
        *   `anpr_db_manager`'s `/api/cameras` (GET).
        *   `anpr_db_manager`'s `/api/events/latest_timestamp` (GET).
    *   **Request Format:** Standard HTTP GET requests.
        *   `/api/events`: Forwards all query parameters (e.g., `page`, `limit`, `plate_number`, `camera_id`, `start_date`, `end_date`).
        *   `/api/cameras`, `/api/events/latest_timestamp`: No specific parameters.
    *   **Expects (Response from `anpr_db_manager`):**
        *   `200 OK` with a JSON body.
        *   Handles `requests.exceptions.RequestException` (e.g., `anpr_db_manager` is down) and aborts with `502 Bad Gateway`.
*   **`anpr_db_manager` (Receiver - `/api/*` endpoints):**
    *   **Data Processing:**
        1.  Establishes a database connection.
        2.  Executes SQL queries based on the endpoint:
            *   `/api/events`: Queries `anpr_events` table with pagination and filtering.
            *   `/api/cameras`: Queries `anpr_events` for distinct `camera_id`s.
            *   `/api/events/latest_timestamp`: Queries `anpr_events` for the maximum `timestamp`.
    *   **Response Emitted:**
        *   `200 OK` with JSON data (events list, camera list, or latest timestamp).
        *   `503 Service Unavailable` if DB connection fails.

### 3. Client (Web Browser) -> `anpr_web` (UI Interaction)

*   **Purpose:** The web browser interacts with `anpr_web` to display the UI, fetch images, and retrieve event data.
*   **Client (Web Browser) (Sender):**
    *   **Endpoints Targeted:**
        *   `anpr_web`'s `/` (GET).
        *   `anpr_web`'s `/images/<path:filename>` (GET).
        *   `anpr_web`'s `/api/*` (GET).
    *   **Request Format:** Standard HTTP GET requests.
*   **`anpr_web` (Receiver):**
    *   **Data Processing:**
        1.  `/`: Serves `index.html`.
        2.  `/images/<path:filename>`: Serves image files from `/app/anpr_images` after path sanitization.
        3.  `/api/*`: Proxies requests to `anpr_db_manager` (as described in section 2).
    *   **Response Emitted:**
        *   `200 OK` (HTML, image data, or proxied JSON).
        *   `404 Not Found` for invalid image paths.
        *   `502 Bad Gateway` if proxying to `anpr_db_manager` fails.

## Current Known Issues (July 12, 2025)

This section documents current unresolved issues.

### 1. `anpr_db_manager_service` Event Ingestion Failure

*   **Problem**: `anpr_listener_service` consistently receives `500 Internal Server Error` when sending ANPR events to `anpr_db_manager`'s `/event` endpoint. `anpr_db_manager_service` also appears to be restarting frequently, suggesting an unhandled exception during event processing.
*   **Impact**: License plate data and images are not being reliably stored in the MariaDB database.

### 2. `anpr_db_manager` API Endpoints Incomplete

*   **Problem**: The `/api/events`, `/api/cameras`, and `/api/events/latest_timestamp` endpoints in `anpr_db_manager.py` are currently placeholder implementations and do not query the database or return actual data.
*   **Impact**: The `anpr_web` UI cannot retrieve historical event data, camera lists, or the latest timestamp from the database.
