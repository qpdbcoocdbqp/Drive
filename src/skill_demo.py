from src.module.skills import SkillsRunner

TEXT = """\
We are thrilled to announce v2.0, a game-changing release that seamlessly
elevates your workflow. In today's fast-paced landscape, teams need robust,
cutting-edge tools. This release delivers just that. Moreover, we've leveraged
the power of AI to unlock unprecedented productivity. It's not just an update;
it's a paradigm shift.
"""

runner = SkillsRunner(
    ["dev-skills"],
    base_url="http://localhost:19001/v1",
    model="sonnet",
    workdir=".",
    max_turns=30,
)

result = runner.run(
    f"Operation: review. This is a release note. Diagnose it and stop:\n\n{TEXT}",
    skill="sepia",
)

print(f"finished={result.finished} turns={result.turns}")
for i, step in enumerate(result.steps, 1):
    print(f"[step {i}] {step['name']} {step['arguments']}")
print("\n=== sepia review ===\n")
print(result.content)
