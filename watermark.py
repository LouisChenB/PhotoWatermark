#!/usr/bin/env python3
"""

功能：
- 输入图片文件或目录路径（若为目录则处理目录下所有匹配扩展名的图片）。
- 从每张图片的 EXIF 中读取拍摄时间（优先 DateTimeOriginal），取年月日作为水印文本（YYYY-MM-DD）。
- 用户可通过命令行设置：字体大小、颜色、位置（top-left/top-right/bottom-left/bottom-right/center）、边距、是否使用文件修改时间作回退。
- **不需要用户指定字体**：脚本会自动在常见系统路径寻找可用的 TrueType 字体 (.ttf)。若找不到则退回到 PIL 的内置默认字体（注意：内置字体大小不可变）。
- 处理后的图片保存在输入目录下的 `_watermark` 子目录中，保留原始文件名与格式。

用法示例：
  python watermarker.py /path/to/images_dir --size 36 --color "#FFFFFF" --position bottom-right
  python watermarker.py /path/to/image.jpg --fallback mtime --position top-left --size 28

参数：
  path：图片文件或目录路径（必填）
  --size：字体大小，默认 36（若使用内置字体，大小可能无效）
  --color：文本颜色，支持 #RRGGBB 或颜色名，默认白色
  --position：位置：top-left, top-right, bottom-left, bottom-right, center（默认 bottom-right）
  --fallback：EXIF 不存在时的回退选项：none 或 mtime（默认 none）
  --margin：距离边缘的像素（默认 12）
  --exts：处理的文件扩展名，逗号分隔（默认 jpg,jpeg,png）

注意：
- 如果想要更可靠的字体大小控制，请在系统中安装至少一个 TrueType 字体（如 DejaVuSans 或 Arial），脚本会自动使用它们。

"""

import os
import sys
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageColor, ExifTags
import datetime

# 常见系统字体路径（脚本会自动尝试）
COMMON_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",  # macOS
    "C:\\Windows\\Fonts\\arial.ttf",  # Windows
]

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.bmp'}


def find_system_font():
    for p in COMMON_FONT_PATHS:
        if os.path.isfile(p):
            return p
    return None


def extract_exif_date(img):
    """从 PIL Image 对象中尽可能提取拍摄时间，返回 'YYYY-MM-DD' 或 None"""
    try:
        exif = img._getexif()
    except Exception as e:
        print(e)
        exif = None
    if not exif:
        return None
    tag_map = {v: k for k, v in ExifTags.TAGS.items()}
    date_tags = ['DateTimeOriginal', 'DateTime', 'DateTimeDigitized']
    for tag_name in date_tags:
        tag_id = tag_map.get(tag_name)
        if tag_id is None:
            continue
        raw = exif.get(tag_id)
        if not raw:
            continue
        try:
            date_part = raw.split()[0]
            date_formatted = date_part.replace(':', '-', 2)
            parts = date_formatted.split('-')
            if len(parts) >= 3:
                return "-".join(parts[:3])
        except Exception as e:
            print(e)
            continue
    return None


def format_mtime(filepath):
    t = os.path.getmtime(filepath)
    dt = datetime.datetime.fromtimestamp(t)
    return dt.strftime("%Y-%m-%d")


def list_image_files(path, exts):
    if os.path.isfile(path):
        return [path]
    files = []
    for root, _, filenames in os.walk(path):
        for entry in filenames:
            ext = os.path.splitext(entry)[1].lower()
            if ext in exts:
                files.append(os.path.join(root, entry))
    files.sort()
    return files


def parse_color(color_str):
    try:
        return ImageColor.getrgb(color_str)
    except Exception as e:
        print(e)
        if color_str.startswith('#'):
            color_str = color_str[1:]
        try:
            if len(color_str) == 6:
                r = int(color_str[0:2], 16)
                g = int(color_str[2:4], 16)
                b = int(color_str[4:6], 16)
                return r, g, b
        except Exception as e:
            print(e)
            pass
    raise ValueError(f"不能解析的颜色: {color_str}")


def position_coords(img_w, img_h, text_w, text_h, position, margin):
    if position == 'top-left':
        x = margin
        y = margin
    elif position == 'top-right':
        x = img_w - text_w - margin
        y = margin
    elif position == 'bottom-left':
        x = margin
        y = img_h - text_h - margin
    elif position == 'bottom-right':
        x = img_w - text_w - margin
        y = img_h - text_h - margin
    elif position == 'center':
        x = (img_w - text_w) // 2
        y = (img_h - text_h) // 2
    else:
        x = img_w - text_w - margin
        y = img_h - text_h - margin
    x = max(0, int(x))
    y = max(0, int(y))
    return x, y


def draw_watermark(image_path, out_path, text, font_path, size, color, position, margin):
    try:
        with Image.open(image_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != 'RGBA':
                im = im.convert('RGBA')

            # 尝试加载系统字体（用户无需提供），找不到则使用内置默认字体
            font = None
            if font_path and os.path.isfile(font_path):
                try:
                    font = ImageFont.truetype(font_path, size=size)
                except Exception as e:
                    print(e)
                    font = None
            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception as e:
                    print(e)
                    font = None

            # 如果使用内置默认字体，textsize 的结果可能与期望大小不同
            left, top, right, bottom = font.getbbox(text)
            text_w, text_h = right - left, bottom - top

            stroke_w = max(1, max(1, size // 20))
            text_w += stroke_w * 2
            text_h += stroke_w * 2

            x, y = position_coords(im.width, im.height, text_w, text_h, position, margin)

            try:
                r, g, b = color
                brightness = (r * 299 + g * 587 + b * 114) / 1000
                stroke_fill = (0, 0, 0) if brightness > 160 else (255, 255, 255)
            except Exception as e:
                print(e)
                stroke_fill = (0, 0, 0)

            text_layer = Image.new('RGBA', im.size, (255, 255, 255, 0))
            text_draw = ImageDraw.Draw(text_layer)
            pos = (x + stroke_w, y + stroke_w)

            # 若 font 为内置字体且不支持所需大小，PIL 会忽略 size 参数；这是设计上的妥协
            text_draw.text(pos, text, font=font, fill=color + (255,), stroke_width=stroke_w, stroke_fill=stroke_fill + (255,))

            im = Image.alpha_composite(im, text_layer)

            ext = os.path.splitext(image_path)[1].lower()
            save_kwargs = {}
            if ext in ('.jpg', '.jpeg'):
                background = Image.new('RGB', im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[3])
                im_out = background
                save_format = 'JPEG'
                save_kwargs['quality'] = 95
            else:
                im_out = im
                if ext == '.png':
                    save_format = 'PNG'
                elif ext == '.webp':
                    save_format = 'WEBP'
                elif ext in ('.tif', '.tiff'):
                    save_format = 'TIFF'
                else:
                    save_format = None

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if save_format:
                im_out.save(out_path, save_format, **save_kwargs)
            else:
                im_out.save(out_path)
            print(f"已保存：{out_path}")
            return True, None
    except Exception as e:
        print(e)
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="给图片添加基于 EXIF 拍摄日期的文本水印（无需指定字体），并保存到目录 _watermark")
    parser.add_argument("path", help="图片文件或目录路径")
    parser.add_argument("--size", type=int, default=36, help="字体大小，默认 36（若使用内置字体，大小可能无效）")
    parser.add_argument("--color", type=str, default="#FFFFFF", help="文本颜色，支持 #RRGGBB 或颜色名（默认白色）")
    parser.add_argument("--position", type=str, default="bottom-right",
                        choices=['top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'],
                        help="文本位置")
    parser.add_argument("--fallback", type=str, choices=['none', 'mtime'], default='none',
                        help="EXIF 不存在时是否回退为文件修改时间（mtime）")
    parser.add_argument("--margin", type=int, default=12, help="距离边缘的像素（默认 12）")
    parser.add_argument("--exts", type=str, default="jpg,jpeg,png", help="处理的文件扩展名，逗号分隔")
    args = parser.parse_args()

    src_path = args.path
    if not os.path.exists(src_path):
        print("路径不存在：", src_path)
        sys.exit(1)

    exts = {('.' + e.strip().lstrip('.')).lower() for e in args.exts.split(',') if e.strip()}
    if not exts:
        exts = {'.jpg', '.jpeg', '.png'}

    if os.path.isfile(src_path):
        files = [src_path]
        base_dir = os.path.dirname(src_path) or '.'
    else:
        base_dir = src_path
        files = list_image_files(src_path, exts)

    if not files:
        print("未找到要处理的图片文件。")
        sys.exit(0)

    # 自动寻找系统字体（用户无需设置），若没有找到则使用内置默认字体
    font_path = find_system_font()
    print("使用字体：", font_path if font_path else "内置默认字体（无法保证大小）")
    print(f"处理 {len(files)} 个文件...")

    base = Path(base_dir).resolve()  # 规范化并得到绝对路径
    dir_name = base.name or "root"
    print(dir_name)
    out_base_dir = base / f"{dir_name}_watermark"
    os.makedirs(out_base_dir, exist_ok=True)

    try:
        color = parse_color(args.color)
    except Exception as e:
        print("颜色解析失败：", e)
        sys.exit(1)

    processed = 0
    skipped = 0
    errors = 0

    for fp in files:
        try:
            print("处理：", fp)
            with Image.open(fp) as im_test:
                date_str = extract_exif_date(im_test)
            if not date_str and args.fallback == 'mtime':
                date_str = format_mtime(fp)

            if not date_str:
                print("  跳过（未找到 EXIF 日期，且未启用回退）")
                skipped += 1
                continue

            text = date_str

            rel = os.path.relpath(fp, start=base_dir)
            out_path = os.path.join(out_base_dir, rel)
            out_dir = os.path.dirname(out_path)
            os.makedirs(out_dir, exist_ok=True)

            ok, err = draw_watermark(fp, out_path, text, font_path, args.size, color, args.position, args.margin)
            if ok:
                processed += 1
            else:
                errors += 1
                print(f"  处理出错: {err}")
        except Exception as e:
            errors += 1
            print(f"处理文件失败 {fp}: {e}")

    print(f"完成。已处理: {processed}，跳过: {skipped}，出错: {errors}")


if __name__ == "__main__":
    main()
