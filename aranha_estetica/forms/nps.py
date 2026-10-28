"""Form da pesquisa NPS publica (/nps/<token>/)."""
import re

from django import forms

from ..services.nps import TAMANHO_MAXIMO_COMENTARIO

MSG_NOTA = 'Escolha uma nota de 0 a 10.'


class NpsForm(forms.Form):
    # texto + fullmatch: o IntegerField aceitaria '07' e o isdigit() deixava passar '²' (int() -> 500)
    nota = forms.CharField(error_messages={'required': MSG_NOTA})
    comentario = forms.CharField(
        required=False, max_length=TAMANHO_MAXIMO_COMENTARIO,
        error_messages={'max_length': f'Comentário com no máximo {TAMANHO_MAXIMO_COMENTARIO} caracteres.'},
    )
    # opt-in explicito p/ virar depoimento publico (a equipe ainda modera)
    autoriza_publicacao = forms.BooleanField(required=False)

    def clean_nota(self) -> int:
        nota = (self.cleaned_data.get('nota') or '').strip()
        if not re.fullmatch(r'10|[0-9]', nota):
            raise forms.ValidationError(MSG_NOTA)
        return int(nota)
