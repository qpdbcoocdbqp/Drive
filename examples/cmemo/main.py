import openai
import datetime
import json
import os
from pathlib import Path
from examples.cmemo.module import (
    MemoryManager,
    TokenCounter,
    TokenPruner,
    PruneResult,
    ContextConsolidator,
    ConsolidationResult,
    MemoryStore,
    MemoryIndex
    )


client = openai.Client(base_url="http://localhost:9006/v1", api_key="not-needed")
model_name = "alm"

def compress_memory_with_llm(contexts):
    """Uses the ALM model to consolidate multiple contexts into a single summary."""
    
    # Reference the tagged structure adopted by source/cmemo/claude-code-prompts
    system_prompt = """Produce a condensed summary of technical contexts.
Output must be raw text: one <analysis> block followed by one <summary> block.

### Analysis Phase
Inside <analysis> tags, walk through the contexts, identifying key technical concepts, pivotal decisions, and architectural patterns.

### Summary Section
The <summary> block must contain:
1. Primary Technical Concepts
2. Historical/Contextual Details
3. Key Components/Definitions
4. Summary Evaluation (Concise)
"""

    user_content = f"### CONTEXTS TO CONSOLIDATE\n{' '.join(contexts)}"
    
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=1000
        )
        full_text = response.choices[0].message.content.strip()
        
        # Extract content within <summary> tags to avoid storing the analysis process
        import re
        summary_match = re.search(r'<summary>(.*?)</summary>', full_text, re.DOTALL)
        if summary_match:
            return summary_match.group(1).strip()
        
        # If the model doesn't provide tags, attempt to clean up and return
        return full_text.split('<summary>')[-1].split('</summary>')[0].strip()
    except Exception as e:
        return f"Error during LLM consolidation: {e}"

# (Removed redundant imports already added at top)

def cleanup_orphaned_memories(manager):
    """Memory Cleanup: Removing orphaned memory files not listed in index."""
    print("\n--- Memory Cleanup Demo ---")
    # memory_path points to .cmemo/memory
    memory_path = manager.base_path / "memory"
    
    print(f"Checking for orphaned files in: {memory_path}")
    if memory_path.exists():
        print("Files in memory directory before cleanup:")
        found_before = False
        for f in memory_path.iterdir():
            if f.is_file() and f.suffix == '.json':
                print(f" - {f.name}")
                found_before = True
        if not found_before:
            print(" (Directory is empty)")
    
    print("\nRunning manager.cleanup_memories()...")
    deleted = manager.cleanup_memories()
    
    if deleted:
        print(f"Successfully deleted orphaned files: {', '.join(deleted)}")
    else:
        print("No orphaned files found. All JSON memories are properly indexed.")
        
    print("\nFiles in memory directory after cleanup:")
    if memory_path.exists():
        found_after = False
        for f in memory_path.iterdir():
            if f.is_file() and f.suffix == '.json':
                print(f" - {f.name}")
                found_after = True
        if not found_after:
            print(" (No JSON memory files remaining)")

def main():
    print(f"Starting memory management demonstration with model: {model_name}")
    
    # Initialize Memory Manager
    manager = MemoryManager(base_path="examples/cmemo/.cmemo")
    
    # 1. Load contexts from jsonl
    jsonl_path = "examples/cmemo/contexts.jsonl"
    contexts = []
    
    if os.path.exists(jsonl_path):
        print(f"Loading content from {jsonl_path}...")
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        data = json.loads(line)
                        contexts.append(data.get("text", ""))
                    except json.JSONDecodeError:
                        continue
    else:
        # If file doesn't exist, use default content
        print(f"Warning: {jsonl_path} not found. Using default content.")
        contexts = [
            "Memory management is a form of resource management applied to computer memory...",
            "Manual memory management involves fulfilling allocation requests..."
        ]
    
    # Print original sections
    print("\n--- Loaded Sections ---")
    for i, ctx in enumerate(contexts):
        print(f"[{i+1}] {ctx[:100]}...")
    
    original_text = "\n".join(contexts)
    original_tokens = manager.count_tokens(original_text)
    print(f"\nTotal Original Tokens: {original_tokens}")
    
    # 2. Trigger consolidation (the 'dream' workflow)
    print("\nTriggering memory consolidation (LLM Dream)...")
    summary = compress_memory_with_llm(contexts)
    
    # 3. Store the consolidated memory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    memory_id = f"{timestamp}_wiki_{int(original_tokens)}"
    manager.memory_store.save(memory_id, {
        "content": summary, 
        "type": "dream_llm",
        "source": "Wikipedia: Memory Management",
        "original_tokens": original_tokens,
        "final_tokens": manager.count_tokens(summary)
    })
    
    # Update Index (Layer 5)
    manager.memory_index.update_index(
        [memory_id], 
        {memory_id: "Consolidated historical and technical overview of memory management from Wikipedia."}
    )
    
    print("\n--- Consolidated 'Vivid Memory' Summary ---")
    print("-" * 50)
    print(summary)
    print("-" * 50)
    
    final_tokens = manager.count_tokens(summary)
    print(f"Consolidated Token Count: {final_tokens}")
    print(f"Compression Ratio: {original_tokens / final_tokens:.2f}x saved space.")
    # 4. Memory Extraction Example (Retrieving and verifying memory)
    print("\n--- Memory Extraction Demo ---")
    # Load the memory just stored from the persistence layer (Layer 4)
    retrieved_memory = manager.memory_store.load(memory_id)
    if retrieved_memory:
        print(f"Successfully extracted memory ID from .cmemo/memory: {memory_id}")
        print(f"Memory Type: {retrieved_memory.get('type')}")
        print(f"Original Token Count: {retrieved_memory.get('original_tokens')}")
        print(f"Summary Content Preview: {retrieved_memory.get('content')[:100]}...")
    else:
        print(f"Failed to extract memory: {memory_id}")

    # 5. Show current memory index (Showing Layer 5 Index)
    print("\n--- Current Memory Index (MEMORY.md) ---")
    print(manager.memory_index.get_index_content())

    # 6. Memory Cleanup Demo (Now using standalone function)
    cleanup_orphaned_memories(manager)

def simulate_new_session_extraction():
    """Simulates a new conversation session, demonstrating how to extract memory without previous variables."""
    print("\n" + "="*60)
    print("Simulated New Session: Extracting memory from persistence")
    print("="*60)
    
    # Re-initialize components (simulate new process start)
    new_manager = MemoryManager(base_path="examples/cmemo/.cmemo")
    
    # 1. Find available long-term memories via the index (Layer 5)
    print("\n[1] Reading Layer 5 index (MEMORY.md)...")
    index_text = new_manager.memory_index.get_index_content()
    
    # 2. Assume we found an interesting memory ID from the index
    # Here we implement a simple parsing logic to extract the first ID after ##
    import re
    ids = re.findall(r'##\s+([^\n]+)', index_text)
    
    if ids:
        target_id = ids[0].strip()
        print(f"\n[2] Selected memory ID from index: {target_id}")
        
        # 3. Load detailed data from Layer 4 persistence
        print(f"[3] Executing extraction: manager.memory_store.load('{target_id}')")
        memory_data = new_manager.memory_store.load(target_id)
        
        if memory_data:
            print("\n>>> Memory Extraction Successful <<<")
            print(f"Source: {memory_data.get('source')}")
            print(f"Content Summary: {memory_data.get('content')[:200]}...")
        else:
            print(f"\n[!] Extraction failed: Could not find content for ID {target_id}.")
    else:
        print("\n[!] No memory IDs recorded in the index.")

if __name__ == "__main__":
    main()
    # Execute new session extraction demo
    simulate_new_session_extraction()
