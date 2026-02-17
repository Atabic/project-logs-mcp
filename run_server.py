#!/usr/bin/env python3
"""
Wrapper script to run MCP server with its own virtual environment.
This ensures the MCP server is completely isolated from Django.
"""

import sys
import os
from pathlib import Path

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = SCRIPT_DIR / "venv" / "bin" / "python"

# Check if local venv Python exists
if not VENV_PYTHON.exists():
    print(f"Error: Virtual environment Python not found at {VENV_PYTHON}", file=sys.stderr)
    print("Please create it with:", file=sys.stderr)
    print(f"  cd {SCRIPT_DIR}", file=sys.stderr)
    print("  python3 -m venv venv", file=sys.stderr)
    print("  source venv/bin/activate  # or venv\\Scripts\\activate on Windows", file=sys.stderr)
    print("  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# Execute the server script with local venv Python
server_script = SCRIPT_DIR / "server.py"

# Use exec to replace this process with the venv Python running the server
os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(server_script)] + sys.argv[1:])
