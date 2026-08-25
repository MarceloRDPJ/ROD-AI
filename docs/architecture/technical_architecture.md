# Arquitetura técnica do ROD do Cerrado

## Objetivo

O ROD é o assistente pessoal de Marcelo e o guardião da rede residencial. Ele roda continuamente no Raspberry Pi, conversa pelo Telegram e executa somente funções que consegue reconhecer e verificar. Perguntas genéricas não são enviadas a um LLM: recebem uma orientação curta para uma skill disponível.

## Arquitetura física real

### Raspberry Pi

- Modelo: Raspberry Pi 3B.
- CPU: 4 núcleos ARM.
- RAM física: aproximadamente 1 GB (`955 MiB` visíveis no sistema).
- Armazenamento: SSD USB montado como filesystem raiz em `/dev/sda1`.
- Sensores adicionais: nenhum.
- Sensor disponível: temperatura interna do SoC em `/sys/class/thermal/thermal_zone0/temp`.
- Operação: headless, 24/7.

O SSD oferece espaço e reduz a dependência de microSD, mas não aumenta CPU ou RAM. Por isso, modelos generativos locais foram retirados do atendimento após testes mostrarem lentidão, uso de swap e respostas incorretas.

### Serviços do Pi

- ROD do Cerrado: bot, serviços em segundo plano, dashboard e API.
- AdGuard Home: DNS e bloqueio de propaganda/domínios.
- CasaOS e serviços domésticos instalados no host.
- Docker: isolamento e reinício automático do ROD.

Não presumir GPIO, fan, UPS ou sensores externos. Funcionalidades relacionadas só podem afirmar disponibilidade depois de consultar hardware ou serviço real.

### Poco X3 NFC

- nó Android dedicado, com 6 GB de RAM, conectado ao Pi por Wi-Fi;
- credenciais e identificadores ficam cifrados no Android Keystore;
- executa RPA somente leitura nos aplicativos/canais oficiais;
- WhatsApp oficial funciona como aparelho adicional, sem SIM no Poco;
- USB/ADB é manutenção, não transporte de produção.

## Arquitetura de rede

```text
Internet
   |
Modem/roteador doméstico (NAT, DHCP e firewall)
   |
   +-- Raspberry Pi 3B
   |     +-- AdGuard Home / DNS
   |     +-- CasaOS
   |     +-- rod_cerrado
   |
   +-- computadores, celulares e dispositivos domésticos
   +-- Poco X3 NFC
         +-- agente ROD (HMAC + fila de jobs)
         +-- Saneago oficial
         +-- Chrome / Equatorial oficial
         +-- WhatsApp / Clara oficial de Goiás

Telegram Bot API <--- conexão HTTPS iniciada pelo ROD (long polling)
GitHub          <--- fetch/push HTTPS para atualização de código
```

O contêiner usa `network_mode: host` porque ping, descoberta da LAN, Wake-on-LAN e acesso aos serviços locais dependem da rede do host. A porta `8000` fica disponível na LAN por esse modo. Não há configuração de proxy público neste repositório; não se deve afirmar que o dashboard está exposto à internet.

## Arquitetura lógica

```text
Telegram: mensagem ou botão
        |
        v
normalizer.py
        |
        v
rules.py (regras exatas e ações sensíveis)
        |
        v
intent_engine.py (RapidFuzz e padrões tolerantes a erros)
        |
        v
brain.py (conhecimento local, consulta atual ou esclarecimento)
        |
        v
executor.py (validação, confirmação e execução)
        |
        +-- modules/: sistema, rede, AdGuard, lembretes, hidratação
        +-- services/: guardian, scheduler, collector, energia, automações
        +-- tools/: RSS e consultas públicas leves
        +-- database/: SQLite
```

### Ordem de decisão

1. Normalizar caixa, acentos e ruído textual.
2. Interromper ou continuar um fluxo multietapas válido.
3. Aplicar regras determinísticas.
4. Comparar com exemplos de intents usando RapidFuzz.
5. Aceitar mais variação ortográfica para consultas sem efeito colateral.
6. Manter limiar estrito para bloqueio, reinício, exclusão e alteração.
7. Consultar `LocalBrain` ou uma fonte real quando aplicável.
8. Se não existir skill segura, responder imediatamente pedindo contexto.

Nenhuma etapa do atendimento chama `llama.cpp`, modelo GGUF ou API paga de IA.

## Contexto curto

`ContextEngine` persiste estado por `chat_id`. Ele serve para fluxos como hidratação, lembretes e confirmações. Entidades anteriores só são herdadas quando a atividade é recente; fluxos expiram após 10 minutos. Contexto não autoriza ação perigosa e não transforma conversa em conhecimento inventado.

## Dados reais e respostas

- `system_status`: usa métricas do host/conteiner via `psutil` e sensor térmico disponível.
- `network_status`: executa verificação real de conectividade.
- `network_speed`: executa speedtest; pode levar mais tempo por natureza da medição, não por IA.
- `network_scan`: consulta a LAN usando ferramentas do sistema.
- lembretes e hidratação: leem e gravam SQLite.
- informações atuais: usam ferramentas públicas leves configuradas e retornam erro claro se a fonte falhar.
- contas Equatorial: cadeia `web_session → clara_whatsapp → cache`, sempre com
  origem e idade explícitas; o cache nunca libera pagamento.

Respostas técnicas podem ser compactas, por exemplo: `CPU: 12%, RAM: 43%, Temp: 48 °C.` Humanização é aplicada no texto ao redor, sem alterar números nem inventar diagnóstico.

## Processos e contêineres

O Compose de produção possui somente o serviço `homebot`, com nome `rod_cerrado`:

- limite de memória: 512 MB;
- reserva: 256 MB;
- limite de CPU: 2 núcleos;
- reinício: `unless-stopped`;
- healthcheck HTTP em `/api/system/health`;
- logs Docker rotacionados em três arquivos de até 10 MB.

Volumes persistentes:

- `src/jarvis/database`;
- `src/jarvis/storage`;
- `.env`;
- `src/jarvis/config.yaml`.

Modelos e `llama.cpp` não são montados. Arquivos GGUF que permanecerem no SSD não são usados pelo ROD.

## API e dashboard

O FastAPI inicia junto com o bot em `0.0.0.0:8000`.

- `/`: dashboard estático.
- `/api/system/health`: banco e modo de atendimento.
- `/api/system/status`: CPU, RAM, disco, temperatura e uptime.
- `/api/dashboard`: resumo agregado.
- `/api/network/*`: scan, ping e speedtest.
- demais rotas: hidratação, automações, logs, webhooks e configuração sanitizada.

A API não tem autenticação própria documentada no código. Deve permanecer restrita à LAN/VPN/firewall até que autenticação seja implementada.

## Arquitetura de nuvem

Não existe backend de nuvem do ROD. Serviços externos usados são clientes outbound:

- Telegram Bot API: transporte das mensagens.
- GitHub: origem e histórico do código.
- fontes públicas opcionais: cotação, RSS e páginas configuradas.
- WhatsApp/Clara: canal oficial outbound da Equatorial; não é backend do ROD nem
  API não oficial.

Ausências deliberadas: OpenAI, Gemini, Groq, Tuya e LLM local no atendimento.

## Segurança e confiabilidade

- Apenas `ALLOWED_USER_ID` pode controlar o bot.
- Tokens ficam no `.env`, nunca na imagem ou no Git.
- Ações sensíveis exigem confirmação.
- Mensagens Telegram usam envio seguro com fallback de formatação.
- Logs sanitizam segredos conhecidos.
- `.dockerignore` exclui `.env`, bancos, modelos, testes e artefatos.
- Healthcheck não consulta LLM e não fica degradado por uma função desativada.

## Limitações conhecidas

- Telegram não funciona durante queda da internet, embora processos locais continuem.
- Sem UPS/sensor elétrico, queda de energia é inferida pela interrupção e pelo retorno do Pi, não medida diretamente.
- Speedtest consome rede e demora conforme o serviço externo.
- Scans dependem de permissões, interface e visibilidade da LAN.
- O dashboard local ainda não deve ser exposto diretamente à internet.
- O módulo legado `llm_fallback.py` permanece apenas para compatibilidade/testes históricos; não é instanciado pelo atendimento nem pelo healthcheck.
- O CI possui meta global de cobertura que pode falhar mesmo com testes funcionais verdes.

## Recuperação

- Falha do bot: consultar `docker logs --tail 200 rod_cerrado`.
- Falha do healthcheck: testar `curl http://127.0.0.1:8000/api/system/health`.
- Falha de deploy: manter o contêiner atual, verificar Git e construir antes de recriar.
- Queda de energia: `restart: unless-stopped` deve restaurar o serviço após o boot.
- Banco: fazer backup dos volumes antes de migrações ou mudanças destrutivas.
