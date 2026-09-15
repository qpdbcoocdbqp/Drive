import os
import json
from openai_codex import Codex, CodexConfig, SkillInput, TextInput


config = CodexConfig(
    config_overrides=(
        "model_provider=local",
        "model_providers.local.name=local",
        "model_providers.local.base_url=http://localhost:19001/v1",
        "model_providers.local.wire_api=responses",
        "approval_policy=never",
        "sandbox_mode=workspace-write",
        # "skills.directories=['./.agents/skills']",
    )
)

with Codex(config=config) as codex:
    skills_response = codex._client._request_raw("skills/list", {})
    print("\n--- Codex Skills (skills/list) ---")
    print(json.dumps(skills_response, indent=2, ensure_ascii=False))

# read file test
with Codex(config=config) as codex:
    thread = codex.thread_start(model="sonnet", model_provider="local", cwd=os.getcwd())
    result = thread.run("Explain this repository in three bullets.")
    print(result.final_response)
    # thread_info = thread.read(include_turns=True)

# if thread_info.thread.turns:
#     for i, turn in enumerate(thread_info.thread.turns):
#         print(f"--- Turn {i+1} ---")
#         print("Status:", turn.status)
#         print("Items / Input / Output:", turn)

# interpreter test
with Codex(config=config) as codex:
    thread = codex.thread_start(model="sonnet", model_provider="local", cwd=os.getcwd())
    result = thread.run("What time is it in Taipie?")
    print(result.final_response)
    # thread_info = thread.read(include_turns=True)

template = """
# Cover letter

[Your Name]

[Your Phone Number] | [Your Email] | [Your LinkedIn Profile] | [Your Portfolio/Website]

[Date]

[Hiring Manager's Name or "Hiring Team"]

[Hiring Manager's Title, e.g., Recruiting Manager]

[Company Name]

[Company Address]

Dear [Hiring Manager's Name or "Hiring Manager" / "Selection Committee"],

[Opening Paragraph: State the position & express enthusiasm]

I am writing to express my strong interest in the [Job Title] position at [Company Name], as advertised on [Where you found the job, e.g., LinkedIn / company career page]. With my background in [Your Field/Industry] and expertise in [1–2 Key Skills relevant to the role], I am excited about the opportunity to contribute to [Company Name]'s team and help drive [a key goal or project of the company].

[Body Paragraph 1: Highlight key achievements & core competency]

Throughout my experience as a [Your Current or Recent Job Title] at [Current/Previous Company], I have developed strong skills in [Skill 1, Skill 2, and Skill 3]. In my recent role, I successfully [Action verb + major accomplishment with quantifiable results, e.g., increased organic web traffic by 35% in six months]. Furthermore, I have a proven track record of [Another relevant skill/achievement, e.g., managing cross-functional teams to deliver projects on time], which prepares me to excel in the responsibilities required for this role.

[Body Paragraph 2: Connect your value to the company’s needs]

What particularly draws me to [Company Name] is your commitment to [Mention a specific company value, project, product, or recent achievement]. My proficiency in [Specific tool, technology, or methodology] aligns directly with your team's current focus on [Role responsibility mentioned in job description]. I am confident that my problem-solving abilities and [Soft skill, e.g., proactive communication style] will allow me to make an immediate, positive impact on your team.

[Closing Paragraph: Call to action & thank you]

Thank you for your time and consideration. I would welcome the opportunity to discuss how my experience and skills align with the needs of [Company Name]. Please find my resume attached for your review, and feel free to contact me at [Your Phone Number] or [Your Email Address] to arrange an interview.

Sincerely,

[Your Signature]

[Your Printed Name]
"""


# skill test
with Codex(config=config) as codex:
    thread = codex.thread_start(model="sonnet", model_provider="local", cwd=os.getcwd())
    prompt = f"""Use the supplied `sepia` skill to refactor this cover letter.

Follow the skill's routing table and apply the `refactor` operation, preserving
structure, intent, and all placeholders. Do not invent facts. Do not run
`sepia` as a shell or PowerShell command; the skill is supplied by the Codex
runtime as a typed skill input.

Cover letter:
{template}"""
    result = thread.run([
        SkillInput(
            name="sepia",
            path=os.path.abspath(os.path.join(os.getcwd(), ".agents", "skills", "sepia", "SKILL.md")),
        ),
        TextInput(text=prompt),
    ])
    print(result.final_response)
    thread_info = thread.read(include_turns=True)
    print("\n--- Skill thread turns/events ---")
    print(thread_info)
