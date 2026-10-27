"""
Дублирование постов в группу ВКонтакте: текст + ссылка на пост в Telegram
(ВК показывает под ссылкой карточку с фото).

Работает, только если заданы секрет VK_TOKEN (ключ доступа сообщества с правом «Стена»)
и переменная VK_GROUP_ID (числовой ID группы). Иначе ничего не делает.
Ошибки ВК не мешают публикации в Telegram.
"""

import html
import os
import re

import requests


def to_plain(text):
    return html.unescape(re.sub(r"</?b>", "", text))


def post_to_vk(text, link=None):
    token = os.getenv("VK_TOKEN", "").strip()
    group = re.sub(r"\D", "", os.getenv("VK_GROUP_ID", ""))
    if not token or not group:
        return
    message = to_plain(text)
    if link:
        message += f"\n\n👉 Фото и подробности в Telegram: {link}"
    data = {"owner_id": f"-{group}", "from_group": 1, "message": message,
            "access_token": token, "v": "5.199"}
    if link:
        data["attachments"] = link
    try:
        r = requests.post("https://api.vk.com/method/wall.post", data=data, timeout=30)
        j = r.json()
        if "error" in j:
            print(f"  ВК: ошибка {j['error'].get('error_code')}: {j['error'].get('error_msg')}")
        else:
            print(f"  ВК: опубликовано, пост {j['response'].get('post_id')}")
    except Exception as e:  # ВК не должен ломать публикацию в Telegram
        print(f"  ВК: не удалось отправить ({e})")
