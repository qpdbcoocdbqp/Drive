```bash
docker run --rm -it \
-v "/$(pwd)/volume:/volume" \
--name builder \
node:22-slim bash

cp /volume/pnpm-linux-x64 /usr/local/bin/pnpm
git clone https://github.com/NVIDIA/NemoClaw.git nemoclaw

mkdir -p /volume/pnpm_nemo_cache
export PNPM_CACHE=/volume/pnpm_nemo_cache

cd /volume/nemoclaw

pnpm install --ignore-scripts --store-dir=$PNPM_CACHE --prefer-offline

cd /volume/nemoclaw/nemoclaw && pnpm install --ignore-scripts --store-dir=$PNPM_CACHE && pnpm run build
cd /volume/nemoclaw/ && pnpm link --global

npm pack "@whiskeysockets/baileys@7.0.0-rc.9" --pack-destination ./local_git
git clone https://github.com/whiskeysockets/libsignal-node.git tmp
npm pack --pack-destination ../local_git

cat > .pnpmrc << EOF
registry=https://registry.npmjs.org/
store-dir=/volume/pnpm_nemo_cache
EOF

export PNPM_CACHE=/volume/pnpm_nemo_cache
pnpm fetch --store-dir=$PNPM_CACHE
pnpm install --offline
```