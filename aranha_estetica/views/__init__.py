"""Views por area: publico/ (site e cliente), conta/ (login e 2FA), painel/ (equipe),
profissional/ (portal do profissional) e infra/ (healthcheck, cron, webhooks, push).
As rotas de cada area ficam em aranha_estetica/urls/<area>.py. As areas nao importam umas das
outras (contrato "Areas de views independentes" no pyproject.toml); o helper de view que mais de
uma area usa fica em comum.py."""
