"""Forms de procedimento."""
from django import forms
from django.core.validators import MinValueValidator

from app_shivazen.models import Procedimento


class ProcedimentoForm(forms.ModelForm):
    class Meta:
        model = Procedimento
        fields = '__all__'

    def clean_duracao_minutos(self):
        dur = self.cleaned_data.get('duracao_minutos', 0)
        if dur is None or dur <= 0:
            raise forms.ValidationError('Duracao deve ser maior que zero.')
        if dur > 720:
            raise forms.ValidationError('Duracao maior que 12h — verifique.')
        return dur
