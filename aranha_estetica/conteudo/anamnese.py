"""Ficha de anamnese padrao (questionario pre-atendimento do agendamento online).

Fonte unica do ``seed`` (banco novo) e da migration 0080 (banco que ja tinha a ficha antiga).

Desenho (2026-11-24): toda pergunta e sim/nao e obrigatoria — um toque cada, sem campo de texto
para "inventar" resposta. O texto so aparece (e so entao e exigido) como detalhe de um Sim
(``"se": "<key da pergunta sim/nao>"``). Antes a ficha era opcional e o texto livre aceitava
qualquer coisa: a cliente pulava tudo ou preenchia com "." e a profissional atendia sem saber de
alergia.

``alerta_saude: False`` na pergunta de controle da alergia: quem vira alerta para a equipe e o
detalhe ("A que voce tem alergia?: Lidocaina"), nao o "Sim" solto.
"""

ANAMNESE_PADRAO_NOME = 'Anamnese padrão'

ANAMNESE_PADRAO = [
    {'key': 'gestante_ou_amamentando', 'tipo': 'bool', 'label': 'Está gestante ou amamentando?', 'obrigatorio': True},
    {'key': 'tem_alergia', 'tipo': 'bool', 'label': 'Tem alergia a algum produto, cosmético ou medicamento?',
     'obrigatorio': True, 'alerta_saude': False},
    {'key': 'alergias', 'tipo': 'text', 'label': 'A que você tem alergia?', 'obrigatorio': True, 'se': 'tem_alergia'},
    {'key': 'uso_de_acidos', 'tipo': 'bool', 'label': 'Está usando ácidos ou retinoides na pele?', 'obrigatorio': True},
    {'key': 'fez_procedimento_recente', 'tipo': 'bool', 'label': 'Fez algum procedimento estético nos últimos 6 meses?',
     'obrigatorio': True},
    {'key': 'procedimentos_recentes', 'tipo': 'longtext', 'label': 'Qual procedimento e quando?', 'obrigatorio': True,
     'se': 'fez_procedimento_recente'},
]

# A ficha que o seed gravava ate a 0079 (a migration 0080 so troca a que ainda esta igual a esta)
ANAMNESE_PADRAO_ANTIGA = [
    {'key': 'gestante_ou_amamentando', 'tipo': 'bool', 'label': 'Está gestante ou amamentando?', 'obrigatorio': True},
    {'key': 'alergias', 'tipo': 'text', 'label': 'Tem alergia a algum produto, cosmético ou medicamento?', 'obrigatorio': False},
    {'key': 'uso_de_acidos', 'tipo': 'bool', 'label': 'Está usando ácidos ou retinoides na pele?', 'obrigatorio': False},
    {'key': 'procedimentos_recentes', 'tipo': 'longtext', 'label': 'Fez algum procedimento estético nos últimos 6 meses? Qual?', 'obrigatorio': False},
]
