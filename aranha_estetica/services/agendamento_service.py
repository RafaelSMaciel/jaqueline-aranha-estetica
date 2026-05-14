"""Application Service de agendamento.

Encapsula transacao + regras de negocio + emissao de eventos para criar
um Atendimento. Substitui logica que estava espalhada em views/booking.py.

View deve apenas:
1. Receber HTTP
2. Construir CriarAgendamentoCommand
3. Chamar AgendamentoService().criar(cmd)
4. Tratar excecoes do dominio (DomainError) → status apropriado
5. Renderizar resposta

Migracao incremental: novos fluxos usam este service. Views legadas
continuam ate refactor completo.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..domain.event_bus import EventBus
from ..domain.events import AtendimentoCriado, ConsentRegistrado
from ..exceptions import (
    BusinessRuleViolation,
    ClienteBloqueadoError,
    ResourceNotFound,
    ValidationError,
)
from ..models import (
    AceitePrivacidade,
    Atendimento,
    Cliente,
    Procedimento,
    Profissional,
    VersaoTermo,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


# ─── DTO / Command ────────────────────────────────────────────────────
@dataclass
class CriarAgendamentoCommand:
    """Input imutavel p/ criacao de agendamento.

    Validacao de tipos basica via dataclass. Validacao semantica
    acontece em AgendamentoService.criar().
    """
    profissional_id: int
    procedimento_id: int
    data_hora_inicio: datetime
    nome_completo: str
    email: Optional[str] = None
    telefone: Optional[str] = None
    cpf: Optional[str] = None
    consents: dict = field(default_factory=dict)
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    aceite_lgpd: bool = False


# ─── Service ──────────────────────────────────────────────────────────
class AgendamentoService:
    """Application Service para criar e cancelar agendamentos."""

    @transaction.atomic
    def criar(self, cmd: CriarAgendamentoCommand) -> Atendimento:
        """Cria atendimento, registra consents + audit, publica evento.

        Raises:
            ValidationError: input invalido (LGPD nao aceito, dados faltantes)
            ResourceNotFound: profissional/procedimento inexistente
            ClienteBloqueadoError: cliente com 3+ no-shows
            BusinessRuleViolation: slot ocupado, data invalida
        """
        if not cmd.aceite_lgpd:
            raise ValidationError('Aceite LGPD obrigatorio.')

        if not cmd.nome_completo or len(cmd.nome_completo.strip()) < 3:
            raise ValidationError('Nome completo invalido.')

        # Resolve profissional + procedimento
        try:
            profissional = Profissional.objects.get(pk=cmd.profissional_id, ativo=True)
        except Profissional.DoesNotExist as exc:
            raise ResourceNotFound('Profissional nao encontrado ou inativo.') from exc

        try:
            procedimento = Procedimento.objects.get(pk=cmd.procedimento_id, ativo=True)
        except Procedimento.DoesNotExist as exc:
            raise ResourceNotFound('Procedimento nao encontrado ou inativo.') from exc

        # Upsert cliente (por email se houver, senao cria novo)
        cliente = self._upsert_cliente(cmd)

        if cliente.bloqueado_online:
            raise ClienteBloqueadoError(
                f'Cliente {cliente.nome_completo} bloqueado por excesso de no-shows.'
            )

        # Verifica conflito de slot (regra simplificada — slot calculator
        # completo continua em utils/precos.py; aqui apenas check basico)
        if self._slot_ocupado(profissional, cmd.data_hora_inicio, procedimento.duracao_minutos):
            raise BusinessRuleViolation('Horario indisponivel.')

        # Registra consents ANTES de persistir Atendimento — LGPD: audit
        # trail de consent precisa preceder processamento de dado pessoal.
        # Transaction.atomic garante rollback se Atendimento falhar depois.
        self._registrar_consents(cliente, cmd)

        # Cria atendimento
        from datetime import timedelta
        data_fim = cmd.data_hora_inicio + timedelta(minutes=procedimento.duracao_minutos)
        atendimento = Atendimento.objects.create(
            cliente=cliente,
            profissional=profissional,
            procedimento=procedimento,
            data_hora_inicio=cmd.data_hora_inicio,
            data_hora_fim=data_fim,
            status='PENDENTE',
        )

        # Audit + LGPD
        registrar_log(
            None,
            f'Atendimento criado via booking publico (cliente={cliente.pk})',
            'atendimento',
            atendimento.pk,
            ip=cmd.ip,
        )

        # Publica evento (handlers reagem fora da transacao via on_commit)
        transaction.on_commit(lambda: EventBus.publish(AtendimentoCriado(
            atendimento_id=atendimento.pk,
            cliente_id=cliente.pk,
            profissional_id=profissional.pk,
            procedimento_id=procedimento.pk,
            occurred_at=timezone.now(),
        )))

        logger.info(
            'agendamento_criado',
            extra={
                'atendimento_id': atendimento.pk,
                'cliente_id': cliente.pk,
                'profissional_id': profissional.pk,
            },
        )
        return atendimento

    @transaction.atomic
    def aprovar(self, atendimento: Atendimento, by_user=None) -> bool:
        """Aprova atendimento PENDENTE -> AGENDADO + dispara email confirmacao.

        Returns:
            True se transitou. False se ja estava em outro estado (no-op).

        Side effects:
            - Audit log via registrar_log
            - Email assincrono de confirmacao (Celery, fallback sync)
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False

        atendimento.aprovar(by_user=by_user)
        registrar_log(by_user, 'Aprovou agendamento', 'atendimento', atendimento.pk)

        if atendimento.cliente.email:
            data_fmt = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
            valor = atendimento.valor_cobrado
            dados = {
                'nome': atendimento.cliente.nome_completo,
                'procedimento': atendimento.procedimento.nome,
                'profissional': atendimento.profissional.nome,
                'data_hora': data_fmt,
                'valor': f'R$ {float(valor):.2f}' if valor else 'A consultar',
            }
            transaction.on_commit(
                lambda: self._enviar_email_confirmacao(atendimento.cliente.email, dados)
            )
        return True

    @transaction.atomic
    def rejeitar(self, atendimento: Atendimento, motivo: str = '', by_user=None) -> bool:
        """Rejeita atendimento PENDENTE -> CANCELADO.

        Returns:
            True se transitou. False se ja estava em outro estado.

        Side effects:
            - Audit log
            - Email assincrono de cancelamento (Celery, fallback sync)
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False
        atendimento.cancelar(motivo=motivo or 'rejeitado pela recepcao', by_user=by_user)
        registrar_log(by_user, 'Rejeitou agendamento', 'atendimento', atendimento.pk)

        if atendimento.cliente.email:
            data_fmt = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
            dados = {
                'nome': atendimento.cliente.nome_completo,
                'procedimento': atendimento.procedimento.nome,
                'profissional': atendimento.profissional.nome,
                'data_hora': data_fmt,
            }
            transaction.on_commit(
                lambda: self._enviar_email_cancelamento(atendimento.cliente.email, dados)
            )
        return True

    @staticmethod
    def _enviar_email_cancelamento(email: str, dados: dict) -> None:
        """Envia email de cancelamento via Celery (fallback sync)."""
        try:
            from ..tasks import send_email_async
            send_email_async.delay('enviar_cancelamento_email', email, dados)
        except (ImportError, AttributeError) as exc:
            logger.warning('email_celery_indisponivel_fallback_sync', extra={'error': str(exc)})
            from ..utils.email import enviar_cancelamento_email
            enviar_cancelamento_email(email, dados)

    @staticmethod
    def _enviar_email_confirmacao(email: str, dados: dict) -> None:
        """Envia email de confirmacao via Celery (fallback sync)."""
        try:
            from ..tasks import send_email_async
            send_email_async.delay('enviar_confirmacao_agendamento_email', email, dados)
        except (ImportError, AttributeError) as exc:
            logger.warning('email_celery_indisponivel_fallback_sync', extra={'error': str(exc)})
            from ..utils.email import enviar_confirmacao_agendamento_email
            enviar_confirmacao_agendamento_email(email, dados)

    # ─── Helpers ──────────────────────────────────────────────────────
    def _upsert_cliente(self, cmd: CriarAgendamentoCommand) -> Cliente:
        """Upsert cliente por email (se houver) ou cria novo."""
        cliente = None
        if cmd.email:
            cliente = Cliente.objects.filter(email__iexact=cmd.email).first()

        if cliente:
            # Atualiza campos opcionais se vazios
            if not cliente.telefone and cmd.telefone:
                cliente.telefone = cmd.telefone
            if not cliente.cpf and cmd.cpf:
                cliente.cpf = cmd.cpf
            cliente.save()
            return cliente

        return Cliente.objects.create(
            nome_completo=cmd.nome_completo.strip(),
            email=cmd.email,
            telefone=cmd.telefone,
            cpf=cmd.cpf,
        )

    def _slot_ocupado(self, profissional, data_inicio, duracao_min) -> bool:
        """Check basico de conflito (logica completa em utils/precos.py)."""
        from datetime import timedelta
        data_fim = data_inicio + timedelta(minutes=duracao_min)
        return Atendimento.objects.filter(
            profissional=profissional,
            data_hora_inicio__lt=data_fim,
            data_hora_fim__gt=data_inicio,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO'],
        ).exists()

    def _registrar_consents(self, cliente: Cliente, cmd: CriarAgendamentoCommand) -> None:
        """Registra AceitePrivacidade + atualiza flags granulares de consent."""
        # Aceite LGPD versionado
        versao = VersaoTermo.objects.filter(tipo='PRIVACIDADE', vigente=True).first()
        if versao:
            AceitePrivacidade.objects.create(
                cliente=cliente,
                versao=versao,
                ip=cmd.ip,
                user_agent=(cmd.user_agent or '')[:500],
            )

        # Consents granulares
        agora = timezone.now()
        canais = {
            'email_marketing': cmd.consents.get('email_marketing', False),
            'whatsapp_nps': cmd.consents.get('whatsapp_nps', False),
            'whatsapp_confirmacao': cmd.consents.get('whatsapp_confirmacao', True),
        }
        for canal, aceito in canais.items():
            if not aceito:
                continue
            setattr(cliente, f'consent_{canal}', True)
            setattr(cliente, f'consent_{canal}_at', agora)
            setattr(cliente, f'consent_{canal}_ip', cmd.ip)
            EventBus.publish(ConsentRegistrado(
                cliente_id=cliente.pk,
                canal=canal,
                aceito=True,
                ip=cmd.ip,
                occurred_at=agora,
            ))
        cliente.save()
