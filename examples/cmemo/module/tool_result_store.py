"""Tool result storage for Layer 1 memory management.

Large tool outputs are stored on disk, and a 2KB preview is returned for context injection.
"""

import hashlib
import os
from pathlib import Path
from typing import Dict, Optional, Tuple


class ToolResultStore:
    """Manages large tool result storage and previews."""

    def __init__(self, storage_path: str = ".cmemo/tool_outputs", threshold_bytes: int = 2048):
        """Initialize ToolResultStore.
        
        Args:
            storage_path: Directory for large output storage
            threshold_bytes: Size above which outputs are moved to disk
        """
        self.storage_path = Path(storage_path)
        self.threshold = threshold_bytes
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def process_result(self, result: str) -> Tuple[str, Optional[str]]:
        """Process a tool result, storing it if it exceeds the threshold.
        
        Args:
            result: The raw tool output string
            
        Returns:
            A tuple of (preview_text, storage_path_if_stored)
        """
        if len(result.encode('utf-8')) <= self.threshold:
            return result, None

        # Hash the content for a unique filename
        content_hash = hashlib.sha256(result.encode('utf-8')).hexdigest()
        file_path = self.storage_path / f"{content_hash}.txt"

        if not file_path.exists():
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(result)

        preview = result[:self.threshold] + f"\n\n[... Output truncated. Full result stored at {file_path} ...]"
        return preview, str(file_path)

    def get_full_result(self, file_path: str) -> Optional[str]:
        """Retrieve a full tool result from disk.
        
        Args:
            file_path: Path to the stored result
            
        Returns:
            The full string content if it exists
        """
        path = Path(file_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return None
