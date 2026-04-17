"""URLs da API REST."""
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView,
)
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
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('schema/swagger/', SpectacularSwaggerView.as_view(url_name='shivazen:schema'), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='shivazen:schema'), name='redoc'),
]
