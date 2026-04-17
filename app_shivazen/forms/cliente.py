"""Forms de cliente e LGPD."""
from django import forms

from app_shivazen.models import Cliente


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            'nome_completo', 'data_nascimento', 'cpf', 'rg', 'profissao',
            'email', 'telefone', 'cep', 'endereco', 'aceita_comunicacao',
        ]
        widgets = {
            'data_nascimento': forms.DateInput(attrs={'type': 'date'}),
            'endereco': forms.Textarea(attrs={'rows': 2}),
        }


class LgpdConsentimentoForm(forms.Form):
    """Consentimento LGPD obrigatorio em fluxos publicos."""
    aceita_comunicacao = forms.BooleanField(required=False)
    aceita_termos = forms.BooleanField(
        required=True,
        error_messages={'required': 'E necessario aceitar os termos de uso.'},
    )
    aceita_privacidade = forms.BooleanField(
        required=True,
        error_messages={'required': 'E necessario aceitar a politica de privacidade.'},
    )


class DsarForm(forms.Form):
    """Data Subject Access Request: cliente solicita seus dados."""
    telefone = forms.CharField(max_length=20, required=True)
    codigo_verificacao = forms.CharField(max_length=6, required=True)
