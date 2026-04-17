"""Serializers DRF para exposicao read-only de dominios principais."""
from rest_framework import serializers

from app_shivazen.models import (
    Atendimento, Cliente, Procedimento, Profissional,
)
from app_shivazen.utils.security import mask_cpf, mask_email, mask_telefone


class ProfissionalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profissional
        fields = ['id', 'nome', 'ativo']


class ProcedimentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Procedimento
        fields = ['id', 'nome', 'slug', 'duracao_minutos', 'categoria', 'descricao']


class ClienteSerializer(serializers.ModelSerializer):
    """Cliente com PII mascarada por padrao."""
    cpf = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    telefone = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = ['id', 'nome_completo', 'cpf', 'email', 'telefone', 'criado_em']

    def get_cpf(self, obj):
        return mask_cpf(getattr(obj, 'cpf', None)) if getattr(obj, 'cpf', None) else None

    def get_email(self, obj):
        return mask_email(obj.email) if obj.email else None

    def get_telefone(self, obj):
        return mask_telefone(obj.telefone) if obj.telefone else None


class AtendimentoSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source='cliente.nome', read_only=True)
    profissional_nome = serializers.CharField(source='profissional.nome', read_only=True)
    procedimento_nome = serializers.CharField(source='procedimento.nome', read_only=True)

    class Meta:
        model = Atendimento
        fields = [
            'id', 'cliente', 'cliente_nome',
            'profissional', 'profissional_nome',
            'procedimento', 'procedimento_nome',
            'data_hora_inicio', 'data_hora_fim',
            'status', 'valor_cobrado', 'criado_em',
        ]
