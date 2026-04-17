"""Forms relacionados a agendamento publico / cancelamento."""
import re
from datetime import datetime

from django import forms
from django.core.exceptions import ValidationError

from app_shivazen.validators import (
    validate_telefone_br, validate_data_nascimento, validate_maior_idade,
)


class AgendamentoPublicoForm(forms.Form):
    """Valida dados de agendamento publico (sem login).

    Substitui validacao inline em views/booking.py::confirmar_agendamento.
    """
    nome = forms.CharField(max_length=150, required=True)
    telefone = forms.CharField(max_length=20, required=True, validators=[validate_telefone_br])
    data_nascimento = forms.DateField(
        required=True,
        validators=[validate_data_nascimento, validate_maior_idade],
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
    )
    email = forms.EmailField(required=False)
    procedimento = forms.IntegerField(required=True, min_value=1)
    profissional = forms.IntegerField(required=True, min_value=1)
    datetime = forms.CharField(required=True)
    aceite_termos = forms.BooleanField(required=True, error_messages={
        'required': 'Voce deve aceitar a politica de privacidade e os termos de uso.',
    })

    def clean_telefone(self):
        tel = self.cleaned_data.get('telefone', '')
        return re.sub(r'\D+', '', tel)

    def clean_nome(self):
        nome = self.cleaned_data.get('nome', '').strip()
        if len(nome.split()) < 2:
            raise ValidationError('Informe o nome completo (nome e sobrenome).')
        return nome

    def clean_datetime(self):
        raw = self.cleaned_data.get('datetime', '')
        for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        raise ValidationError('Data/hora invalida.')


class CancelamentoForm(forms.Form):
    """Validacao de cancelamento via token publico."""
    token = forms.CharField(max_length=64, required=True)
    motivo = forms.CharField(max_length=500, required=False)
