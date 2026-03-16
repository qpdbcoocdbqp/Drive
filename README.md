# Drive

Inspect Claude skils. Playing with [Drive](https://www.youtube.com/watch?v=fgT9zGkiLig).

* **About Drive**

> Drive·Incubus
>
> Make Yourself

## Reference

* [anthropics/skills](https://github.com/anthropics/skills)

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