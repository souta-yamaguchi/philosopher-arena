"""デスクトップ用のショートカットアイコン生成。
4哲学者の顔を 2x2 で並べ、古代羊皮紙風の金枠を付けた 256x256 の .ico を作る。"""
from PIL import Image, ImageDraw
from pathlib import Path

BASE = Path(__file__).parent
PORTRAITS = [
    BASE / "static" / "philosophers" / "socrates.jpg",
    BASE / "static" / "philosophers" / "nietzsche.jpg",
    BASE / "static" / "philosophers" / "kant.jpg",
    BASE / "static" / "philosophers" / "wittgenstein.jpg",
]
OUT = BASE / "static" / "favicon.ico"

SIZE = 256
GOLD = (167, 125, 60)
PARCHMENT = (43, 37, 29)  # ink color as background


def make_icon():
    canvas = Image.new("RGB", (SIZE, SIZE), PARCHMENT)
    half = SIZE // 2
    positions = [(0, 0), (half, 0), (0, half), (half, half)]
    for pos, path in zip(positions, PORTRAITS):
        img = Image.open(path).convert("RGB")
        img = img.resize((half, half), Image.LANCZOS)
        canvas.paste(img, pos)
    # 金色の枠・十字
    draw = ImageDraw.Draw(canvas)
    border = 6
    draw.rectangle([0, 0, SIZE - 1, SIZE - 1], outline=GOLD, width=border)
    cross = 4
    draw.line([(half, 0), (half, SIZE)], fill=GOLD, width=cross)
    draw.line([(0, half), (SIZE, half)], fill=GOLD, width=cross)
    # Windows 向けに複数サイズを含んだ .ico を出力
    canvas.save(
        OUT,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"saved: {OUT}")


if __name__ == "__main__":
    make_icon()
