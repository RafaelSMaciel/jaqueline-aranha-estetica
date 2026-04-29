"""Variante 2 do logo — minimalista, paleta rose/nude, fundo claro."""
from PIL import Image, ImageDraw, ImageFont
import os

BG = (245, 239, 232)
ROSE_DARK = (139, 111, 71)
ROSE_LIGHT = (201, 165, 138)
MUTED = (184, 168, 150)


def tenta_fonte(prefs, size):
    for nome in prefs:
        try:
            return ImageFont.truetype(nome, size)
        except Exception:
            continue
    return ImageFont.load_default()


def gerar(tamanho, path):
    img = Image.new("RGBA", (tamanho, tamanho), BG + (255,))
    d = ImageDraw.Draw(img)

    cx = tamanho // 2
    tam_monog = int(tamanho * 0.55)
    fonte_monog = tenta_fonte(
        ["georgiai.ttf", "georgia.ttf", "timesi.ttf", "times.ttf"], tam_monog
    )

    texto = "JA"
    bbox = d.textbbox((0, 0), texto, font=fonte_monog)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    y_mono = int(tamanho * 0.28) - bbox[1]
    d.text((cx - w / 2 - bbox[0], y_mono), texto,
           fill=ROSE_DARK, font=fonte_monog)

    if tamanho >= 180:
        linha_w = int(tamanho * 0.32)
        linha_y = int(tamanho * 0.76)
        d.line((cx - linha_w // 2, linha_y, cx - int(tamanho * 0.03), linha_y),
               fill=ROSE_LIGHT, width=1)
        d.line((cx + int(tamanho * 0.03), linha_y, cx + linha_w // 2, linha_y),
               fill=ROSE_LIGHT, width=1)
        d.ellipse((cx - 3, linha_y - 3, cx + 3, linha_y + 3), fill=ROSE_LIGHT)

        tam_nome = max(10, int(tamanho * 0.045))
        fonte_nome = tenta_fonte(["arial.ttf", "DejaVuSans.ttf"], tam_nome)
        nome = "JAQUELINE ARANHA"
        bb2 = d.textbbox((0, 0), nome, font=fonte_nome)
        w2 = bb2[2] - bb2[0]
        d.text((cx - w2 / 2, linha_y + int(tamanho * 0.025)),
               nome, fill=ROSE_DARK, font=fonte_nome)

        tam_tag = max(8, int(tamanho * 0.028))
        fonte_tag = tenta_fonte(["arial.ttf", "DejaVuSans.ttf"], tam_tag)
        tag = "estetica & bem-estar"
        bb3 = d.textbbox((0, 0), tag, font=fonte_tag)
        w3 = bb3[2] - bb3[0]
        d.text((cx - w3 / 2, linha_y + int(tamanho * 0.10)),
               tag, fill=MUTED, font=fonte_tag)

    img.save(path, "PNG")
    print(f"Gerado: {path} ({tamanho}x{tamanho})")


OUT = "app_shivazen/static/assets"
os.makedirs(OUT, exist_ok=True)

gerar(512, f"{OUT}/logo-ja-v2.png")
gerar(180, f"{OUT}/logo-ja-v2-180.png")
gerar(64, f"{OUT}/logo-ja-v2-64.png")
