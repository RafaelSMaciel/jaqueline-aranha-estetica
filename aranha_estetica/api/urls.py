"""URLs da API REST."""
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView,
)
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
    # Schema e UIs (swagger/redoc) restritos a staff/admin: a estrutura completa
    # da API (rotas, params, modelos Cliente/Atendimento) nao deve ser exposta a
    # anonimos. SpectacularSwaggerView/RedocView servem HTML publico por padrao,
    # entao a permissao precisa ser explicita aqui.
    path('schema/', SpectacularAPIView.as_view(permission_classes=[IsAdminUser]), name='schema'),
    path('schema/swagger/', SpectacularSwaggerView.as_view(url_name='aranha:schema', permission_classes=[IsAdminUser]), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='aranha:schema', permission_classes=[IsAdminUser]), name='redoc'),
]
