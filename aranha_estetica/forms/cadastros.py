"""Forms dos cadastros do painel: equipe (usuario) e regras operacionais (configuracao).

A regra de dominio fica no clean() do Form; a view so faz is_valid() e persiste. Mesmo contrato de
forms/painel: invalido -> avisar_erros(request, form) + a tela de volta com o digitado e o erro no campo.
"""
from django import forms
from django.contrib.auth.password_validation import validate_password

from ..models import Profissional, Usuario
from ..models.configuracao import CHAVES, chave_do_registro
from ..utils.email import email_configurado


# ─── Equipe: usuarios ─────────────────────────────────────────────────

MSG_EMAIL_EM_USO = 'E-mail já em uso por outro usuário.'


class UsuarioForm(forms.Form):
    """Novo usuario da equipe. Papel vazio = Profissional; senha vazia = link por e-mail (so com provedor).

    clean(): e-mail unico (qualquer caixa, fora a propria conta; a mesma mensagem ao criar e ao editar),
    papel Profissional exige o profissional vinculado e um profissional tem no maximo um login."""
    nome = forms.CharField(label='Nome', max_length=100, error_messages={
        'required': 'Informe o nome.', 'max_length': 'Nome com no máximo 100 caracteres.'})
    email = forms.EmailField(label='E-mail', max_length=100, error_messages={
        'required': 'Informe o e-mail.', 'invalid': 'Informe um e-mail válido.',
        'max_length': 'E-mail com no máximo 100 caracteres.'})
    papel = forms.ChoiceField(label='Papel', choices=Usuario.PAPEL_CHOICES, required=False,
                              error_messages={'invalid_choice': 'Papel inválido.'})
    # o POST manda o id (name="profissional_id"); ModelChoiceField recusa '²'/'abc'/id inexistente
    profissional_id = forms.ModelChoiceField(
        label='Profissional vinculado', queryset=Profissional.objects.all(), required=False,
        error_messages={'invalid_choice': 'Profissional inválido.'})
    senha = forms.CharField(label='Senha inicial', required=False, strip=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        outros = Usuario.objects.filter(email__iexact=email)
        if self.instance is not None:
            outros = outros.exclude(pk=self.instance.pk)
        if outros.exists():
            raise forms.ValidationError(MSG_EMAIL_EM_USO, code='email_em_uso')
        return email

    def clean_papel(self):
        papel = self.cleaned_data.get('papel')
        if papel:
            return papel
        return self.instance.papel if self.instance is not None else Usuario.PAPEL_PROFISSIONAL

    def clean(self):
        dados = super().clean()
        dados['profissional'] = profissional = dados.get('profissional_id')
        if 'papel' in dados and 'profissional_id' not in self.errors:
            if dados['papel'] == Usuario.PAPEL_PROFISSIONAL and profissional is None:
                self.add_error('profissional_id',
                               'Usuário com papel Profissional precisa de um profissional vinculado.')
            elif profissional is not None:
                outros = Usuario.objects.filter(profissional=profissional)
                if self.instance is not None:
                    outros = outros.exclude(pk=self.instance.pk)
                if outros.exists():
                    self.add_error('profissional_id', 'Esse profissional já está vinculado a outro usuário.')
        if 'senha' in self.fields:
            self._validar_senha(dados)
        return dados

    def _validar_senha(self, dados):
        senha = dados.get('senha') or ''
        if not senha:
            if not email_configurado():
                # Sem provedor o link nao chega: a conta ficaria inutilizavel
                self.add_error('senha', 'O envio de e-mail não está configurado: defina uma senha inicial.')
            return
        try:
            validate_password(senha, Usuario(email=dados.get('email', ''), nome=dados.get('nome', '')))
        except forms.ValidationError as exc:
            self.add_error('senha', exc)


class UsuarioEdicaoForm(UsuarioForm):
    """Edicao: sem senha (o reset vai por e-mail) e com `ativo`. clean() impede a equipe de ficar
    trancada fora: ninguem muda o proprio papel nem desativa a propria conta, e o ultimo
    administrador ativo nao e rebaixado nem desativado."""
    ativo = forms.BooleanField(label='Ativo', required=False)

    def __init__(self, *args, instance, request_user, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        self.request_user = request_user
        del self.fields['senha']

    def clean(self):
        dados = super().clean()
        usuario, papel, ativo = self.instance, dados.get('papel'), dados.get('ativo', False)
        if not papel:
            return dados
        if usuario.pk == self.request_user.pk and (papel != usuario.papel or not ativo):
            self.add_error(None, 'Você não pode alterar o próprio papel nem desativar a própria conta.')
        elif (usuario.papel == Usuario.PAPEL_ADMIN and usuario.ativo
              and (papel != Usuario.PAPEL_ADMIN or not ativo)
              and not Usuario.objects.filter(papel=Usuario.PAPEL_ADMIN, ativo=True).exclude(pk=usuario.pk).exists()):
            self.add_error(None, 'É preciso manter ao menos um administrador ativo.')
        return dados


# ─── Regras operacionais (Configuracao) ───────────────────────────────

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
