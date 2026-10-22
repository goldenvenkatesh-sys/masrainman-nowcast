"""Rebuild masks/mlr_maxz_background.png -- the echo-free terrain/sea
background used by nowcast_bot.decode_reflectivity for the Mangaluru MAXZ
product (see PRODUCTS["mlr_maxz"]["background_image"]).

Usage:  python tools/build_mlr_background.py FRAME_DIR [more dirs...]

Takes the per-pixel median colour over every caz_mlr GIF found (use 100+
frames spread over a few days so passing rain never dominates a pixel).
Frames of the wrong size are skipped. Run again only if IMD changes the
Mangaluru basemap (the bot then logs "doesn't match its known background"
every cycle).
"""
import glob
import os
import sys

import numpy as np
from PIL import Image

PLOT_BBOX = (0, 200, 879, 1080)
OUT = os.path.join(os.path.dirname(__file__), "..", "masks", "mlr_maxz_background.png")


def main(dirs):
    files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, "*mlr*.gif")))
    frames = []
    for f in files:
        try:
            im = Image.open(f)
            if im.size != (1310, 1080):
                continue
            frames.append(np.array(im.convert("RGB").crop(PLOT_BBOX), dtype=np.uint8))
        except OSError:
            continue
    if len(frames) < 30:
        sys.exit(f"only {len(frames)} usable frames - need at least 30")
    stack = np.stack(frames)
    med = np.empty(stack.shape[1:], np.uint8)
    for r0 in range(0, med.shape[0], 40):  # row chunks keep memory low
        med[r0:r0 + 40] = np.median(stack[:, r0:r0 + 40], axis=0).astype(np.uint8)
    Image.fromarray(med).save(OUT)
    print(f"wrote {OUT} from {len(frames)} frames")


if __name__ == "__main__":
    main(sys.argv[1:] or ["archive"])
