"""Catalogo das regras de negocio com valor — fonte unica da tabela "Valores-chave" (spec
regras-negocio-registry, R1 e R6).

Cada regra aparece uma vez aqui: rotulo, valor LIDO de onde o codigo o aplica (constante, padrao de
env do settings, default de model, CHECK do banco ou regra operacional de Configuracao — nada
copiado) e onde mora. A tabela do §24 de docs/REGRAS-DE-NEGOCIO.md e gerada daqui:

    python manage.py gerar_docs_regras            # reescreve a tabela entre os marcadores
    python manage.py gerar_docs_regras --check    # so confere: sai com erro se o doc ficou para tras

Mudou um valor no codigo? Rode o comando (o teste test_regras_catalogo reprova o doc desatualizado).
Regra nova com valor? Acrescente uma linha aqui. Regra operacional (que a dona ajusta no painel)
mora em models/configuracao.CHAVES; aqui ela aparece com o padrao e a faixa.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.db.models import CheckConstraint, Q

MARCADOR_INICIO = '<!-- regras:inicio — gerado por `manage.py gerar_docs_regras` (aranha_estetica/regras.py); não edite à mão -->'
MARCADOR_FIM = '<!-- regras:fim -->'


@dataclass(frozen=True)
class Regra:
    rotulo: str
    valor: Callable[[], str]
    onde: str
    operacional: bool = False


# ─── Formatacao ───────────────────────────────────────────────────────────

def _duracao(td: timedelta) -> str:
    segundos = int(td.total_seconds())
    if segundos % 86400 == 0 and segundos >= 2 * 86400:   # 24 h fica em horas
        return _dias(segundos // 86400)
    if segundos % 3600 == 0:
        return f'{segundos // 3600} h'
    if segundos % 60 == 0:
        return f'{segundos // 60} min'
    return f'{segundos} s'


def _dias(dias: int) -> str:
    if dias % 365 == 0:
        anos = dias // 365
        return f'{anos} ano' if anos == 1 else f'{anos} anos'
    return '1 dia' if dias == 1 else f'{dias} dias'


def _reais(valor: Decimal) -> str:
    inteiro, centavos = f'{valor:.2f}'.split('.')
    return f'R$ {int(inteiro):,}'.replace(',', '.') + f',{centavos}'


def _quantidade(taxa: str) -> int:
    """Taxa do django-ratelimit -> quantidade: "20/d" -> 20, "5/m" -> 5."""
    return int(taxa.split('/')[0])


def _padrao(nome: str):
    """Padrao da env `nome` (settings.PADROES_ENV), nao o valor do ambiente de quem gera o doc."""
    return settings.PADROES_ENV[nome]


def _faixa(model, campo: str) -> tuple[int | None, int | None]:
    """(minimo, maximo) de `campo` nos CHECKs do model (`campo__gte` / `campo__lte`)."""
    minimo = maximo = None

    def varrer(no):
        nonlocal minimo, maximo
        for filho in getattr(no, 'children', ()):
            if isinstance(filho, Q):
                varrer(filho)
            elif filho[0] == f'{campo}__gte':
                minimo = filho[1]
            elif filho[0] == f'{campo}__lte':
                maximo = filho[1]

    for restricao in model._meta.constraints:
        if isinstance(restricao, CheckConstraint):
            varrer(restricao.condition)
    return minimo, maximo


def _default(model, campo: str):
    return model._meta.get_field(campo).default


def _operacional(chave: str) -> str:
    from .models.configuracao import CHAVES

    regra = CHAVES[chave]
    if regra.opcional:
        return f'{regra.minimo} a {regra.maximo}, opcional (em branco = não aparece)'
    if regra.tipo == 'email':
        return 'em branco = e-mail de contato da clínica'
    return f'{regra.default} (a dona ajusta de {regra.minimo} a {regra.maximo})'


# ─── Valores (cada funcao le a fonte na hora) ─────────────────────────────

def _antecedencia_minima():
    from .models import Profissional
    minimo, maximo = _faixa(Profissional, 'min_notice_horas')
    return f'{_default(Profissional, "min_notice_horas")} h ({minimo}–{maximo}, por profissional)'


def _antecedencia_maxima():
    from .models import Profissional
    minimo, maximo = _faixa(Profissional, 'max_advance_dias')
    return f'{_default(Profissional, "max_advance_dias")} dias ({minimo}–{maximo}, por profissional)'


def _intervalo_slots():
    from .services.disponibilidade import SlotService
    return _duracao(SlotService.INTERVALO)


def _buffer():
    from .models import Procedimento
    minimo, maximo = _faixa(Procedimento, 'buffer_minutos')
    return f'{minimo}–{maximo} min (por procedimento)'


def _idade_minima():
    from .forms.agendamento import IDADE_MINIMA
    return f'{IDADE_MINIMA} anos'


def _ativos_online():
    from .constants import MAX_ATIVOS_ONLINE
    return f'até {MAX_ATIVOS_ONLINE} (o {MAX_ATIVOS_ONLINE + 1}º é recusado)'


def _autoaprovacao():
    from .constants import LIMITE_AUTOAPROVACAO
    return (f'cliente já atendida, sem falta em aberto e com menos de {LIMITE_AUTOAPROVACAO} horários '
            'futuros: nasce AGENDADO; senão PENDENTE')


def _prazo_pendente():
    from .constants import PRAZO_DECISAO_PENDENTE
    return f'cancelado {_duracao(PRAZO_DECISAO_PENDENTE)} antes do início, se ninguém decidir'


def _validade_ficha():
    from .services.anamnese import VALIDADE_FICHA_MESES
    return f'{VALIDADE_FICHA_MESES} meses'


def _reagendar():
    from .constants import JANELA_MINIMA_ALTERACAO
    return f'≥ {_duracao(JANELA_MINIMA_ALTERACAO)} antes'


def _cancelamento_tardio():
    from .constants import JANELA_MINIMA_ALTERACAO
    return f'< {_duracao(JANELA_MINIMA_ALTERACAO)} de horário AGENDADO/CONFIRMADO (PENDENTE não)'


def _desfecho():
    from .constants import TOLERANCIA_DESFECHO
    return f'início − {_duracao(TOLERANCIA_DESFECHO)}'


def _otp():
    return (f'{_padrao("OTP_TTL_SEGUNDOS") // 60} min / {_padrao("OTP_MAX_TENTATIVAS")} / '
            f'{_padrao("OTP_REENVIO_MINIMO_SEG")} s')


def _sms_por_hora():
    return (f'{_padrao("SMS_MAX_POR_HORA")} / {_padrao("SMS_MAX_POR_IP_HORA")} / '
            f'{_padrao("SMS_MAX_GLOBAL_HORA")}')


def _sessao_cliente():
    from .services import sessao_cliente as s
    return (f'{_duracao(s.VALIDADE_AGENDAMENTO)} / {_duracao(timedelta(seconds=s.DURACAO_SESSAO_SEGUNDOS))} / '
            f'até {_duracao(s.JANELA_DADOS)}')


def _prazo_pedido_lgpd():
    from .services.portal_cliente import PRAZO_RESPOSTA
    return _duracao(PRAZO_RESPOSTA)


def _lista_espera():
    from .views.publico.lista_espera import LIMITE_DIARIO_POR_IP
    return f'{_quantidade(LIMITE_DIARIO_POR_IP)} inscrições/dia por IP'


def _contato():
    from .views.publico import contato
    return (f'{_quantidade(contato.LIMITE_POR_MINUTO_POR_IP)}/min e {_quantidade(contato.LIMITE_DIARIO_POR_IP)}/dia '
            f'por IP; teto de {contato.TETO_DIARIO_GLOBAL} mensagens/dia no site todo')


def _retorno():
    from .models import Procedimento
    return f'{_default(Procedimento, "duracao_retorno_minutos")} min'


def _cashback():
    from .constants import VALOR_CASHBACK_INDICACAO
    return _reais(VALOR_CASHBACK_INDICACAO)


def _nps():
    from .constants import VALIDADE_LINK_NPS
    from .services.nps import NPS_ESPERA, NPS_JANELA_DIAS, NPS_MAX_FALHAS
    return (f'{_duracao(NPS_ESPERA)}–{_dias(NPS_JANELA_DIAS)} / {_duracao(VALIDADE_LINK_NPS)} / '
            f'{NPS_MAX_FALHAS}')


def _nps_intervalo():
    from .constants import NPS_INTERVALO_DIAS
    return f'{_dias(NPS_INTERVALO_DIAS)} (nunca em retorno)'


def _detrator():
    from .services.nps import NOTA_MAXIMA_DETRATOR
    return f'nota ≤ {NOTA_MAXIMA_DETRATOR}'


def _lembrete():
    from .jobs import JOB_AGENDA
    horas = [f'{h.hora:02d}:{h.minuto:02d}' for h in JOB_AGENDA['lembrete_diario']]
    return f'{" e ".join(horas)} (WhatsApp ou e-mail)'


def _depoimento():
    from .services.nps import NOTA_MINIMA_DEPOIMENTO
    return f'nota ≥ {NOTA_MINIMA_DEPOIMENTO} + opt-in + aprovação'


def _numeros_site():
    from .services.prova_social import MIN_AVALIACOES, MIN_CLIENTES
    return f'{MIN_AVALIACOES}+ respostas do NPS / a partir de {MIN_CLIENTES}, arredondado para baixo'


def _numeros_site_cache():
    from .services.prova_social import CACHE_TTL
    return f'a cada {_duracao(timedelta(seconds=CACHE_TTL))} (cache zerado quando `Configuracao` muda)'


def _link_formulario():
    from .constants import VALIDADE_LINK_FORMULARIO
    return _duracao(VALIDADE_LINK_FORMULARIO)


def _retencao_housekeeping():
    return (f'{_padrao("RETENCAO_OTP_HORAS")} h / {_dias(_padrao("RETENCAO_NOTIFICACAO_DIAS"))} / '
            f'{_dias(_padrao("RETENCAO_LOG_AUDITORIA_DIAS"))} / {_dias(_padrao("RETENCAO_AXES_LOG_DIAS"))} / '
            f'{_dias(_padrao("RETENCAO_LINK_ACESSO_DIAS"))}')


def _retencao_lgpd():
    s = settings
    return (f'{_dias(s.RETENCAO_CLIENTE_INATIVO_DIAS)} / {_dias(s.RETENCAO_CARENCIA_SOFT_DELETE_DIAS)} / '
            f'{_dias(s.RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS)} / {_dias(s.RETENCAO_ATENDIMENTO_SAUDE_DIAS)} do '
            f'último evento / {_dias(s.RETENCAO_PACOTE_APOS_EXPIRAR_DIAS)} do fim')


def _lote_promocao():
    from .services.marketing import LOTE_PROMOCAO
    return f'{LOTE_PROMOCAO} por clique'


def _sessao_equipe():
    return f'{_duracao(timedelta(seconds=_padrao("SESSION_COOKIE_AGE")))} deslizantes'


CATALOGO: tuple[Regra, ...] = (
    Regra('Antecedência mínima', _antecedencia_minima, '`Profissional.min_notice_horas`'),
    Regra('Antecedência máxima', _antecedencia_maxima, '`Profissional.max_advance_dias`'),
    Regra('Intervalo dos slots', _intervalo_slots, '`SlotService.INTERVALO`'),
    Regra('Buffer do procedimento', _buffer, '`Procedimento.buffer_minutos`'),
    Regra('Idade mínima (online)', _idade_minima, '`forms/agendamento.IDADE_MINIMA`'),
    Regra('Horários futuros ativos para pedir pelo site', _ativos_online, '`constants.MAX_ATIVOS_ONLINE`'),
    Regra('Pedido pelo site aprovado sozinho', _autoaprovacao,
          '`constants.LIMITE_AUTOAPROVACAO`, `services/agenda.status_inicial_publico`'),
    Regra('Pedido pendente sem decisão', _prazo_pendente, '`constants.PRAZO_DECISAO_PENDENTE` (job `limpeza_status`)'),
    Regra('Validade da ficha de anamnese respondida', _validade_ficha, '`services/anamnese.VALIDADE_FICHA_MESES`'),
    Regra('Reagendar pela cliente', _reagendar, '`constants.JANELA_MINIMA_ALTERACAO`'),
    Regra('Cancelamento tardio (conta falta)', _cancelamento_tardio,
          '`constants.JANELA_MINIMA_ALTERACAO`, `services/agenda.cancelamento_tardio`'),
    Regra('Realizado/Faltou a partir de', _desfecho, '`constants.TOLERANCIA_DESFECHO`'),
    Regra('Faltas seguidas até pausar o agendamento online', lambda: _operacional('MAX_FALTAS_BLOQUEIO'),
          'regra operacional `MAX_FALTAS_BLOQUEIO` (`models/configuracao.CHAVES`)', operacional=True),
    Regra('OTP: validade / tentativas / reenvio', _otp,
          'env `OTP_TTL_SEGUNDOS`, `OTP_MAX_TENTATIVAS`, `OTP_REENVIO_MINIMO_SEG` (padrões)'),
    Regra('SMS por hora: telefone / IP / global', _sms_por_hora,
          'env `SMS_MAX_POR_HORA`, `SMS_MAX_POR_IP_HORA`, `SMS_MAX_GLOBAL_HORA` (padrões)'),
    Regra('Sessão da cliente verificada: agendar / área da cliente / baixar os dados', _sessao_cliente,
          '`services/sessao_cliente` (`VALIDADE_AGENDAMENTO`, `DURACAO_SESSAO_SEGUNDOS`, `JANELA_DADOS`)'),
    Regra('Pedido de correção/exclusão: prazo de resposta', _prazo_pedido_lgpd, '`services/portal_cliente.PRAZO_RESPOSTA`'),
    Regra('Lista de espera pelo site', _lista_espera, '`views/publico/lista_espera.LIMITE_DIARIO_POR_IP`'),
    Regra('Formulário de contato', _contato, '`views/publico/contato`'),
    Regra('Duração padrão do retorno', _retorno, '`Procedimento.duracao_retorno_minutos`'),
    Regra('Cashback por indicação', _cashback, '`constants.VALOR_CASHBACK_INDICACAO`'),
    Regra('NPS: janela / validade do link / tentativas', _nps,
          '`services/nps` (`NPS_ESPERA`, `NPS_JANELA_DIAS`, `NPS_MAX_FALHAS`), `constants.VALIDADE_LINK_NPS`'),
    Regra('NPS: intervalo mínimo por cliente', _nps_intervalo, '`constants.NPS_INTERVALO_DIAS`'),
    Regra('Detrator', _detrator, '`services/nps.NOTA_MAXIMA_DETRATOR` (job `detrator_alerta`)'),
    Regra('E-mail do alerta de detrator', lambda: _operacional('email_admin'),
          'regra operacional `email_admin` (`models/configuracao.CHAVES`)', operacional=True),
    Regra('Lembrete D-1', _lembrete, '`jobs.JOB_AGENDA`, `services/lembretes`'),
    Regra('Depoimento', _depoimento, '`services/nps.NOTA_MINIMA_DEPOIMENTO`, `views/publico/site.depoimentos`'),
    Regra('Números do site: nota média / clientes atendidas', _numeros_site,
          '`services/prova_social` (`MIN_AVALIACOES`, `MIN_CLIENTES`)'),
    Regra('Números do site: anos de experiência', lambda: _operacional('anos_experiencia'),
          'regra operacional `anos_experiencia` (`models/configuracao.CHAVES`)', operacional=True),
    Regra('Números do site: atualização', _numeros_site_cache, '`services/prova_social.CACHE_TTL`, `signals.py`'),
    Regra('Link de ficha/pesquisa', _link_formulario, '`constants.VALIDADE_LINK_FORMULARIO`'),
    Regra('Link do termo', lambda: 'até o fim do atendimento', '`services/termos.aceita_assinatura`'),
    Regra('Retenção: OTP / notificação / auditoria / axes / link vencido', _retencao_housekeeping,
          'env `RETENCAO_*` (padrões)'),
    Regra('Retenção LGPD: cliente inativa / soft delete / ficha sem atendimento / saúde / pacote', _retencao_lgpd,
          '`settings.RETENCAO_*` fixos (sem env; publicados na política — `aranha.W016`)'),
    Regra('Lote de e-mail de promoção', _lote_promocao, '`services/marketing.LOTE_PROMOCAO`'),
    Regra('Sessão da equipe (prod)', _sessao_equipe, 'env `SESSION_COOKIE_AGE` (padrão)'),
)


def tabela_markdown() -> str:
    """A tabela do §24 (sem os marcadores)."""
    linhas = ['| Regra | Valor | Onde |', '|---|---|---|']
    for regra in CATALOGO:
        rotulo = f'{regra.rotulo} *(painel)*' if regra.operacional else regra.rotulo
        linhas.append(f'| {rotulo} | {regra.valor()} | {regra.onde} |')
    return '\n'.join(linhas)


def aplicar_no_documento(texto: str) -> str:
    """Troca o que esta entre os marcadores do documento pela tabela gerada (ValueError sem os marcadores)."""
    inicio = texto.index(MARCADOR_INICIO) + len(MARCADOR_INICIO)
    fim = texto.index(MARCADOR_FIM, inicio)
    return f'{texto[:inicio]}\n{tabela_markdown()}\n{texto[fim:]}'
