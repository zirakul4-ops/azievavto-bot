"""
Страничка автомобиля для ВКонтакте (GitHub Pages).

ВК не даёт загружать фото на стену через API, но строит под постом карточку
с крупной картинкой по ссылке, если у страницы есть og:image от 537 px в ширину.
Бот делает такую страницу для каждой машины (docs/cars/<лот>.html) и ставит
пост в очередь vk_queue.json. Отдельный шаг workflow публикует сайт и затем
отправляет пост в ВК (vk.py flush).
"""

import html
import json
import re
from pathlib import Path

PAGES_BASE = "https://zirakul4-ops.github.io/azievavto-bot"
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
QUEUE = ROOT / "vk_queue.json"
PHONE = "+7 987 253 63 93"


def big_photo(url):
    # сайт партнёров отдаёт размеры 480 и 960 (фактически 720×540)
    return re.sub(r"/(\d{3,4})(\?.*)?$", "/960", url)


def plain(text):
    return html.unescape(re.sub(r"</?b>", "", text))


def make_page(car, caption, tg_link):
    lot = car.get("lot") or re.sub(r"\D", "", car.get("url", ""))[-8:]
    photos = [big_photo(u) for u in car.get("photos", [])]
    lines = plain(caption).split("\n")
    title = lines[0].lstrip("🇨🇳🇰🇷🚗 ").strip()
    price = f"{car['total']:,}".replace(",", " ") + " ₽"
    og_title = f"{title} — {price} под ключ в Уфе"
    desc = " · ".join(l.strip() for l in lines[1:8] if l.strip() and "Цена" not in l)[:250]
    e = html.escape
    body = "\n".join(f"<p>{e(l)}</p>" if l.strip() else "" for l in lines[1:])
    imgs = "\n".join(f'<img src="{e(u)}" alt="{e(title)}" loading="lazy">' for u in photos)
    url = f"{PAGES_BASE}/cars/{lot}.html"
    page = f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(og_title)}</title>
<meta property="og:type" content="website">
<meta property="og:site_name" content="Азиев Авто">
<meta property="og:title" content="{e(og_title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
{f'<meta property="og:image" content="{e(photos[0])}">' if photos else ''}
<meta property="og:image:width" content="720"><meta property="og:image:height" content="540">
<style>
body{{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#111317;color:#f5f6f8}}
main{{max-width:760px;margin:0 auto;padding:16px}}
h1{{font-size:24px;margin:8px 0}} .price{{font-size:28px;font-weight:800;color:#f5b82e;margin:4px 0 12px}}
img{{width:100%;border-radius:12px;margin:6px 0;display:block}}
p{{margin:2px 0}} .btn{{display:block;text-align:center;background:#f5b82e;color:#111317;font-weight:700;
padding:14px;border-radius:12px;text-decoration:none;margin:16px 0}}
.site{{font-weight:800;letter-spacing:.5px}}
</style></head><body><main>
<h1>{e(title)}</h1>
<div class="price">{e(price)} под ключ в Уфе</div>
{f'<a class="btn" href="{e(tg_link)}">Смотреть в Telegram</a>' if tg_link else ''}
{imgs}
{body}
<a class="btn" href="tel:{PHONE.replace(' ', '')}">Позвонить: {PHONE} (Булат)</a>
<p class="site">AZIEVAVTO.RU · Азиев Авто · Уфа</p>
</main></body></html>
"""
    path = DOCS / "cars" / f"{lot}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    index = DOCS / "index.html"
    if not index.exists():
        index.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" '
                         'content="0;url=https://t.me/azievavto"><title>Азиев Авто</title>', encoding="utf-8")
    return url


def queue_for_vk(car, caption, tg_link):
    """Создаёт страницу и ставит пост в очередь для ВК."""
    page_url = make_page(car, caption, tg_link)
    q = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []
    q.append({"text": caption, "page": page_url, "tg": tg_link})
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ВК: пост поставлен в очередь, страница {page_url}")
