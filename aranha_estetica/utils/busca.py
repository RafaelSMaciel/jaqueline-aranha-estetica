"""Busca de clientes no painel e no Django admin.

Telefone e CPF sao gravados so com digitos (Cliente.save -> normalizar_*).
Quem digita com mascara ('(17) 99999-0001', '529.982.247-25') precisa ter o
termo reduzido a digitos antes do icontains, senao nada casa.

Nome (spec padrao-sql §5.5 "Busca de cliente com trigram"): no Postgres a busca e
sem acento e sem caixa — ``fn_sem_acento(nome) LIKE '%' || fn_sem_acento(termo) || '%'``,
a MESMA expressao do indice GIN trigram ``ix_cliente_nome_trgm`` (0057; o pg_trgm atende
``LIKE '%x%'`` a partir de 3 caracteres): "joao" acha "João". No SQLite dos testes fica
``nome__icontains`` (sem unaccent).
"""
import re

from django.db import connections
from django.db.models import F, Func, Q, TextField, Value

# Termo "numerico com mascara": so digitos + separadores comuns de telefone/CPF.
_MASCARA_NUMERICA = re.compile(r'[\d\s().+\-/]+')


class SemAcento(Func):
    """fn_sem_acento(x) (so Postgres, 0057): lower(unaccent(x)), IMMUTABLE — casa com o indice."""
    function = 'fn_sem_acento'
    output_field = TextField()


def _q_contato(termo: str, incluir_email: bool) -> Q:
    """E-mail/telefone/CPF. Os digitos so sao extraidos quando o termo inteiro tem cara
    de numero mascarado — 'Maria 2' nao pode virar '2' e casar com todo telefone."""
    q = Q(email__icontains=termo) if incluir_email else Q()
    digitos = re.sub(r'\D', '', termo)
    if len(digitos) >= 3 and _MASCARA_NUMERICA.fullmatch(termo):
        q |= Q(telefone__contains=digitos) | Q(cpf__contains=digitos)
        # '+55 17 99999-0001': o DDI nao e armazenado (validators.normalizar_telefone)
        if digitos.startswith('55') and len(digitos) in (12, 13):
            q |= Q(telefone__contains=digitos[2:])
    else:
        q |= Q(telefone__icontains=termo) | Q(cpf__icontains=termo)
    return q


def q_busca_cliente(termo: str, incluir_email: bool = True) -> Q:
    """Q para buscar Cliente por nome/e-mail/telefone/CPF (nome com icontains: portavel).

    Prefira ``filtrar_clientes`` — no Postgres ela busca o nome sem acento pelo indice trigram.
    """
    termo = (termo or '').strip()
    return Q(nome__icontains=termo) | _q_contato(termo, incluir_email)


def filtrar_clientes(qs, termo: str, incluir_email: bool = True):
    """Filtra um queryset de Cliente por nome/e-mail/telefone/CPF.

    Postgres: nome sem acento/caixa via ``fn_sem_acento`` (indice ``ix_cliente_nome_trgm``);
    SQLite: ``q_busca_cliente`` (icontains).
    """
    termo = (termo or '').strip()
    if connections[qs.db].vendor != 'postgresql':
        return qs.filter(q_busca_cliente(termo, incluir_email))
    return qs.alias(nome_sem_acento=SemAcento(F('nome'))).filter(
        Q(nome_sem_acento__contains=SemAcento(Value(termo))) | _q_contato(termo, incluir_email)
    )
