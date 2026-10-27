"""
Бот канала «Азиев Авто»: берёт новый автомобиль из каталога avexavto.ru
и публикует его в Telegram-канал (альбом фото + текст с ценой под ключ).

Один запуск = одна публикация. За день — 5 машин разных марок. Расписание (5 раз в день) задаётся
в .github/workflows/post.yml.

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather (обязательно)
  TELEGRAM_CHANNEL    — канал, например @azievavto (обязательно)
  DRY_RUN=1           — ничего не публиковать, только показать пост в логе
  MAX_PRICE, MIN_PRICE — фильтр по цене до Уфы в рублях (необязательно)
  MIN_YEAR            — минимальный год выпуска (необязательно)
  BRANDS              — список марок через запятую, напр. "BMW,Audi" (необязательно)
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from carpage import queue_for_vk
from photos import pick as pick_photos

SITE = "https://avexavto.ru"
CATALOG_URL = SITE + "/catalog"
MAX_CATALOG_PAGES = 10
MAX_PHOTOS = 5  # не более 5 фото в посте

PHONE = "+7 987 253 63 93"
WEBSITE = "azievavto.ru"

STATE_FILE = Path(__file__).with_name("posted.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AzievAvtoChannelBot/1.0)",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

NUM = r"([\d    ]+)"


def log(*args):
    print(*args, flush=True)


def to_int(s):
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def fmt_rub(n):
    return f"{n:,}".replace(",", " ") + " ₽"


def fmt_km(n):
    return f"{n:,}".replace(",", " ") + " км"


def get(url, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log(f"  ошибка загрузки {url}: {e}")
            time.sleep(3 * (attempt + 1))
    return None


# ---------- учёт опубликованного ----------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"posted": []}


def ufa_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%d")


def brand_of(name):
    return (name or "").split()[0].lower() if name else ""


def slug_brand_used(url, brands):
    """Быстрая проверка по адресу карточки, чтобы не загружать лишние страницы."""
    slug = url.rstrip("/").split("/")[-1].lower()
    return any(slug.startswith(b.replace(" ", "-") + "-") for b in brands)


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------- каталог ----------

LOT_RE = re.compile(r"-(\d{6,10})-[a-z0-9]+/?$", re.I)


def catalog_links(page):
    url = CATALOG_URL if page == 1 else f"{CATALOG_URL}?page={page}"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/cars/" not in href:
            continue
        m = LOT_RE.search(href.split("?")[0])
        if not m:
            continue
        lot = m.group(1)
        if lot in seen:
            continue
        seen.add(lot)
        links.append((lot, urljoin(SITE, href)))
    return links


# ---------- карточка автомобиля ----------

def find(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_car(page_html, url):
    soup = BeautifulSoup(page_html, "html.parser")
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    car = {"url": url}

    h1 = soup.find("h1")
    car["name"] = h1.get_text(" ", strip=True) if h1 else None

    # строка комплектации идёт сразу после заголовка: «2021 280TSI DSG 2WD Connected»
    trim = None
    if h1:
        for s in h1.find_all_next(string=True):
            if s.find_parent("h1") is h1:
                continue
            s = s.strip()
            if s:
                # ожидаем строку вида «2021 ...»; иначе комплектацию не указываем
                trim = s if re.match(r"^(19|20)\d{2}\b", s) and len(s) < 80 else None
                break
    car["trim"] = trim

    car["country"] = find(r"(Китай|Корея)\s*·\s*Лот", text)
    car["lot"] = find(r"Лот\s*(\d+)", text)
    car["year"] = to_int(find(r"Год(?: выпуска)?\s*\n?\s*(\d{4})", text))
    car["mileage"] = to_int(find(r"Пробег\s*\n?\s*" + NUM + r"\s*км", text))

    eng = re.search(r"Двигатель\s*\n?\s*([\d.,]+)\s*л,\s*(\d+)\s*л\.с", text)
    if eng:
        car["volume"] = float(eng.group(1).replace(",", "."))
        car["hp"] = int(eng.group(2))
    else:
        car["volume"] = None
        car["hp"] = to_int(find(r"Мощность\s*\n?\s*(\d+)\s*л\.с", text))

    car["drive"] = find(r"Привод\s*\n?\s*(2WD|4WD|AWD)", text)
    car["gearbox"] = find(r"КПП\s*\n?\s*([а-яёa-z]+)", text)
    car["fuel"] = find(r"Топливо\s*\n?\s*([а-яёa-z]+)", text)

    car["total"] = to_int(find(r"Итого до Уфы\s*\n?\s*" + NUM + r"₽", text))
    if not car["total"]:
        car["total"] = to_int(find(NUM + r"₽\s*\n?\s*до Уфы", text))
    car["rate_date"] = find(r"Курсы на\s*(\d{2}\.\d{2}\.\d{4})", text)

    def money(label):
        return to_int(find(re.escape(label) + r"\s*\n?\s*" + NUM + r"₽", text))

    car["breakdown"] = {
        "car": money("Авто в рублях"),
        "customs": money("Таможня"),
        "util": money("Утильсбор"),
        "china": money("Расходы по Китаю"),
        "logistics": money("Логистика"),
        "bank": money("Банк"),
        "russia": money("Расходы по РФ"),
        "commission": money("Комиссия"),
    }

    car["photos"] = extract_photos(soup)
    return car


def extract_photos(soup):
    """Ссылки на фото из карточки, без повторов, по возможности в лучшем размере."""
    photos, seen = [], set()
    for img in soup.find_all("img"):
        if img.find_parent("a", href=re.compile(r"/cars/")):
            continue  # превью «похожих предложений» — это другие машины
        src = img.get("src") or img.get("data-src") or ""
        best = src
        srcset = img.get("srcset") or ""
        if srcset:
            cands = []
            for part in srcset.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                w = to_int(bits[1]) if len(bits) > 1 else 0
                cands.append((w or 0, bits[0]))
            if cands:
                best = max(cands)[1]
        if "/media/" not in best and "/media/" not in src:
            continue
        m = re.search(r"/media/([^/?]+)", best) or re.search(r"/media/([^/?]+)", src)
        mid = m.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        photos.append(urljoin(SITE, best))
    return photos[:20]  # вся галерея; 5 лучших выбирает photos.pick


def download_photo(url):
    """Пытаемся взять крупную версию (/960), если сервер её не отдаёт — исходную."""
    candidates = []
    big = re.sub(r"/(\d{3,4})(\?.*)?$", "/960", url)
    if big != url:
        candidates.append(big)
    candidates.append(url)
    for u in candidates:
        try:
            r = requests.get(u, headers=HEADERS, timeout=30)
        except requests.RequestException:
            continue
        ctype = r.headers.get("Content-Type", "")
        if r.ok and ctype.startswith("image/") and len(r.content) > 5000:
            return r.content, ctype
    return None, None


# ---------- текст поста ----------

GEARBOX = {"автомат": "автомат", "механика": "механика", "вариатор": "вариатор", "робот": "робот"}
DRIVE = {"2WD": "привод 2WD", "4WD": "полный привод 4WD", "AWD": "полный привод AWD"}
FLAG = {"Китай": "🇨🇳", "Корея": "🇰🇷"}


def build_caption(car):
    e = html.escape
    title = car["name"] or "Автомобиль"
    trim = car.get("trim") or ""
    year = car.get("year")
    # из «2021 280TSI DSG 2WD Connected» убираем год, он и так в заголовке
    if year and trim.startswith(str(year)):
        trim = trim[len(str(year)):].strip()
    head = f"{title} {trim[len(title.split()[-1]):].strip() if trim.split()[:1] == title.split()[-1:] else trim}".strip()
    if year:
        head += f", {year}"

    lines = [f"{FLAG.get(car.get('country'), '🚗')} <b>{e(head)}</b>", ""]
    lines.append(f"💰 <b>Цена под ключ в Уфе: {fmt_rub(car['total'])}</b>")
    lines.append("")

    km = car.get("mileage")
    if km is not None:
        lines.append("📍 Пробег: новый автомобиль" if km < 100 else f"📍 Пробег: {fmt_km(km)}")

    eng = []
    if car.get("volume"):
        eng.append(f"{car['volume']:.1f} л")
    if car.get("hp"):
        eng.append(f"{car['hp']} л.с.")
    if car.get("fuel"):
        eng.append(car["fuel"])
    if eng:
        lines.append("⚙️ Двигатель: " + ", ".join(eng))

    trans = []
    if car.get("gearbox"):
        trans.append(GEARBOX.get(car["gearbox"].lower(), car["gearbox"]))
    if car.get("drive"):
        trans.append(DRIVE.get(car["drive"], car["drive"]))
    if trans:
        lines.append("🔄 Коробка: " + ", ".join(trans))

    b = car["breakdown"]
    if b.get("car") and b.get("customs") is not None:
        rest = sum(v or 0 for k, v in b.items() if k in ("china", "logistics", "bank", "russia", "commission"))
        lines += [
            "",
            "Из чего складывается цена:",
            f"• автомобиль в {'Китае' if car.get('country') != 'Корея' else 'Корее'} — {fmt_rub(b['car'])}",
            f"• таможня — {fmt_rub(b['customs'])}",
        ]
        if b.get("util") is not None:
            lines.append(f"• утильсбор — {fmt_rub(b['util'])}")
        if rest:
            lines.append(f"• доставка, расходы в {'Китае' if car.get('country') != 'Корея' else 'Корее'} и РФ, банк и комиссия — {fmt_rub(rest)}")

    lines += ["", "✅ Подбор, проверка, доставка, растаможка и оформление — всё берём на себя"]
    if car.get("rate_date"):
        lines.append(f"📅 Расчёт по курсу ЦБ на {car['rate_date']}")
    lines += ["", f"📞 Расчёт и бронь: {PHONE}, Булат", f"🌐 <b>{WEBSITE.upper()}</b>",
              "", "🔔 Подписывайтесь на канал, чтобы первыми видеть новые авто: @azievavto"]
    return "\n".join(lines)


# ---------- фильтры ----------

def passes_filters(car):
    def env_int(name):
        v = os.getenv(name, "").strip()
        return int(re.sub(r"\D", "", v)) if re.search(r"\d", v) else None

    max_p, min_p, min_y = env_int("MAX_PRICE"), env_int("MIN_PRICE"), env_int("MIN_YEAR")
    max_km = env_int("MAX_MILEAGE")
    brands = [x.strip().lower() for x in os.getenv("BRANDS", "").split(",") if x.strip()]
    if max_p and car["total"] > max_p:
        return False
    if min_p and car["total"] < min_p:
        return False
    if min_y and (car.get("year") or 0) < min_y:
        return False
    if max_km and (car.get("mileage") is None or car["mileage"] > max_km):
        return False
    if brands and not any((car.get("name") or "").lower().startswith(br) for br in brands):
        return False
    return True


def is_complete(car):
    return bool(car.get("name") and car.get("total") and car.get("photos"))


# ---------- Telegram ----------

def send_to_telegram(car, caption):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", os.environ["TELEGRAM_CHANNEL"].strip()).strip("/ ")
    if not chat.startswith("@") and not re.fullmatch(r"-?\d+", chat): chat = "@" + chat
    api = f"https://api.telegram.org/bot{token}"

    files, media = {}, []
    for i, url in enumerate(car["photos"]):
        content, ctype = download_photo(url)
        if not content:
            continue
        key = f"p{i}"
        files[key] = (f"{key}.jpg", content, ctype)
        item = {"type": "photo", "media": f"attach://{key}"}
        if not media:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)

    if len(media) >= 2:
        r = requests.post(f"{api}/sendMediaGroup",
                          data={"chat_id": chat, "media": json.dumps(media)},
                          files=files, timeout=120)
    elif len(media) == 1:
        key = next(iter(files))
        r = requests.post(f"{api}/sendPhoto",
                          data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                          files={"photo": files[key]}, timeout=120)
    else:
        log("  не удалось скачать ни одного фото — пропускаю автомобиль")
        return False

    if not r.ok or not r.json().get("ok"):
        log(f"  Telegram ответил ошибкой: {r.status_code} {r.text[:500]}")
        if r.status_code in (401, 403) or "chat not found" in r.text:
            # ошибка настройки, а не машины — продолжать бессмысленно
            sys.exit("Проверьте TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL и что бот — администратор канала.")
        return False
    res = r.json()["result"]
    msg_id = (res[0] if isinstance(res, list) else res)["message_id"]
    link = f"https://t.me/{chat[1:]}/{msg_id}" if chat.startswith("@") else None
    queue_for_vk(car, caption, link)
    return True


# ---------- основной цикл ----------

def main():
    dry = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    if not dry:
        for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"):
            if not os.getenv(v, "").strip():
                sys.exit(f"Не задан секрет {v}")

    state = load_state()
    posted = set(state["posted"])
    today = ufa_today()
    if state.get("today", {}).get("date") != today:
        state["today"] = {"date": today, "brands": []}
    used_brands = state["today"]["brands"]
    log(f"Сегодня ({today}) уже были марки: {', '.join(used_brands) or 'нет'}")
    tried = 0

    for page in range(1, MAX_CATALOG_PAGES + 1):
        links = catalog_links(page)
        log(f"Страница каталога {page}: найдено {len(links)} авто")
        if not links:
            break
        for lot, url in links:
            if lot in posted or slug_brand_used(url, used_brands):
                continue
            tried += 1
            if tried > 40:
                save_state(state)
                sys.exit("Не удалось подобрать подходящий автомобиль за 40 попыток.")
            log(f"Проверяю лот {lot}: {url}")
            r = get(url)
            if not r:
                continue
            car = parse_car(r.text, url)
            if not is_complete(car):
                log(f"  неполные данные (название/цена/фото) — пропускаю: "
                    f"{car.get('name')}, {car.get('total')}, фото {len(car.get('photos', []))}")
                continue
            if brand_of(car["name"]) in used_brands:
                log("  марка уже была сегодня — пропускаю")
                continue
            if not passes_filters(car):
                log("  не подходит под фильтры — пропускаю")
                posted.add(lot)  # чтобы не проверять повторно
                state["posted"] = sorted(posted)
                continue

            car["photos"] = pick_photos(car["photos"], HEADERS, log)
            caption = build_caption(car)
            log("\n" + "=" * 40 + "\n" + caption + "\n" + "=" * 40)
            log(f"Фото: {len(car['photos'])}")

            if dry:
                log("DRY_RUN: в канал не отправлено.")
                return
            if send_to_telegram(car, caption):
                posted.add(lot)
                state["posted"] = sorted(posted)
                used_brands.append(brand_of(car["name"]))
                save_state(state)
                log(f"Опубликовано: {car['name']} (лот {lot})")
                return
    save_state(state)
    sys.exit("Новых автомобилей для публикации не найдено.")


if __name__ == "__main__":
    main()
