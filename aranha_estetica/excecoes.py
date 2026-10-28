"""Recusas de regra de negocio — o jeito unico de um service dizer "nao".

Antes uma recusa vinha como None, False, TransicaoInvalida, a _Recusa privada do booking ou um
IntegrityError com pgcode lido na view. Agora o service levanta uma destas (mensagem pronta p/
a tela + `codigo` p/ teste e JSON) e a view so mostra:

    except RegraDeNegocio as exc:
        messages.error(request, exc.mensagem)        # JSON: utils/respostas.json_recusa(exc)
"""
class RegraDeNegocio(Exception):
    codigo = 'regra_de_negocio'

    def __init__(self, mensagem, *, codigo=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        if codigo:
            self.codigo = codigo


class HorarioIndisponivel(RegraDeNegocio):
    """Horario fora da agenda ou ocupado (inclusive a corrida perdida para o EXCLUDE do PG)."""
    codigo = 'horario_indisponivel'


class TransicaoInvalida(RegraDeNegocio):
    """Mudanca de status que a FSM do atendimento nao permite (Atendimento.TRANSICOES)."""
    codigo = 'transicao_invalida'


def eh_sobreposicao(exc) -> bool:
    """IntegrityError da ex_atendimento_sobreposicao (SQLSTATE 23P01): outro request reservou a
    janela entre a checagem e o INSERT."""
    causa = getattr(exc, '__cause__', None)
    return getattr(causa, 'pgcode', None) == '23P01' or 'ex_atendimento_sobreposicao' in str(exc)


