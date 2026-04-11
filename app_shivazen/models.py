# app_shivazen/models.py
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from datetime import date, datetime, timedelta
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import update_last_login

# Desconecta sinal que tenta atualizar last_login (não presente no schema customizado)
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')


# =====================================================================
# CONTROLE DE ACESSO
# =====================================================================

class Funcionalidade(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'funcionalidade'

    def __str__(self):
        return self.nome


class Perfil(models.Model):
    nome = models.CharField(max_length=50, unique=True)
    descricao = models.TextField(blank=True, null=True)
    funcionalidades = models.ManyToManyField(Funcionalidade, through='PerfilFuncionalidade')

    class Meta:
        managed = True
        db_table = 'perfil'

    def __str__(self):
        return self.nome


class PerfilFuncionalidade(models.Model):
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE)
    funcionalidade = models.ForeignKey(Funcionalidade, on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'perfil_funcionalidade'
        unique_together = (('perfil', 'funcionalidade'),)


# =====================================================================
# PROFISSIONAIS
# =====================================================================

class Profissional(models.Model):
    nome = models.CharField(max_length=100)
    especialidade = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'profissional'

    def __str__(self):
        return self.nome

    def get_horarios_disponiveis(self, data_selecionada):
        from django.utils import timezone

        dia_semana = data_selecionada.isoweekday() % 7 + 1
        disponibilidades = DisponibilidadeProfissional.objects.filter(
            profissional=self,
            dia_semana=dia_semana
        )
        if not disponibilidades.exists():
            return []

        agendamentos = Atendimento.objects.filter(
            profissional=self,
            data_hora_inicio__date=data_selecionada,
            status__in=['AGENDADO', 'CONFIRMADO']
        )
        bloqueios = BloqueioAgenda.objects.filter(
            profissional=self,
            data_hora_inicio__date__lte=data_selecionada,
            data_hora_fim__date__gte=data_selecionada
        )

        horarios_disponiveis = []
        intervalo = timedelta(minutes=30)

        for disponibilidade in disponibilidades:
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
                    horario_str = hora_atual.strftime('%H:%M')
                    if horario_str not in horarios_disponiveis:
                        horarios_disponiveis.append(horario_str)
                hora_atual += intervalo

        return sorted(horarios_disponiveis)


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
        user = self.create_user(email, password, **extra_fields)
        # Vincular ao perfil Administrador (cria se nao existir)
        from django.apps import apps
        Perfil = apps.get_model('app_shivazen', 'Perfil')
        perfil_admin, _ = Perfil.objects.get_or_create(
            nome='Administrador',
            defaults={'descricao': 'Acesso total ao sistema'}
        )
        user.perfil = perfil_admin
        user.save(update_fields=['perfil_id'])
        return user


class Usuario(AbstractBaseUser):
    perfil = models.ForeignKey(Perfil, on_delete=models.RESTRICT, null=True, blank=True)
    profissional = models.OneToOneField(
        Profissional, on_delete=models.SET_NULL, null=True, blank=True
    )
    nome = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, unique=True)
    password = models.CharField(max_length=255, db_column='senha_hash')
    ativo = models.BooleanField(default=True)

    last_login = None  # Não presente no schema

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']
    objects = UsuarioManager()

    class Meta:
        managed = True
        db_table = 'usuario'

    @property
    def is_active(self):
        return self.ativo

    @property
    def is_staff(self):
        return bool(self.perfil and self.perfil.nome == 'Administrador')

    @property
    def first_name(self):
        return self.nome

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff


# =====================================================================
# CLIENTES
# =====================================================================

class Cliente(models.Model):
    nome_completo = models.CharField(max_length=150)
    data_nascimento = models.DateField(blank=True, null=True)
    cpf = models.CharField(max_length=14, unique=True, blank=True, null=True)
    rg = models.CharField(max_length=20, blank=True, null=True)
    profissao = models.TextField(blank=True, null=True)
    email = models.EmailField(max_length=100, blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)
    faltas_consecutivas = models.SmallIntegerField(default=0)
    bloqueado_online = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'cliente'
        indexes = [
            models.Index(fields=['telefone'], name='idx_cliente_telefone'),
        ]

    def __str__(self):
        return self.nome_completo

    def registrar_falta(self):
        self.faltas_consecutivas += 1
        if self.faltas_consecutivas >= 3:
            self.bloqueado_online = True
        self.save()

    def resetar_faltas(self):
        self.faltas_consecutivas = 0
        self.bloqueado_online = False
        self.save()


# =====================================================================
# PROCEDIMENTOS E PREÇOS
# =====================================================================

class Procedimento(models.Model):
    CATEGORIA_CHOICES = [
        ('FACIAL', 'Facial'),
        ('CORPORAL', 'Corporal'),
        ('CAPILAR', 'Capilar'),
        ('OUTRO', 'Outro'),
    ]

    nome = models.CharField(max_length=100)
    slug = models.SlugField(max_length=140, unique=True, blank=True, null=True)
    descricao = models.TextField(blank=True, null=True)
    descricao_longa = models.TextField(blank=True, null=True)
    duracao_minutos = models.SmallIntegerField()
    categoria = models.CharField(max_length=20, default='OUTRO', choices=CATEGORIA_CHOICES)
    imagem_destaque = models.URLField(max_length=500, blank=True, null=True)
    ativo = models.BooleanField(default=True)
    profissionais = models.ManyToManyField(Profissional, through='ProfissionalProcedimento')

    class Meta:
        managed = True
        db_table = 'procedimento'

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.nome) or f'procedimento-{self.pk or "novo"}'
            slug = base
            counter = 1
            while Procedimento.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f'{base}-{counter}'
            self.slug = slug
        super().save(*args, **kwargs)


class ProfissionalProcedimento(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'profissional_procedimento'
        unique_together = (('profissional', 'procedimento'),)


class Preco(models.Model):
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True
    )  # NULL = preço genérico do procedimento
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True, null=True)
    vigente_desde = models.DateField(default=date.today)

    class Meta:
        managed = True
        db_table = 'preco'


class DisponibilidadeProfissional(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    dia_semana = models.SmallIntegerField()  # 1=Dom, 2=Seg, ..., 7=Sab
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()

    class Meta:
        managed = True
        db_table = 'disponibilidade_profissional'
        # Sem unique_together — suporta múltiplos turnos por dia
        constraints = [
            models.CheckConstraint(
                check=models.Q(dia_semana__gte=1) & models.Q(dia_semana__lte=7),
                name='chk_disponibilidade_dia_semana'
            )
        ]


class BloqueioAgenda(models.Model):
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True
    )
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    motivo = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'bloqueio_agenda'


# =====================================================================
# PROMOÇÕES
# =====================================================================

class Promocao(models.Model):
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    desconto_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    preco_promocional = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    ativa = models.BooleanField(default=True)
    imagem_url = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'promocao'

    @property
    def esta_vigente(self):
        from django.utils import timezone
        hoje = timezone.now().date()
        return self.ativa and self.data_inicio <= hoje <= self.data_fim


# =====================================================================
# AGENDAMENTO
# =====================================================================

class Atendimento(models.Model):
    STATUS_CHOICES = [
        ('AGENDADO', 'Agendado'),
        ('CONFIRMADO', 'Confirmado'),
        ('REALIZADO', 'Realizado'),
        ('CANCELADO', 'Cancelado'),
        ('FALTOU', 'Faltou'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT)
    profissional = models.ForeignKey(Profissional, on_delete=models.RESTRICT)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT)
    promocao = models.ForeignKey(
        Promocao, on_delete=models.SET_NULL, blank=True, null=True
    )
    reagendado_de = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='reagendamentos'
    )
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    valor_cobrado = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    valor_original = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    descricao_preco = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='AGENDADO', choices=STATUS_CHOICES)
    token_cancelamento = models.CharField(max_length=64, unique=True, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.token_cancelamento:
            import secrets
            self.token_cancelamento = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    class Meta:
        managed = True
        db_table = 'atendimento'
        indexes = [
            models.Index(fields=['status'], name='idx_atendimento_status'),
            models.Index(fields=['data_hora_inicio'], name='idx_atendimento_data'),
            models.Index(fields=['cliente', 'status'], name='idx_atendimento_cli_status'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=[
                    'AGENDADO', 'CONFIRMADO', 'REALIZADO', 'CANCELADO', 'FALTOU'
                ]),
                name='chk_atendimento_status'
            )
        ]


# =====================================================================
# PRONTUÁRIO (HÍBRIDO)
# =====================================================================

class Prontuario(models.Model):
    """Anamnese base permanente do cliente."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE)
    alergias = models.TextField(blank=True, null=True)
    contraindicacoes = models.TextField(blank=True, null=True)
    historico_saude = models.TextField(blank=True, null=True)
    medicamentos_uso = models.TextField(blank=True, null=True)
    observacoes_gerais = models.TextField(blank=True, null=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'prontuario'


class ProntuarioPergunta(models.Model):
    TIPO_CHOICES = [
        ('TEXTO', 'Texto livre'),
        ('BOOLEAN', 'Sim / Não'),
        ('SELECAO', 'Seleção'),
    ]

    texto = models.TextField()
    tipo_resposta = models.CharField(max_length=50, choices=TIPO_CHOICES)
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'prontuario_pergunta'
        constraints = [
            models.CheckConstraint(
                check=models.Q(tipo_resposta__in=['TEXTO', 'BOOLEAN', 'SELECAO']),
                name='chk_prontuario_pergunta_tipo'
            )
        ]


class ProntuarioResposta(models.Model):
    """Respostas vinculadas ao prontuário (dados permanentes do cliente)."""
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE)
    pergunta = models.ForeignKey(ProntuarioPergunta, on_delete=models.RESTRICT)
    resposta_texto = models.TextField(blank=True, null=True)
    resposta_boolean = models.BooleanField(blank=True, null=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'prontuario_resposta'
        unique_together = (('prontuario', 'pergunta'),)


class AnotacaoSessao(models.Model):
    """Observações clínicas específicas de cada atendimento."""
    atendimento = models.ForeignKey(
        Atendimento, on_delete=models.CASCADE, related_name='anotacoes'
    )
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'anotacao_sessao'


# =====================================================================
# TERMOS DE CONSENTIMENTO (HÍBRIDO)
# =====================================================================

class VersaoTermo(models.Model):
    """Template versionado de um termo (LGPD ou por tipo de procedimento)."""
    TIPO_CHOICES = [
        ('LGPD', 'LGPD / Privacidade'),
        ('PROCEDIMENTO', 'Termo de Procedimento'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )  # NULL quando tipo = LGPD
    titulo = models.TextField()
    conteudo = models.TextField()
    versao = models.CharField(max_length=20)  # ex: '1.0', '2.1'
    vigente_desde = models.DateField()
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'versao_termo'
        constraints = [
            models.CheckConstraint(
                check=models.Q(tipo__in=['LGPD', 'PROCEDIMENTO']),
                name='chk_versao_termo_tipo'
            )
        ]


class AceitePrivacidade(models.Model):
    """LGPD: cliente assina uma vez por versão de termo de privacidade."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    ip = models.CharField(max_length=45, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'aceite_privacidade'
        unique_together = (('cliente', 'versao_termo'),)


class AssinaturaTermoProcedimento(models.Model):
    """Termo de procedimento: assinado uma vez por cliente por versão — persiste entre sessões."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    atendimento = models.ForeignKey(
        Atendimento, on_delete=models.SET_NULL, blank=True, null=True
    )  # Atendimento que gerou a assinatura
    ip = models.CharField(max_length=45, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'assinatura_termo_procedimento'
        unique_together = (('cliente', 'versao_termo'),)


# =====================================================================
# NOTIFICAÇÕES
# =====================================================================

class Notificacao(models.Model):
    TIPO_CHOICES = [
        ('LEMBRETE', 'Lembrete'),
        ('CONFIRMACAO', 'Confirmação'),
        ('CANCELAMENTO', 'Cancelamento'),
        ('NPS', 'Pesquisa NPS'),
    ]
    CANAL_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('SMS', 'SMS'),
        ('EMAIL', 'E-mail'),
    ]
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('ENVIADO', 'Enviado'),
        ('FALHOU', 'Falhou'),
    ]
    RESPOSTA_CHOICES = [
        ('CONFIRMOU', 'Confirmou'),
        ('CANCELOU', 'Cancelou'),
    ]

    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=30, default='LEMBRETE', choices=TIPO_CHOICES)
    canal = models.CharField(max_length=20, default='WHATSAPP', choices=CANAL_CHOICES)
    status_envio = models.CharField(max_length=20, default='PENDENTE', choices=STATUS_CHOICES)
    resposta_cliente = models.CharField(
        max_length=20, blank=True, null=True, choices=RESPOSTA_CHOICES
    )
    token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    mensagem = models.TextField(blank=True, null=True)
    enviado_em = models.DateTimeField(blank=True, null=True)
    respondido_em = models.DateTimeField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'notificacao'


# =====================================================================
# AVALIAÇÃO NPS
# =====================================================================

class AvaliacaoNPS(models.Model):
    atendimento = models.OneToOneField(Atendimento, on_delete=models.CASCADE)
    nota = models.SmallIntegerField()  # Escala 0-10 (NPS real)
    comentario = models.TextField(blank=True, null=True)
    alerta_enviado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'avaliacao_nps'
        constraints = [
            models.CheckConstraint(
                check=models.Q(nota__gte=0) & models.Q(nota__lte=10),
                name='chk_avaliacao_nps_nota'
            )
        ]


# =====================================================================
# PACOTES
# =====================================================================

class Pacote(models.Model):
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    preco_total = models.DecimalField(max_digits=10, decimal_places=2)
    ativo = models.BooleanField(default=True)
    validade_meses = models.SmallIntegerField(default=12)

    class Meta:
        managed = True
        db_table = 'pacote'


class ItemPacote(models.Model):
    pacote = models.ForeignKey(Pacote, on_delete=models.CASCADE, related_name='itens')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    quantidade_sessoes = models.SmallIntegerField(default=1)

    class Meta:
        managed = True
        db_table = 'item_pacote'
        unique_together = (('pacote', 'procedimento'),)


class PacoteCliente(models.Model):
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('FINALIZADO', 'Finalizado'),
        ('CANCELADO', 'Cancelado'),
        ('EXPIRADO', 'Expirado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='pacotes_comprados')
    pacote = models.ForeignKey(Pacote, on_delete=models.RESTRICT)
    criado_em = models.DateTimeField(auto_now_add=True)
    valor_pago = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='ATIVO', choices=STATUS_CHOICES)
    data_expiracao = models.DateField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'pacote_cliente'
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=['ATIVO', 'FINALIZADO', 'CANCELADO', 'EXPIRADO']),
                name='chk_pacote_cliente_status'
            )
        ]

    def save(self, *args, **kwargs):
        if not self.data_expiracao and self.pacote and self.pacote.validade_meses:
            from django.utils import timezone
            from dateutil.relativedelta import relativedelta
            self.data_expiracao = (
                timezone.now() + relativedelta(months=self.pacote.validade_meses)
            ).date()
        super().save(*args, **kwargs)

    def verificar_finalizacao(self):
        for item in self.pacote.itens.all():
            sessoes_feitas = self.sessoes_realizadas.filter(
                atendimento__procedimento=item.procedimento
            ).count()
            if sessoes_feitas < item.quantidade_sessoes:
                return
        self.status = 'FINALIZADO'
        self.save()


class SessaoPacote(models.Model):
    pacote_cliente = models.ForeignKey(
        PacoteCliente, on_delete=models.CASCADE, related_name='sessoes_realizadas'
    )
    atendimento = models.OneToOneField(
        Atendimento, on_delete=models.RESTRICT, related_name='sessao_pacote_vinculada'
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'sessao_pacote'


# =====================================================================
# LISTA DE ESPERA
# =====================================================================

class ListaEspera(models.Model):
    TURNO_CHOICES = [
        ('MANHA', 'Manhã'),
        ('TARDE', 'Tarde'),
        ('NOITE', 'Noite'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    profissional_desejado = models.ForeignKey(
        Profissional, on_delete=models.SET_NULL, blank=True, null=True
    )
    data_desejada = models.DateField()
    turno_desejado = models.CharField(
        max_length=20, blank=True, null=True, choices=TURNO_CHOICES
    )
    notificado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    token_reserva = models.CharField(max_length=64, blank=True, null=True)
    expira_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'lista_espera'
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(turno_desejado__isnull=True) |
                    models.Q(turno_desejado__in=['MANHA', 'TARDE', 'NOITE'])
                ),
                name='chk_lista_espera_turno'
            )
        ]


# =====================================================================
# AUDITORIA E SISTEMA
# =====================================================================

class LogAuditoria(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, blank=True, null=True)
    acao = models.TextField()
    tabela_afetada = models.CharField(max_length=100, blank=True, null=True)
    id_registro_afetado = models.IntegerField(blank=True, null=True)
    detalhes = models.JSONField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'log_auditoria'


class ConfiguracaoSistema(models.Model):
    chave = models.CharField(max_length=100, unique=True)
    valor = models.TextField(blank=True, null=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'configuracao_sistema'

    def __str__(self):
        return f'{self.chave}: {self.valor}'


class CodigoVerificacao(models.Model):
    telefone = models.CharField(max_length=20)
    codigo = models.CharField(max_length=6)
    criado_em = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False)

    class Meta:
        managed = True
        db_table = 'codigo_verificacao'

    @property
    def esta_valido(self):
        from django.utils import timezone
        return not self.usado and (timezone.now() - self.criado_em).total_seconds() < 600
