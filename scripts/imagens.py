"""Variantes responsivas das fotos do site (WebP + JPEG de fallback em algumas larguras).

    python scripts/imagens.py              # gera/atualiza aranha_estetica/static/assets/clinica/variantes/
    python scripts/imagens.py --verificar  # so confere (sai 1 se faltar variante ou o manifest estiver velho)

Fonte: cada foto em aranha_estetica/static/assets/clinica/*.webp (as originais ficam como estao).
Saida (versionada no git, servida pelo collectstatic/WhiteNoise como qualquer estatico):
    variantes/<nome>-<largura>.webp   (o navegador escolhe pelo srcset/sizes)
    variantes/<nome>-<largura>.jpg    (fallback do <img> p/ navegador sem WebP)
    variantes/manifest.json           ({nome: {largura, altura, larguras}} — lido pela tag {% foto %})

Larguras: LARGURAS, sem ampliar (a maior variante e a largura original). Requer Pillow
(requirements-dev.txt). Saida deterministica para a mesma versao do Pillow: rodar de novo sem
foto nova nao muda nenhum arquivo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
FONTE = RAIZ / 'aranha_estetica' / 'static' / 'assets' / 'clinica'
DESTINO = FONTE / 'variantes'
MANIFEST = DESTINO / 'manifest.json'
LARGURAS = (480, 768, 1024)
QUALIDADE_WEBP = 78
QUALIDADE_JPEG = 80


def _larguras_de(largura_original: int) -> list[int]:
    return sorted({min(w, largura_original) for w in LARGURAS})


def _manifest() -> dict:
    fotos = {}
    for foto in sorted(FONTE.glob('*.webp')):
        with Image.open(foto) as im:
            largura, altura = im.size
        fotos[foto.stem] = {'largura': largura, 'altura': altura, 'larguras': _larguras_de(largura)}
    return fotos


def _saidas(nome: str, dados: dict) -> list[Path]:
    return [DESTINO / f'{nome}-{w}.{ext}' for w in dados['larguras'] for ext in ('webp', 'jpg')]


def gerar() -> list[Path]:
    DESTINO.mkdir(exist_ok=True)
    manifest = _manifest()
    escritos = []
    for nome, dados in manifest.items():
        with Image.open(FONTE / f'{nome}.webp') as original:
            rgb = original.convert('RGB')
        for w in dados['larguras']:
            h = round(dados['altura'] * w / dados['largura'])
            img = rgb if w == dados['largura'] else rgb.resize((w, h), Image.Resampling.LANCZOS)
            webp, jpg = DESTINO / f'{nome}-{w}.webp', DESTINO / f'{nome}-{w}.jpg'
            img.save(webp, 'WEBP', quality=QUALIDADE_WEBP, method=6)
            img.save(jpg, 'JPEG', quality=QUALIDADE_JPEG, optimize=True, progressive=True)
            escritos += [webp, jpg]
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return escritos


def verificar() -> list[str]:
    """Problemas encontrados (vazio = tudo em dia)."""
    esperado = _manifest()
    problemas = []
    atual = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else None
    if atual != esperado:
        problemas.append('manifest.json desatualizado')
    for nome, dados in esperado.items():
        problemas += [f'falta {p.relative_to(RAIZ)}' for p in _saidas(nome, dados) if not p.exists()]
    return problemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--verificar', action='store_true', help='so confere, nao escreve')
    args = parser.parse_args(argv)
    if args.verificar:
        problemas = verificar()
        for p in problemas:
            print(p)
        return 1 if problemas else 0
    escritos = gerar()
    total = sum(p.stat().st_size for p in escritos)
    print(f'{len(escritos)} variantes em {DESTINO.relative_to(RAIZ)} ({total / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
