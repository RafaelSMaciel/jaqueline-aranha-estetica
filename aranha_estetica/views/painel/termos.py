"""Termos de consentimento: versoes no painel, link de assinatura e a pagina publica de aceite."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...forms.painel import TermoForm, avisar_erros
from ...models import Atendimento, Procedimento, VersaoTermo
from ...services.termos import aceita_assinatura, obter_link_termo, termos_pendentes
from ...utils.auditoria import registrar_log
from ...validators import normalizar_telefone
from .comum import voltar_para_origem as _voltar

logger = logging.getLogger(__name__)


@staff_required
def admin_termos(request):
    """Lista e gerencia versoes de termos."""
    termos = VersaoTermo.objects.select_related('procedimento').order_by('-ativa', '-vigente_desde')

    context = {
        'termos': termos,
        'procedimentos': Procedimento.objects.filter(ativo=True),
    }
    return render(request, 'painel/termos.html', context)


@staff_required
def admin_criar_termo(request):
    """Publica nova versao de termo.

    So 1 versao ativa por escopo (uq_versao_termo_ativa_*): a vigente do mesmo
    tipo/procedimento e arquivada na mesma transacao. Clientes que aceitaram
    a versao anterior passam a ter o termo novo pendente (versionamento).
    """
    if request.method != 'POST':
        return redirect('aranha:admin_termos')

    form = TermoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return redirect('aranha:admin_termos')
    d = form.cleaned_data
    tipo, procedimento, versao = d['tipo'], d['procedimento'], d['versao']

    try:
        with transaction.atomic():
            # filter(procedimento=None) vira IS NULL — escopo global do tipo.
            # LGPD e sempre global: arquiva tambem LGPD legada presa a procedimento.
            vigentes = VersaoTermo.objects.filter(tipo=tipo, ativa=True)
            if tipo == 'PROCEDIMENTO':
                vigentes = vigentes.filter(procedimento=procedimento)
            arquivadas = vigentes.update(ativa=False)
            novo = VersaoTermo.objects.create(
                tipo=tipo, procedimento=procedimento, titulo=d['titulo'], conteudo=d['conteudo'],
                versao=versao, vigente_desde=d['vigente_desde'], ativa=True,
            )
    except (IntegrityError, DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao publicar termo: {e}', exc_info=True)
        messages.error(request, 'Não foi possível publicar o termo. Tente novamente.')
        return redirect('aranha:admin_termos')

    registrar_log(
        request.user, f'Publicou termo {tipo} v{versao}', 'versao_termo', novo.pk,
        {'arquivadas': arquivadas}, request=request,
    )
    if arquivadas:
        messages.success(request, 'Nova versão publicada; a versão anterior foi arquivada.')
    else:
        messages.success(request, 'Termo publicado!')
    return redirect('aranha:admin_termos')


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_gerar_link_termo(request, pk):
    """Gera um link NOVO do termo do atendimento p/ a recepcao enviar.

    So o hash do token fica no banco: o link nao pode ser reexibido, entao
    cada clique emite outro; os anteriores seguem validos ate o fim do
    atendimento (D1, spec padrao-sql §7.1). A tela mostra o link p/ copiar,
    WhatsApp/e-mail ja com o link no texto e "abrir aqui" (aceite no balcao,
    no tablet da clinica).
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'), pk=pk,
    )
    if not aceita_assinatura(atendimento):
        messages.error(
            request,
            'Este atendimento não aceita mais termo (cancelado, concluído ou horário já encerrado).',
        )
        return _voltar(request)
    pendentes = termos_pendentes(atendimento.cliente, atendimento.procedimento)
    if not pendentes:
        messages.info(request, f'{atendimento.cliente.nome} já aceitou todos os termos deste atendimento.')
        return _voltar(request)

    notif, link, criada = obter_link_termo(atendimento, request.POST.get('canal', 'WHATSAPP'))
    registrar_log(
        request.user, 'Gerou link do termo', 'atendimento', atendimento.pk,
        {'notificacao': notif.pk, 'notificacao_nova': criada, 'versoes': [t.pk for t in pendentes]},
        request=request,
    )
    return render(request, 'painel/termo_link.html', {
        'atendimento': atendimento,
        'cliente': atendimento.cliente,
        'link': link,
        'pendentes': pendentes,
        'telefone_wa': normalizar_telefone(atendimento.cliente.telefone or ''),
    })
