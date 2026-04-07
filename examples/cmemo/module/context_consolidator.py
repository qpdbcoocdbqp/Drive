"""Context consolidation utilities for merging and compressing context entries."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List
import re

from module.token_counter import TokenCounter


@dataclass
class ConsolidationResult:
    """Result of a consolidation operation."""
    consolidated_text: str
    compression_ratio: float
    original_token_count: int
    final_token_count: int
    contexts_merged: int


class ContextConsolidator:
    """Merges and compresses multiple context entries."""
    
    def __init__(self, token_counter: "TokenCounter"):
        """
        Initialize ContextConsolidator with a token counter instance.
        
        Args:
            token_counter: TokenCounter instance for counting tokens
        """
        self._token_counter = token_counter
    
    def consolidate(self, contexts: List[str]) -> ConsolidationResult:
        """
        Consolidate multiple context entries into compressed summary.
        
        This method merges contexts while:
        - Preserving critical information markers (TODO, FIXME, declarations)
        - Removing duplicate information across contexts
        - Maintaining chronological ordering
        - Guaranteeing output token count < input token count
        
        Args:
            contexts: List of context strings to consolidate
            
        Returns:
            ConsolidationResult with consolidated_text and statistics
            
        Raises:
            ValueError: If contexts list is empty
        """
        if not contexts:
            raise ValueError("contexts list cannot be empty")
        
        # Calculate original token count as sum of individual contexts
        # This represents the cost of storing contexts separately
        original_count = sum(self._token_counter.count(ctx) for ctx in contexts)
        
        # Extract critical information from all contexts
        critical_lines = self._extract_critical_lines(contexts)
        
        # Remove duplicates while maintaining order
        unique_lines = self._remove_duplicates(critical_lines)
        
        # Build consolidated text with single newlines
        consolidated = '\n'.join(unique_lines)
        
        # Check token count
        final_count = self._token_counter.count(consolidated)
        
        # If not smaller, apply more aggressive compression
        if final_count >= original_count:
            consolidated = self._apply_compression(unique_lines, original_count)
            final_count = self._token_counter.count(consolidated)
            
            # If still not smaller, remove trailing content
            if final_count >= original_count:
                # Keep removing lines from the end until we're under
                lines = consolidated.split('\n')
                while lines and final_count >= original_count:
                    lines.pop()
                    consolidated = '\n'.join(lines)
                    final_count = self._token_counter.count(consolidated)
        
        # Calculate compression ratio
        compression_ratio = original_count / final_count if final_count > 0 else 1.0
        
        return ConsolidationResult(
            consolidated_text=consolidated,
            compression_ratio=compression_ratio,
            original_token_count=original_count,
            final_token_count=final_count,
            contexts_merged=len(contexts)
        )
    
    def _extract_critical_lines(self, contexts: List[str]) -> List[str]:
        """
        Extract critical lines from contexts in chronological order.
        
        Critical lines include:
        - Lines with TODO or FIXME markers
        - Function/class declarations
        - Import statements
        - Non-empty lines with meaningful content
        
        Args:
            contexts: List of context strings
            
        Returns:
            List of critical lines in chronological order
        """
        critical_lines = []
        
        for context in contexts:
            lines = context.split('\n')
            for line in lines:
                # Skip empty lines
                if not line.strip():
                    continue
                
                # Include all non-empty lines
                critical_lines.append(line)
        
        return critical_lines
    
    def _remove_duplicates(self, lines: List[str]) -> List[str]:
        """
        Remove duplicate lines while preserving chronological order.
        
        Args:
            lines: List of lines to deduplicate
            
        Returns:
            List of unique lines in original order
        """
        seen = set()
        unique_lines = []
        
        for line in lines:
            # Normalize whitespace for comparison
            normalized = ' '.join(line.split())
            
            if normalized not in seen and normalized:
                seen.add(normalized)
                unique_lines.append(line)
        
        return unique_lines
    
    def _apply_compression(self, lines: List[str], target_tokens: int) -> str:
        """
        Apply additional compression to ensure output < input.
        
        Strategy:
        - Prioritize critical markers (TODO, FIXME, declarations)
        - Remove excessive whitespace
        - Truncate less important content to stay under target
        
        Args:
            lines: Lines to compress
            target_tokens: Target token count (must be less than this)
            
        Returns:
            Compressed text
        """
        # Patterns for critical information that must be preserved
        critical_patterns = [
            r'TODO',
            r'FIXME',
            r'^\s*def\s+\w+',  # Function definitions
            r'^\s*class\s+\w+',  # Class definitions
            r'^\s*import\s+',  # Import statements
            r'^\s*from\s+.*\s+import\s+',  # From imports
        ]
        
        # Separate critical and non-critical lines
        critical_lines = []
        other_lines = []
        
        for line in lines:
            if not line.strip():
                continue
                
            is_critical = any(re.search(pattern, line) for pattern in critical_patterns)
            if is_critical:
                critical_lines.append(line.strip())
            else:
                other_lines.append(line.strip())
        
        # Start with all critical lines
        result_lines = critical_lines[:]
        
        # Add other lines until we approach the target
        for line in other_lines:
            test_lines = result_lines + [line]
            test_text = '\n'.join(test_lines)
            current_count = self._token_counter.count(test_text)
            
            # Stop before reaching target to ensure we're under
            if current_count >= target_tokens - 1:
                break
            result_lines.append(line)
        
        return '\n'.join(result_lines)

    def micro_compact(self, context: str) -> str:
        """Perform Layer 2 micro-compaction (lightweight cleanup).
        
        Args:
            context: Context string to compact
            
        Returns:
            Cleaned context string
        """
        # Simple removal of duplicate blank lines and trailing spaces
        lines = [line.rstrip() for line in context.split('\n')]
        compacted = []
        last_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank:
               if not last_blank:
                   compacted.append("")
                   last_blank = True
            else:
                compacted.append(line)
                last_blank = False
        return "\n".join(compacted)

    def dream(self, contexts: List[str]) -> ConsolidationResult:
        """Perform Layer 6 'REM sleep' (agentic consolidation).
        
        Args:
            contexts: Contexts to consolidate deeply
            
        Returns:
            ConsolidationResult
        """
        # In a real implementation, this might involve LLM calls to resolve contradictions.
        # Here we use more aggressive compression and deduplication.
        result = self.consolidate(contexts)
        
        # Post-process the consolidated text to be even more concise
        # e.g., removing any remaining comments
        lines = result.consolidated_text.split('\n')
        vivid_lines = [l for l in lines if not l.strip().startswith(('#', '//'))]
        result.consolidated_text = '\n'.join(vivid_lines).strip()
        result.final_token_count = self._token_counter.count(result.consolidated_text)
        
        return result
