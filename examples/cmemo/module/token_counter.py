"""Token counting utilities for Claude-compatible token counting."""

import tiktoken


class TokenCounter:
    """Provides consistent token counting using tiktoken library."""
    
    def __init__(self):
        """Initialize TokenCounter with Claude-compatible encoding."""
        # Use cl100k_base encoding which is compatible with Claude models
        self._encoding = tiktoken.get_encoding("cl100k_base")
    
    def count(self, text: str) -> int:
        """
        Count tokens in text.
        
        Args:
            text: Input text to count
            
        Returns:
            Non-negative integer token count
        """
        if not text:
            return 0
        
        tokens = self._encoding.encode(text)
        return len(tokens)
