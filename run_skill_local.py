import os
import yaml
import requests
import json

class LocalSkillExecutor:
    """
    A simple example simulating the core logic of skill-creator:
    Load SKILL.md -> Assemble System Prompt -> Call local model for execution.
    """
    
    def __init__(self, api_url="http://localhost:9006/v1", model="llama3"):
        self.api_url = api_url
        self.model = model

    def load_skill(self, skill_path):
        """Extract metadata and body content from SKILL.md"""
        with open(skill_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split YAML Frontmatter and Markdown Body
        parts = content.split('---')
        if len(parts) >= 3:
            metadata = yaml.safe_load(parts[1])
            body = parts[2].strip()
            return metadata, body
        return {}, content

    def execute(self, skill_path, user_prompt):
        """Execute the skill"""
        metadata, body = self.load_skill(skill_path)
        
        # Simulate Claude Skill's prompt assembly logic
        system_instruction = (
            f"You are an expert AI assistant using the following skill: {metadata.get('name', 'Unknown')}\n"
            f"Description: {metadata.get('description', '')}\n\n"
            "INSTRUCTIONS:\n"
            f"{body}"
        )

        print(f"--- Executing skill: {metadata.get('name')} using local model [{self.model}] ---")
        
        # Call local model via OpenAI-compatible API (e.g., Ollama, vLLM, LM Studio)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.2  # Skills usually require high determinism
        }

        try:
            response = requests.post(f"{self.api_url}/chat/completions", json=payload)
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            return f"Execution failed: {str(e)}"

# ================= Usage Example =================
if __name__ == "__main__":
    # 1. Configure local model path and endpoint
    # Ensure your local server (e.g., Ollama) is running
    executor = LocalSkillExecutor(
        api_url="http://localhost:9006/v1", 
        model="qwen-thinking" 
    )

    # 2. Specify an existing skill file path
    target_skill = "./source/skills/skills/frontend-design/SKILL.md"
    
    # 3. Define the test prompt
    test_prompt = "Design a minimalist Todo App interface using HTML and vanilla CSS."

    # 4. Execute and output the result
    if os.path.exists(target_skill):
        output = executor.execute(target_skill, test_prompt)
        print("\n[Model Output]:\n")
        print(output)
        
        # 5. (Optional) Simulate skill-creator's save logic
        os.makedirs("local_eval_results", exist_ok=True)
        with open("local_eval_results/output.md", "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\nResult saved to local_eval_results/output.md")
    else:
        print(f"Error: Skill file not found at {target_skill}")
