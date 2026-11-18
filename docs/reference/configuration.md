# Configuração

Use `.env.example` como contrato. O arquivo `.env` real nunca deve ser exibido ou versionado.

| Variável | Necessária | Finalidade |
|---|---:|---|
| `TELEGRAM_TOKEN` | sim | token entregue pelo BotFather |
| `ALLOWED_USER_ID` | sim | único usuário autorizado no Telegram |
| `TIMEZONE` | recomendada | agendamentos e datas |
| `PC_MAC` / `PC_IP` | não | Wake-on-LAN e verificação do PC |
| `DASHBOARD_AUTH_ENABLED` | sim em produção | exige sessão no painel |
| `DASHBOARD_USERNAME` | sim | usuário do painel |
| `DASHBOARD_PASSWORD_HASH` | sim | hash gerado localmente |
| `DASHBOARD_SESSION_SECRET` | sim | assinatura de sessão |
| `DASHBOARD_COOKIE_SECURE` | depende | `true` apenas com HTTPS |

Gere credenciais sem gravar senha no repositório:

```bash
python scripts/generate_dashboard_credentials.py
```

Integrações do AdGuard ficam em `src/jarvis/config.yaml`; credenciais reais devem ser injetadas localmente. Nunca exponha a porta 8000 diretamente à internet.
