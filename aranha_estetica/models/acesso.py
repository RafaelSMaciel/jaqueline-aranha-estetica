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

# Desconecta sinal que tenta atualizar last_login (nao presente no schema customizado)
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')


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
        extra_fields.setdefault('papel', Usuario.PAPEL_ADMIN)
        return self.create_user(email, password, **extra_fields)


class Usuario(AbstractBaseUser):
    PAPEL_ADMIN = 'ADMIN'
    PAPEL_PROFISSIONAL = 'PROFISSIONAL'
    PAPEL_RECEPCAO = 'RECEPCAO'
    PAPEL_CHOICES = [
        (PAPEL_ADMIN, 'Administrador'),
        (PAPEL_PROFISSIONAL, 'Profissional'),
        (PAPEL_RECEPCAO, 'Recepcao'),
    ]

    papel = models.CharField(
        max_length=20, choices=PAPEL_CHOICES, default=PAPEL_RECEPCAO,
    )
    profissional = models.OneToOneField(
        'Profissional', on_delete=models.SET_NULL, null=True, blank=True
    )
    nome = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, unique=True)
    password = models.CharField(max_length=255, db_column='senha_hash')
    ativo = models.BooleanField(default=True)

    last_login = None

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']
    objects = UsuarioManager()

    class Meta:
        managed = True
        db_table = 'usuario'
        constraints = [
            models.CheckConstraint(
                check=models.Q(papel__in=['ADMIN', 'PROFISSIONAL', 'RECEPCAO']),
                name='chk_usuario_papel',
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
