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
IMAGE_DIR="${APP_DIR}/anpr_images"
LOG_DIR="${APP_DIR}/logs"
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
    echo "  reconfigure  Stops services, allowing manual edit of .env and config.ini before restart."
    echo "  update       Pulls the latest code from git, rebuilds Docker images, and restarts services. Preserves data and config files."
    echo
    echo "If no command is provided, it will run the first-time setup or show this help."
}

# Function to install required system dependencies
install_dependencies() {
    echo "--- Checking and installing required system dependencies... ---"
    
    # Determine sudo command
    if [ "$(id -u)" -ne 0 ]; then
        SUDO_CMD="sudo"
    else
        SUDO_CMD=""
    fi

    # Detect OS and set compose command
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID=$ID
    else
        echo -e "${RED}Cannot determine OS. Please install dependencies manually: docker, docker-compose, curl, unzip.${NC}"
        exit 1
    fi

    echo "Detected OS: $OS_ID"

    case "$OS_ID" in
        ubuntu|debian)
            echo "Updating package list..."
            $SUDO_CMD apt-get update -y
            echo "Installing dependencies: docker.io, docker-compose, curl, unzip..."
            $SUDO_CMD apt-get install -y docker.io docker-compose curl unzip
            export COMPOSE_CMD="$SUDO_CMD docker-compose"
            ;;
        centos|rhel|fedora)
            echo "Installing dependencies using yum/dnf..."
            $SUDO_CMD yum install -y yum-utils
            $SUDO_CMD yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            $SUDO_CMD yum install -y docker-ce docker-ce-cli containerd.io curl unzip
            
            echo "Installing Docker Compose ${DOCKER_COMPOSE_VERSION_RHEL_FEDORA}..."
            $SUDO_CMD curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION_RHEL_FEDORA}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
            if [ $? -ne 0 ]; then
                echo -e "${RED}Error: Failed to download Docker Compose version ${DOCKER_COMPOSE_VERSION_RHEL_FEDORA}.${NC}"
                echo -e "${RED}Please check the version in the script or your internet connection.${NC}"
                exit 1
            fi
            $SUDO_CMD chmod +x /usr/local/bin/docker-compose
            COMPOSE_CMD="$SUDO_CMD /usr/local/bin/docker-compose"
            ;;
        *)
            echo -e "${RED}Unsupported OS: $OS_ID. Please install dependencies manually: docker, docker-compose, curl, unzip.${NC}"
            exit 1
            ;;
    esac

    echo "Starting and enabling Docker service..."
    $SUDO_CMD systemctl start docker
    $SUDO_CMD systemctl enable docker

    echo -e "${GREEN}--- All dependencies are installed and configured. ---${NC}"
}

# Function to update the system: pull git changes, rebuild and restart services
update_system() {
    echo -e "${YELLOW}--- Starting System Update ---${NC}"

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
        echo -e "${YELLOW}Alternatively, if you are sure, you can try a standard 'git pull' manually and then re-run './setup.sh update' skipping the pull part (not implemented yet).${NC}"
        exit 1
    fi

    # 2. Rebuild docker images
    echo "Rebuilding Docker images if necessary..."
    if $COMPOSE_CMD build; then
        echo -e "${GREEN}Docker images rebuilt successfully.${NC}"
    else
        echo -e "${RED}Error: Failed to rebuild Docker images.${NC}"
        echo -e "${YELLOW}Please check the output above for any Docker build errors.${NC}"
        exit 1
    fi

    # 3. Restart services with new images
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

# --- Main Script Logic ---

# Default to 'usage' if no command is given
COMMAND=${1:-usage}

case $COMMAND in
    start)
        install_dependencies
        handle_config_creation
        handle_sdk
        echo "--- Creating required directories... ---"
        mkdir -p "${IMAGE_DIR}" "${LOG_DIR}"
        echo "--- Building and starting services... ---"
        $COMPOSE_CMD up --build -d
        echo -e "${GREEN}--- Services started successfully. ---${NC}"
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
            $COMPOSE_CMD down -v # Removes containers and anonymous volumes
            echo "Deleting data from host-mounted directories (./app/db, ./app/anpr_images, ./app/logs)..."
            rm -rf "${APP_DIR}/db/"*
            rm -rf "${IMAGE_DIR}/"*
            rm -rf "${LOG_DIR}/"*
            mkdir -p "${APP_DIR}/db" "${IMAGE_DIR}" "${LOG_DIR}" # Recreate dirs
            echo -e "${GREEN}--- All services, containers, and application data from host volumes have been removed. ---${NC}"
        else
            echo -e "${RED}WARNING: This will stop all services and remove containers.${NC}"
            echo -e "${RED}You will also be asked if you want to permanently delete data from host-mounted volumes:${NC}"
            echo -e "${RED}  - ./app/db (database data)"
            echo -e "${RED}  - ./app/anpr_images (captured images)"
            echo -e "${RED}  - ./app/logs (application logs)"
            read -p "Are you sure you want to stop services and remove containers? (y/N) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                echo "--- Stopping services and removing containers... ---"
                $COMPOSE_CMD down -v
                echo -e "${GREEN}--- Services stopped and containers removed. ---${NC}"

                read -p "Do you also want to delete all data in ./app/db, ./app/anpr_images, and ./app/logs? This is IRREVERSIBLE. (y/N) " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    echo "Deleting ./app/db/*, ./app/anpr_images/*, ./app/logs/*..."
                    rm -rf "${APP_DIR}/db/"*
                    rm -rf "${IMAGE_DIR}/"*
                    rm -rf "${LOG_DIR}/"*
                    mkdir -p "${APP_DIR}/db" "${IMAGE_DIR}" "${LOG_DIR}" # Recreate dirs
                    echo -e "${GREEN}--- All application data from host volumes has been removed. ---${NC}"
                else
                    echo -e "${YELLOW}--- Application data on host (./app/db, ./app/anpr_images, ./app/logs) remains. ---${NC}"
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