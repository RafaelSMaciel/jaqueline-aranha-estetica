"""Busca de clientes no painel.

Telefone e CPF sao gravados so com digitos (Cliente.save -> normalizar_*).
Quem digita com mascara ('(17) 99999-0001', '529.982.247-25') precisa ter o
termo reduzido a digitos antes do icontains, senao nada casa.
"""
import re

from django.db.models import Q

# Termo "numerico com mascara": so digitos + separadores comuns de telefone/CPF.
_MASCARA_NUMERICA = re.compile(r'[\d\s().+\-/]+')


def q_busca_cliente(termo: str, incluir_email: bool = True) -> Q:
    """Q para buscar Cliente por nome/e-mail/telefone/CPF.

    Os digitos so sao extraidos quando o termo inteiro tem cara de numero
    mascarado — 'Maria 2' nao pode virar '2' e casar com todo telefone.
    """
    termo = (termo or '').strip()
    q = Q(nome__icontains=termo)
    if incluir_email:
        q |= Q(email__icontains=termo)

    digitos = re.sub(r'\D', '', termo)
    if len(digitos) >= 3 and _MASCARA_NUMERICA.fullmatch(termo):
        q |= Q(telefone__contains=digitos) | Q(cpf__contains=digitos)
        # '+55 17 99999-0001': o DDI nao e armazenado (validators.normalizar_telefone)
        if digitos.startswith('55') and len(digitos) in (12, 13):
            q |= Q(telefone__contains=digitos[2:])
    else:
        q |= Q(telefone__icontains=termo) | Q(cpf__icontains=termo)
    return q
