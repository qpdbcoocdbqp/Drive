"""Unified memory management orchestration following the 7-layer architecture."""

import os
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .token_counter import TokenCounter
from .token_pruner import TokenPruner, PruneResult
from .context_consolidator import ContextConsolidator, ConsolidationResult
from .memory_store import MemoryStore
from .tool_result_store import ToolResultStore
from .session_memory import SessionMemory
from .memory_index import MemoryIndex


class MemoryManager:
    """7-Layer Defensive Pyramid Memory Orchestrator.
    
    Layers:
    1. Tool Result Storage (ToolResultStore)
    2. Micro-compaction (ContextConsolidator.micro_compact)
    3. Session Memory (SessionMemory)
    4. Persistence Layer (MemoryStore)
    5. Memory Index (MemoryIndex)
    6. AutoDream (ContextConsolidator.dream)
    7. Policy Layer (CLAUDE.md)
    """
    
    def __init__(
        self,
        base_path: str = ".cmemo",
        prune_threshold: int = 4000,
        consolidation_threshold: int = 8000
    ):
        """Initialize the 7-layer memory system."""
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        
        # Core Components
        self.token_counter = TokenCounter()
        self.token_pruner = TokenPruner(self.token_counter)
        self.consolidator = ContextConsolidator(self.token_counter)
        
        # Layer 1: Tool Result Storage
        self.tool_store = ToolResultStore(str(self.base_path / "tool_outputs"))
        
        # Layer 3: Session Memory
        self.session_memory = SessionMemory()
        
        # Layer 4: Persistence
        self.memory_store = MemoryStore(str(self.base_path / "memory"))
        
        # Layer 5: Indexing
        self.memory_index = MemoryIndex(str(self.base_path / "memory"))
        
        # Thresholds
        self.prune_threshold = prune_threshold
        self.consolidation_threshold = consolidation_threshold
        
        # Layer 7: Policy
        self.policy = self._load_policy()

    def _load_policy(self) -> str:
        """Layer 7: Load project-specific policy (CLAUDE.md)."""
        policy_path = Path("CLAUDE.md")
        if policy_path.exists():
            return policy_path.read_text(encoding="utf-8")
        return "No project-specific policy (CLAUDE.md) found."

    def process_tool_output(self, output: str) -> str:
        """Layer 1: Process tool output through the storage layer."""
        preview, _ = self.tool_store.process_result(output)
        return preview

    def prepare_context(self, conversation_history: List[str]) -> str:
        """Prepare the full context with all layers.
        
        1. Inject Layer 7 (Policy)
        2. Inject Layer 5 (Memory Index)
        3. Inject Layer 3 (Session Memory Snapshot)
        4. Apply Layer 2 (Micro-compaction) on history
        5. Combine and Prune to threshold
        """
        compacted_history = [self.consolidator.micro_compact(msg) for msg in conversation_history]
        
        context_parts = [
            "=== PROJECT POLICY (L7) ===",
            self.policy,
            "",
            "=== MEMORY INDEX (L5) ===",
            self.memory_index.get_index_content(),
            "",
            self.session_memory.get_context_snapshot(),
            "",
            "=== CONVERSATION HISTORY ===",
            "\n---\n".join(compacted_history)
        ]
        
        full_context = "\n".join(context_parts)
        
        # Final Pruning if still too large
        if self.token_counter.count(full_context) > self.prune_threshold:
            pruned = self.token_pruner.prune(full_context, self.prune_threshold)
            return pruned.pruned_text
            
        return full_context

    def trigger_dream(self, contexts: List[str]):
        """Layer 6: Trigger REM sleep consolidation."""
        result = self.consolidator.dream(contexts)
        # Store the 'vivid memory' in persistence (Layer 4)
        # In a real app, this might be triggered asynchronously
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        memory_id = f"{timestamp}_dream"
        self.memory_store.save(memory_id, {"content": result.consolidated_text, "type": "dream"})
        
        # Update Index (Layer 5)
        # For simplicity, we just list the file
        self.memory_index.update_index(
            [f.stem for f in Path(self.base_path / "memory").glob("*.json")],
            {memory_id: f"Dream consolidation of {result.contexts_merged} contexts."}
        )
        return result

    def update_session(self, section: str, content: str):
        """Update Layer 3 session memory."""
        self.session_memory.update_note(section, content)

    def count_tokens(self, text: str) -> int:
        return self.token_counter.count(text)

    def cleanup_memories(self) -> List[str]:
        """Remove persistent memory files that are no longer indexed."""
        return self.memory_index.cleanup_unindexed_memories()
