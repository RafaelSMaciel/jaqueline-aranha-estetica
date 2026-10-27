"""Campos com tipo de banco do padrao SQL (docs/specs/padrao-sql.md §1.2, §7.2).

HashSha256Field — hash de segredo (sha256/HMAC em hex): ``char(64)``, nunca
``varchar``. Sem o ``_like`` (varchar_pattern_ops) que o Django cria para
CharField unico/indexado no Postgres. O CHECK de formato
(``ck_<tabela>_<x>_hash_formato``, regex) e so-PG e vem por migration.
Comparacao com parametro texto funciona (o literal vira ``bpchar``); SQL manual
deve usar sempre o hash inteiro. bulk_create no PG nao usa UNNEST (ver
get_placeholder: o UNNEST truncaria o char(64)).

Modulo CONGELADO (migrations aplicadas importam daqui): mudanca = classe nova.
"""
from django.db import models
from django.db.models.query_utils import DeferredAttribute


class HashSha256Field(models.CharField):
    description = 'Hash SHA-256 em hexadecimal (char(64))'

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 64
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop('max_length', None)
        return name, path, args, kwargs

    def db_type(self, connection):
        return 'char(64)'

    def get_placeholder(self, value, compiler, connection):
        # Desliga o bulk_create por UNNEST do Postgres (Django 5.2): ele tira o
        # tamanho do tipo p/ montar o cast do array — char(64)[] vira char[] =
        # char(1)[] e o hash seria TRUNCADO p/ 1 caractere. Com get_placeholder
        # o Django volta ao INSERT ... VALUES (placeholder padrao, sem cast).
        return '%s'


# ─── Cifragem em repouso (utils/cifragem; LGPD art. 11/46) ─────────────────────
# O valor lido do banco carrega o texto cifrado de origem: Fernet e aleatorio, entao recifrar
# o mesmo conteudo mudaria a coluna a cada save — o tg_<t>_historico registraria campo
# "alterado" sem ninguem ter mexido. pre_save reaproveita o cifrado quando o conteudo nao mudou.

class TextoLido(str):
    """str decifrado + o 'cif1:...' de onde veio (None = legado em claro: o save cifra)."""

    def __new__(cls, texto, cifra=None):
        obj = super().__new__(cls, texto)
        obj.cifra = cifra
        return obj

    def __reduce__(self):
        return (TextoLido, (str(self), self.cifra))


class DictLido(dict):
    """dict decifrado + o token de origem e a foto canonica do conteudo lido (mutacao in-place
    muda o canonico e o save recifra)."""

    def __init__(self, dados, cifra=None, canon=None):
        super().__init__(dados)
        self.cifra = cifra
        if canon is None:
            from ..utils.cifragem import canonico
            canon = canonico(dados)
        self.canon = canon

    def __reduce__(self):
        # leva a foto do que foi LIDO: mutado + pickle (cache/sessao) segue recifrando no save
        return (DictLido, (dict(self), self.cifra, self.canon))


class _DescritorCifrado(DeferredAttribute):
    """Guarda o valor lido do banco ao lado do atributo (pre_save compara com ele)."""

    def __set__(self, instance, value):
        if isinstance(value, (TextoLido, DictLido)):
            instance.__dict__['_cifra_lida_' + self.field.attname] = value
        instance.__dict__[self.field.attname] = value


class _CampoCifrado:
    descriptor_class = _DescritorCifrado

    def __init__(self, *args, finalidade='saude', **kwargs):
        self.finalidade = finalidade
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.finalidade != 'saude':
            kwargs['finalidade'] = self.finalidade
        return name, path, args, kwargs

    def pre_save(self, model_instance, add):
        valor = super().pre_save(model_instance, add)
        lido = model_instance.__dict__.get('_cifra_lida_' + self.attname)
        if lido is not None and lido.cifra and valor is not None and self._conteudo(valor) == self._conteudo_lido(lido):
            return self._reaproveitar(valor, lido.cifra)
        return valor


class TextoCifrado(_CampoCifrado, models.TextField):
    """TextField cifrado em repouso: no banco 'cif1:<token>', no Python o texto claro.
    '' fica '' (o SQL testa `<> ''`). Busca/igualdade por conteudo nao funciona no banco."""
    description = 'Texto cifrado em repouso (Fernet)'

    def from_db_value(self, value, expression, connection):
        from ..utils.cifragem import decifrar, esta_cifrado
        if value is None or value == '':
            return value
        if esta_cifrado(value):
            return TextoLido(decifrar(value, self.finalidade), cifra=value)
        return TextoLido(value)

    def get_prep_value(self, value):
        from ..utils.cifragem import cifrar
        value = super().get_prep_value(value)
        if value is None or value == '':
            return value
        if isinstance(value, TextoLido) and value.cifra:
            return value.cifra
        return cifrar(str(value), self.finalidade)

    @staticmethod
    def _conteudo(valor):
        return str(valor)

    _conteudo_lido = _conteudo

    @staticmethod
    def _reaproveitar(valor, cifra):
        return TextoLido(str(valor), cifra=cifra)


class JsonCifrado(_CampoCifrado, models.JSONField):
    """JSONField cifrado em repouso: no banco {"_cifra": "<token>"} (segue objeto — os CHECKs
    jsonb_typeof = 'object' valem), no Python o JSON claro. {} fica {} (o SQL testa
    `<> '{}'`). Lookup por chave (campo__chave=...) nao funciona no banco."""
    description = 'JSON cifrado em repouso (Fernet)'

    def from_db_value(self, value, expression, connection):
        from ..utils.cifragem import decifrar_json, json_cifrado
        dados = super().from_db_value(value, expression, connection)
        if json_cifrado(dados):
            claro = decifrar_json(dados, self.finalidade)
            return DictLido(claro, cifra=dados['_cifra']) if isinstance(claro, dict) else claro
        return DictLido(dados) if isinstance(dados, dict) else dados

    def get_prep_value(self, value):
        from ..utils.cifragem import canonico, cifrar_json
        value = super().get_prep_value(value)
        if value is None or value == {}:
            return value
        if isinstance(value, DictLido) and value.cifra and canonico(value) == value.canon:
            return {'_cifra': value.cifra}
        return cifrar_json(value, self.finalidade)

    @staticmethod
    def _conteudo(valor):
        from ..utils.cifragem import canonico
        return canonico(valor)

    @staticmethod
    def _conteudo_lido(lido):
        return lido.canon

    @staticmethod
    def _reaproveitar(valor, cifra):
        return DictLido(valor, cifra=cifra) if isinstance(valor, dict) else valor
