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
# openshell gateway start --name nemoclaw
```

* run OpenShell

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