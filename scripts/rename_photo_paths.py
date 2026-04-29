"""Substitui paths assets/health/<old>.webp por assets/clinica/<new>.webp em
templates e views.
"""
import os
import re

# Mapeamento: nome antigo (sem extensao) -> novo nome (sem extensao)
mapping = {
    "cardiology-1": "facial-1",
    "cardiology-2": "procedimento-2",
    "cardiology-3": "pos-operatorio-1",
    "consultation-4": "consulta-1",
    "dermatology-1": "facial-2",
    "dermatology-4": "facial-3",
    "emergency-1": "procedimento-1",
    "facilities-6": "espaco-1",
    "facilities-9": "espaco-2",
    "laboratory-3": "laboratorio-1",
    "maternal-2": "gestante-1",
    "neurology-2": "facial-4",
    "neurology-3": "corporal-3",
    "neurology-4": "corporal-1",
    "oncology-2": "massagem-1",
    "orthopedics-4": "corporal-2",
    "pediatrics-3": "depilacao-1",
    "staff-10": "equipe-1",
    "staff-14": "equipe-2",
    "vaccination-3": "pele-1",
}

target_files = []
for root, _, fs in os.walk("app_shivazen"):
    for f in fs:
        if f.endswith((".html", ".py")) and "__pycache__" not in root:
            target_files.append(os.path.join(root, f))

print(f"arquivos: {len(target_files)}")
total = 0
files_changed = 0
for fp in target_files:
    with open(fp, "r", encoding="utf-8") as fh:
        content = fh.read()
    original = content
    for old, new in mapping.items():
        # Substitui assets/health/<old>.webp -> assets/clinica/<new>.webp
        pattern = r"assets/health/" + re.escape(old) + r"\.webp"
        replacement = f"assets/clinica/{new}.webp"
        new_content, n = re.subn(pattern, replacement, content)
        if n:
            content = new_content
            total += n
    if content != original:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
        files_changed += 1
        print(f"  fixed: {fp}")
print(f"TOTAL: {total} substituicoes em {files_changed} arquivos")
