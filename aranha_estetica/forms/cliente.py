"""Forms de cliente (edicao pelo painel)."""
from django import forms
from django.core.exceptions import ValidationError

from aranha_estetica.models import Cliente
from aranha_estetica.validators import (
    normalizar_cpf,
    normalizar_telefone,
    validate_cpf,
    validate_telefone_br,
)


class ClientePainelForm(forms.ModelForm):
    """Edicao de cadastro no painel (admin_cliente_detalhe).

    Normaliza telefone/CPF ANTES da validacao de constraints (duplicado
    mascarado vira erro de formulario, nao IntegrityError). Nao inclui os
    consents LGPD: campo ausente no POST nao pode zerar o opt-in do titular.
    """

    # Sobrescreve os fields do model: aceitam a versao com mascara (o model
    # guarda so digitos, max 11 no CPF).
    telefone = forms.CharField(required=False, max_length=20)
    cpf = forms.CharField(required=False, max_length=14)

    class Meta:
        model = Cliente
        fields = [
            'nome', 'telefone', 'email', 'cpf', 'data_nascimento', 'rg',
            'profissao', 'cep', 'endereco', 'ativo', 'aceita_comunicacao',
        ]

    def _ja_usado(self, **filtro):
        """Unicidade entre clientes ativos (as UniqueConstraint parciais do model
        dependem de deletado_em, fora do form — o ModelForm nao as valida)."""
        return Cliente.objects.filter(**filtro).exclude(pk=self.instance.pk).exists()

    def clean_nome(self):
        nome = (self.cleaned_data.get('nome') or '').strip()
        if not nome:
            raise ValidationError('Informe o nome.')
        return nome

    def clean_telefone(self):
        digitos = normalizar_telefone(self.cleaned_data.get('telefone') or '')
        if len(digitos) in (12, 13) and digitos.startswith('55'):
            digitos = digitos[2:]
        if not digitos:
            return None
        validate_telefone_br(digitos)
        if self._ja_usado(telefone=digitos):
            raise ValidationError('Telefone já cadastrado para outro cliente.')
        return digitos

    def clean_cpf(self):
        digitos = normalizar_cpf(self.cleaned_data.get('cpf') or '')
        if not digitos:
            return None
        validate_cpf(digitos)
        if self._ja_usado(cpf=digitos):
            raise ValidationError('CPF já cadastrado para outro cliente.')
        return digitos

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email and self._ja_usado(email__iexact=email):
            raise ValidationError('E-mail já cadastrado para outro cliente.')
        return email or None

    def _limpar_opcional(self, campo):
        valor = (self.cleaned_data.get(campo) or '').strip()
        return valor or None

    def clean_rg(self):
        return self._limpar_opcional('rg')

    def clean_profissao(self):
        return self._limpar_opcional('profissao')

    def clean_cep(self):
        return self._limpar_opcional('cep')

    def clean_endereco(self):
        return self._limpar_opcional('endereco')
