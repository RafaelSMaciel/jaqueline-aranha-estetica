"""ViewSets DRF read-only para integracoes (apenas staff autenticado)."""
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from app_shivazen.models import (
    Atendimento, Cliente, Procedimento, Profissional,
)

from .serializers import (
    AtendimentoSerializer, ClienteSerializer,
    ProcedimentoSerializer, ProfissionalSerializer,
)


class IsStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class ProfissionalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Profissional.objects.filter(ativo=True).order_by('nome')
    serializer_class = ProfissionalSerializer
    permission_classes = [IsStaff]


class ProcedimentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Procedimento.objects.all().order_by('nome')
    serializer_class = ProcedimentoSerializer
    permission_classes = [IsStaff]
    lookup_field = 'slug'


class ClienteViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Cliente.objects.all().order_by('-criado_em')
    serializer_class = ClienteSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(nome_completo__icontains=q)
        return qs


class AtendimentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Atendimento.objects.select_related(
        'cliente', 'profissional', 'procedimento'
    ).order_by('-data_hora_inicio')
    serializer_class = AtendimentoSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        return qs

    @action(detail=False, methods=['get'])
    def hoje(self, request):
        from django.utils import timezone
        hoje = timezone.localdate()
        qs = self.get_queryset().filter(data_hora_inicio__date=hoje)
        page = self.paginate_queryset(qs)
        ser = self.get_serializer(page or qs, many=True)
        return self.get_paginated_response(ser.data) if page else Response(ser.data)
