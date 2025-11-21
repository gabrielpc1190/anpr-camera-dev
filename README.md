# ANPR Camera System - Project Documentation

## 1. Project Summary

This project implements a complete Automatic Number Plate Recognition (ANPR) system designed for continuous 24/7 operation. It captures vehicle license plate events from Dahua IP cameras, processes the data, stores the information and images in a database, and provides a web interface to view the events.

The system is designed to be robust and resilient, with automatic recovery mechanisms for system reboots and temporary connection failures with the cameras.

## 2. Main Features

*   **Real-time Event Capture**: Actively listens for traffic events sent by Dahua cameras.
*   **Asynchronous Processing**: Events are sent to a database service asynchronously to avoid data loss, even under high load.
*   **Persistent Storage**: Saves detailed event information and the license plate image in a MariaDB database.
*   **Web Interface**: a simple web interface to view, filter, and paginate captured license plate events.
*   **Docker Orchestration**: All services are containerized and managed with Docker Compose for easy deployment and scalability.
*   **Enhanced Resilience**: The system automatically recovers from reboots and reconnects to cameras that have lost their connection.
*   **Enriched Data Capture**: In addition to the license plate, the system now captures:
    *   **Physical Vehicle Type**: (e.g., "MotorVehicle").
    *   **Direction of Movement**: (e.g., "Approaching", "Leaving").
    *   **Access Status**: (e.g., "Normal Car", "Trust Car").

## 3. System Architecture

The system is composed of several microservices that work together:

1.  **`anpr-listener`**:
    *   A Python service that connects to the Dahua SDK.
    *   It subscribes to traffic events (ANPR) from the configured cameras.
    *   **Self-healing**: Includes a health loop that periodically checks the connection with each camera and automatically attempts to reconnect if a camera reboots or loses connection.
    *   Upon receiving an event, it sends the data and image asynchronously to the `anpr-db-manager`.

2.  **`anpr-db-manager`**:
    *   A Python API (Flask/Gunicorn) that acts as the single source of truth for the database.
    *   It receives events from the `listener`, saves the image to disk, and writes the metadata to the database.
    *   It exposes endpoints for the web interface to query the data.

3.  **`anpr-web`**:
    *   A Python web application (Flask/Gunicorn) that serves the user interface.
    *   It acts as a proxy, querying data from the `anpr-db-manager` to display it to the user securely.

4.  **`mariadb`**:
    *   The database service where all events are stored.

5.  **`cloudflared-tunnel`**:
    *   An optional service that securely exposes the web interface to the internet through a Cloudflare tunnel.

## 4. Configuration

The project configuration is divided into two main files:

*   **`.env`**: Manages environment credentials and secrets, such as database passwords and API tokens.
*   **`app/config.ini`**: Manages the application's configuration, such as the IP addresses, names, and credentials for each camera.

## 5. Usage

The project is managed through a helper script `setup.sh`.

*   **To start all services**:
    ```bash
    ./setup.sh start
    ```
*   **To stop all services**:
    ```bash
    ./setup.sh stop
    ```
*   **To view real-time logs**:
    ```bash
    ./setup.sh logs
    ```
*   **To follow the logs of a specific service** (e.g., `anpr-listener`):
    ```bash
    ./setup.sh logs anpr-listener
    ```
*   **To reset the admin password**:
    ```bash
    ./setup.sh reset-admin
    ```
*   **To create a new user**:
    ```bash
    ./setup.sh create-user
    ```
*   **To delete an existing user**:
    ```bash
    ./setup.sh delete-user
    ```
*   **To rebuild all containers**:
    ```bash
    ./setup.sh rebuild
    ```

## 6. System Resilience

The system has been improved to ensure continuous and reliable operation:

*   **Recovery from Reboots**: Thanks to the `service_healthy` conditions in `docker-compose.yml`, the services start in the correct order, preventing the `anpr-listener` from failing if the database is not yet ready.
*   **Automatic Camera Reconnection**: The `anpr-listener` now actively checks the connection status with each camera every 60 seconds. If a camera disconnects (due to a reboot or network failure), the service will automatically try to reconnect until it succeeds.

## 7. Troubleshooting

This section documents common problems and their solutions.

#### **Problem 1: The service stops working after a system reboot.**

*   **Symptom**: The `anpr-listener` container appears as "Up" in `docker-compose ps`, but it does not process new events and does not generate new logs.
*   **Cause**: Race condition on startup. The `listener` was trying to start before the `anpr-db-manager` was fully ready, failing to connect and ending up in a "zombie" state.
*   **Solution**: `docker-compose.yml` was modified so that `anpr-listener` waits for the `service_healthy` condition of `anpr-db-manager` to be successful.

#### **Problem 2: The system stops capturing events if a camera reboots.**

*   **Symptom**: The `anpr-listener` log shows a "Device disconnected" message but never captures events from that camera again.
*   **Cause**: The script had no logic to retry the connection after a disconnection.
*   **Solution**: A self-healing loop was implemented in `anpr_listener.py` that checks for dropped connections every 60 seconds and tries to log in and subscribe to events again.

#### **Problem 3: The vehicle's direction, vehicle type, or other data is not captured.**

*   **Symptom**: The fields in the database or logs appear as "Unknown" or empty, even if the license plate is read correctly.
*   **Cause 1**: The camera is not configured to analyze and send this data. The corresponding IVS (Intelligent Video System) rules must be activated in the camera's web interface.
*   **Cause 2**: The Python script is looking for an incorrect field name in the data sent by the SDK.
*   **Solution**: Debug logs and SDK files were analyzed to identify the correct field names (`szDrivingDirection`, `szObjectType`, etc.), and fallback logic was implemented in `anpr_listener.py` to ensure that data is captured from the most reliable available source.

## 8. Authentication and User Management

The system includes an authentication module to protect access to the web interface (`anpr-web`).

### Features
*   **Route Protection**: All routes except `/login` and `/health` require authentication.
*   **Secure Storage**: Passwords are stored as hashes using `bcrypt`.

### User Management

Manage users through the `setup.sh` script:

*   **Reset admin password**:
    ```bash
    ./setup.sh reset-admin
    ```
*   **Create a new user**:
    ```bash
    ./setup.sh create-user
    ```
*   **Delete a user**:
    ```bash
    ./setup.sh delete-user
    ```

## 9. Cloudflare Tunnel Configuration
If you are using Cloudflare Tunnel to expose the application, ensure that the service in the Cloudflare Zero Trust dashboard is pointing to `http://127.0.0.1:5000` (or `localhost:5000`) instead of `http://anpr-web:5000`.
Because the services run in `network_mode: host`, the container hostname `anpr-web` is not resolvable.
