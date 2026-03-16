# Deep Analysis of Skill Creator Operation Process

`skill-creator` is a "meta-skill" within the Claude Skill ecosystem, designed to create, test, evaluate, and optimize other skills through a standardized process. Its operation process can be divided into four core phases: **Intent Capture & Writing**, **Parallel Testing & Evaluation**, **Feedback Improvement Loop**, and **Trigger Optimization**.

---

## 1. Intent Capture & Writing Phase

Before writing any code, `skill-creator` clarifies goals through in-depth interviews:

- **Intent Extraction**: Clearly define the core problem the skill aims to solve.
- **Trigger Scenarios**: Define the context in which the user should trigger this skill.
- **Architectural Design**: Adopts a **three-layer loading pattern (Progressive Disclosure)**:
    1.  **Metadata (YAML)**: Minimalist name and "pushy" task description (~100 words).
    2.  **SKILL.md Body**: Core logic and constraints (ideally < 500 lines).
    3.  **Bundled Resources**: Extensive documentation (references/) or scripts (scripts/).
- **Writing Principle**: Emphasize "Theory of Mind" by explaining "Why" rather than just giving "Must" commands to guide the model.

---

## 2. Parallel Testing & Evaluation Phase

This is the most technical part of `skill-creator`, typically including the following steps:

### A. Parallel Runs
Run two sets of subagents simultaneously for comparison:
- **With-Skill**: Claude loaded with the new version of the skill.
- **Baseline**:
    - For new skills: No skill loaded at all.
    - For improvements: A snapshot of the old skill version.

### B. Assertions & Metrics
While the model is running, write objective assertions for automated scoring:
- **Pass Rate**.
- **Performance Data**: Record `total_tokens` and `duration_ms` (key for evaluating skill efficiency).

### C. Result Visualization (The Viewer)
Use the `generate_review.py` script to generate an HTML review interface:
- **Outputs Tab**: Displays outputs from both versions for qualitative human feedback.
- **Benchmark Tab**: Shows token consumption, time comparison, and assertion pass rates.

---

## 3. Feedback Improvement Loop

Iterate based on feedback from the review interface:

- **Generalize Feedback**: Avoid overfitting to specific cases; emphasize solving issues through metaphors or different working patterns.
- **Lean Prompting**: Remove ineffective instructions by reading execution transcripts.
- **Common Script Extraction**: If similar helper scripts are written across multiple test cases, extract them to the `scripts/` directory as formal parts of the skill.

---

## 4. Trigger Optimization Phase

Once the skill logic is stable, the final step ensures it is accurately "awakened":

1.  **Generate Test Set**: Create 20 complex, realistic queries (10 should-trigger and 10 "near-miss" should-not-trigger cases).
2.  **Run Optimization Loop**:
    - Run 5 iterations in the background using `run_loop.py`.
    - The system automatically modifies the `description` and runs trigger tests.
    - **Scoring Mechanism**: Select the best description based on performance on a held-out test set.
3.  **Apply Description**: Update the YAML frontmatter of the skill with the optimized description.

---

## Core Philosophy: Expert-Centric Design

The operation logic of `skill-creator` reflects Claude's understanding of Skills:
- **Not a rigid manual**: But an expert's cognitive framework.
- **Data-Driven**: All improvements are based on actual metrics from With-Skill vs Baseline comparisons.
- **Transparency**: Forces human intervention in evaluation via the Viewer to ensure AI-generated content meets expectations.
