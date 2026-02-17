#!/bin/bash
# Wrapper script to run MCP server with its own virtual environment
# This ensures the MCP server is completely isolated from Django

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="$SCRIPT_DIR/venv"

# Check if local venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Error: Virtual environment not found at $VENV_DIR" >&2
    echo "Please create it with: cd $SCRIPT_DIR && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

# Activate local virtual environment
source "$VENV_DIR/bin/activate"

# Run the server
exec python "$SCRIPT_DIR/server.py"
