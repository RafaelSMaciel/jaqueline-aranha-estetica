# Contribuindo — shivazen-app

## Workflow

1. Branch a partir de `dev` — `git checkout -b feature/nome-curto`
2. Commits no padrão `tipo: descricao curta` (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`)
3. PR para `dev`. `main` só recebe merge de `dev` em release.
4. CI deve passar: testes + ruff + migrations check

## Padrões de código

- **PEP 8 + snake_case** em tudo (Python, URLs, templates, IDs CSS lógicos)
- **ModelForm** sempre que tocar input de usuário (não usar `request.POST.get` cru)
- **Service layer** para regra de negócio. Views ficam finas, só HTTP.
- **`@transaction.atomic`** em qualquer operação multi-tabela
- **Validators** centralizados em `app_shivazen/validators.py`
- **PII masking** obrigatório em logs e LogAuditoria

## Testes

```bash
python manage.py test app_shivazen
```

- Testes para nova lógica de negócio são **obrigatórios**
- Tests não devem mockar DB (usar SQLite in-memory de teste)
- Use factories de `app_shivazen/tests/factories.py` em vez de criar objetos cru

## Migrations

- Criar via `python manage.py makemigrations app_shivazen`
- Sempre revisar SQL gerado: `python manage.py sqlmigrate app_shivazen 0010`
- Migrations destrutivas (drop column) precisam de janela de manutenção

## Segurança

- **NUNCA** committar `.env`, segredos, ou DB de prod
- Toda nova rota POST precisa CSRF (Django default cobre)
- Toda inserção de HTML cru precisa nonce CSP
- Nunca usar `==` para comparar tokens — use `hmac.compare_digest`

## LGPD

Qualquer feature que toque dado pessoal precisa:
1. Mapear no `LgpdService.exportar_dados_cliente`
2. Permitir anonimização via `LgpdService.esquecer_cliente`
3. Logar via `AuditoriaService.registrar` (auto-mascara PII)

## Code review

- Mudanças em models → revisar índices e constraints
- Mudanças em settings/middleware → testar em dev + staging
- Mudanças em LGPD → aprovação obrigatória do responsável legal
