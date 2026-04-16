from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import Atendimento, PacoteCliente, SessaoPacote, ListaEspera
from .tasks import job_notificar_fila_espera
import logging

logger = logging.getLogger(__name__)

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

    if status_atual == status_anterior:
        return

    # REGRA: FILA DE ESPERA — cancelamento, falta ou reagendamento libera vaga
    if status_atual in ['CANCELADO', 'FALTOU', 'REAGENDADO'] and status_anterior in ['AGENDADO', 'CONFIRMADO']:
        job_notificar_fila_espera.delay(
            procedimento_id=instance.procedimento.pk,
            data_livre_str=instance.data_hora_inicio.isoformat()
        )

    # REGRA: REGISTRO DE FALTA — 3-strike system
    if status_atual == 'FALTOU' and status_anterior in ['AGENDADO', 'CONFIRMADO']:
        instance.cliente.registrar_falta()
        logger.info(f"[FALTA] Cliente {instance.cliente.pk} — faltas: {instance.cliente.faltas_consecutivas}")

    # REGRA: REALIZADO — resetar faltas + debitar pacote
    if status_atual == 'REALIZADO':
        # Reset faltas consecutivas
        instance.cliente.resetar_faltas()

        # Debitar sessao de pacote
        if not hasattr(instance, 'sessao_pacote_vinculada'):
            pacotes_ativos = PacoteCliente.objects.filter(
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
                        SessaoPacote.objects.create(
                            pacote_cliente=pc,
                            atendimento=instance
                        )
                        logger.info(f"[PACOTE] Sessao {sessoes_ja_feitas + 1}/{item.quantidade_sessoes} debitada do pacote {pc.pk}")

                        # Verificar se pacote foi finalizado
                        pc.verificar_finalizacao()
                        break
