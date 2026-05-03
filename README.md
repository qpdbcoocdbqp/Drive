# Drive

Inspect Claude skils. Playing with [Drive](https://www.youtube.com/watch?v=fgT9zGkiLig).

* **About Drive**

> Drive·Incubus
>
> Make Yourself

## Reference

* [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)

## Hermes

### Setup

```bash
# Clone the repository
cd source
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent

# Install dependencies
uv pip install -e ".[all]"

# Initial setup (API keys, models, providers)
hermes setup
```

### Run

```bash
# Start the interactive Terminal UI
hermes

# Start the messaging gateway (Telegram, Discord, etc.)
hermes gateway setup
hermes gateway start

# Check status and diagnose issues
hermes doctor

# Dashboard web UI
hermes dashboard --port 18080 --no-open

```

### Run in Docker

* [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker)

Hermes can be run in Docker to isolate the environment. All data (config, keys, sessions) is stored in a persistent volume.

#### 1. Initial Setup
Run the setup wizard interactively:
```bash
mkdir -p ./.hermes
docker run -it --rm \
  -v /$(pwd)/.hermes:/opt/data \
  nousresearch/hermes-agent setup
```

#### 2. Interactive CLI Chat
```bash
docker run -it --rm \
  -v /$(pwd)/.hermes:/opt/data \
  nousresearch/hermes-agent
```

#### 3. Persistent Gateway Mode
Run in the background for messaging platforms (Telegram, Discord, etc.):

* Discord: `.hermes/.env`

  ```ini
  DISCORD_BOT_TOKEN=<BOT_TOKEN>
  DISCORD_ALLOWED_USERS=<USER_ID>
  ```

```bash
docker run -itd \
  --name hermes \
  --memory=3g --cpus=2 --shm-size=1g \
  --restart unless-stopped \
  -v /$(pwd)/.hermes:/opt/data \
  nousresearch/hermes-agent gateway run
```

* [joeynyc/hermes-hudui](https://github.com/joeynyc/hermes-hudui)
* [browser tool](docs/hermes/browser.md)
* [Web tool - firecrawl](docs/hermes/firecrawl-read-pdf.md)
* [Custom toolsets](docs/hermes/custom_toolsets.md)

```bash
docker build -t hermes-hudui:0.5.0 .
docker run -it --rm -p 3001:3001 --name hermes-hudui hermes-hudui:0.5.0 bash
```

### Skill

* Build-in skill is managed by `~/.hermes/skills/.bundled_manifest`
* Delete Build-in skill: 
  1. change skill hash to `none`
  2. delete the SKILL.md
  3. restart container
  4. check skills: `hermes skills list`

* Install skill: 
  * Example `skill-creator`: `hermes skills install anthropics/skills/skill-creator`

* Create skill `all-in-podcast-latest-title-capture`

  ```bash
  # Run Hermes chat
  hermes
  # chat input messages
  # input 1
  To navigate youtube homepage, search `All-in podcast`
  # input 2
  find the latest episode. save title of the episode to a file. file name is the title.
  # input 3
  create a skill `all_in_podcast_latest_title_capture`. which handle the previous job.
  ```

### Tool

* Build-in tools are managed by `~/.hermes/config.yaml`.
* On CLI, `platform_toolsets.cli`
* On Discord, `platform_toolsets.discord`

```yaml
platform_toolsets:
  cli:
  - web
  - terminal
  discord:
  - web
  - terminal
```

### Session

* delete session

```bash
# show session
hermes sessions list
# delete 3 days ago
hermes sessions prune --older-than 3
# delete by ID
hermes sessions delete <session_id>  -y
```

### Delegation task (Sub-agent)

* Set in `~/.hermes/config.yaml`.

```yaml
delegation:
  model: alm
  provider: custom
  base_url: http://host.docker.internal:9006/v1
  api_key: "***"
  inherit_mcp_toolsets: true
  max_iterations: 50
  child_timeout_seconds: 600
  reasoning_effort: ""
  max_concurrent_children: 3
  max_spawn_depth: 1
  orchestrator_enabled: true
  default_toolsets:
    - file
    - skills
    - terminal
    - vision
```



## Relations

### [Skills](docs/anthropics-skills.md)
* [anthropics/skills](https://github.com/anthropics/skills)

### [OpenShell and NemoClaw](docs/openshell-and-nemoclaw.md)
* [NVIDIA/NemoClaw](https://github.com/NVIDIA/NemoClaw)
* [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell)
* [NVIDIA/OpenShell-Community](https://github.com/NVIDIA/OpenShell-Community)

### [OpenClaw](docs/openclaw.md)

### [Claw-Code](docs/agent-code.md)
* [ultraworkers/claw-code](https://github.com/ultraworkers/claw-code)
