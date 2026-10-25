"""生成程序图标。

为什么需要这一步
    没有图标的 exe 在资源管理器里显示成默认图标，看上去不像一个正式程序。
    主用户第一次拿到的是安装包，图标是她看到的第一样东西。

做法
    先用四倍尺寸画，再缩到各档尺寸。直接在小画布上画圆形会有锯齿，
    四倍超采样是最省事又够用的抗锯齿办法。

图案
    深蓝圆角方块加一个白色放大镜。放大镜是「找图」这件事最直白的符号，
    而且在 16×16 下仍然能认出来。镜片里放一个极简的山与太阳，
    暗示里面是一张图。

用法
    python work/make_icon.py
    写入 assets/picfinder.ico 与 assets/picfinder.png

这不是美术设计，只是占位。开发者若有自己的图标，替换这两个文件即可，
打包脚本不关心图标内容，只关心路径。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT / "assets"

# 超采样倍数。四倍足够，再高只是浪费内存。
SCALE = 4
SIZE = 256
CANVAS = SIZE * SCALE

BACKGROUND = (31, 78, 121, 255)
GLASS = (255, 255, 255, 255)
GLASS_FILL = (222, 235, 247, 255)
HILL = (31, 78, 121, 255)
SUN = (240, 173, 78, 255)


def rounded_square(draw: ImageDraw.ImageDraw) -> None:
    margin = int(CANVAS * 0.04)
    draw.rounded_rectangle(
        (margin, margin, CANVAS - margin, CANVAS - margin),
        radius=int(CANVAS * 0.22),
        fill=BACKGROUND,
    )


def magnifier(draw: ImageDraw.ImageDraw) -> None:
    """放大镜：一个圆环加一根斜把手，镜片里画山与太阳。"""
    # 镜片。位置略偏左上，把手才有地方伸出去。
    center = (int(CANVAS * 0.44), int(CANVAS * 0.42))
    radius = int(CANVAS * 0.26)
    stroke = int(CANVAS * 0.055)

    left = center[0] - radius
    top = center[1] - radius
    right = center[0] + radius
    bottom = center[1] + radius

    # 先画镜片内的图，再画圆环，这样圆环压住图的边缘，接缝干净。
    inner = (left + stroke * 0.6, top + stroke * 0.6, right - stroke * 0.6, bottom - stroke * 0.6)
    draw.ellipse(inner, fill=GLASS_FILL)

    # 山：两个三角形，底边贴着镜片下缘。
    base_y = inner[3]
    draw.polygon(
        [
            (inner[0], base_y),
            ((inner[0] + inner[2]) / 2 - radius * 0.15, inner[1] + (base_y - inner[1]) * 0.42),
            (inner[2], base_y),
        ],
        fill=HILL,
    )
    # 太阳。
    sun_r = radius * 0.16
    sun_c = (inner[2] - radius * 0.42, inner[1] + radius * 0.36)
    draw.ellipse(
        (sun_c[0] - sun_r, sun_c[1] - sun_r, sun_c[0] + sun_r, sun_c[1] + sun_r),
        fill=SUN,
    )

    # 圆环。先画粗一点的白环，再在内部挖出镜片区域，形成描边效果。
    draw.ellipse((left, top, right, bottom), outline=GLASS, width=stroke)

    # 把手。从镜片右下方斜着伸出去。
    handle_start = (
        center[0] + radius * 0.72,
        center[1] + radius * 0.72,
    )
    handle_end = (
        center[0] + radius * 1.62,
        center[1] + radius * 1.62,
    )
    draw.line((handle_start, handle_end), fill=GLASS, width=int(stroke * 1.25))
    # 圆头收尾，否则斜线的端点是方的，看着很硬。
    half = stroke * 0.62
    draw.ellipse(
        (handle_end[0] - half, handle_end[1] - half, handle_end[0] + half, handle_end[1] + half),
        fill=GLASS,
    )


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rounded_square(draw)
    magnifier(draw)

    master = image.resize((SIZE, SIZE), Image.LANCZOS)
    png_path = ASSETS / "picfinder.png"
    master.save(png_path, "PNG")

    # Windows 会在不同位置用不同尺寸，多存几档比让系统缩放清楚。
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_path = ASSETS / "picfinder.ico"
    master.save(ico_path, format="ICO", sizes=sizes)

    print(f"png {png_path} {png_path.stat().st_size} bytes")
    print(f"ico {ico_path} {ico_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
