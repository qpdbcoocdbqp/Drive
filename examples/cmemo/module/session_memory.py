"""Session memory for Layer 3 memory management.

Maintains structured, real-time notes about the active session, including
current status, core task specifications, and discovered patterns.
"""

from typing import Dict, List, Optional


class SessionMemory:
    """Manages active session state and mental model."""

    def __init__(self):
        """Initialize Session Memory with default sections."""
        self.notes: Dict[str, str] = {
            "current_status": "Starting session...",
            "core_task_spec": "Calculating task requirements...",
            "discovered_patterns": "Learning from session activity...",
            "technical_constraints": "N/A"
        }
        self.session_log: List[str] = []

    def update_note(self, section: str, content: str):
        """Update a specific section of the session notes.
        
        Args:
            section: Section key to update
            content: New content string
        """
        if section in self.notes:
            self.notes[section] = content
        else:
            self.notes[section] = content  # Allow dynamic section creation

    def add_log_entry(self, entry: str):
        """Add a brief entry to the session log.
        
        Args:
            entry: Log entry text
        """
        self.session_log.append(entry)

    def get_context_snapshot(self) -> str:
        """Return a formatted string of the session memory for injection.
        
        Returns:
            A markdown-formatted representation of the current session state
        """
        snapshot = ["## SESSION MEMORY"]
        for key, value in self.notes.items():
            snapshot.append(f"### {key.replace('_', ' ').title()}")
            snapshot.append(value)
            snapshot.append("")
        
        if self.session_log:
            snapshot.append("### RECENT LOG ENTRIES")
            # Show only last 5 entries to keep context lean
            snapshot.extend(self.session_log[-5:])
            
        return "\n".join(snapshot)

    def clear(self):
        """Clear the session memory."""
        self.notes = {
            "current_status": "",
            "core_task_spec": "",
            "discovered_patterns": "",
            "technical_constraints": ""
        }
        self.session_log = []
