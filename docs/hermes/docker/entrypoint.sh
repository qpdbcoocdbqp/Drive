#!/bin/sh

# Start nginx in the background (proxy :9223 → :9224 with Host: localhost)
nginx

# Clear the lock
rm -rf /home/chrome/debug/SingletonLock

# Start Chrome (using exec so it becomes PID 1 and receives signals)
exec /usr/bin/google-chrome-stable \
    --headless=new \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port=9224 \
    --remote-allow-origins=* \
    --user-data-dir=/home/chrome/debug \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-features=IsolateOrigins,site-per-process \
    --disable-software-rasterizer \
    --disable-extensions \
    --disable-background-networking \
    --disable-sync \
    --metrics-recording-only \
    --mute-audio \
    --disable-default-apps \
    --disable-popup-blocking \
    --disable-translate \
    --hide-scrollbars \
    --window-size=1920,1080
