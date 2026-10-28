# aranha_estetica/models/acesso.py — Controle de acesso e usuarios
#
# Remodelagem v2.1 fase 1c (docs/specs/remodelagem-banco-v2.md):
# Perfil/Funcionalidade/PerfilFuncionalidade removidos — RBAC granular que
# nunca foi consultado (autorizacao real sempre foi papel-unico por string).
# Substituido por Usuario.papel (CharField + choices + CHECK no banco).
# Permissao granular futura: django.contrib.auth Groups/Permissions nativos.
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import update_last_login
from django.db import models

from .mixins import atualizado_em_legado, criado_em_legado

# Desconecta sinal que tenta atualizar last_login (nao presente no schema customizado)
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')


class UsuarioManager(BaseUserManager):
    def get_by_natural_key(self, username):
        """Login insensivel a caixa (reset de senha e bootstrap_admin ja usam
        iexact). Exato primeiro: o legado pode ter 2 e-mails que so diferem na
        caixa (UNIQUE do banco diferencia) — ambiguo falha limpo (DoesNotExist)."""
        try:
            return self.get(**{self.model.USERNAME_FIELD: username})
        except self.model.DoesNotExist:
            if not username:
                raise
            candidatos = list(self.filter(**{f'{self.model.USERNAME_FIELD}__iexact': username})[:2])
            if len(candidatos) == 1:
                return candidatos[0]
            raise

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
        extra_fields.setdefault('papel', Usuario.PAPEL_ADMIN)
        return self.create_user(email, password, **extra_fields)


class Usuario(AbstractBaseUser):
    PAPEL_ADMIN = 'ADMIN'
    PAPEL_PROFISSIONAL = 'PROFISSIONAL'
    PAPEL_CHOICES = [
        (PAPEL_ADMIN, 'Administrador'),
        (PAPEL_PROFISSIONAL, 'Profissional'),
    ]

    # Dois papeis (a recepcao e feita pela dona, ADMIN). Default PROFISSIONAL: ADMIN nunca e
    # implicito — create_superuser/bootstrap_admin setam ADMIN.
    papel = models.CharField(
        max_length=20, choices=PAPEL_CHOICES, default=PAPEL_PROFISSIONAL, db_default=PAPEL_PROFISSIONAL,
    )
    profissional = models.OneToOneField(
        'Profissional', on_delete=models.SET_NULL, null=True, blank=True,
        db_comment='Profissional que este login representa (1:1 opcional).',
    )
    nome = models.CharField(max_length=100)
    # UNIQUE no Meta (uq_usuario_email): o auth.E003 aceita UniqueConstraint total no USERNAME_FIELD
    email = models.EmailField(max_length=100)
    password = models.CharField(
        max_length=255, db_column='senha_hash',
        db_comment='Hash do Django (pbkdf2_sha256$...). Excecao a regra char(64) de *_hash.',
    )
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    last_login = None

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']
    objects = UsuarioManager()

    class Meta:
        managed = True
        db_table = 'usuario'
        db_table_comment = (
            'Equipe (login por e-mail). papel ADMIN/PROFISSIONAL; is_staff = ADMIN. '
            'Nunca excluir: desativar (eh_ativo=false).'
        )
        constraints = [
            models.UniqueConstraint(fields=['email'], name='uq_usuario_email'),
            models.CheckConstraint(
                condition=models.Q(papel__in=['ADMIN', 'PROFISSIONAL']),
                name='ck_usuario_papel',
            ),
        ]

    def __str__(self):
        return f'{self.nome} ({self.get_papel_display()})'

    @property
    def is_active(self):
        return self.ativo

    @property
    def is_staff(self):
        return self.papel == self.PAPEL_ADMIN

    @property
    def first_name(self):
        return self.nome

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff
