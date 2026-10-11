from PIL import Image, ImageDraw

def make(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 8
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(24, 30, 44, 255))
    # 屏幕
    sx0, sy0 = int(size * 0.16), int(size * 0.22)
    sx1, sy1 = int(size * 0.84), int(size * 0.52)
    d.rounded_rectangle([sx0, sy0, sx1, sy1], radius=max(2, size // 40),
                        fill=(10, 14, 24, 255), outline=(90, 160, 255, 255),
                        width=max(1, size // 48))
    # 屏幕上的两行“字”
    for i, w in enumerate((0.52, 0.34)):
        y = sy0 + int((sy1 - sy0) * (0.28 + i * 0.34))
        d.rounded_rectangle([sx0 + int(size * 0.07), y,
                             sx0 + int(size * 0.07) + int((sx1 - sx0) * w), y + max(2, size // 26)],
                            radius=size // 40, fill=(150, 200, 255, 255))
    # 4 个盘位（绿/绿/红/绿）
    colors = [(60, 220, 120, 255), (60, 220, 120, 255), (240, 80, 80, 255), (60, 220, 120, 255)]
    bw = (size * 0.72) / 4
    for i, c in enumerate(colors):
        x0 = int(size * 0.14 + i * bw)
        x1 = int(x0 + bw * 0.62)
        y0 = int(size * 0.68)
        y1 = int(size * 0.86)
        d.rounded_rectangle([x0, y0, x1, y1], radius=max(2, size // 40), fill=c)
    return img

make(64).save(r'C:\Users\yanti\nas-lcd\xk-panel\fnos\ICON.PNG')
make(256).save(r'C:\Users\yanti\nas-lcd\xk-panel\fnos\ICON_256.PNG')
print('图标已生成')
