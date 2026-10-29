"""Forms dos cadastros do painel: regras operacionais (configuracao).

A regra de dominio fica no clean() do Form; a view so faz is_valid() e persiste. Mesmo contrato de
forms/painel: invalido -> avisar_erros(request, form) + a tela de volta com o digitado e o erro no campo.
"""
from django import forms

from ..models.configuracao import CHAVES, chave_do_registro

# Chaves que existiam e mudaram de lugar: a tela explica onde ficam agora
CHAVES_RESERVADAS = {
    'prontuario_perguntas': (
        'As perguntas do prontuário agora ficam em Anamneses (formulário do tipo '
        '"Questionário do prontuário").'
    ),
}
MSG_CHAVE_FORA_DO_REGISTRO = (
    'Essa chave não é uma regra operacional. Nome, contatos e cor da clínica ficam em Identidade visual.'
)


class ConfiguracaoForm(forms.Form):
    """Valor de uma regra do registro (models/configuracao.CHAVES), validado pelo tipo e faixa dela.

    Sem chave livre: chave fora do registro e recusada. `chave=` fixa a regra (edicao de linha ja
    gravada); sem ela, a chave vem do POST (primeira gravacao da regra)."""
    # sem label: o erro da chave ja diz o que houve (avisar_erros nao prefixa)
    chave = forms.CharField(error_messages={'required': MSG_CHAVE_FORA_DO_REGISTRO})
    valor = forms.CharField(label='Valor', required=False)

    def __init__(self, *args, chave=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.chave_fixa = chave
        if chave is not None:
            del self.fields['chave']
        nome = chave_do_registro(chave if chave is not None else self.data.get('chave'))
        if nome is not None:   # o aviso no topo leva o nome da regra, nao "Valor"
            self.fields['valor'].label = CHAVES[nome].rotulo

    def clean_chave(self):
        bruto = self.cleaned_data['chave'].strip()
        if bruto.lower() in CHAVES_RESERVADAS:
            raise forms.ValidationError(CHAVES_RESERVADAS[bruto.lower()], code='reservada')
        nome = chave_do_registro(bruto)
        if nome is None:
            raise forms.ValidationError(MSG_CHAVE_FORA_DO_REGISTRO, code='fora_do_registro')
        return nome

    def clean(self):
        dados = super().clean()
        nome = chave_do_registro(self.chave_fixa) if self.chave_fixa is not None else dados.get('chave')
        if self.chave_fixa is not None and nome is None:
            raise forms.ValidationError(MSG_CHAVE_FORA_DO_REGISTRO, code='fora_do_registro')
        if nome is None:
            return dados
        dados['chave'] = nome
        try:
            dados['valor'] = CHAVES[nome].validar(dados.get('valor', ''))
        except forms.ValidationError as exc:
            self.add_error('valor', exc)
        return dados
