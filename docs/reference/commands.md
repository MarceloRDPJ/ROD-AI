# Comandos suportados

## Sistema e rede

- `status`, `status do raspi`, `temperatura do pi`
- `speed`, `sped`, `sped treste`, `qual a velocidade da net`
- `ping`, `dispositivos da rede`, `menu rede`
- estatísticas do AdGuard
- `bloquear 192.168.1.77` ou `bloquear youtube.com`, sempre com confirmação
- depois de um alerta recente, `bloquear ele` resolve apenas o dispositivo daquele alerta e ainda pede confirmação
- `renomear 192.168.1.77 para TV Sala`
- `ligar pc` quando `PC_MAC` estiver configurado

O speedtest mede a conexão vista pelo Raspberry Pi e fica limitado pela Ethernet de 100 Mbit/s do Pi 3B.

## Organização pessoal

- `menu agenda`, `meus lembretes`
- criação, listagem e cancelamento de lembretes
- registro e resumo de hidratação

## Segurança

Frases ambíguas como `bluqear sit` não acionam bloqueio. Ações que alteram estado exigem verbo explícito, alvo válido e confirmação. A confirmação pode ser feita pelo botão ou por `confirmar`; ela expira em 15 minutos e sobrevive a reinícios do contêiner. Assuntos fora das skills recebem uma orientação curta com atalhos para Sistema, Rede, Agenda e Menu, sem resposta inventada.
