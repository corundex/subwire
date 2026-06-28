
#!/bin/bash

(
    # Colors first!
    GREEN='\033[0;32m'
    LIGHTBLUE='\033[1;36m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    CYAN='\033[0;36m'
    NC='\033[0m' # No Color
    
    # Get the directory where this script is located and run from there
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR"
    
    echo
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🐳 DOCKER SERVICES DEPLOYMENT${NC}"
    echo -e "${LIGHTBLUE}  📁 Running from: $(basename "$SCRIPT_DIR")${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo
    
    export TARGET=${TARGET:-master.home.lan}
    
    echo -e "${YELLOW}📦 Building and deploying Docker services...${NC}"
    DOCKER_HOST=$TARGET docker compose up --build --detach --force-recreate
    status=$?

    echo
    if [ $status -eq 0 ]; then
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ✅ DOCKER SERVICES DEPLOYMENT COMPLETED!${NC}"
        echo -e "${LIGHTBLUE}  🎯 Docker services deployed to $(echo $TARGET | cut -d'.' -f1)${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    else
        echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  ❌ DOCKER SERVICES DEPLOYMENT FAILED (exit ${status})${NC}"
        echo -e "${LIGHTBLUE}  🎯 Target: $(echo $TARGET | cut -d'.' -f1)${NC}"
        echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
    fi
    echo
    exit $status
)
