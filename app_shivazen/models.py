# app_shivazen/models.py
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from datetime import date, datetime, timedelta
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import update_last_login

# Desconecta o sinal que tenta atualizar a coluna 'last_login' que não existe no novo schema customizado
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')

class Funcionalidade(models.Model):
    id_funcionalidade = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'funcionalidade'

class Perfil(models.Model):
    id_perfil = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=50, unique=True)
    descricao = models.TextField(blank=True, null=True)
    funcionalidades = models.ManyToManyField(Funcionalidade, through='PerfilFuncionalidade')

    class Meta:
        managed = True
        db_table = 'perfil'

class PerfilFuncionalidade(models.Model):
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE, db_column='id_perfil')
    funcionalidade = models.ForeignKey(Funcionalidade, on_delete=models.CASCADE, db_column='id_funcionalidade')

    class Meta:
        managed = True
        db_table = 'perfil_funcionalidade'
        unique_together = (('perfil', 'funcionalidade'),)

class Profissional(models.Model):
    id_profissional = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100)
    especialidade = models.CharField(max_length=100, blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
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
        managed = True
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
        managed = True
        db_table = 'cliente'

class ProntuarioPergunta(models.Model):
    id_pergunta = models.AutoField(primary_key=True)
    texto = models.TextField()
    tipo_resposta = models.CharField(max_length=50)
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'prontuario_pergunta'

class Procedimento(models.Model):
    id_procedimento = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True, null=True)
    duracao_minutos = models.IntegerField()
    ativo = models.BooleanField(default=True)
    profissionais = models.ManyToManyField(Profissional, through='ProfissionalProcedimento')

    class Meta:
        managed = True
        db_table = 'procedimento'

class ProfissionalProcedimento(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_column='id_procedimento')

    class Meta:
        managed = True
        db_table = 'profissional_procedimento'
        unique_together = (('profissional', 'procedimento'),)

class Prontuario(models.Model):
    id_prontuario = models.AutoField(primary_key=True)
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, db_column='id_cliente')

    class Meta:
        managed = True
        db_table = 'prontuario'

class Preco(models.Model):
    id_preco = models.AutoField(primary_key=True)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_column='id_procedimento')
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional', blank=True, null=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'preco'

class DisponibilidadeProfissional(models.Model):
    id_disponibilidade = models.AutoField(primary_key=True)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional')
    dia_semana = models.IntegerField()
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()

    class Meta:
        managed = True
        db_table = 'disponibilidade_profissional'

class BloqueioAgenda(models.Model):
    id_bloqueio = models.AutoField(primary_key=True)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_column='id_profissional', blank=True, null=True)
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    motivo = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
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
        managed = True
        db_table = 'atendimento'

class ProntuarioResposta(models.Model):
    id_resposta = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    pergunta = models.ForeignKey(ProntuarioPergunta, on_delete=models.RESTRICT, db_column='id_pergunta')
    resposta_texto = models.TextField(blank=True, null=True)
    resposta_boolean = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'prontuario_resposta'

class Notificacao(models.Model):
    id_notificacao = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    tipo = models.CharField(max_length=30, default='LEMBRETE')  # LEMBRETE, CONFIRMACAO, CANCELAMENTO, NPS
    canal = models.CharField(max_length=20, default='WHATSAPP')
    status_envio = models.CharField(max_length=20, default='PENDENTE')  # PENDENTE, ENVIADO, FALHOU
    resposta_cliente = models.CharField(max_length=20, blank=True, null=True)  # CONFIRMOU, CANCELOU
    token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    data_hora_envio = models.DateTimeField(blank=True, null=True)
    data_hora_resposta = models.DateTimeField(blank=True, null=True)
    mensagem = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'notificacao'

class TermoConsentimento(models.Model):
    id_termo = models.AutoField(primary_key=True)
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_column='id_atendimento')
    usuario_assinatura = models.ForeignKey(Usuario, on_delete=models.SET_NULL, db_column='id_usuario_assinatura', blank=True, null=True)
    ip_assinatura = models.CharField(max_length=45, blank=True, null=True)
    data_hora_assinatura = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
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
        managed = True
        db_table = 'log_auditoria'


class Promocao(models.Model):
    id_promocao = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_column='id_procedimento', blank=True, null=True)
    desconto_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    preco_promocional = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    ativa = models.BooleanField(default=True)
    imagem_url = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'promocao'

    @property
    def esta_vigente(self):
        from django.utils import timezone
        hoje = timezone.now().date()
        return self.ativa and self.data_inicio <= hoje <= self.data_fim


class CodigoVerificacao(models.Model):
    id = models.AutoField(primary_key=True)
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
        # Código expira em 10 minutos
        return not self.usado and (timezone.now() - self.criado_em).total_seconds() < 600

# =====================================================================
# MODELOS DE EXPANSÃO (Novas Funcionalidades Premium)
# =====================================================================

class Pacote(models.Model):
    id_pacote = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    preco_total = models.DecimalField(max_digits=10, decimal_places=2)
    ativo = models.BooleanField(default=True)
    
    class Meta:
        managed = True
        db_table = 'pacote'

class ItemPacote(models.Model):
    id_item_pacote = models.AutoField(primary_key=True)
    pacote = models.ForeignKey(Pacote, on_delete=models.CASCADE, related_name='itens')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    quantidade_sessoes = models.IntegerField(default=1)

    class Meta:
        managed = True
        db_table = 'item_pacote'

class PacoteCliente(models.Model):
    id_pacote_cliente = models.AutoField(primary_key=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='pacotes_comprados')
    pacote = models.ForeignKey(Pacote, on_delete=models.RESTRICT)
    data_compra = models.DateTimeField(auto_now_add=True)
    valor_pago = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='ATIVO') # ATIVO, FINALIZADO, CANCELADO

    class Meta:
        managed = True
        db_table = 'pacote_cliente'

class SessaoPacote(models.Model):
    id_sessao_pacote = models.AutoField(primary_key=True)
    pacote_cliente = models.ForeignKey(PacoteCliente, on_delete=models.CASCADE, related_name='sessoes_realizadas')
    atendimento = models.OneToOneField(Atendimento, on_delete=models.RESTRICT, related_name='sessao_pacote_vinculada')
    data_debito = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'sessao_pacote'


class ListaEspera(models.Model):
    id_lista_espera = models.AutoField(primary_key=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    profissional_desejado = models.ForeignKey(Profissional, on_delete=models.CASCADE, blank=True, null=True)
    data_desejada = models.DateField()
    turno_desejado = models.CharField(max_length=20, blank=True, null=True) # MANHA, TARDE, NOITE
    notificado = models.BooleanField(default=False)
    data_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'lista_espera'


class AvaliacaoNPS(models.Model):
    id_avaliacao = models.AutoField(primary_key=True)
    atendimento = models.OneToOneField(Atendimento, on_delete=models.CASCADE)
    nota = models.IntegerField() # 1 a 5
    comentario = models.TextField(blank=True, null=True)
    data_avaliacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'avaliacao_nps'


class MetaProfissional(models.Model):
    id_meta = models.AutoField(primary_key=True)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    mes = models.IntegerField()
    ano = models.IntegerField()
    valor_meta = models.DecimalField(max_digits=10, decimal_places=2)
    
    class Meta:
        managed = True
        db_table = 'meta_profissional'
        unique_together = (('profissional', 'mes', 'ano'),)


class TokenGoogleAgenda(models.Model):
    id_token = models.AutoField(primary_key=True)
    profissional = models.OneToOneField(Profissional, on_delete=models.CASCADE)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    token_uri = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    scopes = models.TextField()

    class Meta:
        managed = True
        db_table = 'token_google_agenda'


# =====================================================================
# CONTROLE DE VENDAS E ORÇAMENTOS
# =====================================================================

class Venda(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('PAGO', 'Pago'),
        ('CANCELADO', 'Cancelado'),
    ]

    id_venda = models.AutoField(primary_key=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, db_column='id_cliente', related_name='vendas')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT, db_column='id_procedimento')
    profissional = models.ForeignKey(Profissional, on_delete=models.SET_NULL, db_column='id_profissional', null=True, blank=True)
    data = models.DateField()
    sessoes = models.IntegerField(default=1)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')
    observacoes = models.TextField(blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'venda'
        ordering = ['-data']

    def __str__(self):
        return f'Venda #{self.pk} - {self.cliente.nome_completo} - {self.procedimento.nome}'


class Orcamento(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado'),
        ('RECUSADO', 'Recusado'),
        ('EXPIRADO', 'Expirado'),
    ]

    id_orcamento = models.AutoField(primary_key=True)
    # Dados do cliente
    nome_completo = models.CharField(max_length=150)
    data_nascimento = models.DateField(blank=True, null=True)
    profissao = models.CharField(max_length=100, blank=True, null=True)
    endereco_cep = models.CharField(max_length=200, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    rg = models.CharField(max_length=20, blank=True, null=True)
    cpf = models.CharField(max_length=14, blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)

    # Dados do orçamento
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT, db_column='id_procedimento')
    sessoes = models.IntegerField(default=1)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    data = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')
    observacoes = models.TextField(blank=True, null=True)

    # Questionário pré-procedimento
    tratamento_estetico_anterior = models.TextField(blank=True, null=True, verbose_name='Realizou algum tratamento estético? Se sim, qual?')
    doenca_pele = models.TextField(blank=True, null=True, verbose_name='Possui doença de pele (psoríase, vitiligo, dermatites, lúpus)?')
    tratamento_cancer = models.TextField(blank=True, null=True, verbose_name='Está em tratamento de câncer ou fez tratamento há menos de 5 anos?')
    melasma_pintas = models.TextField(blank=True, null=True, verbose_name='Tem melasma ou pintas mais pigmentadas? Se sim, em qual local?')
    uso_acido = models.TextField(blank=True, null=True, verbose_name='Utiliza algum tipo de ácido?')
    medicacao_continua = models.TextField(blank=True, null=True, verbose_name='Toma alguma medicação contínua?')
    gravida_amamentando = models.TextField(blank=True, null=True, verbose_name='Está grávida ou amamentando?')
    alergia = models.TextField(blank=True, null=True, verbose_name='Tem alergia a algum medicamento ou alimento? Se sim, qual?')
    implante_marcapasso = models.TextField(blank=True, null=True, verbose_name='Tem algum implante, marcapasso ou prótese de metal?')

    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'orcamento'
        ordering = ['-data_criacao']

    def __str__(self):
        return f'Orçamento #{self.pk} - {self.nome_completo} - {self.procedimento.nome}'


# =====================================================================
# CONTROLE DE ESTOQUE
# =====================================================================

class CategoriaProduto(models.Model):
    id_categoria = models.AutoField(primary_key=True)
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'categoria_produto'

    def __str__(self):
        return self.nome


class Produto(models.Model):
    id_produto = models.AutoField(primary_key=True)
    categoria = models.ForeignKey(CategoriaProduto, on_delete=models.SET_NULL, db_column='id_categoria', null=True, blank=True, related_name='produtos')
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    marca = models.CharField(max_length=100, blank=True, null=True)
    codigo_barras = models.CharField(max_length=50, unique=True, blank=True, null=True)
    preco_custo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    preco_venda = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantidade_estoque = models.IntegerField(default=0)
    estoque_minimo = models.IntegerField(default=5)
    unidade = models.CharField(max_length=20, default='UN')  # UN, ML, G, KG
    data_validade = models.DateField(blank=True, null=True)
    lote = models.CharField(max_length=50, blank=True, null=True)
    ativo = models.BooleanField(default=True)
    data_cadastro = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'produto'
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.marca or "Sem marca"})'

    @property
    def estoque_baixo(self):
        return self.quantidade_estoque <= self.estoque_minimo

    @property
    def valor_estoque(self):
        return self.quantidade_estoque * self.preco_custo

    @property
    def margem_lucro(self):
        if self.preco_custo and self.preco_custo > 0:
            return ((self.preco_venda - self.preco_custo) / self.preco_custo) * 100
        return 0

    @property
    def vencido(self):
        if self.data_validade:
            return self.data_validade < date.today()
        return False

    @property
    def proximo_vencer(self):
        if self.data_validade:
            hoje = date.today()
            return hoje <= self.data_validade <= hoje + timedelta(days=30)
        return False

    @property
    def precisa_comprar(self):
        return self.estoque_baixo


class MovimentacaoEstoque(models.Model):
    TIPO_CHOICES = [
        ('ENTRADA', 'Entrada'),
        ('SAIDA', 'Saída'),
        ('AJUSTE', 'Ajuste'),
        ('PERDA', 'Perda'),
    ]

    id_movimentacao = models.AutoField(primary_key=True)
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, db_column='id_produto', related_name='movimentacoes')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    quantidade = models.IntegerField()
    quantidade_anterior = models.IntegerField(default=0)
    quantidade_posterior = models.IntegerField(default=0)
    motivo = models.TextField(blank=True, null=True)
    usuario = models.ForeignKey('Usuario', on_delete=models.SET_NULL, db_column='id_usuario', null=True, blank=True)
    data_movimentacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'movimentacao_estoque'
        ordering = ['-data_movimentacao']

    def __str__(self):
        return f'{self.tipo} - {self.produto.nome} ({self.quantidade})'


# =====================================================================
# CONFIGURAÇÕES DO SISTEMA
# =====================================================================

class ConfiguracaoSistema(models.Model):
    id_config = models.AutoField(primary_key=True)
    chave = models.CharField(max_length=100, unique=True)
    valor = models.TextField(blank=True, null=True)
    descricao = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'configuracao_sistema'

    def __str__(self):
        return f'{self.chave}: {self.valor}'