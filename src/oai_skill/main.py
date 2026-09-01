from openai import OpenAI
from src.oai_skill.skill_runner import OpenAISkillRunner, SkillCatalog


skills_dir = "./dev-skills"
model = "sonnet"
skill = "code-review"

runner = OpenAISkillRunner(
    OpenAI(base_url="http://localhost:19001/v1", api_key="***"),
    SkillCatalog([skills_dir]), model=model,
    cwd=".",  # git commands run from the workspace root
    )
print(runner.run(prompt="Review the current diff", skills=[skill]))
