# [Local Chrome via CDP](https://hermes-agent.nousresearch.com/docs/user-guide/features/browser#local-chrome-via-cdp-browser-connect)

## Chrome

```bash
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --headless --disable-gpu --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 --user-data-dir="$(pwd)\examples\hermes\temp\chrome-debug" --no-first-run --no-default-browser-check

# host
curl http://localhost:9222/json/version

# {
   # "Browser": "Chrome/147.0.7727.57",
   # "Protocol-Version": "1.3",
   # "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/147.0.0.0 Safari/537.36",
   # "V8-Version": "14.7.173.16",
   # "WebKit-Version": "537.36 (@b4f28b5637d02137aa7f80eb8d589b0c005c3426)",
   # "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/8823b931-87d3-4bb6-b4ec-3feeb2cd8667"
# }

```

## Browser Tool

### Usage

```bash
/browser connect
/browser connect ws://host:port
/browser status
/browser disconnect
```

### Test

```bash
# start Hermes container
cd examples/hermes
docker compose up -d hermes-gateway

# container
curl -H "Host: localhost" http://host.docker.internal:9222/json/version

# hermes
uv run hermes

# input message
navigate https://en.wikipedia.org/wiki/Main_Page
save screenshot to ~/media/
```

### Container Chrome

```bash
# build image
cd docs/hermes/docker
sed -i 's/\r$//' entrypoint.sh
docker build -t chrome-cdp -f cdp-Dockerfile .

# run container
cd examples/hermes
docker run -itd \
--shm-size=2g \
-p 9223:9223 \
--name headless-chrome \
chrome-cdp
```