#!/bin/bash
# tests/verify_resilience.sh

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "--- Starting Resilience Test ---"

# 1. Ensure all services are running
echo "Checking if services are up..."
if [ "$(docker ps -q -f name=anpr-web)" ] && [ "$(docker ps -q -f name=anpr-db-manager)" ]; then
    echo -e "${GREEN}All services are running.${NC}"
else
    echo -e "${RED}Services are not running. Please start them first.${NC}"
    exit 1
fi

# 2. Stop anpr-web
echo "Stopping anpr-web container..."
docker stop anpr-web

# 3. Check anpr-db-manager health
echo "Checking anpr-db-manager health..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/health)

if [ "$HTTP_STATUS" -eq 200 ]; then
    echo -e "${GREEN}SUCCESS: anpr-db-manager is still responding (HTTP 200).${NC}"
else
    echo -e "${RED}FAILURE: anpr-db-manager is NOT responding (HTTP $HTTP_STATUS).${NC}"
    exit 1
fi

# 4. Check anpr-listener status
echo "Checking anpr-listener status..."
if [ "$(docker ps -q -f name=anpr-listener)" ]; then
    echo -e "${GREEN}SUCCESS: anpr-listener is still running.${NC}"
else
    echo -e "${RED}FAILURE: anpr-listener has stopped.${NC}"
    exit 1
fi

# 5. Restart anpr-web
echo "Restarting anpr-web..."
docker start anpr-web

echo -e "${GREEN}--- Resilience Test Passed ---${NC}"
