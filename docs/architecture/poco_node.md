# Nó Android Poco X3 NFC

## Decisão arquitetural

O Raspberry Pi continua sendo o núcleo confiável do ROD: Telegram, regras,
agenda, banco principal, monitoramento da rede e ações sensíveis permanecem no
Pi. O Poco X3 NFC (`surya`) funciona como satélite de voz, interface física e
worker para tarefas que se beneficiam do Android, da câmera ou de mais CPU/RAM.

O funcionamento normal não depende de ADB. ADB é usado apenas para preparação,
manutenção e desenvolvimento. Em produção, o Poco permanece carregando pela
USB-C e comunica-se com o Pi pela rede Wi-Fi local.

## Inventário seguro de contas

Desde o agente 1.0.5, o heartbeat informa `water_properties` e
`energy_properties`. Os valores não são números de unidade consumidora: são
somente chaves lógicas de uma lista fechada (`casa`, `kitnet_01`, `kitnet_02`,
`sala_comercial`, `restaurante`). O Pi valida novamente essa lista antes de
persistir. CPF, data, login, senha e números de conta continuam exclusivamente
no Android Keystore.

O Telegram pode assim montar o menu completo antes da primeira leitura. Agentes
Android antigos continuam compatíveis: o Pi usa o histórico confirmado e deixa
claro que recebeu apenas contagens.

## Espera e recuperação

O Telegram mantém uma única mensagem por consulta. Se a tarefa ultrapassar
quatro segundos, essa mensagem recebe estados qualitativos e o indicador de
digitação é renovado. Não há porcentagem falsa: os portais não expõem progresso
mensurável.

Na Equatorial, a reaparição do formulário depois do envio, sem mensagem de
recusa, é um reload transitório. O ROD preenche novamente os dados do cofre em
no máximo quatro reaparições. Recusa explícita e verificação humana encerram esse
canal e a cadeia segue para a Clara oficial no WhatsApp. Não se usa solucionador
de CAPTCHA, token fabricado, bypass de SMS ou biblioteca WhatsApp não oficial.

## Responsabilidades

### Raspberry Pi

- fonte de verdade dos lembretes, contas e histórico;
- bot do Telegram e autorização por `ALLOWED_USER_ID`;
- roteamento determinístico de intents;
- Mosquitto ou transporte HTTPS local autenticado;
- fila de jobs, supervisão e política de retentativas;
- rede, AdGuard, Wake-on-LAN e Home Assistant;
- validação final antes de apresentar ou persistir resultados.

### Poco X3 NFC

- interface neural e respostas faladas;
- palavra de ativação e transcrição de comandos;
- alarmes locais sincronizados;
- RPA somente leitura nos aplicativos oficiais;
- OCR, QR/barcode, imagem, áudio e documentos;
- diagnóstico Wi-Fi e speedtest a partir do celular;
- câmera e sensores quando uma skill explícita solicitar;
- cache local e fila de resultados para sobreviver a oscilações do Wi-Fi.

## Transporte e estados

O transporte implementado é HTTP na LAN entre o agente e a API do Pi. Corpo e
caminho de cada requisição são autenticados com HMAC-SHA256, timestamp curto e
segredo aleatório guardado no Android Keystore. A criação de jobs aceita somente
chamadas locais do Pi. Não existe shell remoto nem porta aberta no Poco. Cada
tarefa possui `job_id`, ação enumerada e prazo. O estado percorre:

```text
queued -> accepted -> running -> completed
                            `-> failed
                            `-> expired
```

O Pi persiste fila, heartbeat e resultados em `storage/poco_node.json`. Jobs
abandonados expiram e não bloqueiam a fila seguinte. O agente consulta a fila a
cada 15 segundos e sempre devolve `completed` ou `failed` com erro sanitizado.
Quando o Pi está inacessível, o intervalo cresce por backoff exponencial com
jitter até 120 segundos, e volta ao normal na primeira resposta boa.

Um job entregue entra em `accepted` imediatamente. Se o Wi-Fi cair exatamente
nesse instante, o aparelho nunca chega a vê-lo. Passado o *lease*
(`POCO_JOB_LEASE_SECONDS`, padrão 60), o Pi devolve o job à fila em vez de deixá-lo
parado até o TTL. Após `max_attempts` reentregas sem confirmação, o job falha com
diagnóstico em vez de girar para sempre.

O agente guarda todo resultado terminal numa fila local SQLite (`rod_outbox.db`)
antes de qualquer tentativa de rede. Uma consulta de conta leva minutos e o Wi-Fi
pode cair logo depois da leitura; sem essa fila o resultado morria dentro da
exceção HTTP e o Pi relatava timeout de um trabalho que o telefone concluiu. A
entrega é retentada nos ciclos seguintes e sobrevive ao reinício do processo. O
mesmo banco registra cada `job_id` já executado, então uma reentrega jamais roda
duas vezes. Resposta 4xx significa recusa definitiva do Pi e descarta a entrada;
erro de rede a mantém guardada.

## RPA de contas

Saneago é consultada pelo aplicativo oficial instalado pela Play Store. O agente
acorda a tela por tempo limitado, descarta somente o bloqueio simples, abre o app,
lê primeiro a árvore de acessibilidade e usa OCR local ML Kit como fallback. A
sessão e as credenciais permanecem no Android. Login, senha e contas são cifrados
com AES-GCM por uma chave não exportável do Android Keystore; o Pi não recebe
senha, cookie, CPF, data de nascimento ou credencial.

A Equatorial tenta o portal oficial no Chrome do Poco e, quando ele é recusado,
conversa com a Clara de Goiás pelo aplicativo oficial do WhatsApp. O Poco fica
vinculado como aparelho adicional e não precisa de SIM. A máquina de conversa
escolhe consulta de débitos, responde somente às perguntas reconhecidas e aceita
valor apenas com referência ou vencimento. Se a Clara declarar ausência de débito,
isso é registrado como resultado definitivo. O ROD não burla proteção antibot e
jamais apresenta uma consulta simulada.

O fluxo reconhece telas por `resource-id`, texto e descrição. OCR visual é
fallback para WebView/Canvas; coordenadas fixas não são o método principal. Uma
tela desconhecida encerra a execução e produz diagnóstico sanitizado.

O agente não pode pagar, confirmar PIX, negociar dívida, solicitar religação,
trocar titularidade ou alterar cadastro. Essas ações permanecem fora da lista de
jobs, mesmo quando a interface do aplicativo as oferece.

A leitura Saneago permite somente conta, valor da fatura atual, referência,
vencimento e consumo. Nome do titular e endereço são descartados no Android e
não entram no resultado, logs ou armazenamento do Pi.

Para evitar associar uma fatura ao imóvel errado, cada job leva apenas a chave
estável do imóvel (`kitnet_01`, `kitnet_02`, `sala_comercial` ou `casa`). O Poco
resolve o número no cofre. Na Saneago, uma conta diferente da solicitada causa
falha explícita; o ROD não renomeia o resultado incorreto.

## Disponibilidade

O Poco envia heartbeat a cada 30 segundos por um executor próprio, separado do
que executa jobs. Compartilhar a mesma thread fazia o Pi marcar o nó como offline
no meio da consulta que ele mesmo havia despachado. O heartbeat carrega também
`busy` e `pending_results`, para distinguir aparelho ocupado de aparelho ausente.

O `GuardianService` observa esse heartbeat a cada 60 segundos, mas atraso virou
somente telemetria. Medições no aparelho real mostraram que o Android pode atrasar
o executor mesmo com o foreground service e a fila de jobs respondendo; anunciar
"Poco offline" nesse estado era falso. O watchdog registra atraso e recuperação
no log, sem poluir o Telegram. Nó que nunca enviou heartbeat também não gera
alerta — não houve queda a relatar.

Quando o ping do Pi falha duas vezes, o guardião enfileira `network_check` no
Poco. Internet validada pelo Android suprime o falso alerta e aponta problema no
caminho do Pi. O alerta de indisponibilidade é enviado somente quando os dois
pontos negam acesso; se o Poco não responder, a mensagem declara a incerteza em
vez de culpar o provedor. O resultado ativo fica em cache por cinco minutos para
não transformar uma falha prolongada em jobs repetidos.

O agente dedicado mantém um `PARTIAL_WAKE_LOCK` e um `WifiLock` de alto
desempenho enquanto o serviço está ativo; ambos são liberados em `onDestroy`.
Durante a execução de um job existe ainda um wake lock próprio, e cada
leitor segura o wake lock de tela pelo orçamento inteiro do seu fluxo. Antes
disso o bloqueio expirava aos 60 segundos no meio da consulta, apagando a tela e
derrubando tanto o app quanto o OCR.

Se o Pi ficar indisponível, o Poco mantém interface, alarmes já sincronizados,
diagnóstico local e cache. Um modo de contingência do Telegram só poderá ser
ativado depois de implementar eleição que impeça Pi e Poco de consumir o mesmo
token simultaneamente.

## Orçamento de tempo

Consulta de conta não é requisição comum: o agente dirige o app oficial de ponta
a ponta e pode precisar refazer a sessão. Os limites das camadas precisam ser
coerentes entre si, ou a tarefa é abortada por um lado enquanto o outro ainda
trabalha.

| Camada | Limite | Onde |
| --- | --- | --- |
| Espera do Pi pela conclusão | 240 s | `POCO_BILL_JOB_TIMEOUT_SECONDS` |
| TTL do job | timeout + 30 s | `_run_poco_job` |
| Lease antes de reentregar | 60 s | `POCO_JOB_LEASE_SECONDS` |
| Wake lock do job no agente | 300 s | `AgentService` |
| Wake lock de tela Saneago | 240 s | `SaneagoReader` |
| Wake lock de tela Equatorial | 180 s | `EquatorialReader` |
| Conversa Clara no Android | 125 s | `JarvisAccessibilityService` |
| Espera do leitor Clara | 140 s | `ClaraWhatsAppReader` |
| Ida e volta na ponte de acessibilidade | 25 s / 30 s | leitores |
| Polling saudável | 15 s | `AgentService` |

## Cache de faturas

Toda leitura real é guardada no aparelho, cifrada com AES-GCM por chave própria
do Keystore, indexada por provedor e imóvel. Quando a consulta ao vivo falha, o
Pi pede `read_bill_cache` e acrescenta a última leitura confirmada à resposta,
sempre com a idade explícita e a frase de que aquilo é cache, não a medição de
agora. Apresentar cache como leitura atual seria inventar um fato.

## Energia e temperatura

- Poco e Pi usam alimentação própria; o Poco não alimenta o Pi por OTG.
- ADB sem fio pareado ou o agente local permitem manter a USB-C no carregador.
- O aparelho opera sem capa, em suporte ventilado e fora de caixas fechadas.
- A interface reduz FPS e brilho em repouso.
- O agente consulta bateria e status térmico do Android.
- Estado térmico `MODERATE` suspende OCR, STT e speedtest pesados.
- Estado `SEVERE` encerra workers, apaga a tela e alerta o Pi.
- Limites numéricos de bateria são política conservadora ajustada por benchmark,
  não especificação oficial da Xiaomi.

## Preparação pelo PC (USB)

A primeira preparação é feita por cabo, que é mais confortável para configurar e
depurar. Antes de limpar qualquer coisa, faz-se inventário. Nenhum dado, conta ou
aplicativo é removido sem que a lista exata apareça na tela primeiro.

- `scripts/poco_usb_inventory.ps1` — somente leitura. Modelo, codinome, Android,
  MIUI, patch de segurança, bateria, armazenamento, volume de mídia, aplicativos
  de terceiros, já desativados, acessibilidade ativa e isenções de bateria. Grava
  relatório com data em `.tools/poco-reports/`. Não instala, não desinstala, não
  desativa e não apaga nada. Detecta e explica os estados `unauthorized` e
  `offline` do adb em vez de falhar em silêncio.
- `scripts/poco_usb_disable_apps.ps1` — desativa bloatware de forma reversível,
  a partir de uma lista curada por você com o relatório na mão. Usa
  `pm disable-user --user 0`, nunca `pm uninstall`: nada é removido de fato e
  nenhum dado de usuário é apagado. Imprime o plano, recusa pacotes protegidos
  (sistema, Play Services, Chrome, Saneago e o próprio agente ROD), exige a
  palavra `CONFIRMO` digitada e grava um script de desfazer. Aceita `-WhatIf`
  para ver o plano sem tocar no aparelho.

Nesta fase não se desbloqueia bootloader, não se instala ROM e não se faz root.
ADB é ferramenta de preparação e manutenção, nunca o canal operacional.

## Sistema e segurança

A primeira implantação usa a ROM global oficial, bootloader bloqueado, Verified
Boot e SELinux. LineageOS só será considerado se testes prolongados demonstrarem
que a MIUI não sustenta o agente. Root, bootloader desbloqueado, Docker em PRoot
e ADB TCP/5555 permanente não fazem parte da arquitetura.

O Poco deve ser dedicado, sem aplicativos bancários ou dados pessoais. O agente
aceita somente ações enumeradas, guarda chaves no Android Keystore e não oferece
shell genérico ao Telegram. Logs e screenshots não podem conter CPF, senha,
token, código de barras ou conteúdo integral de faturas por padrão.

## Estado da entrega

Implementado: inventário, agente Android, Keystore, heartbeat em thread própria,
fila persistente no Pi, fila local SQLite no aparelho, reentrega por lease com
deduplicação por `job_id`, backoff com jitter, watchdog no `GuardianService`,
status/bateria/temperatura, validação real de internet, painel RDP, cofre das contas,
login assistido Saneago, leitura Saneago, fluxo Equatorial via Chrome, instalação e
pareamento assistidos do WhatsApp, canal Clara e cache de faturas rotulado.

As ações aceitas são exatamente `device_status`, `network_check`,
`read_bill_cache`, `refresh_saneago_bills`, `refresh_equatorial_bills`,
`clara_equatorial_bills`, `get_equatorial_pix` e `get_equatorial_boleto`.
`network_speed` e `scan_document` foram retiradas da lista: o Pi as enfileirava e
o aparelho só as recusava depois do timeout inteiro. Voltam quando existirem de
fato no agente.

Uma consulta só é considerada validada quando a concessionária devolve conteúdo
acessível no aparelho real. Mudanças de tela, CAPTCHA ou conta selecionada errada
geram falha honesta. Voz, alarmes e interface neural animada permanecem evolução
posterior, separada do núcleo confiável.

## Artefatos de pagamento: Pix e boleto

Consultar a fatura e obter o artefato para pagar são coisas diferentes, e por
isso são jobs diferentes: `get_equatorial_pix` devolve o Pix copia e cola, e
`get_equatorial_boleto` devolve o PDF oficial. Nenhum dos dois paga, confirma
Pix, abre banco ou gera transação — isso está fora da lista de ações e continua
fora. O ROD entrega ao proprietário exatamente o que ele mesmo usaria para pagar,
e para aí.

Os dois fluxos começam refazendo a consulta pelo motor principal. Parece
desperdício e não é: o pedido do artefato chega minutos depois da consulta,
quando a aba pode ter mudado, a sessão pode ter caído e a fatura pode já ter
sido paga. Refazer é o que garante que o artefato pertence à fatura em aberto
agora, e reusa inteira a máquina de sessão em vez de manter uma cópia dela.

### Pix

A página de resultado lista uma linha por fatura em aberto, com colunas de
referência, valor, download e pagamento via PIX. A célula de PIX chama uma função
JavaScript que exibe um QR em PNG; não há texto de payload na árvore. O agente
aciona o controle **da linha cuja referência casa com a pedida**, captura a tela
por `takeScreenshot`, decodifica com ML Kit restrito a `FORMAT_QR_CODE` (variante
bundled, modelo dentro do APK) e valida o resultado.

Acionar "o primeiro QR da página" funcionaria enquanto houvesse uma fatura em
aberto e entregaria o Pix do mês errado no primeiro mês com duas — exatamente
quando errar custa mais.

A validação é conservadora e recusar é o caminho normal:

- payload não vazio, e um único QR: dois conteúdos diferentes na tela são recusa,
  não escolha;
- prefixo `000201` e presença de `0014BR.GOV.BCB.PIX`;
- estrutura TLV do EMV QR cobrindo o payload inteiro, sem sobra;
- CRC16-CCITT-FALSE (polinômio `0x1021`, inicial `0xFFFF`, sem reflexão) sobre
  todo o payload incluindo `6304` e excluindo os quatro dígitos do checksum.

Nada é corrigido. Um payload "quase válido" seria uma ordem de pagamento
adulterada com aparência de oficial.

### Boleto

O objetivo é obter o PDF oficial, não interpretá-lo: download não é leitura. O
link da página aponta `mostrarFaturaCodigoBarras.aspx?invoice=N`, onde `N` é a
posição da linha. Da página aproveita-se apenas esse número; esquema, host e
caminho são montados no agente, porque aceitar caminho vindo do portal seria
deixá-lo escolher para onde o agente autenticado envia os cookies de sessão.

O download usa a sessão legítima do WebView do próprio ROD:
`CookieManager.getCookie(URL_COMPLETA)` — a URL completa, e não a base, porque
cookie de sessão ASP.NET vem com escopo de path (`/AgenciaGO`) e o
`DownloadListener` do WebView consulta só a base, o que baixaria sem sessão.
Não se tenta ler cookie do Chrome: não existe API para isso, e alcançar arquivo
privado de outro app ou pedir permissão ampla de armazenamento seria trocar um
problema técnico por um problema de privacidade.

Uma sessão caída não responde 401: o portal responde 200 com a tela de login em
HTML. Por isso a resposta é julgada antes de virar boleto — redirecionamento não
é seguido, o corpo é lido com contador e limite rígido, e valem os quatro
primeiros bytes (`%PDF`) mais o `Content-Type`. HTML no lugar do PDF é traduzido
para "refaça o login", que é acionável, e não para "arquivo corrompido", que
mandaria o proprietário tentar de novo para sempre.

### Canal de artefato

PDF em base64 dentro do resultado do job significaria a fatura do proprietário
gravada em texto claro em `poco_node.json`, sem prazo, ao lado de um dashboard
sem autenticação documentada. O canal é outro:

- `POST /api/poco/artifacts` com o binário cru, assinado pelo mesmo HMAC dos
  demais endpoints do nó (timestamp, método, caminho e SHA-256 do corpo);
- restrito à LAN, como o resto da arquitetura, e a entrega só no próprio Pi;
- allowlist de MIME com um único item, `application/pdf`, confirmado pelos bytes;
- limite rígido de tamanho, recusado antes de ler o corpo;
- nome de arquivo gerado internamente, nunca vindo do portal;
- arquivo `600` em diretório `700`, TTL curto, entrega consome o artefato e o
  boot apaga o que sobrou;
- nenhuma listagem: o `artifact_id` opaco é a única forma de alcançar o arquivo, e
  identificador fora do formato é recusado antes de qualquer contato com o disco.

O job devolve `artifact_id`; o Pi resolve `artifact_id` para arquivo temporário.
Caminho arbitrário não é aceito em nenhum ponto.

### Retenção

Pix, código de barras e PDF não podem existir em `poco_node.json`, `homebot.db`
ou log. A fila é gravada no mesmo instante em que o resultado chega, então
filtrar apenas na poda seria tarde: a serialização remove os campos sensíveis, e
o resultado inteiro de `get_equatorial_pix` fica só em memória, com prazo próprio
mais curto que o dos demais. O mecanismo de poda existente (`result_grace_seconds`
e `retention_seconds`) continua valendo e foi estendido, não substituído.

Metadado não sensível pode persistir: provedor, imóvel, referência, valor,
`retrieved_at`, `pix_available` e `boleto_available`.
