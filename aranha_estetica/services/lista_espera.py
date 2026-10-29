"""Lista de Espera — inscricao e aviso de vaga (decisao 17).

- ``inscrever``: o formulario publico e anonimo e NAO cria Cliente. A inscricao guarda nome e
  telefone de contato; so se liga ao cadastro (e so aceita e-mail de contato) quando o celular foi
  verificado por OTP — senao qualquer pessoa prenderia o proprio e-mail ao telefone de terceiros.
- ``candidatos`` / ``notificar_compativeis``: quando um atendimento ativo vira CANCELADO/REAGENDADO
  (services/atendimento.ao_mudar_status), avisa so quem pode usar a vaga — mesmo procedimento e dia,
  profissional desejada (ou qualquer), turno pedido (MANHA antes das 12h, TARDE das 12h as 18h,
  NOITE a partir das 18h) e fora da antecedencia minima da profissional (vaga que o site nao deixa
  agendar nao e avisada). E-mail/WhatsApp so saem no on_commit.
- O aviso grava ``ultimo_aviso_em`` e NAO tira da fila: quem nao conseguiu a vaga continua
  esperando ate a data passar (retencao) ou ate agendar (``encerrar_atendidas``, chamado em
  services/atendimento.ao_criar).
- ``avisar_manual``: o botao "Avisar" do painel (mesma regra de privacidade e envio).
Inscricao anonima nao tem canal automatico (sem e-mail, sem consentimento de WhatsApp): a recepcao
ve nome e celular no painel e faz o contato.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from ..models import Atendimento, Cliente, ListaEspera
from ..utils.datas import data_local, fmt_local

logger = logging.getLogger(__name__)

# Turno pedido -> [inicio, fim) do horario local da vaga
LIMITES_TURNO = {
    ListaEspera.TURNO_MANHA: (time(0, 0), time(12, 0)),
    ListaEspera.TURNO_TARDE: (time(12, 0), time(18, 0)),
    ListaEspera.TURNO_NOITE: (time(18, 0), None),
}


def turno_de(inicio: datetime) -> str:
    """Turno (MANHA/TARDE/NOITE) do horario local de ``inicio``."""
    hora = timezone.localtime(inicio).time()
    for turno, (de, ate) in LIMITES_TURNO.items():
        if de <= hora and (ate is None or hora < ate):
            return turno
    return ListaEspera.TURNO_NOITE


@transaction.atomic
def inscrever(*, nome: str, telefone: str, email: str | None, procedimento, profissional,
              data_desejada: date, turno: str | None, telefone_verificado: bool) -> ListaEspera:
    """Inscreve na fila (idempotente por pessoa + procedimento + dia). Nunca cria nem altera Cliente.

    telefone_verificado: o celular foi confirmado por SMS nesta sessao (OTP do wizard). So entao a
    inscricao se liga ao cadastro desse celular e aceita o e-mail de contato.
    """
    cliente = Cliente.objects.filter(telefone=telefone).first() if telefone_verificado else None
    chave: dict[str, object]
    campos: dict[str, object]
    if cliente is not None:
        chave = {'cliente': cliente}
        campos = {'email_contato': (email or None)}
    else:
        chave = {'telefone_contato': telefone}
        campos = {'nome_contato': nome[:150]}
    existente = ListaEspera.objects.filter(
        procedimento=procedimento, data_desejada=data_desejada, **chave,
    ).first()
    if existente is not None:
        return existente
    try:
        with transaction.atomic():
            return ListaEspera.objects.create(
                procedimento=procedimento, profissional_desejado=profissional, data_desejada=data_desejada,
                turno_desejado=turno or None, **chave, **campos,
            )
    except IntegrityError:   # corrida com a mesma inscricao (UNIQUE): fica a que entrou primeiro
        return ListaEspera.objects.get(procedimento=procedimento, data_desejada=data_desejada, **chave)


def _vaga_agendavel_online(slot: Atendimento) -> bool:
    """O site so oferece horario fora da antecedencia minima da profissional: avisar antes disso
    mandaria a cliente para uma vaga que ela nao consegue marcar."""
    antecedencia = timedelta(hours=getattr(slot.profissional, 'min_notice_horas', 0) or 0)
    return slot.data_hora_inicio - timezone.now() >= antecedencia


def candidatos(atendimento_liberado: Atendimento) -> list[ListaEspera]:
    """Inscricoes compativeis com o horario liberado (FIFO) — so quem pode usar a vaga."""
    slot_data = data_local(atendimento_liberado.data_hora_inicio)
    if slot_data is None or not _vaga_agendavel_online(atendimento_liberado):
        return []
    turno = turno_de(atendimento_liberado.data_hora_inicio)
    return list(
        ListaEspera.objects.select_related('cliente', 'procedimento', 'profissional_desejado')
        .filter(procedimento_id=atendimento_liberado.procedimento_id, data_desejada=slot_data)
        .filter(Q(profissional_desejado__isnull=True)
                | Q(profissional_desejado_id=atendimento_liberado.profissional_id))
        .filter(Q(turno_desejado__isnull=True) | Q(turno_desejado=turno))
        .order_by('criado_em', 'pk')
    )


def notificar_compativeis(atendimento_liberado: Atendimento) -> int:
    """Agenda (on_commit) o aviso aos compativeis. Retorna quantos foram agendados.

    Vaga no passado (ex.: limpeza automatica de pendentes vencidos) nao gera aviso.
    """
    if atendimento_liberado.data_hora_inicio <= timezone.now():
        return 0
    esperas = candidatos(atendimento_liberado)
    if not esperas:
        return 0
    ids = [e.pk for e in esperas]
    atendimento_id = atendimento_liberado.pk
    transaction.on_commit(lambda: _disparar_avisos(ids, atendimento_id))
    logger.info('lista_espera_avisos_agendados', extra={'atendimento_id': atendimento_id, 'count': len(ids)})
    return len(ids)


def encerrar_atendidas(at: Atendimento) -> int:
    """A cliente agendou o procedimento: as inscricoes dela p/ ele saem da fila (fim da finalidade).

    Pelo cadastro e pelo telefone da inscricao anonima (o mesmo celular). Devolve quantas sairam.
    """
    filtro = Q(cliente_id=at.cliente_id)
    telefone = getattr(at.cliente, 'telefone', None)
    if telefone:
        filtro |= Q(telefone_contato=telefone)
    apagadas, _ = (
        ListaEspera.objects.filter(filtro, procedimento_id=at.procedimento_id,
                                   data_desejada__gte=data_local(timezone.now()))
        .delete()
    )
    if apagadas:
        logger.info('lista_espera_encerrada_ao_agendar', extra={'atendimento_id': at.pk, 'count': apagadas})
    return apagadas


def link_agendamento(procedimento_id: int, profissional_id: int | None = None, dia: date | None = None) -> str:
    """Wizard ja aberto no procedimento, na profissional e no dia da vaga."""
    params: dict[str, object] = {'procedimento': procedimento_id}
    if profissional_id:
        params['profissional'] = profissional_id
    if dia:
        params['data'] = dia.isoformat()
    return f"{settings.SITE_URL.rstrip('/')}{reverse('aranha:agendamento_publico')}?{urlencode(params)}"


def _marcar_aviso(espera_id: int) -> int:
    return ListaEspera.objects.filter(pk=espera_id).update(ultimo_aviso_em=timezone.now())


def _disparar_avisos(espera_ids, atendimento_id) -> int:
    """Pos-commit: envia e-mail e WhatsApp; grava ultimo_aviso_em so com entrega (fica na fila)."""
    try:
        slot = Atendimento.objects.select_related('procedimento').get(pk=atendimento_id)
    except Atendimento.DoesNotExist:
        return 0
    link = link_agendamento(slot.procedimento_id, slot.profissional_id, data_local(slot.data_hora_inicio))
    entregues = 0
    for espera in (ListaEspera.objects.select_related('cliente', 'procedimento')
                   .filter(pk__in=espera_ids, cliente__isnull=False).order_by('criado_em', 'pk')):
        ok_email = _enviar_email_lista_espera(espera, slot, link)
        ok_wa = _enviar_wa_lista_espera(espera, slot, link)
        if ok_email or ok_wa:
            entregues += _marcar_aviso(espera.pk)
    logger.info('lista_espera_notificados', extra={'atendimento_id': atendimento_id, 'count': entregues})
    return entregues


def _nome_para(espera: ListaEspera, destino: str) -> str:
    """Nome do cadastro so quando o aviso vai p/ o e-mail DO cadastro.

    email_contato vem do formulario publico: o nome cadastrado da titular so vai para o e-mail que
    ela mesma cadastrou (aviso manual e automatico seguem a mesma regra).
    """
    cliente = espera.cliente
    if cliente is None:
        return ''
    cadastrado = (cliente.email or '').strip().lower()
    if cadastrado and (destino or '').strip().lower() == cadastrado:
        return cliente.nome or ''
    return ''


def _enviar_email_lista_espera(espera: ListaEspera, slot: Atendimento | None, link: str) -> bool:
    """E-mail de vaga (a cliente pediu o aviso ao entrar na lista). slot=None: aviso manual."""
    destino = espera.email_aviso
    if not destino:
        return False
    if slot is not None:
        data, hora = fmt_local(slot.data_hora_inicio, '%d/%m/%Y'), fmt_local(slot.data_hora_inicio, '%H:%M')
        procedimento = slot.procedimento.nome
    else:
        data, hora, procedimento = espera.data_desejada.strftime('%d/%m/%Y'), '', espera.procedimento.nome
    try:
        from ..utils.email import enviar_fila_espera_email
        return bool(enviar_fila_espera_email(destino, {
            'nome': _nome_para(espera, destino), 'procedimento': procedimento,
            'data': data, 'hora': hora, 'link': link,
        }))
    except Exception as exc:  # noqa: BLE001 — best-effort por destinatario
        logger.warning('lista_espera_email_falha', extra={'espera_id': espera.pk, 'erro': type(exc).__name__})
        return False


def _enviar_wa_lista_espera(espera: ListaEspera, slot: Atendimento, link: str) -> bool:
    """WhatsApp template de vaga (requer cadastro com telefone + consentimento de lembrete)."""
    from . import consentimentos

    cliente = espera.cliente
    if cliente is None or not (
        cliente.telefone and consentimentos.vigente(cliente, *consentimentos.LEMBRETE_WHATSAPP)
    ):
        return False
    try:
        from ..utils.whatsapp import enviar_template
        components = [{
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': cliente.nome},
                {'type': 'text', 'text': slot.procedimento.nome},
                {'type': 'text', 'text': fmt_local(slot.data_hora_inicio, '%d/%m %H:%M')},
                {'type': 'text', 'text': link},
            ],
        }]
        return bool(enviar_template(
            cliente.telefone, settings.WHATSAPP_TEMPLATE_LISTA_ESPERA, components=components,
        ))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning('lista_espera_wa_falha', extra={'espera_id': espera.pk, 'erro': type(exc).__name__})
        return False


def avisar_manual(espera: ListaEspera) -> bool:
    """Botao "Avisar" do painel: e-mail quando ha destino (mesma regra do automatico) e o aviso fica
    registrado (ultimo_aviso_em) mesmo sem e-mail — a recepcao completa pelo WhatsApp/telefone.
    True = o e-mail saiu."""
    link = link_agendamento(espera.procedimento_id, espera.profissional_desejado_id, espera.data_desejada)
    enviado = _enviar_email_lista_espera(espera, None, link)
    _marcar_aviso(espera.pk)
    espera.ultimo_aviso_em = timezone.now()
    return enviado
