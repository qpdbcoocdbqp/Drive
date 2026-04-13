# Drive

Inspect Claude skils. Playing with [Drive](https://www.youtube.com/watch?v=fgT9zGkiLig).

* **About Drive**

> Drive·Incubus
>
> Make Yourself

## Reference

* [ultraworkers/claw-code](https://github.com/ultraworkers/claw-code)
* [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
* [anthropics/skills](https://github.com/anthropics/skills)
* [accomplish-ai/accomplish](https://github.com/accomplish-ai/accomplish)
* [NVIDIA/NemoClaw](https://github.com/NVIDIA/NemoClaw)
* [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell)
* [NVIDIA/OpenShell-Community](https://github.com/NVIDIA/OpenShell-Community)

## Setup

```bash
mkdir source
cd source
git clone https://github.com/anthropics/skills.git

cd ../
uv venv --python 3.13
source venv/bin/activate
uv install requests pyyaml
```

## Run

```bash
python run_skill_local.py
```

## OpenShell and NemoClaw

* setup

```bash
## for Windows use wsl to run this
# wsl

uv tool install -U openshell

mkdir source
cd source
git clone https://github.com/NVIDIA/NemoClaw.git ./source/nemoclaw

cd source/nemoclaw
# First time run nemoclaw
sed -i 's/\r$//' install.sh && ./install.sh
# Or use onboard command
nemoclaw onboard

# Image: ghcr.io/nvidia/openshell/cluster:0.0.15
# Image: ghcr.io/nvidia/openshell/gateway:0.0.15

# ✓ Gateway nemoclaw destroyed.
#   Using pinned OpenShell gateway image: ghcr.io/nvidia/openshell/cluster:0.0.15
# ✓ Checking Docker
# ✓ Downloading gateway
# ✓ Initializing environment
# ✓ Starting gateway
# ✓ Gateway ready
# ...
#   ──────────────────────────────────────────────────
#   Sandbox      nemo (Landlock + seccomp + netns)
#   Model        lm (Other OpenAI-compatible endpoint)
#   NIM          not running
#   ──────────────────────────────────────────────────
#   Next:
#   Run:         nemoclaw nemo connect
#   Status:      nemoclaw nemo status
#   Logs:        nemoclaw nemo logs --follow
#   ──────────────────────────────────────────────────

# [INFO]  === Installation complete ===

# Enter sandnox use openshell
openshell sandbox connect nemo
# or nemoclaw
nemoclaw nemo connect

# In sandbox
# check model connection
curl https://inference.local/v1/models

# show openclaw dashboard
/usr/local/bin/nemoclaw-start
# [gateway] Local UI: http://127.0.0.1:18789/#token=<token>
# [gateway] Remote UI: http://127.0.0.1:18789/#token=<token>

# start openclaw features: gateway
nohup openclaw gateway run > /tmp/gateway.log 2>&1 & 
openclaw devices list
openclaw devices approve <Device_Uid>
# Terminal UI
openclaw tui 

# After exit sandbox, destroy the sandbox
nemoclaw nemo destroy
```

* Run OpenShell

  * gateway

    ```bash
    # start gateway
    openshell gateway start --name openshell --port 8080 --disable-gateway-auth

    # check gateway
    openshell gateway info
    openshell gateway select openshell
    openshell status

    # terminal browser
    openshell term

    # clean gateway
    openshell gateway destroy --name openshell
    ```

  * sandbox

    ```bash
    # create sandbox from Dockerfile
    openshell sandbox create \
    --from examples/test-byoc/Dockerfile \
    --forward 8081 \
    --name byoc \
    -- python /sandbox/app.py > /tmp/app.log 2>&1 &

    # chech sandboxs
    openshell sandbox list

    # forward port 8081
    openshell forward start -d 8081 byoc

    # check test app in sandbox
    curl http://127.0.0.1:8081/health
    curl http://127.0.0.1:8081/hello

    # clean sandbox
    openshell forward stop 8081
    openshell sandbox delete byoc
    ```

  * provider

    ```bash
    
    # start llama-server
    ./llama-server -m ./models/your_model.gguf --port 9006 --host 0.0.0.0

    # check llama-server
    curl http://0.0.0.0:9006/health
    curl http://0.0.0.0:9006/v1/models

    # create provider
    openshell provider create \
      --name llamacpp-local \
      --type openai \
      --credential OPENAI_API_KEY=unused \
      --config OPENAI_BASE_URL=http://host.openshell.internal:9006/v1

    openshell inference set --provider llamacpp-local --model lm
    openshell inference get
    openshell provider list

    # create sandbox to test provider
    docker pull ghcr.io/nvidia/openshell-community/sandboxes/base:latest

    openshell sandbox create --name test-llama-local -- \
        curl -k https://inference.local/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
          "messages": [{"role": "user", "content": "hi, how are you?"}],
          "max_tokens": 50
        }'

    openshell sandbox list
    openshell sandbox delete test-llama-local
    ```

* Run OpenClaw

  * agent access

    ```bash
    # create openclaw sandbox
    openshell sandbox create \
      --from source/NemoClaw/Dockerfile \
      --name openclaw

    # enter sandbox
    openshell sandbox connect openclaw
    # in sandbox
    # test local model is available
    curl https://inference.local/v1/models

    # send message with openclaw
    openclaw agent --local \
      -m "Don't use any tools. Just say hello back to me." \
      --session-id test_hello \
      --verbose on

    ```

## OpenClaw

* Run in Docker

  ```bash
  # Pull image
  docker pull ghcr.io/openclaw/openclaw:main-slim
  docker build -t openclaw:local -f examples/test-openclaw/dockerfile

  # Run container
  docker run -itd \
  -p 18789:18789 \
  -v "/$(pwd)/examples/test-openclaw/volume:/home/node/.openclaw" \
  --name openclaw \
  openclaw:local bash

  # In container
  docker exec -it openclaw bash
  ## Start gateway
  openclaw gateway --bind lan --force
  openclaw gateway status
  openclaw status
  openclaw dashboard --no-open

  # set model provider
  openclaw config


  # test gateway
  >>> {"ok":true,"status":"live"}
  >>> {"ready":true,"failing":[],"uptimeMs":294562}

  ```

* Gateway
  * API: `http://localhost:18789`
  * Dashboard: `http://localhost:18789/#token=<TOKEN>`
    * Approve device request on dashboard
      ```bash
      # list pending requests
      openclaw devices list
      # approve by request ID
      openclaw devices approve <requestId>
      # or approve latest request  
      openclaw devices approve --latest
      ```

* Gateway: OpenAI API
  * Setup: [`openclaw.json`](examples/test-openclaw/volume/openclaw.json)
    * `v1/chat/completions`: `gateway.http.endpoints.chatCompletions.enabled`
    * `v1/responses`: `gateway.http.endpoints.responses.enabled`
    * `v1/embeddings`: `agents.defaults.memorySearch.enabled`

  * Python Client: [examples](examples/test-openclaw/client.py)

* Add plugin tool
  * [`get_weather`](examples/test-openclaw/volume/extensions/weather-plugin)
  * install plugin

    ```bash
    docker exec -u 0 openclaw sh -c "chmod -R 755 /home/node/.openclaw/extensions/weather-plugin && chown -R node:node /home/node/.openclaw/extensions/weather-plugin"
    docker exec openclaw openclaw config set plugins.allow '["weather-plugin"]' --strict-json
    docker exec openclaw openclaw config set tools.allow '["get_weather"]' --strict-json
    docker exec openclaw openclaw config set tools.deny '["group:openclaw","group:fs","group:runtime"]' --strict-json
    docker exec openclaw sh -c "openclaw plugins install /home/node/.openclaw/extensions/weather-plugin"
    docker exec -d openclaw sh -c "openclaw gateway --bind lan --force"
    docker exec openclaw sh -c "openclaw plugins list"
    docker exec openclaw sh -c "openclaw gateway status"
    docker exec openclaw sh -c "openclaw status"
    ```

  * testing
  
    ```bash
    curl -sS http://localhost:18789/v1/chat/completions \
      -H 'Authorization: Bearer <TOKEN>' \
      -H 'Content-Type: application/json' \
      -d '{
        "model": "openclaw/default",
        "messages": [
          {"role": "user", "content": "What is the weather in Tokyo?"}
        ]
      }'
    ```

## Hermes

* [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)

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
docker run -it \
  --name hermes \
  --memory=3g --cpus=2 --shm-size=1g \
  --restart unless-stopped \
  -v /$(pwd)/.hermes:/opt/data \
  nousresearch/hermes-agent gateway run
```

## Claw-Code

### Setup

```bash
cargo install agent-code
# check installed version
agent --version
# agent 0.14.0
```



