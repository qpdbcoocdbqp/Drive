#!/bin/sh

# Start nginx in the background (proxy :9223 → :9224 with Host: localhost)
nginx

# Clear the lock
rm -rf /home/chrome/debug/SingletonLock

# Start Chrome (using exec so it becomes PID 1 and receives signals)
exec /usr/bin/google-chrome-stable \
    --headless=new \
    --remote-debugging-port=9224 \
    --remote-allow-origins=* \
    --user-data-dir=/home/chrome/debug \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-features=IsolateOrigins,site-per-process
