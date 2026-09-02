from openai import OpenAI
from src.oai_skill.skill_runner import OpenAISkillRunner, SkillCatalog


skills_dir = "./dev-sepia"
model = "sonnet"
skill = "sepia"

runner = OpenAISkillRunner(
    OpenAI(base_url="http://localhost:19001/v1", api_key="***"),
    SkillCatalog([skills_dir]), model=model,
    cwd=".",  # git commands run from the workspace root
    )
review_response= runner.run(prompt="""review content: 
The module deliberately treats a skill as instructions, not executable Python.
The model can discover compact metadata in its system prompt and requests the
full document through ``skill_view`` only when it is relevant.  This mirrors
Hermes' progressive-disclosure design while remaining independent of Hermes.

Progressive-disclosure tiers (matching Hermes):
  Tier 1 — compact index in the system prompt (name + description only).
  Tier 2 — full skill body loaded on demand via the ``skill_view`` tool call.
  Tier 3 — supporting files (references/, templates/, assets/, scripts/) loaded
            via ``skill_view`` with an explicit ``file_path`` argument.
""",
skills=[skill])

print(review_response)
