"""UI de branding — edita nome, cores, contatos e logo sem redeploy.

Persiste em ConfiguracaoSistema (chave-valor). O context_processor
`clinica_globals` precisa ser ajustado para ler DO banco com fallback env var.
"""
import os

from django.conf import settings
from django.contrib import messages
from django.core.files.storage import default_storage
from django.shortcuts import redirect, render

from ..decorators import staff_required
from ..models import ConfiguracaoSistema
from ..utils.audit import registrar_log


BRANDING_FIELDS = [
    ('CLINIC_NAME', 'Nome da clínica', 'text'),
    ('CLINIC_SUBTITLE', 'Subtítulo', 'text'),
    ('CLINIC_EMAIL', 'E-mail de contato', 'email'),
    ('CLINIC_PHONE', 'Telefone exibido', 'text'),
    ('CLINIC_ADDRESS', 'Endereço', 'text'),
    ('WHATSAPP_NUMERO', 'WhatsApp (só dígitos, ex: 5517...)', 'text'),
    ('INSTAGRAM_URL', 'URL do Instagram', 'url'),
    ('SITE_URL', 'URL pública do site', 'url'),
    ('THEME_COLOR_PRIMARY', 'Cor primária (hex ex #8b6f47)', 'color'),
    ('THEME_COLOR_ACCENT', 'Cor secundária/accent', 'color'),
    ('THEME_COLOR_DARK', 'Cor escura (topbar/rodapé)', 'color'),
]

LOGO_UPLOAD_DIR = 'branding'


def _get_config_dict():
    return {c.chave: c.valor for c in ConfiguracaoSistema.objects.all()}


@staff_required
def admin_branding(request):
    """Edita todos campos de branding em uma unica tela."""
    configs_dict = _get_config_dict()

    if request.method == 'POST':
        alterados = []
        for chave, _label, _tipo in BRANDING_FIELDS:
            novo_valor = request.POST.get(chave, '').strip()
            atual = configs_dict.get(chave, '')
            if novo_valor != atual:
                config, _ = ConfiguracaoSistema.objects.get_or_create(
                    chave=chave, defaults={'valor': novo_valor}
                )
                config.valor = novo_valor
                config.save()
                alterados.append(chave)

        logo_file = request.FILES.get('logo')
        if logo_file:
            ext = os.path.splitext(logo_file.name)[1].lower()
            if ext not in ('.png', '.jpg', '.jpeg', '.svg', '.webp'):
                messages.error(request, 'Formato de logo invalido. Use PNG, JPG, SVG ou WebP.')
            elif logo_file.size > 5 * 1024 * 1024:
                messages.error(request, 'Logo deve ter no maximo 5MB.')
            else:
                destino = os.path.join(LOGO_UPLOAD_DIR, f'logo-clinica{ext}')
                if default_storage.exists(destino):
                    default_storage.delete(destino)
                nome_salvo = default_storage.save(destino, logo_file)
                url_logo = default_storage.url(nome_salvo)
                config, _ = ConfiguracaoSistema.objects.get_or_create(
                    chave='LOGO_URL', defaults={'valor': url_logo}
                )
                config.valor = url_logo
                config.save()
                alterados.append('LOGO_URL')

        if alterados:
            registrar_log(
                request.user, 'Atualizou branding',
                'configuracao_sistema', None,
                detalhes={'chaves': alterados},
            )
            messages.success(
                request,
                f'Branding atualizado ({len(alterados)} campo(s)). '
                'Recarregue as paginas publicas para ver mudancas.'
            )
        else:
            messages.info(request, 'Nenhuma alteracao detectada.')

        return redirect('aranha:admin_branding')

    campos = []
    for chave, label, tipo in BRANDING_FIELDS:
        valor_atual = configs_dict.get(chave, '') or os.environ.get(chave, '')
        campos.append({
            'chave': chave, 'label': label, 'tipo': tipo, 'valor': valor_atual,
        })

    logo_url = configs_dict.get('LOGO_URL', '')

    context = {
        'campos': campos,
        'logo_url': logo_url,
    }
    return render(request, 'painel/branding.html', context)
