"""AJAX do wizard de agendamento — calendario do mes e horarios do dia.

Calendario: ``disponibilidade.dias_com_expediente`` (expediente, feriado/folga, bloqueio do dia
inteiro, max_advance). Horarios: ``disponibilidade.horarios_agrupados`` sobre o SlotService
(ocupacao no intervalo inteiro, buffer, antecedencia, bloqueios). Pedido com varios tratamentos:
``?carrinho=`` leva os horarios ja escolhidos dos outros — os que batem com eles nao sao oferecidos. Aqui so os parametros e o JSON
no contrato de utils/respostas: sucesso {"ok": true, ...}; erro {"ok": false, "codigo", "mensagem"}.
"""
import json
import logging
from datetime import date, datetime, timedelta

from django.utils import timezone

from django_ratelimit.decorators import ratelimit

from ...constants import MAX_TRATAMENTOS_POR_PEDIDO
from ...models import Procedimento
from ...services.disponibilidade import dias_com_expediente, horarios_agrupados, profissionais_para
from ...utils.datas import ler_data, ler_mes
from ...utils.parse import id_int
from ...utils.respostas import json_erro, json_ok

logger = logging.getLogger(__name__)

MSG_PARAMETROS = 'Parâmetros obrigatórios: data, procedimento_id'


def _procedimento_ativo(procedimento_id):
    """(procedimento, JsonResponse de erro)."""
    procedimento_id = str(procedimento_id or '').strip()
    if not procedimento_id:
        return None, json_erro('parametros_ausentes', MSG_PARAMETROS)
    pk = id_int(procedimento_id)
    if pk is None:
        return None, json_erro('parametro_invalido', 'Parâmetro inválido')
    procedimento = Procedimento.objects.filter(pk=pk, ativo=True).first()
    if procedimento is None:
        return None, json_erro('procedimento_nao_encontrado', 'Procedimento não encontrado', status=404)
    return procedimento, None


def _profissionais(request, procedimento):
    """Profissionais habilitados; ?profissional_id= restringe (link 'Agendar com X').

    Se o profissional pedido nao realiza o procedimento, ignora o filtro
    (mostra todos) em vez de deixar o calendario vazio.
    """
    profissionais = list(profissionais_para(procedimento))
    prof_id = id_int(request.GET.get('profissional_id'))
    if prof_id is not None:
        filtrados = [p for p in profissionais if p.pk == prof_id]
        if filtrados:
            return filtrados
    return profissionais


MAX_CARRINHO_BYTES = 600


def _reservas_do_pedido(request) -> list[tuple]:
    """``?carrinho=[{"p": procedimento_id, "f": profissional_id, "i": "<ISO com fuso>"}]`` — os outros
    tratamentos ja escolhidos no mesmo pedido -> [(profissional_id, inicio, fim, buffer)].

    So filtra a lista oferecida (o POST valida o conjunto de novo): item malformado e ignorado.
    """
    bruto = request.GET.get('carrinho', '')
    if not bruto or len(bruto) > MAX_CARRINHO_BYTES:
        return []
    try:
        itens = json.loads(bruto)
    except ValueError:
        return []
    if not isinstance(itens, list):
        return []
    reservas = []
    for item in itens[:MAX_TRATAMENTOS_POR_PEDIDO]:
        if not isinstance(item, dict):
            continue
        proc_id, prof_id = id_int(item.get('p')), id_int(item.get('f'))
        try:
            inicio = datetime.fromisoformat(str(item.get('i') or ''))
        except ValueError:
            continue
        procedimento = Procedimento.objects.filter(pk=proc_id, ativo=True).first() if proc_id is not None else None
        if procedimento is None or prof_id is None or timezone.is_naive(inicio):
            continue
        reservas.append((
            prof_id, inicio, inicio + timedelta(minutes=procedimento.duracao_minutos or 30),
            timedelta(minutes=procedimento.buffer_minutos or 0),
        ))
    return reservas


@ratelimit(key='ip', rate='60/m', method='GET', block=True)
def api_horarios_disponiveis(request):
    """AJAX: horarios livres (hora local) de uma data p/ um procedimento.

    GET: data (YYYY-MM-DD), procedimento_id, profissional_id (opcional), carrinho (opcional).
    """
    data_str = request.GET.get('data', '')
    if not data_str or not request.GET.get('procedimento_id'):
        return json_erro('parametros_ausentes', MSG_PARAMETROS)
    try:
        dia = ler_data(data_str)
    except ValueError:
        return json_erro('data_invalida', 'Data inválida')

    procedimento, erro = _procedimento_ativo(request.GET.get('procedimento_id'))
    if erro:
        return erro
    return json_ok(
        data=data_str,
        procedimento=procedimento.nome,
        duracao=procedimento.duracao_minutos,
        horarios=horarios_agrupados(procedimento, _profissionais(request, procedimento), dia,
                                    reservas=_reservas_do_pedido(request)),
    )


@ratelimit(key='ip', rate='60/m', method='GET', block=True)
def api_dias_disponiveis(request):
    """AJAX: dias do mes com expediente p/ o procedimento (de hoje ate o max_advance).

    GET: mes (YYYY-MM), procedimento_id, profissional_id (opcional).
    """
    mes_str = request.GET.get('mes', '')
    if not mes_str or not request.GET.get('procedimento_id'):
        return json_erro('parametros_ausentes', 'Parâmetros obrigatórios: mes, procedimento_id')
    try:
        primeiro = ler_mes(mes_str)
        ultimo = (date(primeiro.year + 1, 1, 1) if primeiro.month == 12
                  else date(primeiro.year, primeiro.month + 1, 1)) - timedelta(days=1)
    except (ValueError, OverflowError):
        return json_erro('mes_invalido', 'Mês inválido')

    procedimento, erro = _procedimento_ativo(request.GET.get('procedimento_id'))
    if erro:
        return erro
    dias = dias_com_expediente(_profissionais(request, procedimento), primeiro, ultimo)
    return json_ok(mes=mes_str, dias_disponiveis=[dia.isoformat() for dia in dias])
