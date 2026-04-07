"""Memory index for Layer 5 memory management.

Maintains a central index (MEMORY.md) of all persistent memory files
to allow efficient retrieval and summary of long-term knowledge.
"""

from pathlib import Path
from typing import Dict, List


class MemoryIndex:
    """Manages MEMORY.md index for persistent memory files."""

    def __init__(self, storage_path: str = ".cmemo/memory", index_file: str = "MEMORY.md"):
        """Initialize MemoryIndex.
        
        Args:
            storage_path: Directory for persistent memory
            index_file: Name of the index file
        """
        self.storage_path = Path(storage_path)
        self.index_path = self.storage_path / index_file
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def update_index(self, memory_files: List[str], summaries: Dict[str, str]):
        """Update the index file with the list of memory files and their summaries.
        
        Args:
            memory_files: List of memory file basenames
            summaries: Dictionary of file name to short summary
        """
        content = ["# Persistent Memory Index", ""]
        content.append("This file contains a directory of all long-term memory for this project.")
        content.append("")
        
        for file in sorted(memory_files):
            summary = summaries.get(file, "No summary available.")
            content.append(f"## {file}")
            content.append(summary)
            content.append("")

        with open(self.index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(content))

    def get_index_content(self) -> str:
        """Return the content of the index file for context injection.
        
        Returns:
            The raw text of MEMORY.md if it exists, otherwise a placeholder.
        """
        if self.index_path.exists():
            with open(self.index_path, "r", encoding="utf-8") as f:
                return f.read()
        return "MEMORY.md index not found. No persistent memory yet."
