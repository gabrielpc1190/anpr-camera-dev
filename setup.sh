#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e

# --- Style Definitions ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Configuration ---
# Define paths directly in the script, as they are related to the project structure
APP_DIR="app"
IMAGE_DIR_HOST="${APP_DIR}/anpr_images" # Path on the host for images
LOG_DIR_HOST="${APP_DIR}/logs"         # Path on the host for logs
DB_DIR_HOST="${APP_DIR}/db"            # Path on the host for MariaDB data (bind mount)
DEPS_MARKER_FILE="${LOG_DIR_HOST}/.dependencies_checked" # Marker file for dependency installation
SDK_TEMP_DIR=".sdk_temp"
ENV_FILE=".env"
CONFIG_INI="${APP_DIR}/config.ini"

# SDK Download URL (from README)
SDK_URL="https://materialfile.dahuasecurity.com/uploads/soft/20250508/General_NetSDK_Eng_Python_linux64_IS_V3.060.0000000.0.R.250409.zip"
SDK_ZIP_NAME="dahua_sdk.zip"
DOCKER_COMPOSE_VERSION_RHEL_FEDORA="v2.20.2" # Default Docker Compose version for RHEL/Fedora/CentOS

# --- Helper Functions ---

# Function to display help/usage
usage() {
    echo "Usage: $0 [command]"
    echo
    echo "Commands:"
    echo "  start        Automates SDK download, validates config, builds, and starts all services."
    echo "  stop         Stops and removes all running containers."
    echo "  restart      Restarts all services."
    echo "  logs [service] Shows logs for all services or a specific one. (e.g., '$0 logs anpr-listener')"
    echo "  clean [--force] Stops services and optionally deletes data from host volumes. Use --force to skip confirmation."
    echo "  reconfigure  Stops services and prompts you to edit configuration files."
    echo "  update       Updates the system by pulling git changes, rebuilding images, and restarting services."
    echo
}

# Function to handle SDK download and preparation
handle_sdk() {
    echo "--- Checking for Dahua NetSDK... ---"
    # Check if any .whl file already exists in the app directory.
    # If it does, we assume it's the correct one and skip.
    local existing_whl_file
    existing_whl_file=$(find "${APP_DIR}" -maxdepth 1 -name '*.whl' -print -quit)

    if [ -n "$existing_whl_file" ]; then
        echo -e "${GREEN}--- SDK .whl file ('${existing_whl_file#${APP_DIR}/}') already exists in ${APP_DIR}. Skipping download. ---${NC}"
        return
    fi

    echo -e "${YELLOW}--- SDK .whl file not found in ${APP_DIR}. Starting download and preparation... ---${NC}"
    
    # Create a temporary directory for the SDK
    mkdir -p "${SDK_TEMP_DIR}"
    local sdk_zip_path="${SDK_TEMP_DIR}/${SDK_ZIP_NAME}"

    # Download the SDK
    echo "Downloading SDK from Dahua servers (${SDK_URL})..."
    curl -L -o "${sdk_zip_path}" "${SDK_URL}"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: Failed to download SDK from ${SDK_URL}.${NC}"
        echo -e "${RED}Please check the URL and your internet connection.${NC}"
        rm -rf "${SDK_TEMP_DIR}" # Clean up before exiting
        exit 1
    fi

    # Unzip the SDK
    echo "Unzipping SDK archive: ${sdk_zip_path}..."
    unzip -o "${sdk_zip_path}" -d "${SDK_TEMP_DIR}"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: Failed to unzip SDK archive at ${sdk_zip_path}.${NC}"
        echo -e "${RED}The archive might be corrupted, incomplete, or 'unzip' command might be missing (should have been installed).${NC}"
        rm -rf "${SDK_TEMP_DIR}" # Clean up before exiting
        exit 1
    fi

    # Find the .whl file within the unzipped contents
    local found_whl_file_in_temp
    found_whl_file_in_temp=$(find "${SDK_TEMP_DIR}" -name "*.whl" | head -n 1)

    if [ -z "$found_whl_file_in_temp" ]; then
        echo -e "${RED}Error: Could not find any .whl file in the downloaded SDK archive.${NC}"
        rm -rf "${SDK_TEMP_DIR}"
        exit 1
    fi

    if [ $(find "${SDK_TEMP_DIR}" -name "*.whl" | wc -l) -gt 1 ]; then
        echo -e "${YELLOW}Warning: Multiple .whl files found in SDK archive. Using the first one: $(basename "${found_whl_file_in_temp}")${NC}"
    fi

    # Move the found .whl file (with its original name) to the app directory
    echo "Moving .whl file '$(basename "${found_whl_file_in_temp}")' to ${APP_DIR}/"
    mv "${found_whl_file_in_temp}" "${APP_DIR}/"

    # Clean up the temporary directory
    echo "Cleaning up temporary files..."
    rm -rf "${SDK_TEMP_DIR}"

    echo -e "${GREEN}--- SDK has been successfully prepared in ${APP_DIR}. ---${NC}"
}

# Function to install system dependencies
install_dependencies() {
    # Check if we've already installed dependencies (using marker file)
    if [ -f "$DEPS_MARKER_FILE" ]; then
        # Dependencies already checked, just set COMPOSE_CMD
        if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
            COMPOSE_CMD="docker compose"
        elif command -v docker-compose &>/dev/null; then
            COMPOSE_CMD="docker-compose"
        else
            echo -e "${RED}Error: Neither 'docker compose' nor 'docker-compose' found.${NC}"
            exit 1
        fi
        return
    fi

    echo "--- Checking system dependencies... ---"
    
    # Ensure LOG_DIR_HOST exists for marker file
    mkdir -p "${LOG_DIR_HOST}"
    
    # Check for Docker
    if ! command -v docker &>/dev/null; then
        echo -e "${RED}Error: Docker is not installed.${NC}"
        echo "Please install Docker first. Visit: https://docs.docker.com/get-docker/"
        exit 1
    fi
    
    # Check for Docker Compose (plugin or standalone)
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        echo -e "${GREEN}Found Docker Compose (plugin): $(docker compose version)${NC}"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        echo -e "${GREEN}Found Docker Compose (standalone): $(docker-compose version)${NC}"
    else
        echo -e "${RED}Error: Docker Compose is not installed.${NC}"
        echo "Please install Docker Compose. Visit: https://docs.docker.com/compose/install/"
        exit 1
    fi
    
    # Check for curl
    if ! command -v curl &>/dev/null; then
        echo -e "${RED}Error: curl is not installed.${NC}"
        echo "Please install curl: sudo apt-get install curl (Debian/Ubuntu) or sudo yum install curl (RHEL/CentOS)"
        exit 1
    fi
    
    # Check for unzip
    if ! command -v unzip &>/dev/null; then
        echo -e "${RED}Error: unzip is not installed.${NC}"
        echo "Please install unzip: sudo apt-get install unzip (Debian/Ubuntu) or sudo yum install unzip (RHEL/CentOS)"
        exit 1
    fi
    
    # Create marker file to skip this check next time
    touch "$DEPS_MARKER_FILE"
    echo -e "${GREEN}--- All system dependencies are present. ---${NC}"
}

# Function to update the system
update_system() {
    echo "--- Starting System Update Process ---"

    # Check if this is a git repository
    if [ ! -d ".git" ]; then
        echo -e "${RED}Error: This does not appear to be a git repository.${NC}"
        echo -e "${YELLOW}The update command currently only supports updating via git.${NC}"
        exit 1
    fi

    # 1. Pull latest changes from git
    echo "Attempting to pull latest changes from git repository (fast-forward only)..."
    if git pull --ff-only; then
        echo -e "${GREEN}Successfully pulled latest changes from git.${NC}"
    else
        echo -e "${RED}Error: Failed to pull changes from git repository.${NC}"
        echo -e "${YELLOW}This might be due to local changes conflicting with remote changes, or other git issues.${NC}"
        echo -e "${YELLOW}Please resolve any git conflicts manually or stash your local changes, then try updating again.${NC}"
        exit 1
    fi

    # 2. Ensure SDK is present before rebuilding
    echo "Ensuring Dahua NetSDK .whl file is available for build..."
    handle_sdk # Ensure the .whl file is in ./app/

    # 3. Rebuild docker images
    echo "Rebuilding Docker images if necessary..."
    if $COMPOSE_CMD build; then
        echo -e "${GREEN}Docker images rebuilt successfully.${NC}"
    else
        echo -e "${RED}Error: Failed to rebuild Docker images.${NC}"
        echo -e "${YELLOW}Please check the output above for any Docker build errors.${NC}"
        exit 1
    fi

    # 4. Restart services with new images
    echo "Restarting Docker services..."
    # Using 'up -d' will recreate containers if their image or configuration has changed.
    # '--remove-orphans' cleans up any services removed from the compose file.
    if $COMPOSE_CMD up -d --remove-orphans; then
        echo -e "${GREEN}Docker services restarted successfully with updated images/configurations.${NC}"
    else
        echo -e "${RED}Error: Failed to restart Docker services.${NC}"
        echo -e "${YELLOW}Please check the output above for any errors.${NC}"
        exit 1
    fi

    echo -e "${GREEN}--- System Update Completed Successfully ---${NC}"
}

# Function to handle the creation of config files from examples
handle_config_creation() {
    echo "--- Checking for configuration files... ---"
    local config_created=false

    # Check for .env file
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${YELLOW}Warning: .env file not found.${NC}"
        if [ -f ".env.example" ]; then
            echo "Creating .env from .env.example..."
            cp .env.example .env
            config_created=true
        else
            echo -e "${RED}Error: .env.example not found at '$(pwd)/.env.example'. Cannot create .env file.${NC}"
            echo -e "${RED}Please ensure this file exists in the root of the project.${NC}"
            exit 1
        fi
    fi

    # Check for config.ini file
    if [ ! -f "$CONFIG_INI" ]; then
        echo -e "${YELLOW}Warning: ${CONFIG_INI} not found.${NC}"
        if [ -f "${CONFIG_INI}.example" ]; then
            echo "Creating ${CONFIG_INI} from ${CONFIG_INI}.example..."
            cp "${CONFIG_INI}.example" "$CONFIG_INI"
            config_created=true
        else
            echo -e "${RED}Error: ${CONFIG_INI}.example not found at '$(pwd)/${CONFIG_INI}.example'. Cannot create config.ini.${NC}"
            echo -e "${RED}Please ensure this file exists in the '${APP_DIR}' directory.${NC}"
            exit 1
        fi
    fi

    # If we created any config file, inform the user and exit
    if [ "$config_created" = true ]; then
        echo -e "\n${GREEN}--- Configuration Files Created ---${NC}"
        echo "I have created the necessary configuration files for you:"
        echo "  - .env"
        echo "  - app/config.ini"
        echo
        echo -e "${YELLOW}ACTION REQUIRED: Please edit these files to add your database credentials, camera details, and any tokens.${NC}"
        echo "After editing, please run './setup.sh start' again."
        exit 1
    fi

    echo -e "${GREEN}--- Configuration files are present. ---${NC}"
}

# Function to setup admin user if not exists
setup_admin_user() {
    echo "--- Checking for admin user... ---"
    
    # Check if anpr-web container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^anpr-web$"; then
        echo -e "${YELLOW}Web container not running yet. Admin user setup will be skipped for now.${NC}"
        echo -e "${YELLOW}You can create users later using: docker exec -it anpr-web python app/user_manager.py${NC}"
        return
    fi
    
    # Check if any users exist
    USER_COUNT=$(docker exec anpr-web python -c "
from app.models import db, User
from app.anpr_web import app
with app.app_context():
    print(User.query.count())
" 2>/dev/null || echo "0")
    
    if [ "$USER_COUNT" -gt 0 ]; then
        echo -e "${GREEN}Admin user already exists. Skipping user creation.${NC}"
        return
    fi
    
    echo -e "${YELLOW}No admin user found. Let's create the first admin user.${NC}"
    read -p "Enter admin username: " ADMIN_USERNAME
    
    if [ -z "$ADMIN_USERNAME" ]; then
        echo -e "${RED}Username cannot be empty. Skipping admin user creation.${NC}"
        return
    fi
    
    # Password validation loop
    while true; do
        read -s -p "Enter admin password (min 10 chars, 1 uppercase, 1 number, 1 special char): " ADMIN_PASSWORD
        echo
        read -s -p "Confirm password: " ADMIN_PASSWORD_CONFIRM
        echo
        
        if [ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_CONFIRM" ]; then
            echo -e "${RED}Passwords do not match. Please try again.${NC}"
            continue
        fi
        
        # Validate password strength
        if [ ${#ADMIN_PASSWORD} -lt 10 ]; then
            echo -e "${RED}Password must be at least 10 characters long.${NC}"
            continue
        fi
        
        if ! echo "$ADMIN_PASSWORD" | grep -q '[A-Z]'; then
            echo -e "${RED}Password must contain at least one uppercase letter.${NC}"
            continue
        fi
        
        if ! echo "$ADMIN_PASSWORD" | grep -q '[0-9]'; then
            echo -e "${RED}Password must contain at least one number.${NC}"
            continue
        fi
        
        if ! echo "$ADMIN_PASSWORD" | grep -q '[!@#$%^&*(),.?:{}|<>~`]'; then
            echo -e "${RED}Password must contain at least one special character.${NC}"
            continue
        fi
        
        break
    done
    
# Create the user
    echo "Creating admin user..."

    # Comando de Python con reintentos para manejar la condición de carrera del DB
    ADMIN_COMMAND="
from app.models import db, User
from app.anpr_web import app
import time, sys

MAX_RETRIES = 5
for i in range(MAX_RETRIES):
    try:
        with app.app_context():
            # Intentamos crear el usuario
            user = User(username='$ADMIN_USERNAME')
            user.set_password('$ADMIN_PASSWORD')
            db.session.add(user)
            db.session.commit()
            print('Admin user created successfully!')
            sys.exit(0) # Éxito: salir con 0
    except Exception as e:
        if i < MAX_RETRIES - 1:
            print(f'Database not ready or error occurred (attempt {i+1}/{MAX_RETRIES}). Retrying in 3 seconds...')
            time.sleep(3)
        else:
            print(f'FATAL ERROR after {MAX_RETRIES} attempts: {e}', file=sys.stderr)
            sys.exit(1) # Fallo: salir con 1
"

    # Ejecutar el comando sin suprimir errores (para visibilidad)
    docker exec anpr-web python -c "$ADMIN_COMMAND"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Admin user '$ADMIN_USERNAME' created successfully!${NC}"
    else
        echo -e "${RED}Failed to create admin user. You can create it manually later using:${NC}"
        echo -e "${YELLOW}docker exec -it anpr-web python app/user_manager.py${NC}"
    fi
}

# Function to setup Cloudflare token
setup_cloudflare_token() {
    echo "--- Checking Cloudflare tunnel configuration... ---"
    
    # Check if CLOUDFLARE_TOKEN is already set in .env
    if grep -q "^CLOUDFLARE_TOKEN=.\+" "$ENV_FILE" 2>/dev/null; then
        echo -e "${GREEN}Cloudflare token is already configured.${NC}"
        return
    fi
    
    echo -e "${YELLOW}Cloudflare tunnel token is not configured.${NC}"
    read -p "Do you want to configure Cloudflare tunnel now? (y/n) " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Skipping Cloudflare configuration. The tunnel service will not work.${NC}"
        echo -e "${YELLOW}You can add it later by editing .env and adding: CLOUDFLARE_TOKEN=your_token${NC}"
        return
    fi
    
    read -p "Enter your Cloudflare tunnel token: " CF_TOKEN
    
    if [ -z "$CF_TOKEN" ]; then
        echo -e "${YELLOW}No token provided. Skipping Cloudflare configuration.${NC}"
        return
    fi
    
    # Add or update CLOUDFLARE_TOKEN in .env
    if grep -q "^CLOUDFLARE_TOKEN=" "$ENV_FILE" 2>/dev/null; then
        # Update existing line
        sed -i "s|^CLOUDFLARE_TOKEN=.*|CLOUDFLARE_TOKEN=$CF_TOKEN|" "$ENV_FILE"
    else
        # Add new line
        echo "" >> "$ENV_FILE"
        echo "# Cloudflare Tunnel Configuration" >> "$ENV_FILE"
        echo "CLOUDFLARE_TOKEN=$CF_TOKEN" >> "$ENV_FILE"
    fi
    
    echo -e "${GREEN}Cloudflare token configured successfully!${NC}"
    echo -e "${YELLOW}Note: You'll need to restart services for the tunnel to start working.${NC}"
}

# --- Main Script Logic ---

# Default to 'usage' if no command is given
COMMAND=${1:-usage}

case $COMMAND in
    start)
        install_dependencies
        handle_config_creation
        setup_cloudflare_token
        handle_sdk
        echo "--- Creating required host directories... ---"
        # LOG_DIR_HOST is already created by install_dependencies if marker was absent
        # So, mainly ensure IMAGE_DIR_HOST here.
        mkdir -p "${IMAGE_DIR_HOST}"
        # Ensure LOG_DIR_HOST again just in case install_dependencies was skipped due to marker
        mkdir -p "${LOG_DIR_HOST}"

        # Check and create DB_DIR_HOST
        if [ ! -d "${DB_DIR_HOST}" ]; then
            echo -e "${YELLOW}Database directory (${DB_DIR_HOST}) not found. Creating it...${NC}"
            mkdir -p "${DB_DIR_HOST}"
            echo -e "${GREEN}Database directory ${DB_DIR_HOST} created successfully.${NC}"
        else
            echo -e "${GREEN}Using existing database directory: ${DB_DIR_HOST}.${NC}"
        fi

        echo "--- Building and starting services... ---"
        $COMPOSE_CMD up --build -d
        echo -e "${GREEN}--- Services started successfully. ---${NC}"
        
        # Setup admin user after services are running
        setup_admin_user
        
        echo -e "${YELLOW}Use './setup.sh logs' to monitor.${NC}"
        ;;
    stop)
        install_dependencies
        echo "--- Stopping and removing containers... ---"
        $COMPOSE_CMD down
        echo "--- Services stopped. ---"
        ;;
    restart)
        install_dependencies
        echo "--- Restarting all services... ---"
        $COMPOSE_CMD restart
        echo "--- Services restarted. ---"
        ;;
    logs)
        install_dependencies
        shift # Remove 'logs' from the arguments
        echo "--- Showing logs. Press Ctrl+C to exit. ---"
        $COMPOSE_CMD logs -f --tail=100 "$@"
        ;;
    clean)
        install_dependencies
        FORCE_CLEAN=false
        if [ "$2" == "--force" ]; then
            FORCE_CLEAN=true
        fi

        if [ "$FORCE_CLEAN" = true ]; then
            echo -e "${YELLOW}--- Force cleaning environment... ---${NC}"
            echo "Stopping and removing containers..."
            $COMPOSE_CMD down -v # Removes containers and anonymous volumes (though we don't have anonymous for DB anymore)

            echo "Deleting data from host-mounted directories (${IMAGE_DIR_HOST}, ${LOG_DIR_HOST}, ${DB_DIR_HOST})..."
            rm -rf "${IMAGE_DIR_HOST}/"*
            rm -rf "${LOG_DIR_HOST}/"* # This will also remove the .dependencies_checked marker
            rm -rf "${DB_DIR_HOST}/"*
            # Recreate dirs after cleaning
            mkdir -p "${IMAGE_DIR_HOST}" "${LOG_DIR_HOST}" "${DB_DIR_HOST}"
            echo -e "${GREEN}--- All services, containers, and host-mounted data (images, logs, database) have been cleared. ---${NC}"
            echo -e "${YELLOW}Note: The .dependencies_checked marker has been removed; system dependencies will be re-checked on next relevant command.${NC}"
        else
            echo -e "${RED}WARNING: This will stop all services and remove containers.${NC}"
            echo -e "${RED}You will also be asked if you want to permanently delete data from:${NC}"
            echo -e "${RED}  - Captured images in ${IMAGE_DIR_HOST}"
            echo -e "${RED}  - Log files in ${LOG_DIR_HOST} (including the .dependencies_checked marker)"
            echo -e "${RED}  - The MariaDB database files in ${DB_DIR_HOST}"
            read -p "Are you sure you want to stop services and remove containers? (y/N) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                echo "--- Stopping services and removing containers... ---"
                $COMPOSE_CMD down -v # -v also removes anonymous volumes
                echo -e "${GREEN}--- Services stopped and containers removed. ---${NC}"

                read -p "Do you also want to delete all data in ${IMAGE_DIR_HOST}, ${LOG_DIR_HOST}, and ${DB_DIR_HOST}? This is IRREVERSIBLE. (y/N) " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    echo "Deleting contents of ${IMAGE_DIR_HOST}, ${LOG_DIR_HOST}, ${DB_DIR_HOST}..."
                    rm -rf "${IMAGE_DIR_HOST}/"*
                    rm -rf "${LOG_DIR_HOST}/"* # This removes the .dependencies_checked marker too
                    rm -rf "${DB_DIR_HOST}/"*
                    mkdir -p "${IMAGE_DIR_HOST}" "${LOG_DIR_HOST}" "${DB_DIR_HOST}" # Recreate dirs

                    echo -e "${GREEN}--- All specified application data has been removed. ---${NC}"
                    echo -e "${YELLOW}Note: The .dependencies_checked marker has been removed; system dependencies will be re-checked on next relevant command.${NC}"
                else
                    echo -e "${YELLOW}--- Application data (images, logs, DB files) remains. ---${NC}"
                fi
            else
                echo "--- Clean operation cancelled. ---"
            fi
        fi
        ;;
    reconfigure)
        echo "--- Reconfiguration process started. ---"
        # Add your interactive configuration logic here if you have it.
        # For now, it's a placeholder.
        echo "Stopping services first..."
        $COMPOSE_CMD down
        echo "Please edit your .env and app/config.ini files now."
        echo "Once you are done, run './setup.sh start'."
        ;;
    update)
        install_dependencies # Ensure docker-compose is available
        update_system # Call the actual update logic
        ;;
    *)
        usage
        ;;
esac