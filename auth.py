"""
Authentication Manager for MCP Server.

Handles authentication with the ERP API and maintains session state.
Includes authorization checks to ensure only authorized users can access.
"""

import json
import os
from typing import Optional, Dict, Set
from pathlib import Path


class AuthenticationManager:
    """Manages authentication and authorization for the MCP server."""

    def __init__(self):
        """Initialize the authentication manager."""
        self.tokens: Dict[str, str] = {}  # user_id/email -> token
        self.sessions: Dict[str, Dict] = {}  # user_id/email -> session data
        self.current_token: Optional[str] = None  # Current active token
        self.current_user: Optional[str] = None  # Current user identifier
        self.authorized_emails: Set[str] = set()
        self._load_authorized_users()

    def _load_authorized_users(self):
        """Load list of authorized email addresses from config file."""
        config_path = Path(__file__).parent / "authorized_users.json"
        
        # Allow environment variable override
        auth_list_env = os.getenv("MCP_AUTHORIZED_USERS")
        if auth_list_env:
            emails = [e.strip() for e in auth_list_env.split(",") if e.strip()]
            self.authorized_emails = set(emails)
            return

        # Load from file if exists
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                    self.authorized_emails = set(data.get("authorized_emails", []))
            except Exception:
                # If file is malformed, use empty set (will require manual authorization)
                self.authorized_emails = set()
        else:
            # Create example file
            self._create_example_config(config_path)

    def _create_example_config(self, config_path: Path):
        """Create an example authorized users config file."""
        example_data = {
            "authorized_emails": [
                "user1@example.com",
                "user2@example.com"
            ],
            "note": "Add email addresses of authorized users here. Only these users can use the MCP server."
        }
        try:
            with open(config_path, "w") as f:
                json.dump(example_data, f, indent=2)
        except Exception:
            pass  # Ignore if we can't create the file

    def is_authorized(self, email: str) -> bool:
        """
        Check if an email is authorized to use the MCP server.
        
        Args:
            email: Email address to check
            
        Returns:
            True if authorized, False otherwise
        """
        # If no authorized list is configured, allow all (for initial setup)
        if not self.authorized_emails:
            return True
        
        # Check if email is in authorized list
        email_lower = email.lower().strip()
        return email_lower in {e.lower() for e in self.authorized_emails}

    def set_token(self, user_id: str, token: str):
        """Store authentication token for a user."""
        user_id_lower = user_id.lower() if user_id else "default"
        self.tokens[user_id_lower] = token
        self.current_token = token
        self.current_user = user_id_lower

    def get_token(self, user_id: Optional[str] = None) -> Optional[str]:
        """
        Get authentication token for a user.
        
        Args:
            user_id: Optional user identifier. If not provided, returns current token.
            
        Returns:
            Token string or None
        """
        if user_id:
            return self.tokens.get(user_id.lower())
        return self.current_token

    def set_current_token(self, token: str, user_id: Optional[str] = None):
        """
        Set the current active token.
        
        Args:
            token: Authorization token (can be full "Token xyz" or just "xyz")
            user_id: Optional user identifier
        """
        # Extract token value if full Authorization header format is provided
        if token.startswith("Token "):
            token = token[6:]  # Remove "Token " prefix
        elif token.startswith("Bearer "):
            token = token[7:]  # Remove "Bearer " prefix
        
        user_id = user_id or "default"
        self.set_token(user_id, token)

    def set_session(self, user_id: str, session_data: Dict):
        """Store session data for a user."""
        user_id_lower = user_id.lower() if user_id else "default"
        self.sessions[user_id_lower] = session_data

    def get_session(self, user_id: Optional[str] = None) -> Optional[Dict]:
        """Get session data for a user."""
        if user_id:
            return self.sessions.get(user_id.lower())
        if self.current_user:
            return self.sessions.get(self.current_user)
        return None

    def clear_auth(self, user_id: Optional[str] = None):
        """Clear authentication data for a user."""
        if user_id:
            user_id_lower = user_id.lower()
            self.tokens.pop(user_id_lower, None)
            self.sessions.pop(user_id_lower, None)
            if self.current_user == user_id_lower:
                self.current_token = None
                self.current_user = None
        else:
            self.tokens.clear()
            self.sessions.clear()
            self.current_token = None
            self.current_user = None
