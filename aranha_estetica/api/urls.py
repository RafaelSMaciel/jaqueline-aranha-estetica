"""URLs da API REST."""
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView
from rest_framework.permissions import IsAdminUser
from rest_framework.routers import DefaultRouter

from .views import (
    AtendimentoViewSet, ClienteViewSet,
    ProcedimentoViewSet, ProfissionalViewSet,
)

router = DefaultRouter()
router.register('profissionais', ProfissionalViewSet, basename='profissional')
router.register('procedimentos', ProcedimentoViewSet, basename='procedimento')
router.register('clientes', ClienteViewSet, basename='cliente')
router.register('atendimentos', AtendimentoViewSet, basename='atendimento')

urlpatterns = [
    path('v1/', include(router.urls)),
    # Schema e UI (swagger) restritos a staff/admin: a estrutura completa
    # da API (rotas, params, modelos Cliente/Atendimento) nao deve ser exposta a
    # anonimos. As views do spectacular servem HTML publico por padrao,
    # entao a permissao precisa ser explicita aqui.
    path('schema/', SpectacularAPIView.as_view(permission_classes=[IsAdminUser]), name='schema'),
    # SplitView: o init do Swagger vem como script externo same-origin (?script),
    # aceito pela CSP com nonce (a SwaggerView padrao usa <script> inline e fica
    # em branco). Redoc removido: depende de <style> inline e worker blob:.
    path(
        'schema/swagger/',
        SpectacularSwaggerSplitView.as_view(url_name='aranha:schema', permission_classes=[IsAdminUser]),
        name='swagger-ui',
    ),
]
