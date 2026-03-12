from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import Atendimento, PacoteCliente, SessaoPacote, ListaEspera
from .tasks import job_notificar_fila_espera
import logging

logger = logging.getLogger(__name__)

@receiver(pre_save, sender=Atendimento)
def capturar_status_anterior(sender, instance, **kwargs):
    """
    Captura o status do atendimento antes de salvar, para podermos
    comparar se houve mudança no post_save.
    """
    if instance.pk:
        try:
            old_instance = Atendimento.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status_atendimento
        except Atendimento.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

@receiver(post_save, sender=Atendimento)
def processar_mudanca_status(sender, instance, created, **kwargs):
    """
    Ao mudar o status do atendimento, verifica regras de negócio:
    1. Se foi CANCELADO: Dispara Notificação para a Fila de Espera.
    2. Se foi REALIZADO: Verifica se o cliente tem um Pacote Ativo e desconta sessão.
    """
    status_atual = instance.status_atendimento
    status_anterior = getattr(instance, '_old_status', None)

    if status_atual == status_anterior:
        return # Nada mudou

    # REGRA 1: FILA DE ESPERA
    if status_atual in ['CANCELADO', 'FALTOU'] and status_anterior in ['AGENDADO', 'CONFIRMADO']:
        job_notificar_fila_espera.delay(
            procedimento_id=instance.procedimento.pk,
            data_livre_str=instance.data_hora_inicio.isoformat()
        )

    # REGRA 2: PACOTES
    if status_atual == 'REALIZADO':
        # Checar se já existe sessão debitada pra esse atendimento para não cobrar 2x
        if not hasattr(instance, 'sessao_pacote_vinculada'):
            # Buscar pacotes ativos do cliente
            pacotes_ativos = PacoteCliente.objects.filter(
                cliente=instance.cliente,
                status='ATIVO'
            )
            for pc in pacotes_ativos:
                # Checar se o pacote tem o procedimento do atendimento
                itens = pc.pacote.itens.filter(procedimento=instance.procedimento)
                if itens.exists():
                    item = itens.first()
                    # Contar quantas sessoes ja foram feitas
                    sessoes_ja_feitas = pc.sessoes_realizadas.filter(atendimento__procedimento=instance.procedimento).count()
                    if sessoes_ja_feitas < item.quantidade_sessoes:
                        # Ainda tem crédito! Debitar.
                        SessaoPacote.objects.create(
                            pacote_cliente=pc,
                            atendimento=instance
                        )
                        logger.info(f"[PACOTE] Sessão {sessoes_ja_feitas + 1}/{item.quantidade_sessoes} debitada do pacote {pc.pk} para Atendimento {instance.pk}")
                        
                        # Se gastou a última sessão deste e de todos os itens, podemos finalizar o pacote (Lógica simplificada)
                        # Aqui você poderia adicionar uma checagem mais complexa se o pacote tiver multiplos procedimentos.
                        
                        break # Se debitou de um pacote, não debita do outro
