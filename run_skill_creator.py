import os
import yaml
import json
from openai import OpenAI

class LocalSkillExecutor:
    """
    A simple example simulating the core logic of skill-creator:
    Load SKILL.md -> Assemble System Prompt -> Call local model for execution.
    """
    
    def __init__(self, api_url="http://localhost:9006/v1", model="llama3", api_key="sk-no-key-required"):
        self.client = OpenAI(
            base_url=api_url,
            api_key=api_key
        )
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
            f"You are operating as the '{metadata.get('name', 'skill-creator')}' expert tool.\n"
            f"PRIMARY OBJECTIVE: {metadata.get('description', '')}\n\n"
            "CORE INSTRUCTIONS:\n"
            f"{body}\n\n"
            "CRITICAL OUTPUT FORMAT:\n"
            "1. You MUST ALWAYS start your output with a YAML frontmatter block enclosed by '---'.\n"
            "2. The block MUST contain 'name:' and 'description:' fields.\n"
            "3. Following the frontmatter, provide the full markdown content of the skill.\n"
            "DO NOT provide any conversational filler or introductory text before the '---' block."
        )

        print(f"--- Executing skill: {metadata.get('name')} using local model [{self.model}] ---")
        
        try:
            # Call local model via OpenAI SDK
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1  # Skills require high determinism
            )
            return response.choices[0].message.content
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
    target_skill = "./source/skills/skills/skill-creator/SKILL.md"
    
    # 3. Define a structured test prompt
    test_prompt = (
        "Project: Python Code Auto-Formatter\n"
        "Requirement: Create a new skill named 'python-standard-formatter'.\n"
        "Capabilities: It should help users format their code using 'black' and check errors using 'flake8'.\n"
        "Trigger context: When users ask to clean up, format, or lint their Python files.\n\n"
        "Please generate the complete SKILL.md content starting with the necessary YAML frontmatter."
    )

    # 4. Execute and output the result
    if os.path.exists(target_skill):
        output = executor.execute(target_skill, test_prompt)
        print("\n[Model Output]:\n")
        print(output)
        
        # 5. (Optional) Simulate skill-creator's save logic
        os.makedirs("local_eval_results_creator", exist_ok=True)
        with open("local_eval_results_creator/output.md", "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\nResult saved to local_eval_results_creator/output.md")
    else:
        print(f"Error: Skill file not found at {target_skill}")
