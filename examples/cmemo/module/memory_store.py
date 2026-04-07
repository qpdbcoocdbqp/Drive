"""Memory persistence component for storing and retrieving memory data."""

import json
import os
from pathlib import Path
from typing import Any, Dict


class MemoryStore:
    """Handles persistence of memory data to the file system.
    
    Provides methods to save and load memory data using JSON serialization.
    Creates storage directories automatically and uses atomic writes to
    prevent data corruption.
    """
    
    def __init__(self, storage_path: str = ".cmemo/memory"):
        """Initialize MemoryStore with storage path.
        
        Args:
            storage_path: Directory path for storing memory files
        """
        self.storage_path = Path(storage_path)
    
    def save(self, memory_id: str, data: Dict[str, Any]) -> bool:
        """Save memory data to disk using JSON serialization.
        
        Creates the storage directory if it doesn't exist. Uses atomic writes
        by writing to a temporary file first, then renaming to prevent corruption.
        
        Args:
            memory_id: Unique identifier for this memory
            data: Dictionary containing memory data to save
            
        Returns:
            True if save successful
            
        Raises:
            IOError: If file write fails
            ValueError: If data is not JSON serializable
        """
        # Create storage directory if it doesn't exist
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Construct file path
        file_path = self.storage_path / f"{memory_id}.json"
        temp_path = self.storage_path / f"{memory_id}.json.tmp"
        
        try:
            # Validate data is serializable
            json_data = json.dumps(data, indent=2)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Data is not JSON serializable: {e}")
        
        try:
            # Atomic write: write to temp file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(json_data)
                f.flush()
                os.fsync(f.fileno())
            
            # Rename temp file to final destination (atomic on most systems)
            temp_path.replace(file_path)
            return True
            
        except IOError as e:
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()
            raise IOError(f"Failed to save memory '{memory_id}': {e}")
    
    def load(self, memory_id: str) -> Dict[str, Any]:
        """Load memory data from disk.
        
        Args:
            memory_id: Unique identifier for memory to load
            
        Returns:
            Dictionary with memory data, or empty dict if file not found
            
        Raises:
            IOError: If file read fails (but file exists)
        """
        file_path = self.storage_path / f"{memory_id}.json"
        
        # Return empty dict if file doesn't exist (not an error)
        if not file_path.exists():
            return {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
            
        except (IOError, json.JSONDecodeError) as e:
            raise IOError(f"Failed to load memory '{memory_id}': {e}")
