"""Forms de cliente: edicao da ficha no painel e o cadastro rapido do agendamento pela recepcao."""
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
    mascarado vira erro de formulario, nao IntegrityError). Consentimentos de
    comunicacao ficam fora: historico proprio (services/consentimentos), com
    revogar/registrar separados na ficha do cliente.
    """

    # (dono inativo, dono ativo) por campo unico; {nome} = a cliente dona do dado
    MENSAGENS_DONO = {
        'telefone': ('Telefone já cadastrado para outro cliente.',) * 2,
        'cpf': ('CPF já cadastrado para outro cliente.',) * 2,
        'email': ('E-mail já cadastrado para outro cliente.',) * 2,
    }

    # Sobrescreve os fields do model: aceitam a versao com mascara (o model
    # guarda so digitos, max 11 no CPF).
    telefone = forms.CharField(required=False, max_length=20)
    cpf = forms.CharField(required=False, max_length=14)

    class Meta:
        model = Cliente
        fields = [
            'nome', 'telefone', 'email', 'cpf', 'data_nascimento', 'cep', 'endereco', 'ativo',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dono_inativo = None   # cliente INATIVA dona do telefone/e-mail digitado (se houver)

    def _checar_dono(self, campo, **filtro):
        """Unicidade entre clientes (as UniqueConstraint parciais do model dependem de deletado_em,
        fora do form — o ModelForm nao as valida). Dono inativo e ativo tem mensagens proprias
        (MENSAGENS_DONO) e o inativo fica em ``dono_inativo``: a busca da recepcao so lista ativas,
        entao a tela precisa do caminho p/ reativar na ficha."""
        dono = Cliente.objects.filter(**filtro).exclude(pk=self.instance.pk).first()
        if dono is None:
            return
        se_inativo, se_ativo = self.MENSAGENS_DONO[campo]
        if not dono.ativo:
            self.dono_inativo = self.dono_inativo or dono
        raise ValidationError((se_ativo if dono.ativo else se_inativo).format(nome=dono.nome), code='unique')

    def clean_nome(self):
        nome = (self.cleaned_data.get('nome') or '').strip()
        if not nome:
            raise ValidationError('Informe o nome.')
        return nome

    def clean_telefone(self):
        digitos = normalizar_telefone(self.cleaned_data.get('telefone') or '')
        if not digitos:
            return None
        validate_telefone_br(digitos)
        self._checar_dono('telefone', telefone=digitos)
        return digitos

    def clean_cpf(self):
        digitos = normalizar_cpf(self.cleaned_data.get('cpf') or '')
        if not digitos:
            return None
        validate_cpf(digitos)
        self._checar_dono('cpf', cpf=digitos)
        return digitos

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email:
            self._checar_dono('email', email__iexact=email)
        return email or None

    def _limpar_opcional(self, campo):
        valor = (self.cleaned_data.get(campo) or '').strip()
        return valor or None

    def clean_cep(self):
        return self._limpar_opcional('cep')

    def clean_endereco(self):
        return self._limpar_opcional('endereco')


MSG_TELEFONE_RAPIDO = 'Telefone inválido: informe DDD + número (10 ou 11 dígitos).'
MSG_INATIVA = 'com cadastro inativo: reative a cliente na ficha (link abaixo) para agendar.'


class ClienteRapidoForm(ClientePainelForm):
    """Cliente nova no agendamento pela recepcao: nome, telefone (obrigatorio; aceita mascara e
    +55) e e-mail opcional. As regras da ficha (ClientePainelForm), com as mensagens da tela de
    agendamento. Campos do POST com o prefixo 'novo_' (novo_nome, novo_telefone, novo_email)."""

    MENSAGENS_DONO = {
        'telefone': (f'Este telefone é de {{nome}}, {MSG_INATIVA}',
                     'Este telefone já é de {nome}: busque e selecione a cliente cadastrada.'),
        'email': (f'Este e-mail é de {{nome}}, {MSG_INATIVA}',
                  'Este e-mail já pertence a outra cliente: busque a cliente cadastrada.'),
    }

    telefone = forms.CharField(max_length=20, error_messages={'required': MSG_TELEFONE_RAPIDO,
                                                              'max_length': MSG_TELEFONE_RAPIDO})
    email = forms.EmailField(required=False, max_length=254, error_messages={'invalid': 'E-mail inválido.'})

    class Meta(ClientePainelForm.Meta):
        fields = ['nome', 'telefone', 'email']
        error_messages = {'nome': {'required': 'Informe o nome da cliente nova.',
                                   'max_length': 'O nome aceita no máximo 150 caracteres.'}}

    def add_prefix(self, field_name):
        return f'novo_{field_name}'

    def clean_nome(self):
        nome = (self.cleaned_data.get('nome') or '').strip()
        if not nome:
            raise ValidationError('Informe o nome da cliente nova.')
        return nome

    def clean_telefone(self):
        # obrigatorio de verdade: so letras/simbolos normalizam p/ '' e a ficha aceitaria vazio
        if not normalizar_telefone(self.cleaned_data.get('telefone') or ''):
            raise ValidationError(MSG_TELEFONE_RAPIDO, code='invalid_phone')
        try:
            return super().clean_telefone()
        except ValidationError as erro:
            if getattr(erro, 'code', None) == 'invalid_phone':
                raise ValidationError(MSG_TELEFONE_RAPIDO, code='invalid_phone') from None
            raise
