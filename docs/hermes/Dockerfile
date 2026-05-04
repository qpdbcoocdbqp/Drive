FROM debian:trixie

# Install necessary utilities, Google Chrome, and socat for port forwarding
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    apt-transport-https \
    socat \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-linux-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Add a non-root user
RUN groupadd -r chrome && useradd -u 1000 -r -g chrome -G audio,video chrome \
    && mkdir -p /home/chrome/debug \
    && chown -R chrome:chrome /home/chrome

USER chrome
WORKDIR /home/chrome

EXPOSE 9223

# Use socat to bridge the loopback-only CDP port to all interfaces
# This fixes the issue where Chrome ignores --remote-debugging-address=0.0.0.0
CMD socat TCP-LISTEN:9223,fork,reuseaddr TCP:127.0.0.1:9224 & \
    google-chrome-stable \
    --headless=new \
    --remote-debugging-port=9224 \
    --no-sandbox \
    --user-data-dir=/home/chrome/debug \
    --remote-allow-origins=* \
    --disable-dev-shm-usage
