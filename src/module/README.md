# Telegram API


* **Start**

```bash
# install
uv pip install telethon
```

Create an application at https://my.telegram.org, then configure:

```bash
export TELEGRAM_API_ID=<youyr-app-id>
export TELEGRAM_API_HASH=<your-api-hash>
# user session will save to TELEGRAM_SESSION for next access
export TELEGRAM_SESSION=./reader

# first login
python -m src.module.telegram login --phone <your-phone-number>
```

* **Usage**

```bash
# show broadcast channel only
python -m src.module.telegram list

# > {
# >   "channels": [
# >     {
# >     ...
# >       "title": "Gooaye 股癌",
# >       "username": "Gooaye",
# >       ...
# >     }
# >   ]
# > }
# 

# read messages
# --mark-read: clear notifications
python -m src.module.telegram read "Gooaye" --limit 4 --mark-read

# > ...
# >   "messages": [
# >     {
# >       ....
# >       "date": "2026-09-30T08:06:30+00:00",
# >       "edit_date": null,
# >       "text": "EP701 | 🐡\n\n願氣氛降臨 !\n\n本集節目由【NordVPN】贊助\n\nNordVPN 限量優惠活動開跑！\n即日起至 2026/10/21，前往 https://nordvpn.com/gooaye \n並使用折扣碼gooaye，除了享有 NordVPN 獨家優惠方案，加贈 4 個月，還可最高領取 NT$1,200 7-ELEVEN 禮券 \n數量有限，送完為止！ 想拿禮券的朋友記得趕快把握這波優惠喔～\n活動詳情與贈品領取方式請以官方網站公告為準。\n\n股癌傳送門：https://linktr.ee/gooaye",
# >       ...
# >     }
# >   ]
# > }
...
```