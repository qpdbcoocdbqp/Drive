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
  * Setup: [`openclaw.json`](../examples/test-openclaw/demo/openclaw.json)
    * `v1/chat/completions`: `gateway.http.endpoints.chatCompletions.enabled`
    * `v1/responses`: `gateway.http.endpoints.responses.enabled`
    * `v1/embeddings`: `agents.defaults.memorySearch.enabled`

  * Python Client: [examples](../examples/test-openclaw/client.py)

* Add plugin tool
  * [`get_weather`](../examples/test-openclaw/demo/extensions/weather-plugin)
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
