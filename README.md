# Drive

Inspect Claude skils. Playing with [Drive](https://www.youtube.com/watch?v=fgT9zGkiLig).

* **About Drive**

> Drive·Incubus
>
> Make Yourself

## Reference

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
git clone https://github.com/NVIDIA/NemoClaw.git

cd NemoClaw

docker build -t nemoclaw .
openshell sandbox create --from examples/test-nemoclaw/Dockerfile --name nemoclaw
openshell sandbox delete nclw
nemoclaw onboard
openshell sandbox connect my-assistant


mkdir -p ~/.nemoclaw
cat > ~/.nemoclaw/sandboxes.json << 'EOF'
{
  "sandboxes": [
    {
      "name": "my-assistant",
      "model": "lm",
      "provider": "llamacpp-local",
      "gpuEnabled": false,
      "policies": [],
      "createdAt": "2026-03-23T14:43:57Z"
    }
  ],
  "defaultSandbox": "my-assistant"
}
EOF
nemoclaw my-assistant connect
openclaw agents add main \
  --provider openai \
  --api-key dummy \
  --base-url https://inference.local/v1 \
  --model lm
openclaw agent --agent main --local -m "hello" --session-id test

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
