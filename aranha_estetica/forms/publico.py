"""Forms dos formularios publicos sem login (lista de espera e contato).

Os templates desenham os campos a mao (mesmos ``name``); aqui ficam a validacao e a normalizacao,
no lugar da leitura campo a campo do POST. ``website`` e o honeypot (partials/_antiabuso.html):
preenchido = robo, a view descarta em silencio (``eh_robo``).
"""
from django import forms
from django.core.exceptions import ValidationError

from ..models import ListaEspera, Procedimento, Profissional
from ..utils import datas
from ..utils import telefone as telefones

HONEYPOT = 'website'
MSG_TELEFONE = 'Informe um celular válido com DDD, ex.: (17) 99999-9999.'


class _AntiRobo(forms.Form):
    website = forms.CharField(required=False, max_length=200)

    def eh_robo(self) -> bool:
        return bool((self.data.get(HONEYPOT) or '').strip())


def primeiro_erro(form: forms.Form) -> str:
    """Mensagem unica p/ o aviso do topo (os templates mostram mensagens, nao erro por campo)."""
    for erros in form.errors.values():
        if erros:
            return str(erros[0])
    return 'Confira os dados do formulário.'


class ListaEsperaForm(_AntiRobo):
    nome = forms.CharField(max_length=150, error_messages={'required': 'Informe seu nome.'})
    telefone = forms.CharField(max_length=30, error_messages={'required': MSG_TELEFONE})
    email = forms.EmailField(required=False, max_length=254, error_messages={'invalid': 'Informe um e-mail válido.'})
    procedimento = forms.ModelChoiceField(
        queryset=Procedimento.objects.filter(ativo=True),
        error_messages={'required': 'Escolha o procedimento.', 'invalid_choice': 'Procedimento inválido.'},
    )
    profissional = forms.ModelChoiceField(queryset=Profissional.objects.filter(ativo=True), required=False)
    data_desejada = forms.DateField(
        input_formats=['%Y-%m-%d'],
        error_messages={'required': 'Escolha a data desejada.', 'invalid': 'Data inválida.'},
    )
    turno = forms.ChoiceField(choices=[('', 'Qualquer turno'), *ListaEspera.TURNO_CHOICES], required=False)

    def clean_nome(self):
        nome = ' '.join((self.cleaned_data.get('nome') or '').split())
        if len(nome) < 2:
            raise ValidationError('Informe seu nome.')
        return nome

    def clean_telefone(self):
        digitos = telefones.valido_ou_vazio(self.cleaned_data.get('telefone'))
        if not digitos:
            raise ValidationError(MSG_TELEFONE)
        return digitos

    def clean_data_desejada(self):
        dia = self.cleaned_data['data_desejada']
        if dia < datas.hoje():
            raise ValidationError('Escolha uma data a partir de hoje.')
        return dia

    def clean_turno(self):
        return self.cleaned_data.get('turno') or None


ASSUNTOS_CONTATO = {
    'agendamento': 'Agendamento',
    'duvida': 'Dúvidas sobre procedimentos',
    'orcamento': 'Solicitar orçamento',
    'parceria': 'Parcerias',
    'outros': 'Outros',
}
MSG_OBRIGATORIO = 'Preencha todos os campos obrigatórios.'


class ContatoForm(_AntiRobo):
    """Formulario de contato (names do template mantidos). Sem caixa de marketing (decisao 48): o
    opt-in de promocoes so existe no agendamento, onde ha prova do consentimento."""
    name = forms.CharField(max_length=150, error_messages={'required': MSG_OBRIGATORIO})
    email = forms.EmailField(
        max_length=254, error_messages={'required': MSG_OBRIGATORIO, 'invalid': 'Informe um e-mail válido.'},
    )
    phone = forms.CharField(max_length=30, required=False)
    subject = forms.ChoiceField(choices=list(ASSUNTOS_CONTATO.items()),
                                error_messages={'required': MSG_OBRIGATORIO, 'invalid_choice': MSG_OBRIGATORIO})
    message = forms.CharField(max_length=5000, error_messages={'required': MSG_OBRIGATORIO})
    privacy = forms.BooleanField(error_messages={'required': 'Aceite a Política de Privacidade para enviar.'})

    def clean_name(self):
        nome = ' '.join((self.cleaned_data.get('name') or '').split())
        if not nome:
            raise ValidationError(MSG_OBRIGATORIO)
        return nome

    def clean_phone(self):
        bruto = (self.cleaned_data.get('phone') or '').strip()
        if not bruto:
            return ''
        digitos = telefones.valido_ou_vazio(bruto)
        if not digitos:
            raise ValidationError('Informe um telefone válido com DDD ou deixe em branco.')
        return digitos

    @property
    def assunto(self) -> str:
        return ASSUNTOS_CONTATO.get(self.cleaned_data.get('subject'), 'Outros')
