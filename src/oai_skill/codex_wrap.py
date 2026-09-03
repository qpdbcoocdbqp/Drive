# import os
from openai_codex import Codex, CodexConfig


# project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

config = CodexConfig(
    config_overrides=(
        "model_provider=local",
        "model_providers.local.name=local",
        "model_providers.local.base_url=http://localhost:19001/v1",
        "model_providers.local.wire_api=responses",
        "approval_policy=never",
        "sandbox_mode=workspace-write",
        "skills.directories=['./.agents/skills']",
    )
)

# read file test
with Codex(config=config) as codex:
    thread = codex.thread_start(model="sonnet", model_provider="local", cwd=".")
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
    thread = codex.thread_start(model="sonnet", model_provider="local")
    result = thread.run("What time is it in Taipie?")
    print(result.final_response)
    # thread_info = thread.read(include_turns=True)

# skill test
with Codex(config=config) as codex:
    thread = codex.thread_start(model="sonnet", model_provider="local", cwd=".")
    result = thread.run("Use sepia skill review sentance: `Furthermore, it is crucial to leverage cutting-edge technology to streamline our workflow.`")
    print(result.final_response)
