# Audit de Fotos — jaqueline-aranha-estetica

Data: 2026-04-29
Diagnóstico: pasta `static/assets/health/` tem 23 fotos nomeadas como **clínica médica/hospital** (cardiology, neurology, oncology, pediatrics) sendo usadas como ilustrações de **clínica estética**. Resultado: nome do arquivo não casa com `alt` text → confusão semântica + risco de imagem visual errada.

---

## Mismatches críticos

| Template | src atual | alt usado | Problema |
|---|---|---|---|
| `home.html:193` | `cardiology-1.webp` | "Harmonização Facial" | Cardiologia ≠ estética facial |
| `home.html:219` | `neurology-4.webp` | "Modelagem Corporal" | Neurologia ≠ corporal |
| `home.html:388` | `vaccination-3.webp` | "Saúde da Pele" | Vacinação ≠ skincare |
| `home.html:400` | `emergency-1.webp` | "Emergências Estéticas" | Emergência hospitalar ≠ estética |
| `especialidades.html:100` | `neurology-2.webp` | "Harmonização Facial" | Mismatch |
| `especialidades.html:119` | `orthopedics-4.webp` | "Modelagem Corporal" | Mismatch |
| `especialidades.html:157` | `pediatrics-3.webp` | "Depilação a Laser" | Pediatria ≠ depilação |
| `especialidades.html:176` | `oncology-2.webp` | "Massagens" | Oncologia ≠ massagem |
| `especialidades.html:195` | `cardiology-3.webp` | "Pós-operatório" | Mismatch contextual |
| `servico_detalhe.html:89` | `maternal-2.webp` | (procedimento genérico) | Maternal ≠ procedimento |

## Fotos OK (semântica neutra ou aceitável)

| src | uso | OK pq |
|---|---|---|
| `staff-10.webp`, `staff-14.webp` | Equipe | "staff" = equipe genérica |
| `facilities-6/9.webp` | Espaço/Clínica | Instalações genéricas |
| `consultation-4.webp` | Consultas | Aceitável |
| `dermatology-1/4.webp` | Skin care | Dermatologia bate c/ pele |

---

## Plano proposto (3 caminhos)

### A. **Mínimo viável** (reorganização sem trocar binários)

Renomeia fotos por contexto + atualiza templates. Conteúdo visual pode não bater 100%, mas semântica fica correta.

```
static/assets/health/  →  static/assets/clinica/
  cardiology-1.webp     →  facial-1.webp     (Harmonização Facial)
  neurology-4.webp      →  corporal-1.webp   (Modelagem)
  vaccination-3.webp    →  pele-1.webp       (Saúde da Pele)
  emergency-1.webp      →  procedimento-1.webp
  orthopedics-4.webp    →  corporal-2.webp
  pediatrics-3.webp     →  depilacao-1.webp
  oncology-2.webp       →  massagem-1.webp
  cardiology-3.webp     →  pos-operatorio-1.webp
  maternal-2.webp       →  gestante-1.webp   (mantém — gestante OK)
  dermatology-1.webp    →  facial-2.webp
  dermatology-4.webp    →  facial-3.webp
  consultation-4.webp   →  consulta-1.webp
  facilities-6.webp     →  espaco-1.webp
  facilities-9.webp     →  espaco-2.webp
  staff-10.webp         →  equipe-1.webp
  staff-14.webp         →  equipe-2.webp
  laboratory-3.webp     →  laboratorio-1.webp
  cardiology-2.webp     →  procedimento-2.webp
  neurology-2.webp      →  facial-4.webp
  neurology-3.webp      →  corporal-3.webp
```

**Esforço**: 30 min. Posso fazer agora.

### B. **Substituição visual** (recomendado p/ produção)

Baixar fotos contextualizadas estética. **Fontes free + sem watermark**:

- **Unsplash** (free MIT) → buscar:
  - `aesthetic clinic facial`
  - `spa beauty salon`
  - `skincare treatment`
  - `body massage therapy`
  - `dermatology skincare`
  - `beauty equipment laser`
  - `clinic interior modern`
  - `professional aesthetician`
- **Pexels** (free) → mesmas keywords
- **Freepik premium** se Jaqueline tiver assinatura (mais opções estética BR)

**Sugestão concreta de URLs Unsplash** (todas gratuitas):
- Facial: https://unsplash.com/s/photos/facial-treatment
- Massagem: https://unsplash.com/s/photos/massage-therapy
- Spa: https://unsplash.com/s/photos/spa
- Skincare: https://unsplash.com/s/photos/skincare
- Equipe estética: https://unsplash.com/s/photos/aesthetician

**Esforço**: 1-2h baixar + tratar tamanhos + substituir.

### C. **IA generativa**

Gerar imagens contextualizadas via Midjourney/DALL-E/Stable Diffusion c/ prompts:
- "Brazilian aesthetic clinic, modern facial treatment room, professional female aesthetician with client, soft warm lighting, photorealistic"
- "Modern spa massage therapy, relaxing atmosphere, woman receiving facial care"

**Esforço**: 2-4h prompts + iteração. Custo: ~$10-30 (Midjourney) ou free (SD local).

---

## Recomendação

**Faz A AGORA** (renomear + ajustar paths) — corrige semântica em 30 min. Depois user decide se faz B (baixar fotos novas) quando tiver tempo.

Aguarde tua decisão:
- **A**: aplico renames + edits agora
- **B**: você baixa, eu troco paths quando enviar
- **C**: você gera via IA, mesmo flow B

Mais contexto: se Jaqueline tiver fotos próprias da clínica (Instagram, sessões pro), use isso — mais autêntico que stock.
