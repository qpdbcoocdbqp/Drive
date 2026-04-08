import openai
from examples.cmemo.module import (
    MemoryManager,
    TokenCounter,
    TokenPruner,
    PruneResult,
    ContextConsolidator,
    ConsolidationResult,
    MemoryStore
    )


client = openai.Client(base_url="http://localhost:9006/v1", api_key="not-needed")
model_name = "alm"

def compress_memory_with_llm(contexts):
    """Uses the ALM model to consolidate multiple contexts into a single summary."""
    
    # 參考 source/cmemo/claude-code-prompts 採用的標籤化結構
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
        
        # 提取 <summary> 標籤中的內容，避免存儲分析過程
        import re
        summary_match = re.search(r'<summary>(.*?)</summary>', full_text, re.DOTALL)
        if summary_match:
            return summary_match.group(1).strip()
        
        # 如果模型沒給標籤，則嘗試清理冗餘後回傳
        return full_text.split('<summary>')[-1].split('</summary>')[0].strip()
    except Exception as e:
        return f"Error during LLM consolidation: {e}"

import json
import os

def main():
    print(f"Starting memory management demonstration with model: {model_name}")
    
    # Initialize Memory Manager
    manager = MemoryManager(base_path="examples/cmemo/.cmemo")
    
    # 1. 從 jsonl 載入 contexts
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
        # 如果檔案不存在，則使用預設內容
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
    memory_id = f"wiki_memory_consolidation_{int(original_tokens)}"
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
    print(f"\nMemory stored in: examples/cmemo/.cmemo/memory/{memory_id}.json")

if __name__ == "__main__":
    main()
