# Build in docker

```bash
docker run --rm -it \
-v "/$(pwd)/volume:/volume" \
--name builder \
node:22-slim bash

cp /volume/pnpm-linux-x64 /usr/local/bin/pnpm
SHELL=/bin/bash pnpm setup && source /root/.bashrc

# get nemoclaw
git clone https://github.com/NVIDIA/NemoClaw.git /volume/nemoclaw

# get libsignal-node
git clone https://github.com/whiskeysockets/libsignal-node.git tmp
npm pack --pack-destination  /volume/local_git

# create PNPM_CACHE
mkdir -p /volume/pnpm_nemo_cache
export PNPM_CACHE=/volume/pnpm_nemo_cache

# install tag:0.0.3
cd /volume/nemoclaw && git checkout 0.0.3
cd /volume/nemoclaw && pnpm install --ignore-scripts --store-dir=$PNPM_CACHE --prefer-offline && pnpm run --if-present build:cli
cd /volume/nemoclaw/nemoclaw && pnpm install --ignore-scripts --store-dir=$PNPM_CACHE --prefer-offline && pnpm run build
cd /volume/nemoclaw/ && pnpm link --global
nemoclaw --help
```
