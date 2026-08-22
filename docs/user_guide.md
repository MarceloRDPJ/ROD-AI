# Guia de uso do ROD

## Como conversar

Use frases naturais pelo Telegram. Acentos, maiúsculas e vários erros comuns são normalizados. Exemplos equivalentes:

- `speed`, `sped`, `speed teste`, `sped treste`;
- `status`, `status do raspi`, `tempratura do pi`;
- `qual a velocidade da net`, `teste velocdade internet`.

O ROD não é um chatbot de conhecimento geral. Se a mensagem não corresponder a uma skill confiável, ele explica o que pode consultar em vez de inventar uma resposta.

## Contas e faturas

Abra **Menu > Contas & faturas** e escolha o imóvel. O menu usa somente os nomes
lógicos configurados no Poco; números de conta e credenciais não são enviados ao
Pi. Durante uma busca demorada, o ROD atualiza a própria mensagem. Não é preciso
tocar novamente.

A resposta ao vivo mostra o horário e apenas os campos realmente lidos. Se o
portal falhar, uma leitura guardada pode aparecer com idade explícita. PIX e
boleto nunca são oferecidos sobre leitura guardada.

Na Equatorial, **ATUALIZAR** força uma nova tentativa. Se o formulário oficial
recarregar silenciosamente, o Poco o preenche outra vez dentro de um limite. Um
CAPTCHA, bloqueio antifraude ou recusa de cadastro é reportado sem contorno.

O APK 1.0.5 usa a identidade escura da RDP Studio e controles com área de toque,
feedback visual e hierarquia operacional. A versão instalada aparece no rodapé.

## Menus e botões

### Menu principal

- Rede & Segurança
- Agenda & Vida
- Automações
- Sistema & Controle
- Sobre Mim

### Rede & Segurança

- Scan Completo
- Teste Velocidade
- Estatísticas
- Bloquear IP / ajuda
- Renomear Device / ajuda
- Status Internet
- Voltar ao menu principal

### Agenda & Vida

- Ver Lembretes
- Criar Lembrete
- Ativar Hidratação
- Análise 30 Dias
- Bebi Água
- Status Água
- Voltar ao menu principal

### Automações

- Ver Automações
- Configurar Automações
- Voltar ao menu principal

Criar automações novas por texto não é anunciado como disponível quando não existe implementação segura.

### Sistema & Controle

- Diagnóstico
- Ajuda para Reiniciar
- Restart AdGuard
- Ver Logs
- Voltar ao menu principal

Reinício e alterações sensíveis pedem confirmação.

## Funcionalidades e frases

### Raspberry Pi e sistema

- `status`
- `status do raspi`
- `temperatura do pi`
- `logs do sistema`
- `reiniciar sistema`

O status consulta CPU, RAM, disco, temperatura e uptime reais. O Pi não possui sensores ambientais adicionais.

### Internet e rede

- `status da internet`
- `speed`
- `quem ta na rede`
- `estatisticas de rede`
- `renomear 192.168.0.15 para TV Sala`
- `ligar o pc`
- `pc ta ligado?`

O speedtest é uma operação real e pode demorar. O scan depende da visibilidade da rede local.

### AdGuard e segurança

- `bloquear site exemplo.com`
- `reiniciar adguard`
- consultas disponíveis no menu de rede

Bloqueios exigem domínio/IP reconhecido e confirmação. Uma frase muito errada como `bluqear sit` não executa ação.

### Lembretes

- `me lembra de tomar remédio em 20 minutos`
- `me lembra de pagar a conta amanhã às 9h`
- `listar lembretes`
- `lembretes de hoje`
- `lembretes atrasados`
- `apagar lembrete 2`
- `editar lembrete 1`

Os lembretes sobrevivem a reinícios porque usam SQLite no volume persistente.

### Hidratação

- `ativar hidratação`
- `bebi`
- `bebi 500ml`
- `status hidratação`
- `analise de hidratacao`
- `pausar hidratação`
- `retomar hidratação`

### Contas de água e energia

- `conta de água casa`
- `conta saneago kitnet 01`
- `conta de luz kitnet 02`
- `fatura equatorial sala comercial`
- `conta de água restaurante`
- `conta de luz restaurante`

O Poco entregue ao proprietário já é provisionado por cabo, sem digitação manual.
A tela **ROD → Cofre de contas** existe apenas para manutenção futura. Login,
senha e identificadores permanecem cifrados pelo Android Keystore e não são
enviados ao Pi. O heartbeat informa somente se cada integração está configurada e
quantas unidades existem. O serviço **ROD — automação local** deve estar ativo.

Na Saneago, o ROD abre o app oficial, reconhece sessão expirada, entra pelo SSO
oficial no Chrome, escolhe a unidade e lê os dados exibidos. Se uma unidade não
estiver vinculada ao login, ele informa isso e interrompe a consulta; nunca reutiliza
o resultado da unidade anterior.

Saneago usa o app oficial. Equatorial usa o portal oficial no Chrome. Se surgir
CAPTCHA, Imperva ou confirmação humana, resolva a tela no Poco e repita o comando.
O ROD não contorna a proteção e não inventa valor de fatura.

A consulta dirige o aplicativo de ponta a ponta e pode levar alguns minutos,
principalmente quando a sessão precisa ser refeita. O ROD abre **uma** mensagem
("Consultando Equatorial — Casa...") e edita essa mesma mensagem com o resultado;
não é preciso repetir o comando enquanto isso. Se o Wi-Fi cair no meio, o Poco
continua a tarefa e guarda o resultado até conseguir entregá-lo.

O resultado da Equatorial traz quatro botões: **PIX**, **BOLETO**, **ATUALIZAR** e
**VOLTAR**. Pix e boleto não são carregados junto com a consulta — só quando o
botão é tocado. PIX responde com o código copia e cola em bloco, para copiar com
um toque; BOLETO responde com o PDF oficial, com nome amigável, e o arquivo
temporário é apagado do Pi depois do envio. **Nenhum dos dois inicia pagamento**, e
o ROD nunca envia link de pagamento.

Tocar duas vezes no mesmo botão não dispara duas automações: o segundo toque
espera (ou reaproveita) a operação que já está em andamento. Nunca há duas
automações simultâneas no Poco para a mesma conta.

No menu **Contas & Faturas** aparecem apenas os imóveis que já tiveram uma
consulta concluída — é o que o Pi pode provar. Limitação conhecida: o heartbeat do
Poco expõe apenas a *contagem* de unidades de água e energia do cofre, nunca os
nomes; por isso o menu não lista o que ainda não foi consultado. Peça pelo nome
uma vez (`conta de luz kitnet 01`) e o imóvel passa a aparecer no menu.

Quando a consulta ao vivo falha, o ROD acrescenta a última leitura confirmada
guardada no Poco, sempre dizendo há quanto tempo ela foi feita e que se trata de
cache. Valor antigo nunca é apresentado como a fatura de agora.

### Informações atuais e conversa

Algumas consultas usam RSS ou fontes públicas configuradas. Se a fonte estiver indisponível, a resposta informa a falha. Perguntas gerais sem skill, como `batata combina com banana?`, recebem uma orientação curta; não são enviadas a IA generativa.

## Telegram

- `/start`: abre a apresentação/menu.
- `/help`: mostra ajuda.
- mensagens de texto: passam pelo roteador local.
- botões: enviam callbacks tratados pelo mesmo pipeline.
- acesso: limitado ao `ALLOWED_USER_ID` configurado.

O bot usa long polling, portanto não precisa de webhook público.

## Dashboard local

Na mesma rede, acesse `http://IP_DO_PI:8000/`. O endereço exato depende do IP reservado para o Raspberry Pi. A API de saúde está em `/api/system/health`.

Não exponha a porta 8000 diretamente à internet: este projeto não documenta autenticação própria para o dashboard.

## Quando algo não responder

1. Tente `menu` ou uma frase direta.
2. Verifique se o Telegram e a internet estão acessíveis.
3. No Pi, execute `docker compose ps`.
4. Consulte `docker logs --tail 100 rod_cerrado`.
5. Teste o healthcheck local.
