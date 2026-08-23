# Validação real — ROD 1.0.5 — 2026-08-22

Validação executada com o Raspberry Pi de produção e o Poco X3 NFC real. Este
arquivo registra somente estados e presença de campos; nenhum identificador,
credencial ou valor de fatura foi copiado.

## Ambiente

- Pi em `main`, commit `05fee01`.
- Contêiner `rod_cerrado` saudável.
- Assistente em `local_skills`, sem IA generativa.
- Poco online, agente 1.0.5 (versionCode 37), Wi-Fi conectado.
- Serviço `ROD — automação local` habilitado e vinculado pelo Android.
- Cinco imóveis lógicos de água e cinco de energia presentes no heartbeat.

## Saneago

A ação real `refresh_saneago_bills` para `casa` passou por abrir aplicativo,
ler sessão, selecionar conta e ler a fatura. Terminou `completed`. O payload
continha os campos `account`, `amount`, `due_date`, `reference`, `consumption` e
`property`. Os valores não foram impressos nesta evidência.

## Equatorial

A ação real `refresh_equatorial_bills` abriu o Chrome, identificou sessão
expirada, abriu o formulário oficial, preencheu unidade e documento pelo cofre e
acionou o botão visível de entrada.

O portal recarregou o formulário quatro vezes sem mensagem de recusa. Nas quatro
reaparições o ROD confirmou estruturalmente os campos, preencheu novamente e
acionou `ENTRAR`. Na reaparição seguinte a máquina encerrou pelo limite e
devolveu `EQUATORIAL_LOGIN_FAILED`. Não houve loop, CAPTCHA contornado, token
fabricado ou tentativa ilimitada. O portal não concedeu uma sessão utilizável
para a fatura neste teste.

## APK

A tela inicial foi aberta e capturada depois dos testes. Exibiu a marca RDP,
`PI + POCO ONLINE`, estado do Keystore e os controles operacionais com feedback
visual. O rodapé usa a versão gerada pelo build, não texto fixo legado.
