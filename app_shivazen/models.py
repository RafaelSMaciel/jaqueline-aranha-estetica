# app_shivazen/models.py
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from datetime import datetime, timedelta
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import update_last_login

# Desconecta o sinal que tenta atualizar a coluna 'last_login' que não existe no novo schema customizado
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')

class Funcionalidade(models.Model):
    id_funcionalidade = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'funcionalidade'

class Perfil(models.Model):
    id_perfil = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=50, unique=True)
    descricao = models.TextField(blank=True, null=True)
    funcionalidades = models.ManyToManyField(Funcionalidade, through='PerfilFuncionalidade')

    class Meta:
        managed = False
        db_table = 'perfil'

class PerfilFuncionalidade(models.Model):
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE, db_column='id_perfil')
    funcionalidade = models.ForeignKey(Funcionalidade, on_delete=models.CASCADE, db_column='id_funcionalidade')

    class Meta:
        managed = False
        db_table = 'perfil_funcionalidade'
        unique_together = (('perfil', 'funcionalidade'),)

class Profissional(models.Model):
    id_profissional = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100)
    especialidade = models.CharField(max_length=100, blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'profissional'

    def get_horarios_disponiveis(self, data_selecionada):
        dia_semana = data_selecionada.isoweekday() % 7 + 1 
        try:
            disponibilidade = DisponibilidadeProfissional.objects.get(
                profissional=self, 
                dia_semana=dia_semana
            )
        except DisponibilidadeProfissional.DoesNotExist:
            return []

        agendamentos = Atendimento.objects.filter(
            profissional=self, 
            data_hora_inicio__date=data_selecionada, 
            status_atendimento__in=['AGENDADO', 'CONFIRMADO']
        )
        bloqueios = BloqueioAgenda.objects.filter(
            profissional=self, 
            data_hora_inicio__date__lte=data_selecionada, 
            data_hora_fim__date__gte=data_selecionada
        )

        horarios_disponiveis = []
        intervalo = timedelta(minutes=30)
        hora_atual = datetime.combine(data_selecionada, disponibilidade.hora_inicio)
        hora_fim_expediente = datetime.combine(data_selecionada, disponibilidade.hora_fim)

        while hora_atual < hora_fim_expediente:
            horario_ocupado = False
            for ag in agendamentos:
                if hora_atual >= ag.data_hora_inicio and hora_atual < ag.data_hora_fim:
                    horario_ocupado = True
                    break
            if not horario_ocupado:
                for bl in bloqueios:
                    if bl.data_hora_inicio <= hora_atual < bl.data_hora_fim:
                        horario_ocupado = True
                        break
            if not horario_ocupado:
                horarios_disponiveis.append(hora_atual.strftime('%H:%M'))
            hora_atual += intervalo

        return horarios_disponiveis

class UsuarioManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('O email deve ser definido')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('ativo', True)
        
        # Superuser precisa de um perfil de administrador ou bypassa checagens de is_staff no app
        # Vamos assumir que criaremos o perfil Administrador e associamos via views.
        return self.create_user(email, password, **extra_fields)

class Usuario(AbstractBaseUser):
    id_usuario = models.AutoField(primary_key=True)
    perfil = models.ForeignKey(Perfil, on_delete=models.RESTRICT, db_column='id_perfil', null=True, blank=True)
    profissional = models.OneToOneField(Profissional, on_delete=models.SET_NULL, db_column='id_profissional', null=True, blank=True)
    nome = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, unique=True)
    password = models.CharField(max_length=255, db_column='senha_hash')
    ativo = models.BooleanField(default=True)

    last_login = None # Disable Django last_login field as not present in schema

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']

    objects = UsuarioManager()

    class Meta:
        managed = False
        db_table = 'usuario'

    @property
    def is_active(self):
        return self.ativo
        
    @property
    def is_staff(self):
        if self.perfil and self.perfil.nome == 'Administrador':
            return True
        # Para compatibilidade do superuser sem perfil:
        if self.email == 'admin@shivazen.com':
            return True
        return False
        
    @property
    def first_name(self):
        return self.nome
        
    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff


class Cliente(models.Model):
    id_cliente = models.AutoField(primary_key=True)
    nome_completo = models.CharField(max_length=150)
    data_nascimento = models.DateField(blank=True, null=True)
    cpf = models.CharField(max_length=14, unique=True, blank=True, null=True)
    rg = models.CharField(max_length=20, blank=True, null=True)
    profissao = models.CharField(max_length=100, blank=True, null=True)
    email = models.CharField(max_length=100, blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)
    data_cadastro = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'cliente'

class ProntuarioPergunta(models.Model):
    id_pergunta = models.AutoField(primary_key=True)
    texto = models.TextField()
    tipo_resposta = models.CharField(max_length=50)
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'prontuario_pergunta'

class Procedimento(models.Model):
    id_procedimento = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True, null=True)
    duracao_minutos = models.IntegerField()
    ativo = models.BooleanField(default=True)
    profissionais = models.ManyToManyField(Profissional, through='ProfissionalProcedimento')

    class Meta:
        managed = False
        db_table = 'procedimento'

class ProfissionalProcedimento(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_column='id_procedimento')

    class Meta:
        managed = False
        db_table = 'profissional_procedimento'
        unique_together = (('profissional', 'procedimento'),)

class Prontuario(models.Model):
    id_prontuario = models.AutoField(primary_key=True)
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, db_column='id_cliente')

    class Meta:
        managed = False
        db_table = 'prontuario'

class Preco(models.Model):
    id_preco = models.AutoField(primary_key=True)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_column='id_procedimento')
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional', blank=True, null=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'preco'

class DisponibilidadeProfissional(models.Model):
    id_disponibilidade = models.AutoField(primary_key=True)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional')
    dia_semana = models.IntegerField()
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()

    class Meta:
        managed = False
        db_table = 'disponibilidade_profissional'

class BloqueioAgenda(models.Model):
    id_bloqueio = models.AutoField(primary_key=True)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional', blank=True, null=True)
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    motivo = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'bloqueio_agenda'

class Atendimento(models.Model):
    id_atendimento = models.AutoField(primary_key=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, db_column='id_cliente')
    profissional = models.ForeignKey(Profissional, on_delete=models.RESTRICT, db_column='id_profissional')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT, db_column='id_procedimento')
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    valor_cobrado = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    status_atendimento = models.CharField(max_length=20, default='AGENDADO')
    observacoes = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'atendimento'

class ProntuarioResposta(models.Model):
    id_resposta = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    pergunta = models.ForeignKey(ProntuarioPergunta, on_delete=models.RESTRICT, db_column='id_pergunta')
    resposta_texto = models.TextField(blank=True, null=True)
    resposta_boolean = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'prontuario_resposta'

class Notificacao(models.Model):
    id_notificacao = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    canal = models.CharField(max_length=20)
    status_envio = models.CharField(max_length=20)
    data_hora_envio = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'notificacao'

class TermoConsentimento(models.Model):
    id_termo = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    usuario_assinatura = models.ForeignKey(Usuario, on_delete=models.SET_NULL, db_column='id_usuario_assinatura', blank=True, null=True)
    ip_assinatura = models.CharField(max_length=45, blank=True, null=True)
    data_hora_assinatura = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'termo_consentimento'

class LogAuditoria(models.Model):
    id_log = models.AutoField(primary_key=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, db_column='id_usuario', blank=True, null=True)
    acao = models.CharField(max_length=255)
    tabela_afetada = models.CharField(max_length=100, blank=True, null=True)
    id_registro_afetado = models.IntegerField(blank=True, null=True)
    detalhes = models.JSONField(blank=True, null=True)
    data_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'log_auditoria'