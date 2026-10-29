"""Forms dos cadastros do painel: equipe (usuario, profissional + grade semanal) e regras operacionais
(configuracao).

A regra de dominio fica no clean() do Form; a view so faz is_valid() e persiste. Mesmo contrato de
forms/painel: invalido -> avisar_erros(request, form) + a tela de volta com o digitado e o erro no campo.
"""
from django import forms
from django.contrib.auth.password_validation import validate_password

from ..models import Procedimento, Profissional, Usuario
from ..models.configuracao import CHAVES, chave_do_registro
from ..utils.email import email_configurado
from ..utils.parse import id_int


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


# ─── Equipe: profissionais e grade semanal ────────────────────────────

# Ordem de exibicao (seg..dom) e mapeamento p/ AgendaHorario.dia_semana
# (ISO 8601: 1=segunda ... 7=domingo = date.isoweekday()).
DIAS_SEMANA = [
    ('segunda', 'Segunda-feira'),
    ('terca', 'Terça-feira'),
    ('quarta', 'Quarta-feira'),
    ('quinta', 'Quinta-feira'),
    ('sexta', 'Sexta-feira'),
    ('sabado', 'Sábado'),
    ('domingo', 'Domingo'),
]
DIA_NUMERO = {
    'segunda': 1, 'terca': 2, 'quarta': 3, 'quinta': 4,
    'sexta': 5, 'sabado': 6, 'domingo': 7,
}
HORA_INICIO_PADRAO = '09:00'
HORA_FIM_PADRAO = '18:00'


class CampoIds(forms.Field):
    """Ids de um grupo de checkboxes (getlist) -> set[int]. Id malformado ('²', 'abc') e ignorado: so vem
    de HTML adulterado (a tela nunca o oferece) e nao pode virar 500 (utils/parse.id_int)."""
    widget = forms.MultipleHiddenInput

    def to_python(self, value):
        return {pk for pk in map(id_int, value or []) if pk is not None}


class ProfissionalForm(forms.ModelForm):
    """Dados do profissional + quais procedimentos ele realiza (Habilitacao, so os ativos: os da tela).

    Sem rotulo nos campos: as mensagens ja dizem o campo (avisar_erros nao prefixa)."""
    nome = forms.CharField(max_length=100, error_messages={
        'required': 'O nome do profissional é obrigatório.',
        'max_length': 'O nome do profissional pode ter no máximo 100 caracteres.'})
    especialidade = forms.CharField(required=False)
    ativo = forms.BooleanField(required=False)
    procedimentos = CampoIds(required=False)

    class Meta:
        model = Profissional
        fields = ['nome', 'especialidade', 'ativo']

    def clean_procedimentos(self):
        ids = self.cleaned_data.get('procedimentos') or set()
        if not ids:
            return set()
        return set(Procedimento.objects.filter(ativo=True, pk__in=ids).values_list('pk', flat=True))


class _HoraOuVazio(forms.TimeField):
    """input type=time ('HH:MM' ou 'HH:MM:SS'). Hora invalida conta como vazia: o clean do dia diz o que
    falta, e um dia desmarcado nao recusa o envio por causa da hora que ficou no campo escondido."""
    input_formats = ['%H:%M', '%H:%M:%S']

    def to_python(self, value):
        try:
            return super().to_python(value)
        except forms.ValidationError:
            return None


class GradeSemanalForm(forms.Form):
    """Grade semanal: uma linha por dia, com os nomes de campo da tela (trabalha_<dia>, hora_inicio_<dia>,
    hora_fim_<dia>). clean(): dia marcado precisa das duas horas e de fim depois do inicio (nunca e
    descartado em silencio); dia desmarcado ignora as horas. `janelas` = {dia: (inicio, fim)}."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.janelas = {}
        for key, _nome in DIAS_SEMANA:
            self.fields[f'trabalha_{key}'] = forms.BooleanField(required=False)
            self.fields[f'hora_inicio_{key}'] = _HoraOuVazio(required=False)
            self.fields[f'hora_fim_{key}'] = _HoraOuVazio(required=False)

    def clean(self):
        dados = super().clean()
        self.janelas = {}
        for key, nome in DIAS_SEMANA:
            if not dados.get(f'trabalha_{key}'):
                continue
            inicio, fim = dados.get(f'hora_inicio_{key}'), dados.get(f'hora_fim_{key}')
            if not inicio or not fim:
                self.add_error(None, f'{nome}: informe a hora de início e a de fim.')
            elif fim <= inicio:
                self.add_error(None, f'{nome}: a hora de fim deve ser depois da hora de início.')
            else:
                self.janelas[key] = (inicio, fim)
        return dados

    def linhas(self):
        """Linhas da tela: o que foi enviado (re-render apos erro) ou a grade vazia (cadastro novo)."""
        enviado = self.is_bound
        if enviado:
            self.is_valid()   # cleaned_data: os campos da grade nunca recusam (o erro e do dia, no clean)
        return [
            {
                'key': key,
                'nome': nome,
                'marcado': bool(enviado and self.cleaned_data.get(f'trabalha_{key}')),
                'hora_inicio': (self.data.get(f'hora_inicio_{key}') if enviado else '') or HORA_INICIO_PADRAO,
                'hora_fim': (self.data.get(f'hora_fim_{key}') if enviado else '') or HORA_FIM_PADRAO,
                'janelas_extras': 0,
            }
            for key, nome in DIAS_SEMANA
        ]


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
