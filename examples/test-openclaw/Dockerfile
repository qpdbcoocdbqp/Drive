FROM ghcr.io/openclaw/openclaw:main-slim
# install python
USER root
RUN apt-get update && \
    apt-get install -y python3 python3-pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
# worker setting
USER node
WORKDIR /home/node
ENTRYPOINT []
