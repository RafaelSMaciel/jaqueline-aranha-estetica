# LGPD — registro das operações, impacto e incidentes

> Rascunho técnico a partir do que o sistema faz hoje (código do branch `dev`). **A revisão jurídica e
> as decisões marcadas com ☐ são da dona da clínica.** Mudou um tratamento, um operador ou um prazo?
> Atualize aqui, na [política de privacidade](../aranha_estetica/templates/publico/politica_privacidade.html)
> e no termo LGPD (Painel > Termos: nova versão). Revisão: uma vez por ano, no mínimo.
> Referência técnica: [`PROJECT.md`](PROJECT.md) §9 (bases legais, consentimentos, direitos, retenção).

## 1. Controlador e encarregado

| | |
|---|---|
| Controlador | A clínica — razão social e CNPJ em Painel > Branding (aparecem na política) ☐ preencher |
| Encarregado (art. 41) | Nome e e-mail em Painel > Branding ☐ nomear (pode ser a própria dona) |
| Canal do titular | Área "Meus dados" do site (consulta e exportação com código por SMS) + e-mail/WhatsApp da clínica; resposta em até 15 dias |

Agente de pequeno porte pode dispensar o encarregado (Res. CD/ANPD nº 2/2022), mas o tratamento de
dado de saúde pesa contra a dispensa e nomear não custa nada: a recomendação é nomear.

## 2. Registro das operações de tratamento (art. 37)

| # | Operação | Dados | Titulares | Base legal | Onde fica | Retenção |
|---|---|---|---|---|---|---|
| 1 | Agendar, confirmar, lembrar e realizar o atendimento | nome, celular, e-mail e nascimento (opcionais), procedimento, profissional, horário, faltas | clientes | execução de contrato (art. 7º V) | `cliente`, `atendimento`, `notificacao` | cadastro: anonimizado após 5 anos sem atendimento; texto das mensagens: 12 meses |
| 2 | Confirmar a identidade no agendamento e no "Meus dados" | celular, código OTP (só o HMAC), IP | clientes | execução de contrato / legítimo interesse (segurança) | `codigo_otp` | 24 h |
| 3 | Ficha de avaliação (anamnese), prontuário, anotações dos atendimentos | **dado de saúde** (alergias, contraindicações, histórico, medicamentos, respostas) | clientes | art. 11 I (consentimento específico, com prova em `aceite_termo`) e II "f" (tutela da saúde) | `prontuario`, `prontuario_versao`, `anotacao_sessao`, `resposta_formulario` — **cifrados** | 20 anos (registro de saúde); ficha de pedido não realizado: 90 dias |
| 4 | Provas de aceite de termos e de consentimento | versão do termo, data, IP, user-agent, canal | clientes | obrigação legal e exercício de direitos (art. 7º II e VI) | `aceite_termo`, `consentimento` | 20 anos (aceite); consentimento: histórico append-only, IP e user-agent apagados no esquecimento |
| 5 | Pesquisa de satisfação e depoimento no site | nota, comentário, autorização de publicação | clientes | legítimo interesse (nota); consentimento (publicação) | `avaliacao_nps` | comentário apagado no esquecimento |
| 6 | Promoções e aniversário por e-mail | nome, e-mail | clientes com consentimento | consentimento (art. 7º I) | `consentimento` + envio | até revogar (link de descadastro) |
| 7 | Lista de espera e contato | nome, contato, mensagem | visitantes/clientes | legítimo interesse / procedimentos preliminares | `lista_espera`, e-mail da clínica | até a data desejada passar |
| 8 | Contas da equipe, 2FA, trilha de auditoria | nome, e-mail, papel, ações, IP | equipe | execução de contrato / obrigação legal | `usuario`, dispositivos OTP, `log_auditoria`, `historico_alteracao` | trilha: 5 anos (mínimo do banco: 180 dias); prontuário na trilha: 20 anos |
| 9 | Segurança do site | IP, data e hora, tentativas de login | visitantes/equipe | legítimo interesse (art. 7º IX) | django-axes, logs da aplicação | até 6 meses |
| 10 | Comissões e financeiro | valores por atendimento e profissional | profissionais | execução de contrato | `movimento_comissao`, `carteira` | livro-razão append-only |

## 3. Operadores e transferência internacional

| Operador | Para quê | Dados que recebe | Fora do Brasil? | Contrato / DPA |
|---|---|---|---|---|
| Railway | hospedagem do site e do Postgres | todos (o dado de saúde chega cifrado ao banco) | sim (EUA/Europa, conforme a região) | ☐ conferir DPA e região |
| Zenvia | SMS com o código | celular, texto do SMS | não | ☐ contrato |
| Meta (WhatsApp Business) | confirmação, lembrete, NPS, aviso de vaga | celular, nome, dados do horário | sim | ☐ termos de processamento da Meta |
| Provedor de e-mail (Resend, Brevo… o que for configurado) | e-mails transacionais e de marketing | e-mail, nome, conteúdo da mensagem | depende do provedor | ☐ DPA |
| Cloudflare (Turnstile) | verificação contra robôs no pedido do código | IP, dados técnicos do navegador | sim | ☐ DPA |
| Sentry (se ligado) | erros do site | eventos sem contato nem saúde (filtrados em `utils/pii.opcoes_sentry`) | sim | ☐ DPA, região de dados |
| Google (fontes, mapa; Calendar se a profissional conectar) | exibição do site; agenda da profissional | IP do visitante; no Calendar, horário, procedimento e nome | sim | ☐ termos do Google Workspace/Calendar |
| Apple/Microsoft/Google (feed ICS do calendário) | agenda da profissional | horário, procedimento, nome | sim | assinatura feita pela profissional |
| Provedor do backup (bucket) | cópia do banco | dump **cifrado com age** (sem a chave privada não abre) | depende | ☐ escolher e registrar |

Transferência internacional (art. 33): para cada operador fora do Brasil ☐ confirmar que o contrato
traz as cláusulas-padrão da ANPD (Res. CD/ANPD nº 19/2024) ou outra hipótese do art. 33.

## 4. Relatório de impacto (RIPD, art. 38) — dado de saúde

**Tratamento.** A clínica registra alergias, contraindicações, histórico de saúde, medicamentos em
uso, respostas da ficha de avaliação e anotações de cada atendimento, para decidir se o
procedimento estético é seguro e para manter o registro do que foi feito.

**Necessidade e proporcionalidade.** Só as perguntas configuradas pela clínica (formulário
PRONTUARIO/ANAMNESE) e só da cliente atendida. A ficha de quem não chegou a ser atendida sai em 90
dias. O registro do atendimento realizado fica 20 anos (registro de saúde).

**Riscos e medidas.**

| Risco | Medida no sistema |
|---|---|
| Vazamento do banco ou de um backup | cifragem em repouso dos campos de saúde (Fernet, chave fora do banco — `CHAVES_CIFRAGEM`); backup cifrado com age e chave privada offline |
| Acesso indevido pela equipe | 2FA obrigatório para ADMIN e PROFISSIONAL; profissional só vê prontuário de cliente com vínculo de atendimento; cada leitura vai para a `log_auditoria` |
| Alteração ou apagamento da prova | versões do prontuário e anotações append-only (trigger); `log_auditoria` e `historico_alteracao` append-only, expurgo só por idade (0059) |
| Envio a terceiros | Sentry sem saúde nem contato; e-mails e WhatsApp sem dado de saúde |
| Identificação por SIM-swap no "Meus dados" | ☐ risco aceito hoje (export com 1 fator); evolução: parte de saúde só por e-mail ou presencial |
| Perda da chave de cifragem | ☐ cópia offline da chave com a dona; rotação documentada (`rotacionar_chave_cifragem`) |

**Risco residual.** Quem controla a hospedagem (conta do Railway) ou uma conta de ADMIN tem a chave
em uso. Mitigação: 2FA nessas contas, poucos membros, revisão de acessos (§6).

☐ Aprovação da dona (data e assinatura).

## 5. Incidente de segurança (art. 48; Res. CD/ANPD nº 15/2024)

1. **Detectar e registrar** a hora em que a clínica soube (o prazo conta daí).
2. **Conter**: revogar sessões (rotacionar `DJANGO_SECRET_KEY`), resetar 2FA (`setup_2fa --force`),
   rotacionar `OTP_HMAC_KEY`, tokens de provedores e, se a chave de cifragem vazou, `CHAVES_CIFRAGEM`
   (`rotacionar_chave_cifragem`); revogar links públicos (o esquecimento e o painel revogam).
3. **Avaliar** pela `log_auditoria` e `historico_alteracao`: quais titulares, quais dados, se o dado de
   saúde estava cifrado (dado ininteligível reduz o risco — art. 48 §3).
4. **Comunicar** à ANPD e aos titulares afetados, quando houver risco ou dano relevante, em até
   **3 dias úteis** do conhecimento: o que aconteceu, dados e titulares envolvidos, medidas tomadas,
   riscos e o contato do encarregado.
5. **Registrar** todo incidente (mesmo sem comunicação) e guardar o registro por **5 anos**: data,
   descrição, dados, titulares, avaliação de risco, medidas, comunicação feita ou o motivo de não ter
   feito. ☐ definir onde fica o registro (fora do próprio sistema).

Modelo de aviso ao titular: *"Em [data] identificamos [o que aconteceu]. Seus dados de [categorias]
podem ter sido acessados. Já [medidas]. Recomendamos [o que a pessoa pode fazer]. Dúvidas: [contato
do encarregado]."*

## 6. Rotina

- ☐ Anual: revisar este documento, a política, os operadores e os contratos.
- ☐ Trimestral: revisar quem tem acesso (painel, Railway, GitHub, DNS, provedores) e remover quem saiu.
- ☐ Mensal: testar a restauração de um backup (`scripts/backup/testar_restauracao.sh`).
