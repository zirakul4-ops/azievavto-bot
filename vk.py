"""
Дублирование постов в группу ВКонтакте.

Работает, только если заданы секрет VK_TOKEN (ключ доступа сообщества с правом «Стена»)
и VK_GROUP_ID (числовой ID группы). Иначе ничего не делает.
Ошибки ВК не мешают публикации в Telegram.

post_to_vk(text, link)  — сразу опубликовать текст со ссылкой (полезные посты);
python vk.py flush      — опубликовать посты из очереди vk_queue.json с карточкой-фото
                          (ссылка на страницу машины на GitHub Pages).
"""

import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

QUEUE = Path(__file__).with_name("vk_queue.json")


VK_CTA = "🔔 Подписывайтесь на группу, чтобы первыми видеть новые авто из Китая и Кореи!"


def to_plain(text):
    """Текст для ВК: без HTML и без призыва подписаться на Telegram-канал."""
    text = html.unescape(re.sub(r"</?b>", "", text))
    lines = [l for l in text.split("\n") if not l.startswith("🔔")]
    return "\n".join(lines).rstrip()


def _creds():
    token = os.getenv("VK_TOKEN", "").strip()
    group = re.sub(r"\D", "", os.getenv("VK_GROUP_ID", ""))
    return token, group


def _wall_post(message, attachment=None):
    token, group = _creds()
    data = {"owner_id": f"-{group}", "from_group": 1, "message": message,
            "access_token": token, "v": "5.199"}
    if attachment:
        data["attachments"] = attachment
    r = requests.post("https://api.vk.com/method/wall.post", data=data, timeout=30)
    return r.json()


def post_to_vk(text, link=None):
    token, group = _creds()
    if not token or not group:
        return
    message = to_plain(text)
    if link:
        message += f"\n\n👉 Фото и подробности в Telegram: {link}"
    message += f"\n\n{VK_CTA}"
    try:
        j = _wall_post(message)
        if "error" in j:
            print(f"  ВК: ошибка {j['error'].get('error_code')}: {j['error'].get('error_msg')}")
        else:
            print(f"  ВК: опубликовано, пост {j['response'].get('post_id')}")
    except Exception as e:  # ВК не должен ломать публикацию в Telegram
        print(f"  ВК: не удалось отправить ({e})")


def wait_for_page(url, timeout=240):
    """GitHub Pages обновляется не мгновенно — ждём, пока страница откроется."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=15).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(10)
    return False


def flush():
    if not QUEUE.exists():
        print("Очередь ВК пуста")
        return
    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    token, group = _creds()
    if not token or not group:
        print("ВК не настроен — очередь очищена")
        QUEUE.write_text("[]\n", encoding="utf-8")
        return
    left = []
    for item in q:
        page, tg = item.get("page"), item.get("tg")
        message = to_plain(item["text"])
        if tg:
            message += f"\n\n👉 Все фото и подробности: {tg}"
        message += f"\n\n{VK_CTA}"
        try:
            pages_ok = os.getenv("PAGES_OK", "true").strip().lower() == "true"
            ready = wait_for_page(page) if (page and pages_ok) else False
            j = _wall_post(message, page if ready else None)
            if "error" in j and ready:
                print(f"  ВК: карточка не принята ({j['error'].get('error_msg')}), публикую без неё")
                ready = False
                j = _wall_post(message)
            if "error" in j:
                print(f"  ВК: ошибка {j['error'].get('error_code')}: {j['error'].get('error_msg')}")
                left.append(item)
            else:
                print(f"  ВК: опубликовано {'с карточкой' if ready else 'без карточки'}, пост {j['response'].get('post_id')}")
        except Exception as e:
            print(f"  ВК: не удалось отправить ({e})")
            left.append(item)
    QUEUE.write_text(json.dumps(left, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "flush":
    flush()
