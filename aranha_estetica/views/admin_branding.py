"""UI de branding — edita nome, cores, contatos e logo sem redeploy.

Persiste em Configuracao (chave-valor). O context_processor
`clinica_globals` precisa ser ajustado para ler DO banco com fallback env var.
"""
import os

from django.contrib import messages
from django.core.files.storage import default_storage
from django.shortcuts import redirect, render

from ..decorators import staff_required
from ..models import Configuracao
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

CONFIG_CACHE_KEY = 'branding_config_dict'
CONFIG_CACHE_TTL = 600  # 10min

# Magic bytes (assinatura no inicio do arquivo) por formato raster aceito.
# Validar o conteudo real evita upload com extensao falsificada.
_LOGO_MAGIC = {
    '.png': [b'\x89PNG\r\n\x1a\n'],
    '.jpg': [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.webp': [b'RIFF'],  # RIFF....WEBP — checagem do 'WEBP' feita abaixo
}


def _logo_conteudo_valido(ext, header):
    """Confere magic bytes do upload contra a extensao declarada.

    SVG e rejeitado a montante (XSS armazenado via <script> inline), entao
    aqui so tratamos formatos raster. Retorna True se o cabecalho casa.
    """
    assinaturas = _LOGO_MAGIC.get(ext, [])
    if not any(header.startswith(a) for a in assinaturas):
        return False
    if ext == '.webp':
        # RIFF<4 bytes tamanho>WEBP
        return header[8:12] == b'WEBP'
    return True


def _get_config_dict():
    """Retorna dict {chave: valor} de Configuracao com cache TTL 10min."""
    from django.core.cache import cache
    cached = cache.get(CONFIG_CACHE_KEY)
    if cached is not None:
        return cached
    data = {c.chave: c.valor for c in Configuracao.objects.all()}
    cache.set(CONFIG_CACHE_KEY, data, CONFIG_CACHE_TTL)
    return data


def _invalidar_cache_branding():
    """Chamar apos save/delete em Configuracao."""
    from django.core.cache import cache
    cache.delete(CONFIG_CACHE_KEY)


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
                config, _ = Configuracao.objects.get_or_create(
                    chave=chave, defaults={'valor': novo_valor}
                )
                config.valor = novo_valor
                config.save()
                alterados.append(chave)

        logo_file = request.FILES.get('logo')
        if logo_file:
            ext = os.path.splitext(logo_file.name)[1].lower()
            # SVG e bloqueado: servido inline pode conter <script> (XSS armazenado)
            # e o codebase nao tem sanitizador. Apenas raster validado por magic bytes.
            if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
                messages.error(request, 'Formato de logo invalido. Use PNG, JPG ou WebP.')
            elif logo_file.size > 5 * 1024 * 1024:
                messages.error(request, 'Logo deve ter no maximo 5MB.')
            elif not _logo_conteudo_valido(ext, logo_file.read(12)):
                logo_file.seek(0)
                messages.error(request, 'Arquivo de logo invalido: o conteudo nao corresponde a uma imagem.')
            else:
                logo_file.seek(0)
                destino = os.path.join(LOGO_UPLOAD_DIR, f'logo-clinica{ext}')
                if default_storage.exists(destino):
                    default_storage.delete(destino)
                nome_salvo = default_storage.save(destino, logo_file)
                url_logo = default_storage.url(nome_salvo)
                config, _ = Configuracao.objects.get_or_create(
                    chave='LOGO_URL', defaults={'valor': url_logo}
                )
                config.valor = url_logo
                config.save()
                alterados.append('LOGO_URL')

        if alterados:
            _invalidar_cache_branding()
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
