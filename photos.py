"""
Подбор фото для поста: 3 фото кузова + 2 фото салона.

Каждое фото распознаётся открытой моделью CLIP (бесплатно, прямо на сервере GitHub).
Если модель недоступна — берутся первые 5 фото, как раньше.
"""

import io

import requests

N_EXTERIOR, N_INTERIOR, TOTAL = 3, 2, 5

LABELS = {
    "кузов": "a photo of the outside of a car showing the car body",
    "салон": "a photo of a car interior with dashboard, steering wheel or seats",
    "колесо": "a close-up photo of a car wheel and tire",
    "мотор": "a photo of a car engine bay under the open hood",
    "багажник": "a photo of an open car trunk",
    "другое": "a photo of a building, a document, a key or text",
}

_model = None


def _load():
    global _model
    if _model is None:
        from transformers import CLIPModel, CLIPProcessor
        name = "openai/clip-vit-base-patch32"
        _model = (CLIPModel.from_pretrained(name).eval(), CLIPProcessor.from_pretrained(name))
    return _model


def classify(urls, headers=None):
    """Возвращает список меток для каждого фото (или None, если распознать не удалось)."""
    import torch
    from PIL import Image
    model, proc = _load()
    images, ok = [], []
    for u in urls:
        try:
            r = requests.get(u, headers=headers or {}, timeout=30)
            r.raise_for_status()
            images.append(Image.open(io.BytesIO(r.content)).convert("RGB"))
            ok.append(True)
        except Exception:
            ok.append(False)
    if not images:
        return [None] * len(urls)
    names = list(LABELS)
    with torch.no_grad():
        inp = proc(text=[LABELS[n] for n in names], images=images, return_tensors="pt", padding=True)
        probs = model(**inp).logits_per_image.softmax(dim=1)
    labels, k = [], 0
    for good in ok:
        if good:
            labels.append(names[int(probs[k].argmax())])
            k += 1
        else:
            labels.append(None)
    return labels


def select(urls, labels):
    """3 кузова + 2 салона по порядку на сайте; если чего-то не хватает — добираем остальными."""
    ext = [i for i, l in enumerate(labels) if l == "кузов"][:N_EXTERIOR]
    inn = [i for i, l in enumerate(labels) if l == "салон"][:N_INTERIOR]
    chosen = ext + inn
    for group in ("кузов", "салон", "колесо", "багажник", "мотор", None):
        for i, l in enumerate(labels):
            if len(chosen) >= TOTAL:
                break
            if l == group and i not in chosen:
                chosen.append(i)
    return [urls[i] for i in chosen[:TOTAL]]


def pick(urls, headers=None, log=print):
    if len(urls) <= TOTAL:
        return urls
    try:
        labels = classify(urls, headers)
    except Exception as e:
        log(f"  распознавание фото недоступно ({e}) — беру первые {TOTAL}")
        return urls[:TOTAL]
    log("  фото: " + ", ".join(f"{i + 1}:{l or '?'}" for i, l in enumerate(labels)))
    return select(urls, labels)
