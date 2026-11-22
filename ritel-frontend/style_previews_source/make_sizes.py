"""
Make the frontend's style preview images from the originals here.

    py -3 ritel-frontend/style_previews_source/make_sizes.py

originals/<style>.png  ->  ../style_previews/thumb/<style>.jpg  (the chooser tiles)
                       ->  ../style_previews/large/<style>.jpg  (the bigger picture shown on hover)
Only style_previews/ ships in the Docker image; this folder (originals, prompts) stays in git.
Change SIZES and re-run to make other sizes.
"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "style_previews")
SIZES = {"thumb": 160, "large": 480}

for name in sorted(os.listdir(os.path.join(HERE, "originals"))):
    style, ext = os.path.splitext(name)
    if ext.lower() != ".png":
        continue
    im = Image.open(os.path.join(HERE, "originals", name)).convert("RGB")
    for folder, px in SIZES.items():
        os.makedirs(os.path.join(OUT, folder), exist_ok=True)
        small = im.copy()
        small.thumbnail((px, px), Image.LANCZOS)
        small.save(os.path.join(OUT, folder, f"{style}.jpg"), quality=88, optimize=True)
    print(style)
