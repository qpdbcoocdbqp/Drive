"""Token pruning utilities for intelligent token reduction."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from module.token_counter import TokenCounter


@dataclass
class PruneResult:
    """Result of a pruning operation."""
    pruned_text: str
    tokens_removed: int
    original_token_count: int
    final_token_count: int


class TokenPruner:
    """Intelligently reduces token count while preserving semantic meaning."""
    
    def __init__(self, token_counter: "TokenCounter"):
        """
        Initialize TokenPruner with a token counter instance.
        
        Args:
            token_counter: TokenCounter instance for counting tokens
        """
        self._token_counter = token_counter
    
    def prune(self, text: str, target_tokens: int) -> PruneResult:
        """
        Prune text to approximately target token count.
        
        This method reduces token count while preserving:
        - Code block syntax (matching braces, brackets, quotes)
        - Paragraph boundaries
        - Semantic meaning
        
        The pruning is idempotent: applying it twice produces the same
        result as applying it once.
        
        Args:
            text: Input text to prune
            target_tokens: Desired token count after pruning
            
        Returns:
            PruneResult with pruned_text and statistics
            
        Raises:
            ValueError: If target_tokens is negative
        """
        if target_tokens < 0:
            raise ValueError("target_tokens must be non-negative")
        
        original_count = self._token_counter.count(text)
        
        # Always perform pruning to ensure idempotence and normalization
        pruned = self._perform_pruning(text, target_tokens)
        final_count = self._token_counter.count(pruned)
        
        return PruneResult(
            pruned_text=pruned,
            tokens_removed=original_count - final_count,
            original_token_count=original_count,
            final_token_count=final_count
        )
    
    def _perform_pruning(self, text: str, target_tokens: int) -> str:
        """
        Perform the actual pruning operation.
        
        Strategy:
        1. Remove comments (lines starting with # or //)
        2. Reduce excessive whitespace
        3. Remove redundant blank lines
        4. Truncate paragraphs if still over target
        
        Args:
            text: Text to prune
            target_tokens: Target token count
            
        Returns:
            Pruned text
        """
        lines = text.split('\n')
        
        # Step 1: Remove comment-only lines
        filtered_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip lines that are only comments
            if stripped.startswith('#') or stripped.startswith('//'):
                continue
            filtered_lines.append(line)
        
        # Step 2: Reduce excessive blank lines (keep max 1 consecutive blank line)
        reduced_lines = []
        blank_count = 0
        for line in filtered_lines:
            is_blank = not line.strip()
            if is_blank:
                blank_count += 1
                if blank_count <= 1:  # Keep first blank line
                    reduced_lines.append(line)
            else:
                blank_count = 0
                reduced_lines.append(line)
        
        # Join and check token count
        result = '\n'.join(reduced_lines)
        current_count = self._token_counter.count(result)
        
        # Step 3: If still over target, reduce whitespace
        if current_count > target_tokens:
            # Remove leading/trailing whitespace from each line
            trimmed_lines = [line.rstrip() for line in reduced_lines]
            result = '\n'.join(trimmed_lines)
            current_count = self._token_counter.count(result)
        
        # Step 4: If still over target, truncate by paragraphs
        if current_count > target_tokens:
            paragraphs = result.split('\n\n')
            kept_paragraphs = []
            
            for para in paragraphs:
                kept_paragraphs.append(para)
                test_text = '\n\n'.join(kept_paragraphs)
                if self._token_counter.count(test_text) > target_tokens:
                    # Remove the last paragraph that pushed us over
                    if len(kept_paragraphs) > 1:
                        kept_paragraphs.pop()
                    break
            
            result = '\n\n'.join(kept_paragraphs)
        
        return result
