from django.core.cache import cache
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import Atendimento, CompraPacote, ConsumoSessao, Configuracao
from .tasks import job_notificar_fila_espera
import logging

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete], sender=Configuracao)
def invalidar_cache_branding(sender, instance, **kwargs):
    """Zera cache de branding quando Configuracao muda.

    Chaves cacheadas (manter sincronizadas com utils/cache.py + admin_branding.py):
    """
    cache.delete('branding_config_v1')   # legado
    cache.delete('branding_config_dict')  # novo (admin_branding._get_config_dict)

@receiver(pre_save, sender=Atendimento)
def capturar_status_anterior(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Atendimento.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Atendimento.DoesNotExist:
            logger.warning('capturar_status_anterior: Atendimento pk=%s nao encontrado no pre_save', instance.pk)
            instance._old_status = None
    else:
        instance._old_status = None

@receiver(post_save, sender=Atendimento)
def processar_mudanca_status(sender, instance, created, **kwargs):
    status_atual = instance.status
    status_anterior = getattr(instance, '_old_status', None)

    if created:
        # Push direto p/ profissional
        try:
            user = getattr(instance.profissional, 'usuario', None)
            if user:
                from .services.push import send_push_to_user
                cliente_nome = instance.cliente.nome_completo if instance.cliente_id else 'Paciente'
                proc_nome = instance.procedimento.nome if instance.procedimento_id else 'Atendimento'
                data_fmt = instance.data_hora_inicio.strftime('%d/%m %H:%M')
                send_push_to_user(user, {
                    'head': 'Novo agendamento',
                    'body': f'{cliente_nome} - {proc_nome} em {data_fmt}',
                    'url': '/painel/calendario/',
                })
        except Exception as e:
            logger.exception('push profissional falhou: %s', e)

        # Sync outbound p/ Google Calendar (gracioso)
        try:
            from .services.gcal import push_atendimento, gcal_disponivel
            if gcal_disponivel() and instance.profissional.gcal_refresh_token:
                push_atendimento(instance)
        except Exception as e:
            logger.exception('gcal push falhou: %s', e)

    if status_atual == status_anterior:
        return

    # REGRA: FILA DE ESPERA — cancelamento, falta ou reagendamento libera vaga
    if status_atual in ['CANCELADO', 'FALTOU', 'REAGENDADO'] and status_anterior in ['PENDENTE', 'AGENDADO', 'CONFIRMADO']:
        job_notificar_fila_espera.delay(
            procedimento_id=instance.procedimento.pk,
            data_livre_str=instance.data_hora_inicio.isoformat()
        )

    # REGRA: REGISTRO DE FALTA — 3-strike system
    if status_atual == 'FALTOU' and status_anterior in ['PENDENTE', 'AGENDADO', 'CONFIRMADO']:
        instance.cliente.registrar_falta()
        logger.info(f"[FALTA] Cliente {instance.cliente.pk} — faltas: {instance.cliente.faltas_consecutivas}")

    # REGRA: REALIZADO — resetar faltas + debitar pacote
    if status_atual == 'REALIZADO':
        # Reset faltas consecutivas
        instance.cliente.resetar_faltas()

        # Debitar sessao de pacote
        if not hasattr(instance, 'sessao_pacote_vinculada'):
            pacotes_ativos = CompraPacote.objects.filter(
                cliente=instance.cliente,
                status='ATIVO'
            ).order_by('criado_em')

            for pc in pacotes_ativos:
                # Verificar validade
                if pc.data_expiracao:
                    from django.utils import timezone
                    if pc.data_expiracao < timezone.now().date():
                        pc.status = 'EXPIRADO'
                        pc.save()
                        continue

                itens = pc.pacote.itens.filter(procedimento=instance.procedimento)
                if itens.exists():
                    item = itens.first()
                    sessoes_ja_feitas = pc.sessoes_realizadas.filter(
                        atendimento__procedimento=instance.procedimento
                    ).count()
                    if sessoes_ja_feitas < item.quantidade_sessoes:
                        ConsumoSessao.objects.create(
                            compra_pacote=pc,
                            atendimento=instance
                        )
                        logger.info(f"[PACOTE] Sessao {sessoes_ja_feitas + 1}/{item.quantidade_sessoes} debitada do pacote {pc.pk}")

                        # Verificar se pacote foi finalizado
                        pc.verificar_finalizacao()
                        break
