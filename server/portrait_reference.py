"""Extract one front-view reference from the shared four-panel character sheet."""
from PIL import Image


def crop_portrait_reference(sheet, output):
    # The template allocates 1/3 to the face, then 2/9 each to front/side/back.
    # Prefer visible panel gaps; use that layout when white gaps are ambiguous.
    with Image.open(sheet) as source:
        image = source.convert("RGB")
    width, height = image.size
    if width < 9 or height < 9:
        raise ValueError("人物设定图尺寸过小，无法提取单人参考图")
    small = image.convert("L").resize((width, max(1, height // 4)), Image.Resampling.BOX)
    pixels = small.load()
    dark = [sum(pixels[x, y] < 180 for y in range(small.height)) for x in range(width)]
    threshold = max(2, max(dark, default=0) * .10)
    gap = max(6, round(width * .006))
    segments, start, last = [], None, None
    for x, amount in enumerate(dark):
        if amount >= threshold:
            if start is None:
                start = x
            last = x
        elif start is not None and x - last >= gap:
            if last - start + 1 >= width * .04:
                segments.append((start, last + 1))
            start = last = None
    if start is not None and last - start + 1 >= width * .04:
        segments.append((start, last + 1))
    left, right = round(width / 3), round(width * 5 / 9)
    if (len(segments) == 4 and segments[0][1] < width * .45
            and width * .28 <= sum(segments[1]) / 2 <= width * .65):
        # Include surrounding background without crossing into adjacent views.
        left = (segments[0][1] + segments[1][0]) // 2
        right = (segments[1][1] + segments[2][0]) // 2
    image.crop((left, 0, right, height)).save(output, format="PNG")
