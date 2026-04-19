"""Servico de LGPD — DSAR (export), unsubscribe, retencao."""
import logging
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from app_shivazen.models import Cliente, Atendimento, AvaliacaoNPS

logger = logging.getLogger(__name__)


class LgpdService:
    RETENCAO_CLIENTE_INATIVO_DIAS = 365 * 2  # 2 anos
    RETENCAO_LOG_AUDITORIA_DIAS = 365

    @staticmethod
    def exportar_dados_cliente(cliente: Cliente) -> dict[str, Any]:
        """DSAR — exporta todos os dados do cliente em dicionario JSON-friendly."""
        atendimentos = (
            Atendimento.objects
            .filter(cliente=cliente)
            .select_related('procedimento', 'profissional')
            .order_by('-data_hora_inicio')
        )
        avaliacoes = AvaliacaoNPS.objects.filter(atendimento__cliente=cliente)

        return {
            'pessoal': {
                'nome_completo': cliente.nome_completo,
                'data_nascimento': cliente.data_nascimento.isoformat() if cliente.data_nascimento else None,
                'cpf': cliente.cpf,
                'rg': cliente.rg,
                'email': cliente.email,
                'telefone': cliente.telefone,
                'cep': cliente.cep,
                'endereco': cliente.endereco,
                'profissao': cliente.profissao,
                'criado_em': cliente.criado_em.isoformat() if cliente.criado_em else None,
            },
            'preferencias': {
                'aceita_comunicacao': cliente.aceita_comunicacao,
                'bloqueado_online': cliente.bloqueado_online,
            },
            'atendimentos': [
                {
                    'data': a.data_hora_inicio.isoformat(),
                    'procedimento': a.procedimento.nome if a.procedimento_id else None,
                    'profissional': a.profissional.nome if a.profissional_id else None,
                    'status': a.status,
                    'valor_cobrado': str(a.valor_cobrado) if a.valor_cobrado else None,
                } for a in atendimentos
            ],
            'avaliacoes_nps': [
                {'nota': a.nota, 'comentario': a.comentario, 'respondida_em': a.respondida_em.isoformat() if a.respondida_em else None}
                for a in avaliacoes
            ],
        }

    @staticmethod
    def unsubscribe_por_token(token: str) -> Cliente | None:
        """Marca cliente como nao-comunicavel via token publico.

        Limpa tanto consent legado (aceita_comunicacao) quanto granulares
        (consent_email_marketing, consent_whatsapp_nps) — o opt-out global
        aplicado por clique unico precisa cobrir todos os canais ativos.
        """
        try:
            cliente = Cliente.objects.get(unsubscribe_token=token)
        except Cliente.DoesNotExist:
            return None
        cliente.aceita_comunicacao = False
        cliente.consent_email_marketing = False
        cliente.consent_whatsapp_nps = False
        cliente.save(update_fields=[
            'aceita_comunicacao', 'consent_email_marketing',
            'consent_whatsapp_nps', 'atualizado_em',
        ])
        logger.info('Cliente %s opt-out via token.', cliente.pk)
        return cliente

    @classmethod
    @transaction.atomic
    def esquecer_cliente(cls, cliente: Cliente) -> None:
        """Direito ao esquecimento: anonimiza dados e soft-delete."""
        cliente.nome_completo = f'[ANONIMIZADO-{cliente.pk}]'
        cliente.cpf = None
        cliente.rg = None
        cliente.email = None
        cliente.telefone = None
        cliente.cep = None
        cliente.endereco = None
        cliente.profissao = None
        cliente.data_nascimento = None
        cliente.aceita_comunicacao = False
        cliente.soft_delete()
        logger.info('Cliente %s anonimizado (direito ao esquecimento).', cliente.pk)

    @classmethod
    def purgar_inativos(cls) -> int:
        """Anonimiza clientes sem atendimentos ha mais de N dias."""
        limite = timezone.now() - timedelta(days=cls.RETENCAO_CLIENTE_INATIVO_DIAS)
        candidatos = Cliente.objects.filter(
            criado_em__lt=limite,
            deletado_em__isnull=True,
        ).exclude(atendimento__data_hora_inicio__gte=limite)

        count = 0
        for cliente in candidatos.iterator():
            cls.esquecer_cliente(cliente)
            count += 1
        return count
