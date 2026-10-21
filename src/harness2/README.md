# Harness² (local model version)

Upstream repo: <https://github.com/google-research/harness2> (commit `1cfe254e8d25b15f37048c3730134686abe60335`)

Run all commands below from `src/harness2/docker`.

## 1. Install

```bash
cd src/harness2/docker

git clone -c core.autocrlf=false https://github.com/google-research/harness2.git module
git -C module checkout 1cfe254e8d25b15f37048c3730134686abe60335
git -C module apply ../provider-refactor.patch

docker compose build
cp harness2.env.example harness2.env

docker compose run --rm harness2          # smoke test; expect "Local demo: 2 tasks completed; 0 failed"
```

## 2. Configure models

Edit `harness2.env`:

```bash
HARNESS2_PROVIDER_BASE_URL=http://host.docker.internal:19001/v1
HARNESS2_PROVIDER_API_KEY=
HARNESS2_SOLVER_MODEL=local/sonnet       # runs the tasks
HARNESS2_IMPROVER_MODEL=local/sonnet     # improves the harness
HARNESS2_JUDGE_MODEL=local/sonnet        # grades the results
# HARNESS2_PROVIDER_CONTEXT=131072       # context limit
# HARNESS2_PROVIDER_OUTPUT=16384         # max output tokens per call
# HARNESS2_RUNS_DIR=runs/<experiment>    # keep results of different experiments apart
```

Model names are `local/<model id on the endpoint>`.

## 3. Usage

Open a shell in the container:

```bash
docker compose run --rm harness2 bash
```

### Run a benchmark

```bash
harness2-setup jb                        # first time: install JobBench (use lab for LAB)
harness2-check --bench jb                # check the environment
harness2 --bench jb --k 1 --mode parallel --max-tasks 1 --dry-run   # preview tasks
harness2 --bench jb --k 1 --mode parallel --max-tasks 1             # run
harness2-judge  --bench jb --k 1 --mode parallel                    # grade
harness2-report --bench jb --k 1 --mode parallel                    # report
```

| Option | Meaning |
|---|---|
| `--k K --mode parallel` | Try K candidate harnesses in parallel |
| `--k K --mode sequential` | Refine the harness over K successive rounds |
| `--only-domains <d>` | Run only the given domains |
| `--max-tasks N` | At most N tasks per domain |
| `--workers N` | Run N domains concurrently |
| `--seed N` | Fix the task order |

- `harness2-judge` and `harness2-report` must use the same `--k`, `--mode` and `--seed` as the run.
- Rerun the same command to resume after an interruption.
- Results are in `runs/jb/`.

### Run your own tasks

Put tasks under `tasks/<domain>/<task>/`:

```
tasks/<domain>/<task>/
├── task_folder/
│   ├── TASK_INSTRUCTIONS.txt   task description; ask for outputs in output/
│   └── <input files>
└── RUBRICS.json                grading criteria
```

`RUBRICS.json`:
```json
{"rubrics": [{"rubric": "What to check", "weight": 10, "criterion": ["Condition 1", "Condition 2"]}]}
```

```bash
harness2-setup jb                                            # first time
cp -r tasks/* dependencies/job-bench-eval/dataset/main/      # after every task change
harness2 --bench jb --only-domains <domain> --k 1 --mode parallel
harness2-judge  --bench jb --k 1 --mode parallel
harness2-report --bench jb --k 1 --mode parallel
```

### Run a single task directly (no refinement)

```bash
export OPENCODE_CONFIG_CONTENT="$(python -c 'import os; from harness2 import config; print(os.environ["OPENCODE_CONFIG_CONTENT"])')"
W=/opt/harness2/runs/jobs/t1_easy
mkdir -p $W && cp -r tasks/local_eval/t1_easy/task_folder/* $W/
/opt/harness2/dependencies/tools/node_modules/.bin/bun run \
  --cwd /opt/harness2/dependencies/opencode/packages/opencode --conditions=browser src/index.ts \
  run "$(cat $W/TASK_INSTRUCTIONS.txt)" --dir $W --model local/sonnet </dev/null
```

Output lands on the host in `runs/jobs/t1_easy/output/`.

### Grade local_eval (on the host)

```bash
python -I answer_keys/grade.py runs/local_eval
```
